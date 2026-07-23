"""Server-side tenant binding for the MCP tool server.

The tenant is fixed when the server starts — from ``--company-id`` or
``FLEET_COMPANY_ID`` — and every tool resolves its scope from here. The model is
never handed the tenant and has no argument with which to change it.

Each tool nevertheless exposes an optional ``company_id`` parameter documented as
"do not set". It exists as a tripwire: if a model ever supplies one, the call is
rejected and audited rather than quietly ignored, which turns an attempted
cross-tenant access into a visible, testable event instead of a silent no-op.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy import Engine
from sqlalchemy.engine import Connection

from ..config import settings
from ..evidence.ledger import EvidenceLedger
from ..storage.db import create_db_engine
from ..services.tenant import TenantGuard


@dataclass
class ServerContext:
    """Process-wide state for one tenant-bound tool server.

    The server owns the evidence ledger because it is the only component that
    knows what the tools genuinely returned. Read tools register what they emit;
    action tools validate their citations against that record. Holding the ledger
    here means neither the model nor the agent process can introduce an evidence
    id that no tool ever produced.

    The ledger spans the session rather than a single turn, so evidence gathered
    while investigating can still justify an action proposed a turn later.
    """

    company_id: str
    engine: Engine
    thread_id: str | None = None
    ledger: EvidenceLedger = field(default_factory=EvidenceLedger)
    # Proposals made through this server. The process lives for exactly one
    # turn, so this is a per-turn count by construction — a thread-scoped count
    # would wrongly accumulate across a long conversation.
    proposals_made: int = 0

    @classmethod
    def create(
        cls, company_id: str, *, database_url: str | None = None
    ) -> "ServerContext":
        engine = create_db_engine(database_url or settings.database_url)
        return cls(company_id=company_id, engine=engine)

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        """A transactional connection.

        Read tools do not write telemetry, but they do append audit records for
        rejected calls, so every tool runs inside a transaction.
        """
        with self.engine.begin() as conn:
            yield conn

    def guard(self, conn: Connection) -> TenantGuard:
        return TenantGuard(conn, self.company_id, self.thread_id)


_context: ServerContext | None = None


def set_context(ctx: ServerContext) -> None:
    global _context
    _context = ctx


def get_context() -> ServerContext:
    if _context is None:
        raise RuntimeError(
            "MCP server context is not initialised — the server must be started "
            "with a bound company id."
        )
    return _context
