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


def trigger(function_app: str, resource_group: str) -> None:
    """Call the Function's on-demand endpoint (fetches the function key with az)."""
    key = subprocess.run(
        ["az", "functionapp", "keys", "list", "-g", resource_group, "-n", function_app,
         "--query", "functionKeys.default", "-o", "tsv"],
        check=True, capture_output=True, text=True, shell=sys.platform == "win32",
    ).stdout.strip()
    import urllib.request
    req = urllib.request.Request(f"https://{function_app}.azurewebsites.net/api/process?code={key}", method="POST")
    print("processing (large files take a few minutes) ...", flush=True)
    with urllib.request.urlopen(req, timeout=1800) as resp:
        print(json.dumps(json.load(resp)["counts"]))


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
        trigger(args.function_app, args.resource_group)
