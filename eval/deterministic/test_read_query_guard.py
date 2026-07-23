"""The read-only query tool must not become a way out of the tenant.

Two layers are under test. ``validate_select`` rejects anything that is not a
single, unqualified, telemetry-only read. Underneath it, tenant-scoped temp
views shadow every queryable table, so a statement the guard wrongly admits
still cannot see another company.

The second layer is the one that matters: regex over SQL is not a soundness
proof, and these tests are written on the assumption that the guard will
eventually be fooled.
"""
from __future__ import annotations

import pytest

from fleet_copilot.mcp_server.sql_guard import (
    QUERYABLE_TABLES,
    UnsafeQuery,
    validate_select,
)

OK = "select device_id, disk_free_pct from snapshots"


# -- layer one: statement validation ----------------------------------------


def test_a_plain_select_is_allowed():
    assert validate_select(OK) == OK


def test_a_cte_is_allowed():
    sql = "with recent as (select * from snapshots) select count(*) from recent"
    assert validate_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "insert into snapshots values (1)",
        "update snapshots set disk_free_pct = 100",
        "delete from snapshots",
        "drop table snapshots",
        "alter table snapshots add column x int",
        "create table evil (id int)",
        "truncate snapshots",
        "grant select on snapshots to public",
    ],
)
def test_writes_and_ddl_are_refused(sql):
    with pytest.raises(UnsafeQuery):
        validate_select(sql)


def test_a_second_statement_is_refused():
    """The classic escalation: a read followed by a write."""
    with pytest.raises(UnsafeQuery):
        validate_select(f"{OK}; drop table snapshots")


def test_a_write_hidden_behind_a_comment_is_refused():
    with pytest.raises(UnsafeQuery):
        validate_select(f"{OK} -- harmless\n; delete from devices")


def test_a_keyword_inside_a_string_literal_is_not_a_write():
    """A device named 'update' must not make an honest query look hostile."""
    assert validate_select(
        "select * from snapshots where hostname = 'update-server'"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "select * from pending_actions",
        "select * from audit_log",
        "select * from threads",
        "select * from checkpoints",
        "select * from evidence",
    ],
)
def test_operational_tables_are_not_queryable(sql):
    """Approvals, audit history and thread bindings are not fleet telemetry."""
    with pytest.raises(UnsafeQuery):
        validate_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "select * from information_schema.tables",
        "select * from pg_catalog.pg_class",
        "select * from sqlite_master",
        "select pg_read_file('/etc/passwd')",
        "select * from snapshots where 1=1 and current_setting('x') is null",
    ],
)
def test_catalogues_and_filesystem_are_unreachable(sql):
    with pytest.raises(UnsafeQuery):
        validate_select(sql)


def test_schema_qualification_is_refused():
    """The tenant filter shadows the *unqualified* name — qualifying skips it."""
    with pytest.raises(UnsafeQuery) as exc:
        validate_select("select * from public.snapshots")
    assert "qualified" in str(exc.value).lower()


def test_a_join_onto_a_forbidden_table_is_refused():
    with pytest.raises(UnsafeQuery):
        validate_select(
            "select * from snapshots join pending_actions on true"
        )


def test_every_queryable_table_is_telemetry():
    """A regression guard on the allowlist itself."""
    assert QUERYABLE_TABLES == {
        "snapshots",
        "devices",
        "employees",
        "compliance_results",
        "installed_software",
        "companies",
    }


# -- layer two: the tenant view, which does not trust layer one --------------


@pytest.mark.asyncio
async def test_results_are_restricted_to_the_bound_tenant(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "run_read_query",
            {"sql": "select distinct company_id from snapshots"},
        )
    companies = {row["company_id"] for row in result["rows"]}
    assert companies == {"acme-001"}


@pytest.mark.asyncio
async def test_an_explicit_foreign_filter_returns_nothing(mcp_session_factory):
    """Asking for another tenant by name is not an error — there is just no row."""
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "run_read_query",
            {"sql": "select * from snapshots where company_id = 'globex-002'"},
        )
    assert result["rows"] == []
    assert result["row_count"] == 0


@pytest.mark.asyncio
async def test_the_company_table_shows_only_the_bound_tenant(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call("run_read_query", {"sql": "select * from companies"})
    assert [r["company_id"] for r in result["rows"]] == ["acme-001"]


@pytest.mark.asyncio
async def test_rows_are_citable(mcp_session_factory):
    """Without evidence the results could not be used in a grounded answer."""
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "run_read_query",
            {"sql": "select device_id, disk_free_pct from snapshots limit 3"},
        )
    fields = {e["field"] for e in result["evidence"]}
    assert "query.row_count" in fields
    assert "query.result" in fields


@pytest.mark.asyncio
async def test_an_empty_result_is_still_citable(mcp_session_factory):
    """Absence is a finding; an uncitable one would force a refusal."""
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "run_read_query",
            {"sql": "select * from snapshots where disk_free_pct < 0"},
        )
    assert result["row_count"] == 0
    counts = [e for e in result["evidence"] if e["field"] == "query.row_count"]
    assert counts and counts[0]["value"] == 0


@pytest.mark.asyncio
async def test_results_are_capped(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "run_read_query", {"sql": "select * from snapshots", "limit": 5}
        )
    assert result["row_count"] == 5
    assert result["truncated"] is True
    assert "Aggregate in SQL" in (result["note"] or "")


@pytest.mark.asyncio
async def test_a_rejected_query_returns_a_typed_error(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "run_read_query", {"sql": "drop table snapshots"}
        )
    assert result["error"] is True
    assert result["reason"] == "invalid_arguments"


@pytest.mark.asyncio
async def test_broken_sql_fails_as_data_not_a_crash(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "run_read_query", {"sql": "select nonexistent_column from snapshots"}
        )
    assert result["error"] is True
    assert result["reason"] == "query_failed"


@pytest.mark.asyncio
async def test_passing_a_company_id_is_still_a_tenant_violation(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "run_read_query",
            {"sql": OK, "company_id": "globex-002"},
        )
    assert result["error"] is True
    assert result["reason"] == "cross_tenant"
