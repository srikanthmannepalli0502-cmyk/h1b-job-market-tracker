"""Bronze ingestion: USCIS H-1B Employer Data Hub exports -> Parquet.

Reads data/raw/uscis/uscis_h1b_employers_fy{year}.csv (the "Crosstab View" export of
https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub, one fiscal
year per file) and writes data/bronze/uscis/uscis_fy{year}.parquet.

The export is in long format: one row per employer location and measure
("New Employment Approval", "Continuation Denial", ...). It is kept that way in
bronze, as text; dbt pivots and types it. Employers are identified by name and
the last four digits of their tax ID; there is no personal data in this source.

Usage:
    python -m pipeline.ingest_uscis            # only new or changed files
    python -m pipeline.ingest_uscis --force
"""

import argparse
import re
import sys
import time
from pathlib import Path

import duckdb
import polars as pl

RAW_DIR = Path("data/raw/uscis")
BRONZE_DIR = Path("data/bronze/uscis")

FILE_PATTERN = re.compile(r"uscis_h1b_employers_fy(?P<fy>\d{4})\.csv$", re.I)

# Bump when ingestion output changes; the lake Function reprocesses older outputs.
READER_VERSION = "uscis-csv-1"

# Export header -> bronze column. The export's "Fiscal Year" header has trailing
# spaces, so headers are matched after trimming.
COLUMNS = {
    "Employer (Petitioner) Name": "employer_name",
    "Fiscal Year": "fiscal_year",
    "Industry (NAICS) Code": "naics",
    "Measure Names": "measure_name",
    "Petitioner City": "petitioner_city",
    "Petitioner State": "petitioner_state",
    "Petitioner Zip Code": "petitioner_zip",
    "Tax ID": "tax_id_last4",
    "Measure Values": "measure_value",
}


def parse_name(path: Path) -> int | None:
    m = FILE_PATTERN.search(Path(path).name)
    return int(m["fy"]) if m else None


def bronze_path(fy: int) -> Path:
    return BRONZE_DIR / f"uscis_fy{fy}.parquet"


def read_uscis_csv(source: Path | bytes, file_name: str) -> pl.DataFrame:
    """Read one export (path, or bytes as in the Azure Function) into text columns."""
    con = duckdb.connect()
    mem = None
    try:
        if isinstance(source, (bytes, bytearray)):
            import uuid

            import fsspec

            mem = fsspec.filesystem("memory")
            location = f"/uscis-ingest/{uuid.uuid4().hex}.csv"
            mem.pipe(location, bytes(source))
            con.register_filesystem(mem)
            uri = f"memory://{location.lstrip('/')}"
        else:
            uri = Path(source).as_posix()
        reader = f"read_csv('{uri}', all_varchar = true, header = true)"
        header = {row[0].strip(): row[0] for row in con.execute(f"describe select * from {reader}").fetchall()}
        missing = [c for c in COLUMNS if c not in header]
        if missing:
            raise ValueError(f"{file_name}: export is missing columns {missing}; did USCIS change the format?")
        select = ", ".join(
            # Counts use thousands separators ("1,234").
            f"replace(\"{header[src]}\", ',', '') as {dst}" if dst == "measure_value"
            else f"nullif(trim(\"{header[src]}\"), '') as {dst}"
            for src, dst in COLUMNS.items()
        )
        df = con.execute(f"select {select} from {reader}").pl()
    finally:
        if mem is not None:
            mem.rm(location)
        con.close()
    return df.with_columns(pl.lit(file_name).alias("source_file"))


def run(force: bool = False) -> int:
    files = sorted(p for p in RAW_DIR.glob("*.csv") if parse_name(p))
    if not files:
        print(f"No USCIS files found in {RAW_DIR}/. See README for download steps.")
        return 1
    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    for path in files:
        out = bronze_path(parse_name(path))
        if not force and out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
            print(f"skip  {path.name} (up to date)")
            continue
        start = time.time()
        df = read_uscis_csv(path, path.name)
        df.write_parquet(out, compression="zstd")
        print(f"wrote {out} : {df.height:,} rows, {out.stat().st_size / 1_048_576:.1f} MB, {time.time() - start:.0f}s")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true")
    sys.exit(run(parser.parse_args().force))
