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
import os
import re
import sys
import time
import uuid
from pathlib import Path

import duckdb
import polars as pl

RAW_DIR = Path("data/raw/lca")
BRONZE_DIR = Path("data/bronze/lca")

# Bump when ingestion output changes; the lake Function reprocesses files built by an older version.
READER_VERSION = "duckdb-xlsx-1"

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


# Excel stores these as day serials; written out as "YYYY-MM-DD HH:MM:SS" text.
DATE_COLUMNS = {"RECEIVED_DATE", "DECISION_DATE", "ORIGINAL_CERT_DATE", "BEGIN_DATE", "END_DATE"}
# Written in shortest round-trip form ("88753.6", not "88753.600000000006"; "150000", not "150000.0").
NUMBER_COLUMNS = {
    "WAGE_RATE_OF_PAY_FROM", "WAGE_RATE_OF_PAY_TO", "PREVAILING_WAGE", "TOTAL_WORKER_POSITIONS",
    "NEW_EMPLOYMENT", "CONTINUED_EMPLOYMENT", "CHANGE_PREVIOUS_EMPLOYMENT", "NEW_CONCURRENT_EMPLOYMENT",
    "CHANGE_EMPLOYER", "AMENDED_PETITION", "TOTAL_WORKSITE_LOCATIONS",
}


def _column_sql(col: str, present: set[str]) -> str:
    q = f'"{col}"'
    if col not in present:
        # Older releases lack some columns; nulls keep one schema across files.
        return f"null::varchar as {q}"
    v = f"nullif({q}, '')"
    if col in DATE_COLUMNS:
        return (f"coalesce(strftime(date '1899-12-30' + to_days(cast(floor(try_cast({v} as double)) as integer)),"
                f" '%Y-%m-%d %H:%M:%S'), {v}) as {q}")
    if col in NUMBER_COLUMNS:
        d = f"try_cast({v} as double)"
        return (f"case when {d} is null then {v}"
                f" when {d} = trunc({d}) and abs({d}) < 1e15 then cast(cast({d} as bigint) as varchar)"
                f" else cast({d} as varchar) end as {q}")
    return f"{v} as {q}"


def _duckdb():
    con = duckdb.connect()
    ext_dir = os.getenv("DUCKDB_EXTENSION_DIR")  # e.g. /tmp/... where $HOME is read-only (Azure Functions)
    if ext_dir:
        con.execute(f"set extension_directory = '{ext_dir}'")
    try:
        con.execute("load excel")
    except duckdb.Error:
        con.execute("install excel")
        con.execute("load excel")
    return con


def read_lca_excel(source: Path | bytes) -> pl.DataFrame:
    """Read a DOL LCA workbook (path, or raw bytes as in the Azure Function) into text columns.

    Uses DuckDB's streaming xlsx reader with only the allow-listed columns selected: peak
    memory stays around 1-1.5 GB for a 250 MB workbook (a whole-sheet reader needed ~3 GB,
    more than an Azure Function instance has). Rows without a case number are skipped,
    which drops the formatted-but-empty rows some releases have at the bottom.
    """
    con = _duckdb()
    mem = None
    try:
        if isinstance(source, (bytes, bytearray)):
            import fsspec

            mem = fsspec.filesystem("memory")
            location = f"/lca-ingest/{uuid.uuid4().hex}.xlsx"
            mem.pipe(location, bytes(source))
            con.register_filesystem(mem)
            uri = f"memory://{location.lstrip('/')}"
        else:
            uri = Path(source).as_posix()
        reader = f"read_xlsx('{uri}', all_varchar = true)"
        present = {row[0] for row in con.execute(f"describe select * from {reader}").fetchall()}
        select = ", ".join(_column_sql(c, present) for c in KEEP_COLUMNS)
        return con.execute(f'select {select} from {reader} where nullif("CASE_NUMBER", \'\') is not null').pl()
    finally:
        if mem is not None:
            mem.rm(location)
        con.close()


def ingest_file(source: Path | bytes, fy: int, q: int, file_name: str | None = None) -> pl.DataFrame:
    """Bronze rows for one release. Pass file_name when `source` is bytes."""
    name = file_name or Path(source).name
    df = read_lca_excel(source).with_columns(
        pl.lit(fy).alias("SOURCE_FISCAL_YEAR"),
        pl.lit(q).alias("SOURCE_QUARTER"),
        pl.lit(name).alias("SOURCE_FILE"),
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
