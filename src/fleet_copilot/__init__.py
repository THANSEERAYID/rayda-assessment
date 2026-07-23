"""Package bootstrap.

``psycopg``'s async driver cannot run on Windows' default ``ProactorEventLoop``
(it raises ``InterfaceError`` the moment a connection is opened). This must be
set before any event loop is created, so it happens at import time here rather
than in any one entry point — the API server, the eval suite, and standalone
scripts all import this package before they touch asyncio.
"""
from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
