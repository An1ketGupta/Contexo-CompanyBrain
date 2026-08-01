"""A minimal in-memory stand-in for the Supabase client.

Supports only the query shapes the document pipeline actually uses — select with
eq/in_/or_/order/limit, insert, update, delete. It exists so the persistence
rules in `services/documents/schema.py` can be tested for real, rather than
asserted about in a comment.

Not a general-purpose fake, and deliberately so: anything it does not support
raises, which keeps it honest about its own coverage.
"""
from __future__ import annotations

import uuid
from typing import Any


class _Result:
    def __init__(self, data: list[dict[str, Any]] | dict[str, Any] | None):
        self.data = data


class _Query:
    def __init__(self, store: "FakeSupabase", table: str):
        self._store = store
        self._table = table
        self._filters: list[tuple[str, str, Any]] = []
        self._op = "select"
        self._payload: Any = None
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._embed: list[str] = []
        self._single = False

    # ── builders ──────────────────────────────────────────────────────────
    def select(self, columns: str = "*") -> "_Query":
        self._op = "select"
        # Parse PostgREST embeds like "*, doc_template_variables(a, b)".
        for part in columns.split(","):
            part = part.strip()
            if "(" in part:
                self._embed.append(part.split("(")[0].strip())
        return self

    def insert(self, row: dict[str, Any] | list[dict[str, Any]]) -> "_Query":
        """A single row or a batch, mirroring PostgREST."""
        self._op = "insert"
        self._payload = row
        return self

    def update(self, patch: dict[str, Any]) -> "_Query":
        self._op = "update"
        self._payload = patch
        return self

    def delete(self) -> "_Query":
        self._op = "delete"
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self._filters.append(("eq", column, value))
        return self

    def neq(self, column: str, value: Any) -> "_Query":
        self._filters.append(("neq", column, value))
        return self

    def in_(self, column: str, values: list[Any]) -> "_Query":
        self._filters.append(("in", column, values))
        return self

    def or_(self, expression: str) -> "_Query":
        self._filters.append(("or", "", expression))
        return self

    def order(self, column: str, desc: bool = False) -> "_Query":
        self._order.append((column, desc))
        return self

    def limit(self, n: int) -> "_Query":
        self._limit = n
        return self

    def maybe_single(self) -> "_Query":
        """PostgREST's "at most one row" mode: `.data` is the row itself
        rather than a list, and None when nothing matched."""
        self._single = True
        self._limit = 1
        return self

    # ── execution ─────────────────────────────────────────────────────────
    def _matches(self, row: dict[str, Any]) -> bool:
        for kind, column, value in self._filters:
            if kind == "eq" and row.get(column) != value:
                return False
            if kind == "neq" and row.get(column) == value:
                return False
            if kind == "in" and row.get(column) not in value:
                return False
            if kind == "or":
                # Only the shape used by list_document_types:
                # "org_id.is.null,org_id.eq.<uuid>"
                ok = False
                for clause in value.split(","):
                    field, op, operand = clause.split(".", 2)
                    if op == "is" and operand == "null" and row.get(field) is None:
                        ok = True
                    elif op == "eq" and str(row.get(field)) == operand:
                        ok = True
                if not ok:
                    return False
        return True

    def execute(self) -> _Result:
        rows = self._store.tables.setdefault(self._table, [])

        if self._op == "insert":
            payload = (
                self._payload
                if isinstance(self._payload, list)
                else [self._payload]
            )
            inserted: list[dict[str, Any]] = []
            for candidate in payload:
                row = dict(candidate)
                row.setdefault("id", str(uuid.uuid4()))
                for column in ("created_at", "updated_at"):
                    row.setdefault(column, "2026-01-01T00:00:00Z")
                self._store.enforce_unique(self._table, row)
                rows.append(row)
                inserted.append(dict(row))
            return _Result(inserted)

        matched = [r for r in rows if self._matches(r)]

        if self._op == "update":
            for row in matched:
                row.update(self._payload)
            return _Result([dict(r) for r in matched])

        if self._op == "delete":
            for row in matched:
                rows.remove(row)
            return _Result([dict(r) for r in matched])

        # select
        for column, desc in reversed(self._order):
            matched.sort(key=lambda r: (r.get(column) is None, r.get(column)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]

        out = [dict(r) for r in matched]
        for embed in self._embed:
            for row in out:
                row[embed] = self._store.resolve_embed(self._table, embed, row)
        if self._single:
            return _Result(out[0] if out else None)
        return _Result(out)


class FakeSupabase:
    """In-memory tables keyed by name."""

    # Which column joins a table to an embedded one.
    EMBED_KEYS = {
        ("doc_template_slots", "doc_template_variables"): "variable_id",
        ("doc_templates", "document_types"): "document_type_id",
    }

    # Unique constraints worth modelling, because callers depend on them
    # firing. The onboarding step catalog makes seeding and per-run
    # materialization idempotent by letting the loser of a race hit the
    # constraint and re-read; a fake that accepted duplicates would report
    # those paths as working while the real database rejected them.
    UNIQUE_KEYS = {
        "onboarding_step_defs": ("org_id", "step_key"),
        "onboarding_run_steps": ("run_id", "step_key"),
        "onboarding_collect_items": ("step_def_id", "item_key"),
        "onboarding_collect_submissions": ("run_step_id", "item_key"),
    }

    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None):
        self.tables: dict[str, list[dict[str, Any]]] = tables or {}

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def enforce_unique(self, table: str, row: dict[str, Any]) -> None:
        """Raise the way Postgres would on a duplicate key."""
        columns = self.UNIQUE_KEYS.get(table)
        if not columns:
            return
        key = tuple(row.get(c) for c in columns)
        for existing in self.tables.get(table, []):
            if tuple(existing.get(c) for c in columns) == key:
                raise ValueError(
                    f'duplicate key value violates unique constraint on '
                    f'{table}{columns}'
                )

    def resolve_embed(
        self, parent: str, embed: str, row: dict[str, Any]
    ) -> dict[str, Any] | None:
        key = self.EMBED_KEYS.get((parent, embed))
        if not key:
            return None
        target_id = row.get(key)
        if not target_id:
            return None
        for candidate in self.tables.get(embed, []):
            if candidate.get("id") == target_id:
                return dict(candidate)
        return None
