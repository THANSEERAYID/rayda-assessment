"""Start the API on an event loop this application can actually use.

Windows forces a choice that matters here. ``psycopg``'s async driver — which
the LangGraph Postgres checkpointer uses — only works on a
``SelectorEventLoop``. Uvicorn picks its loop from a factory that returns
``ProactorEventLoop`` on Windows *unless* it is running with a reload supervisor
or multiple workers:

    uvicorn app                 -> ProactorEventLoop  -> checkpointer fails
    uvicorn app --reload        -> SelectorEventLoop  -> works

That difference is invisible until a turn 500s, and depending on a launch flag
for correctness is not a contract worth having. This entry point pins the loop
explicitly so the app behaves the same however it is started.

    python scripts/run_api.py [--port 8000] [--reload]
"""
from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Fleet Copilot API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    if args.reload:
        # The reload supervisor runs the app in a child process, and uvicorn
        # already selects a SelectorEventLoop for that case.
        uvicorn.run(
            "fleet_copilot.api.main:app",
            host=args.host,
            port=args.port,
            reload=True,
            # Uvicorn watches *.py only. The agent's behaviour lives as much in
            # the prompt files as in the code, and `load_prompt` caches them for
            # the life of the process — so without this an edited prompt is
            # silently ignored until someone restarts by hand.
            reload_includes=["*.py", "*.md"],
        )
        return 0

    # Otherwise create the loop ourselves rather than letting uvicorn choose.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    from fleet_copilot.api.main import app

    async def serve() -> None:
        server = uvicorn.Server(
            uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
        )
        await server.serve()

    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
