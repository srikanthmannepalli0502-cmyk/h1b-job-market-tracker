"""Checks model-written SQL before it runs. Defense in depth: the database it runs
against is also in-memory, holds only gold tables, and has file/network access
disabled, so even SQL that slipped past these checks could not reach anything else.
"""

import json
import re

import duckdb

MAX_SQL_CHARS = 4000

# Functions and keywords that read files, reach the network, or change state.
FORBIDDEN = re.compile(
    r"\b(read_\w+|glob|copy|attach|detach|install|load|pragma|set|reset|call|export|import|"
    r"create|insert|update|delete|drop|alter|truncate|vacuum|checkpoint|"
    r"httpfs|http_get|getenv|current_setting|duckdb_\w+|sniff_csv|query_table|query)\b"
    r"|https?://|s3://|az://|abfss?://|gs://",
    re.IGNORECASE,
)


class UnsafeSQL(ValueError):
    pass


def check(sql: str, allowed_tables: set[str]) -> str:
    """Return the SQL (stripped, no trailing semicolon) if it is a single read-only
    SELECT over allowed tables; raise UnsafeSQL otherwise."""
    if not sql or not sql.strip():
        raise UnsafeSQL("empty query")
    sql = sql.strip().rstrip(";").strip()
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeSQL("query too long")
    if match := FORBIDDEN.search(_without_strings(sql)):
        raise UnsafeSQL(f"forbidden keyword or function: {match.group(0)!r}")

    # Let DuckDB's own parser decide what the statement is.
    con = duckdb.connect()
    try:
        parsed = json.loads(con.execute("select json_serialize_sql(?)", [sql]).fetchone()[0])
    finally:
        con.close()
    if parsed.get("error"):
        raise UnsafeSQL(f"could not parse SQL: {parsed.get('error_message', 'syntax error')}")
    statements = parsed.get("statements", [])
    if len(statements) != 1:
        raise UnsafeSQL("exactly one statement is allowed")
    if statements[0]["node"]["type"] != "SELECT_NODE" and not _is_set_operation(statements[0]["node"]):
        raise UnsafeSQL("only SELECT queries are allowed")

    unknown = _table_names(statements[0]["node"]) - {t.lower() for t in allowed_tables}
    if unknown:
        raise UnsafeSQL(f"unknown or disallowed tables: {sorted(unknown)}")
    return sql


def _without_strings(sql: str) -> str:
    """Blank out string literals so words inside them ('Data Analyst') aren't treated as keywords."""
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _is_set_operation(node: dict) -> bool:
    return node.get("type") == "SET_OPERATION_NODE"


def _table_names(node, ctes: set[str] | None = None) -> set[str]:
    """Base tables referenced anywhere in a serialized DuckDB query tree, excluding CTE names."""
    ctes = set(ctes or set())
    found: set[str] = set()

    def walk(obj):
        if isinstance(obj, dict):
            cte_map = obj.get("cte_map", {}).get("map") if isinstance(obj.get("cte_map"), dict) else None
            if cte_map:
                for entry in cte_map:
                    ctes.add(str(entry.get("key", "")).lower())
            if obj.get("type") == "BASE_TABLE":
                name = str(obj.get("table_name", "")).lower()
                schema = str(obj.get("schema_name", "") or "").lower()
                catalog = str(obj.get("catalog_name", "") or "").lower()
                if schema or catalog:
                    found.add(f"{catalog}.{schema}.{name}".strip("."))
                elif name not in ctes:
                    found.add(name)
            if obj.get("type") == "TABLE_FUNCTION":
                found.add("table function")
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(node)
    return found
