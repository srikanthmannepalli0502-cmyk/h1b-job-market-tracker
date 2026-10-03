"""Azure Function: turn new DOL LCA workbooks in the lake's raw zone into bronze Parquet.

- Timer: every 6 hours (DOL publishes quarterly, so polling is simpler than events).
- HTTP:  POST /api/process (function key) to run on demand; ?force=1 reprocesses everything.
         Azure ends HTTP requests after 230 s, so for large or many files start the timer
         function instead (scripts/upload_raw.py --process does this via the admin API).

Authenticates to the lake with the Function App's managed identity.
"""

import json
import logging
import os

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from lake_ingest import process_new

app = func.FunctionApp()


def containers():
    service = BlobServiceClient(os.environ["LAKE_BLOB_ENDPOINT"], credential=DefaultAzureCredential())
    return (
        service.get_container_client(os.environ.get("RAW_CONTAINER", "raw")),
        service.get_container_client(os.environ.get("BRONZE_CONTAINER", "bronze")),
    )


def summarize(results: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"counts": counts, "results": results}


@app.timer_trigger(schedule="0 0 */6 * * *", arg_name="timer", run_on_startup=False)
def scan_raw(timer: func.TimerRequest) -> None:
    summary = summarize(process_new(*containers()))
    logging.info("scan_raw: %s", json.dumps(summary["counts"]))


@app.route(route="process", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def process_now(req: func.HttpRequest) -> func.HttpResponse:
    force = req.params.get("force") == "1"
    try:
        summary = summarize(process_new(*containers(), force=force))
    except Exception:
        logging.exception("process_now failed")
        return func.HttpResponse(json.dumps({"error": "processing failed; see logs"}), status_code=500, mimetype="application/json")
    return func.HttpResponse(json.dumps(summary, indent=2), mimetype="application/json")
