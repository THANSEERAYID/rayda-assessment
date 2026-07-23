"""Conversation endpoints."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import openai
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ...agent.runtime import (
    ApprovalExpired,
    TurnRequest,
    TurnTimeout,
    create_thread,
    run_turn,
)
from ...agent.progress import ProgressStream, bind, unbind
from ...domain.errors import TenantViolation
from ...agent.llm import MissingAPIKey
from ...storage.db import connect
from ...storage.repositories.audit import ThreadRepository
from ..deps import require_company
from ..llm_errors import translate_llm_error
from ..schemas import MessageIn, StartThreadIn, ThreadOut, TurnOut

router = APIRouter(tags=["chat"])


@router.post("/threads", response_model=ThreadOut)
def start_thread(payload: StartThreadIn) -> ThreadOut:
    """Open a conversation bound to one tenant for its lifetime."""
    company_id = require_company(payload.company_id)
    thread_id = create_thread(company_id, payload.title)
    return ThreadOut(thread_id=thread_id, company_id=company_id, title=payload.title)


@router.get("/threads", response_model=list[ThreadOut])
def list_threads(company_id: str) -> list[ThreadOut]:
    """A company's conversations, most recently active first.

    Carries step counts and the opening question so the trace viewer can offer a
    choice between threads rather than showing whichever one the app happens to
    have selected.
    """
    require_company(company_id)
    with connect() as conn:
        repo = ThreadRepository(conn)
        rows = repo.list_with_activity(company_id)
        questions = repo.first_questions([r.thread_id for r in rows])
    return [
        ThreadOut(
            thread_id=r.thread_id,
            company_id=r.company_id,
            title=r.title or questions.get(r.thread_id),
            step_count=r.step_count,
            last_activity=r.last_activity.isoformat() if r.last_activity else None,
        )
        for r in rows
    ]


@router.post("/messages", response_model=TurnOut)
async def send_message(payload: MessageIn) -> TurnOut:
    """Run one turn.

    Returns either a grounded answer, a typed refusal, or — when the agent
    proposed actions — a result with ``awaiting_approval`` set and the proposals
    attached. Nothing has been carried out in that case.
    """
    require_company(payload.company_id)
    try:
        result = await run_turn(
            TurnRequest(
                thread_id=payload.thread_id,
                company_id=payload.company_id,
                question=payload.message,
                source=payload.source,
            )
        )
    except TenantViolation as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except ApprovalExpired as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TurnTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except MissingAPIKey as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except openai.APIError as exc:
        raise translate_llm_error(exc) from exc
    return TurnOut(**result.model_dump())


@router.post("/messages/stream")
async def send_message_stream(payload: MessageIn) -> StreamingResponse:
    """Run a turn, narrating each step as it happens.

    Server-sent events rather than a WebSocket: the stream is one-directional
    (the client sends nothing once the turn starts), it is plain HTTP so it
    survives proxies without an upgrade handshake, and there is no connection
    lifecycle to manage. A WebSocket would buy bidirectionality nobody needs.

    The turn runs as a task so events can be forwarded while it is still going.
    The final frame carries the same ``TurnOut`` body the non-streaming endpoint
    returns, so a client can use either and get identical data.
    """
    require_company(payload.company_id)

    async def frames() -> AsyncIterator[str]:
        def frame(event: dict) -> str:
            # An SSE frame is "data: <payload>" terminated by a blank line.
            return "data: " + json.dumps(event, default=str) + "\n\n"

        # The turn is started here, inside the generator, rather than in the
        # endpoint body. Starlette iterates this generator in its own task, and
        # the endpoint's context is already gone by then — a turn launched there
        # would leave the MCP client's anyio task group owned by a dead context
        # and hang on teardown after the last node had already run.
        stream = ProgressStream()
        bind(stream)
        turn = asyncio.create_task(
            run_turn(
                TurnRequest(
                    thread_id=payload.thread_id,
                    company_id=payload.company_id,
                    question=payload.message,
                    source=payload.source,
                )
            )
        )
        unbind()

        yield frame({"type": "started", "thread_id": payload.thread_id})

        try:
            while True:
                # Wake periodically so a finished-but-silent turn is noticed and
                # a long model call still gets a keep-alive.
                try:
                    event = await asyncio.wait_for(stream.queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    if turn.done():
                        break
                    yield ": keep-alive\n\n"
                    continue

                if event is None:  # sentinel from close()
                    break
                yield frame(event)

            result = await turn
            yield frame({"type": "result", "result": result.model_dump(mode="json")})

        except Exception as exc:  # the turn failed — report it in-band
            if not turn.done():
                turn.cancel()
            yield frame({"type": "error", **_stream_error(exc)})
        finally:
            stream.close()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx buffers text/event-stream by default, which defeats the point.
            "X-Accel-Buffering": "no",
        },
    )


def _stream_error(exc: Exception) -> dict[str, object]:
    """Map a turn failure onto the same statuses the JSON endpoint returns.

    The stream has already committed to 200 by the time a turn fails, so the
    status travels in the payload instead of the response line.
    """
    if isinstance(exc, TenantViolation):
        return {"status": 403, "detail": exc.message}
    if isinstance(exc, ApprovalExpired):
        return {"status": 409, "detail": str(exc)}
    if isinstance(exc, TurnTimeout):
        return {"status": 504, "detail": str(exc)}
    if isinstance(exc, MissingAPIKey):
        return {"status": 503, "detail": str(exc)}
    if isinstance(exc, openai.APIError):
        translated = translate_llm_error(exc)
        return {"status": translated.status_code, "detail": translated.detail}
    return {"status": 500, "detail": f"{type(exc).__name__}: {exc}"}
