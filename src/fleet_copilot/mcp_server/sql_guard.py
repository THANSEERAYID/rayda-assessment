"""Validation for the read-only query tool.

Letting a model write SQL widens what can be answered and, done naively, throws
away the three properties the rest of this system is built on: tenant isolation
stops being structural, evidence stops being emitted, and capability scoping
stops being enforceable. This module is the first of two defences that keep the
first of those.

**Layer one, here:** the statement must be a single read, may only name
allowlisted telemetry tables, and may not qualify a name with a schema — the
tenant-scoped views are shadowed *unqualified* names, so ``public.snapshots``
would reach straight past them to the whole fleet.

**Layer two, in the tool:** the query never runs against base tables at all. Temp
views filtered to the bound tenant shadow each allowlisted name for the life of
the connection, so even a query this module wrongly admits sees one company's
rows.

Neither layer is trusted alone. Parsing SQL with regular expressions is not a
sound way to prove a statement is safe, which is exactly why the view layer
exists underneath it — and why the allowlist covers telemetry only, never the
operational tables (actions, audit, threads, checkpoints) that carry decisions
and other tenants' identifiers.
"""
from __future__ import annotations

import re

# Telemetry only. Operational tables are excluded deliberately: they hold
# approval decisions, audit history and thread bindings, none of which a
# question about the fleet needs and all of which name other tenants.
QUERYABLE_TABLES = frozenset(
    {
        "snapshots",
        "devices",
        "employees",
        "compliance_results",
        "installed_software",
        "companies",
    }
)

# Anything that writes, changes structure, grants rights, touches the filesystem
# or reaches into the server's own catalogues.
_FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", "commit", "rollback", "savepoint", "vacuum", "analyze",
    "attach", "detach", "pragma", "copy", "call", "do", "merge", "replace",
    "set", "reset", "listen", "notify", "load", "execute", "prepare", "lock",
    "reindex", "cluster", "comment", "refresh", "import", "into", "returning",
)

# Catalogue and filesystem reach — never reachable through a shadowed view.
_FORBIDDEN_NAMES = (
    "pg_catalog", "pg_class", "pg_tables", "pg_user", "pg_shadow", "pg_authid",
    "pg_settings", "pg_stat", "pg_read_file", "pg_ls_dir", "pg_sleep",
    "information_schema", "sqlite_master", "sqlite_schema", "sqlite_temp_master",
    "current_setting", "set_config", "dblink", "lo_import", "lo_export",
)

_TABLE_REF = re.compile(r"\b(?:from|join)\s+([A-Za-z_][\w.\"]*)", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


class UnsafeQuery(ValueError):
    """The statement is not a plain, single, tenant-scoped read."""


def _strip_literals_and_comments(sql: str) -> str:
    """Remove string literals and comments before keyword scanning.

    A device name containing the word "update" is not a write, and `--` can hide
    the rest of a statement from a naive scan.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:''|[^'])*'", " 'literal' ", sql)
    return sql


def validate_select(sql: str) -> str:
    """Return the statement if it is a safe single read, else raise UnsafeQuery."""
    if not sql or not sql.strip():
        raise UnsafeQuery("The query is empty.")

    statement = sql.strip().rstrip(";").strip()
    scannable = _strip_literals_and_comments(statement)

    # A second statement is how a read becomes a write.
    if ";" in scannable:
        raise UnsafeQuery(
            "Only one statement may be run. Remove the ';' and everything after it."
        )

    lowered = scannable.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise UnsafeQuery("Only SELECT statements are allowed.")

    words = {w.lower() for w in _WORD.findall(scannable)}
    banned = words & set(_FORBIDDEN)
    if banned:
        raise UnsafeQuery(
            f"'{sorted(banned)[0]}' is not allowed — this tool only reads."
        )

    for name in _FORBIDDEN_NAMES:
        if name in lowered:
            raise UnsafeQuery(f"'{name}' is not readable through this tool.")

    # A CTE name looks like a table to the scan below, but it resolves to the
    # query's own subselect — whose sources are checked on their own.
    referenced = _referenced_tables(scannable) - _cte_names(scannable)
    unknown = referenced - QUERYABLE_TABLES
    if unknown:
        raise UnsafeQuery(
            f"Table '{sorted(unknown)[0]}' is not queryable. "
            f"Available: {', '.join(sorted(QUERYABLE_TABLES))}."
        )
    if not referenced:
        raise UnsafeQuery(
            "The query names no table. Query one of: "
            f"{', '.join(sorted(QUERYABLE_TABLES))}."
        )
    return statement


_CTE_NAME = re.compile(r"(?:\bwith\b|,)\s*([A-Za-z_]\w*)\s+as\s*\(", re.IGNORECASE)


def _cte_names(sql: str) -> set[str]:
    return {name.lower() for name in _CTE_NAME.findall(sql)}


def _referenced_tables(sql: str) -> set[str]:
    """Table names following FROM or JOIN, rejecting any schema qualification."""
    names: set[str] = set()
    for raw in _TABLE_REF.findall(sql):
        name = raw.strip().strip('"')
        if name.startswith("("):  # a subquery, not a table
            continue
        if "." in name:
            # The tenant filter lives in a view that shadows the *unqualified*
            # name, so a qualified one would bypass it entirely.
            raise UnsafeQuery(
                f"'{name}' is schema-qualified. Use the plain table name."
            )
        names.add(name.lower())
    return names
