"""Storage adapters. Lite implementations run in-process. Scale implementations
swap in later without changing the query tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import duckdb


class AnalyticsStore(Protocol):
    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]: ...

    def execute(self, sql: str, params: list[Any] | None = None) -> None: ...


class DuckDBStore:
    """Column store used in lite mode. The same SQL views are the contract
    a ClickHouse adapter will satisfy in scale mode."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._con = duckdb.connect(str(path))

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        return self._con

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        cursor = self._con.execute(sql, params or [])
        columns = [d[0] for d in cursor.description] if cursor.description else []
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self._con.execute(sql, params or [])

    def close(self) -> None:
        self._con.close()
