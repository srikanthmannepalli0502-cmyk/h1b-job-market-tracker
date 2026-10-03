"""Upload DOL LCA workbooks from data/raw/lca/ to the lake's raw zone.

Skips files already in the lake with the same size. Uses your `az login`
identity (the lake has account keys disabled). The ingestion Function picks the
files up within 6 hours, or immediately with --process.

Usage:
    python scripts/upload_raw.py --account <lake account>             # upload only
    python scripts/upload_raw.py --account <lake account> --process   # upload + trigger the Function
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.ingest_lca import RAW_DIR, parse_name  # noqa: E402

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def upload(account: str) -> int:
    service = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=AzureCliCredential())
    raw = service.get_container_client("raw")
    existing = {b.name: b.size for b in raw.list_blobs(name_starts_with="lca/")}

    uploaded = 0
    for path in sorted(RAW_DIR.glob("*.xlsx")):
        if not parse_name(path):
            print(f"skip  {path.name} (not an LCA disclosure file name)")
            continue
        name = f"lca/{path.name}"
        if existing.get(name) == path.stat().st_size:
            print(f"skip  {path.name} (already in lake)")
            continue
        print(f"upload {path.name} ({path.stat().st_size / 1_048_576:.0f} MB) ...", flush=True)
        with path.open("rb") as f:
            raw.upload_blob(name, f, overwrite=True, max_concurrency=4,
                            content_settings=ContentSettings(content_type=XLSX))
        uploaded += 1
    print(f"{uploaded} file(s) uploaded.")
    return uploaded


def trigger(account: str, function_app: str, resource_group: str, timeout_s: int = 1800) -> None:
    """Start the timer function now and wait until every raw file has an up-to-date manifest.

    Uses the admin endpoint (POST /admin/functions/scan_raw), which runs the function in the
    background. HTTP-triggered calls are cut off by Azure after 230 seconds, too short for
    several large workbooks.
    """
    import time
    import urllib.request

    master_key = subprocess.run(
        ["az", "functionapp", "keys", "list", "-g", resource_group, "-n", function_app,
         "--query", "masterKey", "-o", "tsv"],
        check=True, capture_output=True, text=True, shell=sys.platform == "win32",
    ).stdout.strip()
    req = urllib.request.Request(
        f"https://{function_app}.azurewebsites.net/admin/functions/scan_raw",
        data=b'{"input": ""}', method="POST",
        headers={"x-functions-key": master_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        print(f"started scan_raw (HTTP {resp.status}); waiting for manifests ...", flush=True)

    service = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=AzureCliCredential())
    raw, bronze = service.get_container_client("raw"), service.get_container_client("bronze")
    expected = {Path(b.name).stem: b.etag for b in raw.list_blobs(name_starts_with="lca/") if parse_name(Path(b.name))}
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        done = {}
        for b in bronze.list_blobs(name_starts_with="_manifest/"):
            m = json.loads(bronze.download_blob(b.name).readall())
            if expected.get(Path(m["source_file"]).stem) == m["source_etag"]:
                done[m["source_file"]] = m
        print(f"  {len(done)}/{len(expected)} processed", flush=True)
        if len(done) == len(expected):
            for name, m in sorted(done.items()):
                print(f"  {name}: {m['rows']:,} rows, {m['seconds']}s")
            return
        time.sleep(30)
    sys.exit("timed out waiting for the Function; check App Insights logs")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account", required=True, help="lake storage account (terraform output lake_account)")
    p.add_argument("--process", action="store_true", help="trigger the ingestion Function after uploading")
    p.add_argument("--function-app", default="", help="terraform output function_app_name (with --process)")
    p.add_argument("--resource-group", default="rg-h1b-tracker")
    args = p.parse_args()
    upload(args.account)
    if args.process:
        if not args.function_app:
            sys.exit("--function-app is required with --process")
        trigger(args.account, args.function_app, args.resource_group)
