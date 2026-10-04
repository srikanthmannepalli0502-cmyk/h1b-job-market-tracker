"""Question -> SQL (model) -> guarded query (DuckDB) -> plain-English answer (model).

The model client is passed in, so tests can use a fake and the Function uses Azure OpenAI.
"""

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import duckdb

import prompts
import sql_guard

MAX_QUESTION_CHARS = 300
MAX_ROWS = 200
QUERY_TIMEOUT_S = 8


class ChatClient(Protocol):
    def complete(self, system: str, user: str, *, json_schema: dict | None, effort: str, max_tokens: int) -> str: ...


@dataclass
class Answer:
    answer: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict:
        return {"answer": self.answer, "sql": self.sql, "columns": self.columns,
                "rows": self.rows, "truncated": self.truncated}


class BadQuestion(ValueError):
    pass


def open_gold(gold_dir: Path) -> duckdb.DuckDBPyConnection:
    """In-memory database holding only the gold tables, with file and network access disabled."""
    con = duckdb.connect(":memory:")
    tables = []
    for path in sorted(Path(gold_dir).glob("*.parquet")):
        con.execute(f"create table {path.stem} as select * from read_parquet('{path.as_posix()}')")
        tables.append(path.stem)
    if not tables:
        raise RuntimeError(f"no gold tables in {gold_dir}")
    con.execute("set enable_external_access = false")
    con.execute("set max_memory = '1GB'")
    con.execute("set lock_configuration = true")
    return con


def table_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()}


def run_query(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[list], bool]:
    """Run guarded SQL with a row cap and a time limit."""
    cursor = con.cursor()
    timer = threading.Timer(QUERY_TIMEOUT_S, cursor.interrupt)
    timer.start()
    try:
        result = cursor.execute(f"select * from ({sql}) as q limit {MAX_ROWS + 1}")
        columns = [d[0] for d in result.description]
        rows = [list(r) for r in result.fetchall()]
    except duckdb.InterruptException as e:
        raise TimeoutError("query took too long") from e
    finally:
        timer.cancel()
        cursor.close()
    return columns, rows[:MAX_ROWS], len(rows) > MAX_ROWS


def _jsonable(value):
    if isinstance(value, float):
        return round(value, 4)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def ask(question: str, con: duckdb.DuckDBPyConnection, llm: ChatClient) -> Answer:
    question = (question or "").strip()
    if not question:
        raise BadQuestion("Please type a question.")
    if len(question) > MAX_QUESTION_CHARS:
        raise BadQuestion(f"Please keep questions under {MAX_QUESTION_CHARS} characters.")

    raw = llm.complete(prompts.SQL_SYSTEM, question, json_schema=prompts.SQL_RESPONSE_FORMAT,
                       effort="low", max_tokens=4000)
    plan = json.loads(raw)
    if not plan.get("sql"):
        return Answer(answer=plan.get("cannot_answer") or "I can't answer that from this data.")

    try:
        sql = sql_guard.check(plan["sql"], table_names(con))
        columns, rows, truncated = run_query(con, sql)
    except (sql_guard.UnsafeSQL, duckdb.Error, TimeoutError) as e:
        # One retry: show the model what went wrong.
        raw = llm.complete(prompts.SQL_SYSTEM,
                           f"{question}\n\nYour previous SQL failed: {e}\nPrevious SQL:\n{plan['sql']}",
                           json_schema=prompts.SQL_RESPONSE_FORMAT, effort="low", max_tokens=4000)
        plan = json.loads(raw)
        if not plan.get("sql"):
            return Answer(answer=plan.get("cannot_answer") or "I can't answer that from this data.")
        sql = sql_guard.check(plan["sql"], table_names(con))
        columns, rows, truncated = run_query(con, sql)

    rows = [[_jsonable(v) for v in r] for r in rows]
    preview = "\n".join([",".join(columns)] + [",".join(map(str, r)) for r in rows[:40]])
    summary = llm.complete(prompts.ANSWER_SYSTEM,
                           f"Question: {question}\nSQL: {sql}\nResult ({len(rows)} rows):\n{preview}",
                           json_schema=None, effort="minimal", max_tokens=800)
    return Answer(answer=summary.strip(), sql=sql, columns=columns, rows=rows, truncated=truncated)
