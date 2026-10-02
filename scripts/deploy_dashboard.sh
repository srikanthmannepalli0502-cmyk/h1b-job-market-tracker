#!/usr/bin/env bash
# Upload dashboard/ to the Azure Storage static website under /$SITE_PATH/ and smoke-test it.
# Requires: az logged in, SITE_STORAGE_ACCOUNT set. Data JSON is served gzip-compressed.
set -euo pipefail

: "${SITE_STORAGE_ACCOUNT:?SITE_STORAGE_ACCOUNT must be set}"
SITE_PATH="${SITE_PATH:-h1b}"

rm -rf build && mkdir -p build/site build/data
cp dashboard/index.html dashboard/styles.css dashboard/app.js build/site/
for f in dashboard/data/*.json; do gzip -9 -n -c "$f" > "build/data/$(basename "$f")"; done
du -sh dashboard/data build/data

az storage blob upload-batch --account-name "$SITE_STORAGE_ACCOUNT" \
  --destination '$web' --destination-path "$SITE_PATH" --source build/site \
  --overwrite --auth-mode login --content-cache-control "public, max-age=300" --only-show-errors

az storage blob upload-batch --account-name "$SITE_STORAGE_ACCOUNT" \
  --destination '$web' --destination-path "$SITE_PATH/data" --source build/data \
  --overwrite --auth-mode login --content-type application/json --content-encoding gzip \
  --content-cache-control "public, max-age=300" --only-show-errors

web=$(az storage account show -n "$SITE_STORAGE_ACCOUNT" --query primaryEndpoints.web -o tsv)
base="${web%/}/$SITE_PATH"
curl -fsS "$base/" | grep -q "H-1B Tech Jobs Tracker"
curl -fsS --compressed "$base/data/meta.json" | jq -e '.coverage | length > 0' > /dev/null
echo "Live: $base/"
