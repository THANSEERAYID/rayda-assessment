"""Workflow prompts, surfaced from the MCP server to the UI.

These are read straight from the tool server over MCP rather than duplicated
here, so the catalogue the UI shows is the same one any other MCP client would
discover. Adding a prompt in ``mcp_server/prompts.py`` makes it appear in the UI
with no frontend change.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...agent.mcp_client import open_tool_session
from ..deps import require_company
from ..schemas import PromptArgumentOut, PromptOut, PromptTextOut

router = APIRouter(tags=["prompts"])

# Prompts are static templates, so a throwaway session id is fine — nothing here
# touches conversation state.
_PROBE_THREAD = "prompt-catalogue"


def _title(name: str) -> str:
    return name.replace("_", " ").capitalize()


@router.get("/prompts", response_model=list[PromptOut])
async def list_prompts(company_id: str) -> list[PromptOut]:
    """The workflow catalogue for a tenant."""
    require_company(company_id)
    async with open_tool_session(company_id, _PROBE_THREAD) as session:
        listing = await session.session.list_prompts()

    return [
        PromptOut(
            name=p.name,
            title=getattr(p, "title", None) or _title(p.name),
            description=p.description,
            arguments=[
                PromptArgumentOut(
                    name=a.name,
                    description=a.description,
                    required=bool(a.required),
                )
                for a in (p.arguments or [])
            ],
        )
        for p in listing.prompts
    ]


@router.post("/prompts/{name}", response_model=PromptTextOut)
async def render_prompt(
    name: str, company_id: str, arguments: dict[str, str] | None = None
) -> PromptTextOut:
    """Render a template into the question text to send as a message."""
    require_company(company_id)

    # The failure is recorded and re-raised *after* the session closes. Raising
    # inside it would be caught by the MCP transport's anyio TaskGroup and
    # re-emitted as an ExceptionGroup, which FastAPI turns into a 500 instead of
    # the 404 this is.
    result = None
    failure: Exception | None = None
    async with open_tool_session(company_id, _PROBE_THREAD) as session:
        try:
            result = await session.session.get_prompt(name, arguments or {})
        except Exception as exc:
            failure = exc

    if failure is not None or result is None:
        raise HTTPException(status_code=404, detail=f"Unknown prompt '{name}'.")

    text = "\n\n".join(
        m.content.text for m in result.messages if getattr(m.content, "text", None)
    )
    return PromptTextOut(name=name, text=text)
