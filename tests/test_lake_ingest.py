"""Raw -> bronze lake processing, against an in-memory fake of the Blob container API."""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions"))
import lake_ingest  # noqa: E402
from pipeline.ingest_lca import KEEP_COLUMNS  # noqa: E402


class FakeBlob:
    def __init__(self, container, name):
        self.container, self.name = container, name

    def exists(self):
        return self.name in self.container.blobs

    def download_blob(self):
        return SimpleNamespace(readall=lambda: self.container.blobs[self.name][0])

    def upload_blob(self, data, overwrite=False):
        if self.exists() and not overwrite:
            raise FileExistsError(self.name)
        self.container.put(self.name, data)


class FakeContainer:
    def __init__(self):
        self.blobs: dict[str, tuple[bytes, str]] = {}
        self._version = 0

    def put(self, name, data):
        self._version += 1
        self.blobs[name] = (data, f'"etag-{self._version}"')

    def list_blobs(self, name_starts_with=""):
        return [SimpleNamespace(name=n, etag=e) for n, (_, e) in sorted(self.blobs.items()) if n.startswith(name_starts_with)]

    def get_blob_client(self, name):
        return FakeBlob(self, name)


def workbook(case_number="I-200-1", with_personal=True) -> bytes:
    row = {c: "x" for c in KEEP_COLUMNS}
    row.update({"CASE_NUMBER": case_number, "DECISION_DATE": "2025-09-30 00:00:00"})
    if with_personal:
        row.update({"EMPLOYER_POC_EMAIL": "jane@example.com", "PREPARER_LAST_NAME": "Doe"})
    buf = io.BytesIO()
    pl.DataFrame([row]).write_excel(buf)
    return buf.getvalue()


@pytest.fixture
def lake():
    raw, bronze = FakeContainer(), FakeContainer()
    raw.put("lca/LCA_Disclosure_Data_FY2025_Q4.xlsx", workbook())
    return raw, bronze


def test_processes_new_file_and_writes_manifest(lake):
    raw, bronze = lake
    (result,) = lake_ingest.process_new(raw, bronze)

    assert result["status"] == "processed"
    assert result["rows"] == 1 and (result["fiscal_year"], result["quarter"]) == (2025, 4)
    assert "lca/lca_fy2025_q4.parquet" in bronze.blobs

    manifest = json.loads(bronze.blobs["_manifest/LCA_Disclosure_Data_FY2025_Q4.json"][0])
    assert manifest["source_etag"] == raw.blobs["lca/LCA_Disclosure_Data_FY2025_Q4.xlsx"][1]
    assert len(manifest["sha256"]) == 64


def test_bronze_has_no_personal_data(lake):
    raw, bronze = lake
    lake_ingest.process_new(raw, bronze)
    df = pl.read_parquet(io.BytesIO(bronze.blobs["lca/lca_fy2025_q4.parquet"][0]))
    assert "employer_poc_email" not in df.columns
    assert "jane@example.com" not in df.row(0)
    assert df["source_file"][0] == "LCA_Disclosure_Data_FY2025_Q4.xlsx"


def test_unchanged_file_is_skipped(lake):
    raw, bronze = lake
    lake_ingest.process_new(raw, bronze)
    (again,) = lake_ingest.process_new(raw, bronze)
    assert again["status"] == "unchanged"


def test_replaced_file_is_reprocessed(lake):
    raw, bronze = lake
    lake_ingest.process_new(raw, bronze)
    raw.put("lca/LCA_Disclosure_Data_FY2025_Q4.xlsx", workbook(case_number="I-200-2"))  # new ETag
    (result,) = lake_ingest.process_new(raw, bronze)
    assert result["status"] == "processed"
    df = pl.read_parquet(io.BytesIO(bronze.blobs["lca/lca_fy2025_q4.parquet"][0]))
    assert df["case_number"].to_list() == ["I-200-2"]


def test_new_reader_version_reprocesses(lake, monkeypatch):
    raw, bronze = lake
    lake_ingest.process_new(raw, bronze)
    monkeypatch.setattr(lake_ingest, "READER_VERSION", "next-version")
    (result,) = lake_ingest.process_new(raw, bronze)
    assert result["status"] == "processed" and result["reader_version"] == "next-version"


def test_force_reprocesses(lake):
    raw, bronze = lake
    lake_ingest.process_new(raw, bronze)
    (result,) = lake_ingest.process_new(raw, bronze, force=True)
    assert result["status"] == "processed"


def test_unrecognized_names_are_ignored(lake):
    raw, bronze = lake
    raw.put("lca/LCA_Appendix_A_FY2026_Q3.xlsx", b"not processed")
    raw.put("other/LCA_Disclosure_Data_FY2025_Q1.xlsx", b"outside the lca/ prefix")
    results = {r["source_file"]: r["status"] for r in lake_ingest.process_new(raw, bronze)}
    assert results == {"LCA_Appendix_A_FY2026_Q3.xlsx": "ignored", "LCA_Disclosure_Data_FY2025_Q4.xlsx": "processed"}
