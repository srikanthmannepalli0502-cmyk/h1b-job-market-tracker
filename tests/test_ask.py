"""'Ask the data' assistant: SQL guard, engine (with a scripted fake model), daily cap."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ask"))
import engine  # noqa: E402
import sql_guard  # noqa: E402
import usage  # noqa: E402

TABLES = {"employer_role_year", "employer_approvals", "coverage"}


# ---------------------------------------------------------------- SQL guard

@pytest.mark.parametrize("sql", [
    "select employer from employer_role_year where role_family = 'data_analyst' limit 5",
    "with t as (select employer, sum(certified_applications) n from employer_role_year group by 1) "
    "select t.employer, a.total_denied from t join employer_approvals a using (employer)",
    "select 1 as x from coverage union all select 2 from coverage",
    "select * from employer_role_year where employer ilike '%copy%';",   # keyword inside a string
])
def test_guard_allows_read_only_selects(sql):
    assert sql_guard.check(sql, TABLES)


@pytest.mark.parametrize("sql", [
    "select * from read_csv('C:/secrets.csv')",
    "select * from 'data/warehouse.duckdb'",
    "select * from 'https://evil.example/x.parquet'",
    "select 1 from coverage; drop table coverage",
    "insert into coverage values (1)",
    "select * from information_schema.tables",
    "select * from duckdb_settings()",
    "pragma show_tables",
    "attach 'other.db'",
    "select * from other_table",
    "",
])
def test_guard_rejects_everything_else(sql):
    with pytest.raises(sql_guard.UnsafeSQL):
        sql_guard.check(sql, TABLES)


# ---------------------------------------------------------------- engine

@pytest.fixture
def gold(tmp_path):
    con = duckdb.connect()
    con.execute(f"""copy (select * from (values ('Acme Analytics', 'NY', 2025, 'data_analyst', 40),
                                                 ('Globex', 'TX', 2025, 'data_analyst', 12))
                         t(employer, hq_state, fiscal_year, role_family, certified_applications))
                   to '{(tmp_path / 'employer_role_year.parquet').as_posix()}' (format parquet)""")
    con.close()
    return engine.open_gold(tmp_path)


class FakeChat:
    """Replays scripted responses and records prompts."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, user, *, json_schema, effort, max_tokens):
        self.calls.append({"user": user, "json": json_schema is not None})
        return self.responses.pop(0)


def plan(sql=None, cannot=None):
    return json.dumps({"sql": sql, "cannot_answer": cannot})


def test_answers_with_rows_and_summary(gold):
    chat = FakeChat(plan("select employer, certified_applications from employer_role_year order by 2 desc"),
                    "Acme Analytics led with 40 applications in FY2025.")
    result = engine.ask("Who sponsors data analysts?", gold, chat)
    assert result.rows == [["Acme Analytics", 40], ["Globex", 12]]
    assert result.columns == ["employer", "certified_applications"]
    assert result.answer.startswith("Acme")
    assert "Acme Analytics,40" in chat.calls[1]["user"]      # summary sees the result


def test_unanswerable_question_skips_sql(gold):
    chat = FakeChat(plan(cannot="That isn't in this data."))
    result = engine.ask("What's the weather?", gold, chat)
    assert result.sql is None and result.answer == "That isn't in this data."
    assert len(chat.calls) == 1


def test_unsafe_sql_gets_one_retry_then_fails(gold):
    chat = FakeChat(plan("select * from read_csv('x.csv')"), plan("drop table employer_role_year"))
    with pytest.raises(sql_guard.UnsafeSQL):
        engine.ask("Show me everything", gold, chat)
    assert "previous SQL failed" in chat.calls[1]["user"]


def test_bad_sql_retry_can_recover(gold):
    chat = FakeChat(plan("select nope from employer_role_year"),
                    plan("select count(*) as n from employer_role_year"), "There are 2 rows.")
    result = engine.ask("How many?", gold, chat)
    assert result.rows == [[2]]


def test_database_cannot_read_files(gold, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    with pytest.raises(duckdb.Error):
        gold.execute(f"select * from read_csv('{secret.as_posix()}')")
    with pytest.raises(duckdb.Error):
        gold.execute("set enable_external_access = true")   # configuration is locked


def test_row_cap(gold):
    columns, rows, truncated = engine.run_query(gold, "select * from range(1000)")
    assert len(rows) == engine.MAX_ROWS and truncated


@pytest.mark.parametrize("question", ["", "   ", "x" * 301])
def test_rejects_empty_or_long_questions(gold, question):
    with pytest.raises(engine.BadQuestion):
        engine.ask(question, gold, FakeChat())


# ---------------------------------------------------------------- daily cap

class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def download_blob(self):
        if self.name not in self.store.data:
            raise ResourceNotFoundError("missing")
        body, etag = self.store.data[self.name]
        return SimpleNamespace(properties=SimpleNamespace(etag=etag), readall=lambda: body)

    def upload_blob(self, data, overwrite=False, etag=None, match_condition=None):
        if not overwrite and self.name in self.store.data:
            raise ResourceExistsError("exists")
        if etag is not None and self.store.data.get(self.name, (None, None))[1] != etag:
            raise ResourceModifiedError("changed")
        self.store.version += 1
        self.store.data[self.name] = (data, f"v{self.store.version}")


class FakeContainer:
    def __init__(self):
        self.data, self.version = {}, 0

    def get_blob_client(self, name):
        return FakeBlob(self, name)


NOON = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def test_daily_cap_counts_and_stops():
    c = FakeContainer()
    assert [usage.take_one(c, 3, NOON) for _ in range(3)] == [1, 2, 3]
    with pytest.raises(usage.LimitReached):
        usage.take_one(c, 3, NOON)
    assert usage.take_one(c, 3, datetime(2026, 10, 5, tzinfo=timezone.utc)) == 1   # new day


def test_daily_cap_retries_on_concurrent_update():
    c = FakeContainer()
    usage.take_one(c, 10, NOON)
    blob = c.get_blob_client("questions/2026-10-04.json")
    original = blob.download_blob

    def racing_download():
        result = original()
        c.data["questions/2026-10-04.json"] = (json.dumps({"count": 5}).encode(), "someone-else")
        return result

    blob_with_race = FakeBlob(c, "questions/2026-10-04.json")
    blob_with_race.download_blob = racing_download
    calls = {"n": 0}

    def get_blob_client(name):
        calls["n"] += 1
        return blob_with_race if calls["n"] == 1 else FakeBlob(c, name)

    c.get_blob_client = get_blob_client
    # First attempt loses the race (ETag changed), the retry reads 5 and writes 6.
    assert usage.take_one(c, 10, NOON) == 6
