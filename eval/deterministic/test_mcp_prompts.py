"""The workflow prompt catalogue, exercised over the real MCP protocol.

Prompts are the user-controlled MCP primitive: an administrator picks one, the
model never invokes them. Keeping them on the server rather than in one client's
code means every MCP client discovers the same workflows — so these assert the
catalogue is actually advertised and renders, not just that the functions exist.
"""
from __future__ import annotations

import pytest

from fleet_copilot.mcp_server.prompts import PROMPTS

pytestmark = pytest.mark.anyio

EXPECTED = {
    "fleet_health_review",
    "compliance_audit",
    "hardware_refresh_candidates",
    "device_deep_dive",
    "storage_pressure_triage",
    "unapproved_software_report",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_every_prompt_has_a_docstring():
    """The docstring becomes the description an MCP client shows."""
    for fn in PROMPTS:
        assert fn.__doc__, f"{fn.__name__} has no description"


async def test_catalogue_is_advertised(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        listing = await session.session.list_prompts()

    assert {p.name for p in listing.prompts} == EXPECTED


async def test_each_prompt_carries_a_description(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        listing = await session.session.list_prompts()

    for prompt in listing.prompts:
        assert prompt.description, f"{prompt.name} advertised without a description"


async def test_required_arguments_are_declared(mcp_session_factory):
    """A client needs to know it must collect a device id before rendering."""
    async with mcp_session_factory("acme-001") as session:
        listing = await session.session.list_prompts()

    by_name = {p.name: p for p in listing.prompts}
    deep_dive = by_name["device_deep_dive"]
    required = {a.name for a in (deep_dive.arguments or []) if a.required}
    assert required == {"device_id"}

    # A prompt whose arguments all have defaults must not demand anything.
    refresh = by_name["hardware_refresh_candidates"]
    assert not [a for a in (refresh.arguments or []) if a.required]


async def test_arguments_are_substituted_into_the_rendered_text(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.session.get_prompt(
            "device_deep_dive", {"device_id": "8NM23J95R5I6"}
        )

    text = result.messages[0].content.text
    assert "8NM23J95R5I6" in text


async def test_defaults_apply_when_an_argument_is_omitted(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.session.get_prompt("storage_pressure_triage", {})

    assert "15.0%" in result.messages[0].content.text


async def test_a_prompt_can_name_the_bound_tenant(mcp_session_factory):
    """Prompts read the session's company rather than asking for it."""
    async with mcp_session_factory("globex-002") as session:
        result = await session.session.get_prompt("unapproved_software_report", {})

    assert "globex-002" in result.messages[0].content.text


async def test_an_unknown_prompt_is_rejected(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        with pytest.raises(Exception):
            await session.session.get_prompt("no_such_prompt", {})


async def test_every_advertised_prompt_renders(mcp_session_factory):
    """Nothing in the catalogue is broken or empty."""
    async with mcp_session_factory("acme-001") as session:
        listing = await session.session.list_prompts()
        for prompt in listing.prompts:
            args = {
                a.name: "8NM23J95R5I6"
                for a in (prompt.arguments or [])
                if a.required
            }
            result = await session.session.get_prompt(prompt.name, args)
            text = result.messages[0].content.text
            assert len(text) > 80, f"{prompt.name} rendered suspiciously short"
