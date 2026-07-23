"""Tenant isolation at the HTTP read surface.

``test_tenant_isolation.py`` proves the tool layer is airtight: the MCP server is
bound to one company at launch and refuses foreign ids. That is the path the
*agent* takes. These endpoints are the path a *browser* takes, and they were not
covered — which is how ``GET /threads/{id}/trace`` shipped with no tenant check
at all, returning any thread's questions, tool arguments and retrieved telemetry
to anyone holding an id.

The rule under test: an identifier is never sufficient on its own. Every read is
scoped by a company the caller states, and a mismatch is refused.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from fleet_copilot.api.routers.actions import list_actions
from fleet_copilot.api.routers.chat import list_threads
from fleet_copilot.api.routers.insights import get_insights
from fleet_copilot.api.routers.traces import (
    get_audit,
    get_company_traces,
    get_evidence,
    get_trace,
)
from fleet_copilot.domain.models import Evidence
from fleet_copilot.storage.repositories.evidence import EvidenceRepository

OWNER = "acme-001"
OTHER = "globex-002"


@pytest.fixture()
def acme_thread(engine):
    """A thread with recorded steps, belonging to acme.

    Committed rather than written through the ``conn`` fixture's open
    transaction: the endpoints under test open their own connection, and would
    not see an uncommitted row. Removed afterwards so the suite stays repeatable.
    """
    with engine.begin() as setup:
        setup.execute(
            text(
                "insert into threads (thread_id, company_id, created_at, title) "
                "values ('thr-iso-test', :c, :now, 'isolation')"
            ),
            {"c": OWNER, "now": "2026-07-23 00:00:00"},
        )
        setup.execute(
            text(
                "insert into run_steps "
                "(thread_id, turn_id, seq, node, status, detail, created_at) "
                "values ('thr-iso-test', 'turn-iso', 1, 'plan', 'ok', :d, :now)"
            ),
            {"d": '{"question": "secret question"}', "now": "2026-07-23 00:00:00"},
        )
    yield "thr-iso-test"
    with engine.begin() as teardown:
        teardown.execute(text("delete from run_steps where thread_id='thr-iso-test'"))
        teardown.execute(text("delete from threads where thread_id='thr-iso-test'"))


@pytest.fixture()
def stored_evidence(engine):
    """A persisted reading owned by acme, for the citation-resolution tests."""
    with engine.begin() as setup:
        EvidenceRepository(setup).record_many(
            [
                Evidence(
                    evidence_id="ev-iso-secret",
                    tool="query_devices",
                    device_id="DEV-SECRET",
                    field="disk.free_pct",
                    value=2.0,
                )
            ],
            company_id=OWNER,
        )
    yield "ev-iso-secret"
    with engine.begin() as teardown:
        teardown.execute(text("delete from evidence where evidence_id='ev-iso-secret'"))


# -- run trace --------------------------------------------------------------


def test_trace_is_readable_by_the_owning_tenant(acme_thread):
    assert len(get_trace(acme_thread, OWNER).steps) == 1


def test_trace_is_refused_to_another_tenant(acme_thread):
    with pytest.raises(HTTPException) as exc:
        get_trace(acme_thread, OTHER)
    assert exc.value.status_code == 404


def test_a_foreign_trace_is_indistinguishable_from_a_missing_one(acme_thread):
    """Confirming a thread exists under another tenant is itself a disclosure."""
    with pytest.raises(HTTPException) as foreign:
        get_trace(acme_thread, OTHER)
    with pytest.raises(HTTPException) as missing:
        get_trace("thr-does-not-exist", OTHER)
    assert foreign.value.status_code == missing.value.status_code
    assert foreign.value.detail == missing.value.detail


def test_trace_rejects_an_unknown_company(acme_thread):
    with pytest.raises(HTTPException):
        get_trace(acme_thread, "not-a-company")


# -- company-wide reads -----------------------------------------------------


def test_company_traces_do_not_cross_tenants(acme_thread):
    owner_turns = {s.turn_id for s in get_company_traces(OWNER).steps}
    other_turns = {s.turn_id for s in get_company_traces(OTHER).steps}
    assert "turn-iso" in owner_turns
    assert not (owner_turns & other_turns)


def test_thread_listing_is_scoped(acme_thread):
    assert acme_thread in {t.thread_id for t in list_threads(OWNER)}
    assert acme_thread not in {t.thread_id for t in list_threads(OTHER)}


def test_audit_thread_filter_cannot_reach_another_tenant(acme_thread):
    """`thread_id` is a filter within the tenant, never a way out of it."""
    assert get_audit(OTHER, thread_id=acme_thread).events == []


def test_actions_are_scoped():
    for action in list_actions(OTHER).actions:
        assert action.company_id == OTHER


def test_insights_are_scoped():
    for finding in get_insights(OWNER).findings:
        assert finding.company_id == OWNER


# -- evidence ---------------------------------------------------------------


def test_evidence_ids_do_not_resolve_for_another_tenant(stored_evidence):
    """A citation id is a bare string on a proposal — guessing one must not pay."""
    assert len(get_evidence(OWNER, stored_evidence)["evidence"]) == 1
    assert get_evidence(OTHER, stored_evidence)["evidence"] == []


def test_evidence_rejects_an_unknown_company(stored_evidence):
    with pytest.raises(HTTPException):
        get_evidence("not-a-company", stored_evidence)


def test_every_read_endpoint_demands_a_company():
    """A signature without `company_id` cannot be scoping anything."""
    import inspect

    for fn in (
        get_trace,
        get_company_traces,
        get_audit,
        get_evidence,
        list_threads,
        list_actions,
        get_insights,
    ):
        assert "company_id" in inspect.signature(fn).parameters, fn.__name__
