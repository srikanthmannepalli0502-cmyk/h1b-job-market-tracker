"""Bronze ingestion: DOL LCA disclosure Excel files -> Parquet.

Reads every data/raw/lca/LCA_Disclosure_Data_FY{year}_Q{n}.xlsx, keeps only
employer- and job-level columns, and writes one Parquet file per source file to
data/bronze/lca/. All values stay as text exactly as published; typing happens
in dbt staging, so a format change in one DOL release can't break ingestion.

Columns identifying individual people (employer contacts, attorneys, preparers:
names, emails, phone numbers, addresses) are never written out.

Usage:
    python -m pipeline.ingest_lca            # only new or changed files
    python -m pipeline.ingest_lca --force    # rebuild everything
"""

import argparse
import re
import sys
import time
from pathlib import Path

import fastexcel
import polars as pl

RAW_DIR = Path("data/raw/lca")
BRONZE_DIR = Path("data/bronze/lca")

FILE_PATTERN = re.compile(r"LCA_Disclosure_Data_FY(?P<fy>\d{4})_Q(?P<q>[1-4])\.xlsx$", re.I)

# Allow-list, not deny-list: any new column DOL adds is excluded until reviewed.
KEEP_COLUMNS = [
    "CASE_NUMBER", "CASE_STATUS", "RECEIVED_DATE", "DECISION_DATE", "ORIGINAL_CERT_DATE",
    "VISA_CLASS", "JOB_TITLE", "SOC_CODE", "SOC_TITLE", "FULL_TIME_POSITION",
    "BEGIN_DATE", "END_DATE", "TOTAL_WORKER_POSITIONS",
    "NEW_EMPLOYMENT", "CONTINUED_EMPLOYMENT", "CHANGE_PREVIOUS_EMPLOYMENT",
    "NEW_CONCURRENT_EMPLOYMENT", "CHANGE_EMPLOYER", "AMENDED_PETITION",
    "EMPLOYER_NAME", "TRADE_NAME_DBA", "EMPLOYER_CITY", "EMPLOYER_STATE",
    "EMPLOYER_POSTAL_CODE", "EMPLOYER_COUNTRY", "EMPLOYER_FEIN", "NAICS_CODE",
    "AGENT_REPRESENTING_EMPLOYER", "SECONDARY_ENTITY",
    "WORKSITE_CITY", "WORKSITE_COUNTY", "WORKSITE_STATE", "WORKSITE_POSTAL_CODE",
    "WAGE_RATE_OF_PAY_FROM", "WAGE_RATE_OF_PAY_TO", "WAGE_UNIT_OF_PAY",
    "PREVAILING_WAGE", "PW_UNIT_OF_PAY", "PW_WAGE_LEVEL", "PW_OES_YEAR",
    "TOTAL_WORKSITE_LOCATIONS", "WILLFUL_VIOLATOR", "SUPPORT_H1B", "H1B_DEPENDENT",
    "STATUTORY_BASIS",
]


def parse_name(path: Path) -> tuple[int, int] | None:
    m = FILE_PATTERN.search(path.name)
    return (int(m["fy"]), int(m["q"])) if m else None


def bronze_path(fy: int, q: int) -> Path:
    return BRONZE_DIR / f"lca_fy{fy}_q{q}.parquet"


def read_lca_excel(path: Path) -> pl.DataFrame:
    reader = fastexcel.read_excel(path)
    header = reader.load_sheet(0, n_rows=0).to_polars().columns
    present = [c for c in KEEP_COLUMNS if c in header]
    df = reader.load_sheet(0, use_columns=present, dtypes="string").to_polars()
    # Older releases lack some columns; add them as nulls so every file has one schema.
    missing = [c for c in KEEP_COLUMNS if c not in present]
    if missing:
        df = df.with_columns([pl.lit(None, dtype=pl.String).alias(c) for c in missing])
    return df.select(KEEP_COLUMNS)


def ingest_file(path: Path, fy: int, q: int) -> pl.DataFrame:
    df = read_lca_excel(path).with_columns(
        pl.lit(fy).alias("SOURCE_FISCAL_YEAR"),
        pl.lit(q).alias("SOURCE_QUARTER"),
        pl.lit(path.name).alias("SOURCE_FILE"),
    )
    df.columns = [c.lower() for c in df.columns]
    return df


def run(force: bool = False) -> int:
    files = sorted(p for p in RAW_DIR.glob("*.xlsx") if parse_name(p))
    if not files:
        print(f"No LCA files found in {RAW_DIR}/. See README for download steps.")
        return 1

    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    for path in files:
        fy, q = parse_name(path)
        out = bronze_path(fy, q)
        if not force and out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
            print(f"skip  {path.name} (up to date)")
            continue
        start = time.time()
        df = ingest_file(path, fy, q)
        df.write_parquet(out, compression="zstd")
        mb = out.stat().st_size / 1_048_576
        print(f"wrote {out} : {df.height:,} rows, {mb:.1f} MB, {time.time() - start:.0f}s")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-ingest files even if Parquet is up to date")
    sys.exit(run(parser.parse_args().force))
