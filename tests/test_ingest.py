from pathlib import Path

import polars as pl
import pytest

from pipeline import ingest_lca
from pipeline.ingest_lca import KEEP_COLUMNS, ingest_file, parse_name

# Columns in DOL's layout that identify individual people. None may reach bronze.
PERSONAL_COLUMNS = [
    "EMPLOYER_POC_LAST_NAME", "EMPLOYER_POC_FIRST_NAME", "EMPLOYER_POC_EMAIL", "EMPLOYER_POC_PHONE",
    "AGENT_ATTORNEY_LAST_NAME", "AGENT_ATTORNEY_FIRST_NAME", "AGENT_ATTORNEY_EMAIL_ADDRESS",
    "AGENT_ATTORNEY_PHONE", "PREPARER_LAST_NAME", "PREPARER_FIRST_NAME", "PREPARER_EMAIL",
    "EMPLOYER_PHONE", "EMPLOYER_ADDRESS1",
]


def test_allow_list_excludes_personal_columns():
    assert not set(PERSONAL_COLUMNS) & set(KEEP_COLUMNS)
    assert not [c for c in KEEP_COLUMNS if "POC" in c or "ATTORNEY" in c or "PREPARER" in c or "EMAIL" in c]


@pytest.mark.parametrize("name, expected", [
    ("LCA_Disclosure_Data_FY2025_Q3.xlsx", (2025, 3)),
    ("lca_disclosure_data_fy2026_q1.XLSX", (2026, 1)),
    ("LCA_Appendix_A_FY2026_Q3.xlsx", None),
    ("LCA_Disclosure_Data_FY2025_Q5.xlsx", None),
])
def test_parse_name(name, expected):
    assert parse_name(Path(name)) == expected


@pytest.fixture
def sample_excel(tmp_path: Path) -> Path:
    """A tiny file shaped like a DOL release: kept columns, personal columns, and one column missing."""
    row = {c: "x" for c in KEEP_COLUMNS if c != "H1B_DEPENDENT"}
    row.update({"CASE_NUMBER": "I-200-1", "WAGE_RATE_OF_PAY_FROM": "120000", "DECISION_DATE": "2025-09-30 00:00:00"})
    row.update({c: "Jane Doe" for c in PERSONAL_COLUMNS})
    path = tmp_path / "LCA_Disclosure_Data_FY2025_Q4.xlsx"
    pl.DataFrame([row]).write_excel(path)
    return path


def test_ingest_drops_personal_data_and_fills_missing(sample_excel: Path):
    df = ingest_file(sample_excel, 2025, 4)

    expected = [c.lower() for c in KEEP_COLUMNS] + ["source_fiscal_year", "source_quarter", "source_file"]
    assert df.columns == expected
    assert not {c.lower() for c in PERSONAL_COLUMNS} & set(df.columns)
    assert "Jane Doe" not in df.row(0)
    assert df["h1b_dependent"][0] is None           # missing in this release -> null
    assert df["wage_rate_of_pay_from"][0] == "120000"  # kept as text for dbt to type
    assert df["source_file"][0] == sample_excel.name


def test_run_is_incremental(sample_excel: Path, tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(ingest_lca, "RAW_DIR", sample_excel.parent)
    monkeypatch.setattr(ingest_lca, "BRONZE_DIR", tmp_path / "bronze")

    assert ingest_lca.run() == 0
    assert (tmp_path / "bronze" / "lca_fy2025_q4.parquet").exists()
    assert ingest_lca.run() == 0
    assert "skip" in capsys.readouterr().out
