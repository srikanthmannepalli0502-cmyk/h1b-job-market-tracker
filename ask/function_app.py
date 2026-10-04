"""Azure Function: "Ask the data" API for the H-1B dashboard.

POST /api/ask  {"question": "..."}  ->  {"answer", "sql", "columns", "rows", "truncated"}

Anonymous (it's called from a public web page), so it is protected by: CORS limited to the
dashboard's origin, a daily question cap shared across instances, the deployment's
tokens-per-minute limit, read-only gold tables, and SQL checks (see engine.py, sql_guard.py).
"""

import json
import logging
import os
import tempfile
from pathlib import Path

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

import engine
import usage
from llm import AzureChat

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

_state: dict = {}


def _blob_service() -> BlobServiceClient:
    if "blobs" not in _state:
        _state["blobs"] = BlobServiceClient(os.environ["LAKE_BLOB_ENDPOINT"], credential=DefaultAzureCredential())
    return _state["blobs"]


def _database():
    """Gold tables, downloaded once per instance (cold start) into an in-memory DuckDB."""
    if "db" not in _state:
        gold = _blob_service().get_container_client(os.environ.get("GOLD_CONTAINER", "gold"))
        folder = Path(tempfile.mkdtemp(prefix="gold-"))
        for blob in gold.list_blobs(name_starts_with="marts/"):
            if blob.name.endswith(".parquet"):
                (folder / Path(blob.name).name).write_bytes(gold.download_blob(blob.name).readall())
        _state["db"] = engine.open_gold(folder)
        logging.info("loaded gold tables: %s", sorted(engine.table_names(_state["db"])))
    return _state["db"]


def _chat() -> AzureChat:
    if "chat" not in _state:
        _state["chat"] = AzureChat()
    return _state["chat"]


def _json(body: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/json")


@app.route(route="ask", methods=["POST"])
def ask(req: func.HttpRequest) -> func.HttpResponse:
    try:
        question = engine.validate_question((req.get_json() or {}).get("question", ""))
    except ValueError as e:  # bad JSON or BadQuestion: rejected before counting or loading anything
        message = str(e) if isinstance(e, engine.BadQuestion) else "Send JSON like {\"question\": \"...\"}."
        return _json({"error": message}, 400)

    try:
        usage.take_one(_blob_service().get_container_client(os.environ.get("USAGE_CONTAINER", "usage")),
                       int(os.environ.get("DAILY_QUESTION_LIMIT", "300")))
    except usage.LimitReached:
        return _json({"error": "The assistant has reached today's question limit. Please try again tomorrow."}, 429)

    try:
        result = engine.ask(question, _database(), _chat())
    except engine.BadQuestion as e:
        return _json({"error": str(e)}, 400)
    except Exception:
        logging.exception("ask failed for question of %d chars", len(question or ""))
        return _json({"error": "Sorry, I couldn't answer that. Try rephrasing the question."}, 500)

    logging.info("ask ok: %r -> %d rows", question[:120], len(result.rows))
    return _json(result.to_dict())
