"""Check DOL's performance data page for LCA disclosure files we haven't seen yet.

Compares the LCA_Disclosure_Data_FY*_Q*.xlsx links on the page with the names in
scripts/dol_releases_seen.txt. Prints new files as JSON lines ({"file", "url"}) and,
with --update, adds them to the seen list. Used by the "Check for new DOL data" workflow,
which opens a GitHub issue (and so an email) for each new file.

Usage:
    python scripts/check_dol_releases.py            # report only
    python scripts/check_dol_releases.py --update   # report and record as seen
"""

import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
SEEN = Path(__file__).with_name("dol_releases_seen.txt")
LINK = re.compile(r'href="([^"]*?(LCA_Disclosure_Data_FY\d{4}_Q\d[^"/]*?\.xlsx))"', re.I)


def releases(html: str) -> dict[str, str]:
    """File name -> absolute download URL, for every LCA disclosure link on the page."""
    return {name: urljoin(PAGE, href) for href, name in LINK.findall(html)}


def main() -> int:
    with urllib.request.urlopen(PAGE, timeout=60) as resp:
        html = resp.read().decode("utf-8", "replace")
    found = releases(html)
    if not found:
        # Most likely the page layout changed; fail loudly instead of staying silent forever.
        print(f"no LCA disclosure links found on {PAGE}", file=sys.stderr)
        return 1

    seen = set(SEEN.read_text().split()) if SEEN.exists() else set()
    new = sorted(set(found) - seen)
    for name in new:
        print(json.dumps({"file": name, "url": found[name]}))
    print(f"{len(found)} files on the page, {len(new)} new", file=sys.stderr)

    if "--update" in sys.argv and new:
        SEEN.write_text("\n".join(sorted(seen | set(new))) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
