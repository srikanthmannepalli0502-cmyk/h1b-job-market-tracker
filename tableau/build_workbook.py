"""Generate the Tableau workbook (tableau/H1B_Tech_Jobs_Tracker.twbx) from tableau/data/*.csv.

Written as code so the workbook is reproducible and reviewable: data sources, calculated
fields, parameters, worksheets and dashboards are defined below and serialized to Tableau's
XML format, then packaged with the CSVs.

Usage:
    python -m pipeline.export_tableau          # refresh the CSVs
    python tableau/build_workbook.py           # build the .twbx
"""

import csv
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
OUT_TWB = HERE / "H1B_Tech_Jobs_Tracker.twb"
OUT_TWBX = HERE / "H1B_Tech_Jobs_Tracker.twbx"
PACKAGED_DIR = "Data/H1B"  # where CSVs live inside the .twbx

# Matches Tableau Public 2025.1 (older apps reject workbooks from newer builds).
SOURCE_BUILD = "2025.1.0 (20251.25.0313.2002)"
VERSION = "18.1"
USER_NS = "http://www.tableausoftware.com/xml/user"

ROLE_LABELS = [
    "Data Analyst", "BI Analyst / Engineer", "Data Engineer", "Analytics Engineer", "Data Scientist",
    "AI Engineer / Scientist", "ML Engineer", "Cybersecurity", "Cloud Engineer / Architect",
    "DevOps / SRE / Platform",
]
FISCAL_YEARS = [(2025, "FY2025"), (2026, "FY2026 (Oct-Jun)"), (2024, "FY2024")]

COUNT = "n#,##0"
MONEY = 'c"$"#,##0;-"$"#,##0'
PCT = "p0.0%"


# ---------------------------------------------------------------- model

@dataclass
class Col:
    name: str
    datatype: str            # string | integer | real | boolean
    caption: str
    role: str = ""           # dimension | measure (default from datatype)
    fmt: str | None = None
    formula: str | None = None  # calculated field

    @property
    def is_measure(self) -> bool:
        return (self.role or ("dimension" if self.datatype in ("string", "boolean") else "measure")) == "measure"

    @property
    def type(self) -> str:
        return "quantitative" if self.is_measure else "nominal"


@dataclass
class Source:
    name: str                # csv stem
    caption: str
    cols: list[Col]
    calcs: list[Col] = field(default_factory=list)

    @property
    def ds(self) -> str:
        return f"federated.{self.name}"

    def col(self, name: str) -> Col:
        return next(c for c in self.cols + self.calcs if c.name == name)


PARAM_ROLE = "[Parameters].[Parameter 1]"
PARAM_FY = "[Parameters].[Parameter 2]"
SELECTED = f"[role] = {PARAM_ROLE} AND [fiscal_year] = {PARAM_FY}"

SOURCES = {
    "sponsors": Source("sponsors", "Sponsors", [
        Col("employer", "string", "Employer"),
        Col("hq_state", "string", "HQ State"),
        Col("fiscal_year", "integer", "Fiscal Year", role="dimension"),
        Col("role", "string", "Role"),
        Col("role_group", "string", "Role Group"),
        Col("certified_applications", "integer", "Certified Applications", fmt=COUNT),
        Col("entry_level", "integer", "Entry-level Applications", fmt=COUNT),
        Col("new_hires", "integer", "New-hire Applications", fmt=COUNT),
        Col("median_wage", "real", "Median Salary", fmt=MONEY),
        Col("uscis_new_approved", "integer", "USCIS New-hire Approvals", fmt=COUNT),
        Col("uscis_new_denied", "integer", "USCIS New-hire Denials", fmt=COUNT),
        Col("rank_in_role", "integer", "Rank in Role", role="dimension"),
    ], [
        Col("Calculation_1001", "boolean", "Top 15 Sponsor", formula=f"{SELECTED} AND [rank_in_role] <= 15"),
        Col("Calculation_1002", "boolean", "Rate Eligible",
            formula=f"{SELECTED} AND [rank_in_role] <= 25 AND "
                    "ZN([uscis_new_approved]) + ZN([uscis_new_denied]) >= 20"),
        Col("Calculation_1003", "real", "New-hire Denial Rate", fmt=PCT,
            formula="SUM([uscis_new_denied]) / (SUM([uscis_new_approved]) + SUM([uscis_new_denied]))"),
    ]),
    "wages": Source("wages", "Wages", [
        Col("fiscal_year", "integer", "Fiscal Year", role="dimension"),
        Col("role", "string", "Role"),
        Col("role_group", "string", "Role Group"),
        Col("wage_level", "string", "Wage Level"),
        Col("applications", "integer", "Applications", fmt=COUNT),
        Col("p10_wage", "real", "10th Percentile", fmt=MONEY),
        Col("p25_wage", "real", "25th Percentile", fmt=MONEY),
        Col("median_wage", "real", "Median Salary", fmt=MONEY),
        Col("p75_wage", "real", "75th Percentile", fmt=MONEY),
        Col("p90_wage", "real", "90th Percentile", fmt=MONEY),
    ], [
        Col("Calculation_2001", "boolean", "Selected Levels",
            formula=f"{SELECTED} AND [wage_level] <> 'ALL'"),
    ]),
    "states": Source("states", "States", [
        Col("fiscal_year", "integer", "Fiscal Year", role="dimension"),
        Col("role", "string", "Role"),
        Col("role_group", "string", "Role Group"),
        Col("state", "string", "Worksite State"),
        Col("certified_applications", "integer", "Certified Applications", fmt=COUNT),
        Col("median_wage", "real", "Median Salary", fmt=MONEY),
        Col("rank_in_role", "integer", "Rank in Role", role="dimension"),
    ], [
        Col("Calculation_3001", "boolean", "Top 15 State", formula=f"{SELECTED} AND [rank_in_role] <= 15"),
    ]),
    "growth": Source("growth", "Growth", [
        Col("fiscal_year", "integer", "Fiscal Year", role="dimension"),
        Col("comparison", "string", "Comparison"),
        Col("role", "string", "Role"),
        Col("role_group", "string", "Role Group"),
        Col("prior_year", "integer", "Prior Year Applications", fmt=COUNT),
        Col("this_year", "integer", "This Year Applications", fmt=COUNT),
        Col("pct_change", "real", "Change", fmt=PCT),
    ], [
        Col("Calculation_4001", "boolean", "Selected Year", formula=f"[fiscal_year] = {PARAM_FY}"),
        Col("Calculation_4002", "string", "Direction",
            formula='IF SUM([pct_change]) >= 0 THEN "Growing" ELSE "Shrinking" END'),
    ]),
}


@dataclass
class Sheet:
    name: str
    source: str
    title: str
    rows: list[str]           # column-instance keys, e.g. "none:employer:nk"
    cols: list[str]
    mark: str = "Bar"
    filters: list[str] = field(default_factory=list)  # boolean calc names that must be true
    sort: tuple[str, str] | None = None               # (dimension instance, measure instance)
    text: str | None = None
    color: str | None = None
    tooltips: list[str] = field(default_factory=list)


SHEETS = [
    Sheet("Top Sponsors", "sponsors", f"Top 15 sponsors: <{PARAM_ROLE}>, <{PARAM_FY}>",
          rows=["none:employer:nk"], cols=["sum:certified_applications:qk"],
          filters=["Calculation_1001"], sort=("none:employer:nk", "sum:certified_applications:qk"),
          text="sum:certified_applications:qk",
          tooltips=["sum:median_wage:qk", "sum:entry_level:qk", "sum:new_hires:qk"]),
    Sheet("New-hire Denial Rates", "sponsors",
          "USCIS new-hire denial rate (top 25 sponsors, 20+ decisions)",
          rows=["none:employer:nk"], cols=["usr:Calculation_1003:qk"],
          filters=["Calculation_1002"], sort=("none:employer:nk", "usr:Calculation_1003:qk"),
          text="usr:Calculation_1003:qk",
          tooltips=["sum:uscis_new_approved:qk", "sum:uscis_new_denied:qk"]),
    Sheet("Salary by Level", "wages", f"Median offered salary by wage level: <{PARAM_ROLE}>, <{PARAM_FY}>",
          rows=["none:wage_level:nk"], cols=["sum:median_wage:qk"],
          filters=["Calculation_2001"], text="sum:median_wage:qk",
          tooltips=["sum:p25_wage:qk", "sum:p75_wage:qk", "sum:applications:qk"]),
    Sheet("Top States", "states", f"Where the jobs are: <{PARAM_ROLE}>, <{PARAM_FY}>",
          rows=["none:state:nk"], cols=["sum:certified_applications:qk"],
          filters=["Calculation_3001"], sort=("none:state:nk", "sum:certified_applications:qk"),
          text="sum:certified_applications:qk", tooltips=["sum:median_wage:qk"]),
    Sheet("Growth by Role", "growth", f"Change in certified applications: <{PARAM_FY}> vs prior year",
          rows=["none:role:nk"], cols=["sum:pct_change:qk"],
          filters=["Calculation_4001"], sort=("none:role:nk", "sum:pct_change:qk"),
          text="sum:pct_change:qk", color="usr:Calculation_4002:nk",
          tooltips=["sum:prior_year:qk", "sum:this_year:qk"]),
]

DASHBOARDS = [
    ("1. Sponsors", "Who sponsors H-1B visas for this role", [("Top Sponsors", 58000), ("New-hire Denial Rates", 42000)]),
    ("2. Salaries & Locations", "What it pays and where the jobs are", [("Salary by Level", 50000), ("Top States", 50000)]),
    ("3. Growth", "Which roles are growing (same months compared for partial years)", [("Growth by Role", 100000)]),
]


# ---------------------------------------------------------------- XML helpers

def el(parent, tag, attrs: dict | None = None, text: str | None = None):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in (attrs or {}).items() if v is not None})
    if text is not None:
        e.text = text
    return e


def instance_parts(key: str) -> tuple[str, str, str]:
    deriv, col, suffix = key.split(":")
    return deriv, col, suffix


DERIVATIONS = {"none": "None", "sum": "Sum", "usr": "User", "avg": "Avg"}
TYPES = {"nk": "nominal", "ok": "ordinal", "qk": "quantitative"}


def column_xml(parent, c: Col):
    attrs = {"caption": c.caption, "datatype": c.datatype, "name": f"[{c.name}]",
             "role": "measure" if c.is_measure else "dimension", "type": c.type}
    if c.fmt:
        attrs["default-format"] = c.fmt
    col = el(parent, "column", attrs)
    if c.formula:
        el(col, "calculation", {"class": "tableau", "formula": c.formula})
    return col


REMOTE_TYPES = {"string": 129, "integer": 20, "real": 5, "boolean": 11}
EXTRACT_DIR = HERE / "extracts"
HYPER_DIRECT = os.getenv("TABLEAU_MODE", "hyper") == "hyper"
PACKAGED_EXTRACTS = "Data/Extracts"


def build_extract(s: Source) -> Path:
    """Tableau Public only accepts workbooks whose data sources are extracts: load the CSV
    into a .hyper file (schema "Extract", table "Extract", Tableau's convention)."""
    from tableauhyperapi import (Connection, CreateMode, HyperProcess, SqlType, TableDefinition,
                                 TableName, Telemetry, escape_string_literal)

    sql_types = {"string": SqlType.text(), "integer": SqlType.big_int(), "real": SqlType.double()}
    EXTRACT_DIR.mkdir(exist_ok=True)
    path = EXTRACT_DIR / f"{s.name}.hyper"
    table = TableDefinition(TableName("Extract", "Extract"),
                            [TableDefinition.Column(c.name, sql_types[c.datatype]) for c in s.cols])
    with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
        with Connection(hyper.endpoint, str(path), CreateMode.CREATE_AND_REPLACE) as conn:
            conn.catalog.create_schema("Extract")
            conn.catalog.create_table(table)
            rows = conn.execute_command(
                f"COPY {table.table_name} FROM {escape_string_literal(str(DATA_DIR / f'{s.name}.csv'))} "
                "WITH (format csv, header true, null '')")
    print(f"  extract {path.name}: {rows:,} rows")
    return path


def extract_xml(ds, s: Source):
    """Declare the packaged .hyper as this data source's extract."""
    ext = el(ds, "extract", {"count": "-1", "enabled": "true", "units": "records"})
    conn = el(ext, "connection", {"authentication": "auth-none", "author-locale": "en_US", "class": "hyper",
                                  "dbname": f"{PACKAGED_EXTRACTS}/{s.name}.hyper", "default-settings": "yes",
                                  "schema": "Extract", "sslmode": "", "tablename": "Extract",
                                  "update-time": "10/04/2026 12:00:00 PM", "username": "tableau_internal_user"})
    el(conn, "relation", {"name": "Extract", "table": "[Extract].[Extract]", "type": "table"})
    el(conn, "refresh", {"increment-key": "", "incremental-updates": "false"})
    md = el(conn, "metadata-records")
    for i, c in enumerate(s.cols):
        rec = el(md, "metadata-record", {"class": "column"})
        el(rec, "remote-name", text=c.name)
        el(rec, "remote-type", text=str(REMOTE_TYPES[c.datatype]))
        el(rec, "local-name", text=f"[{c.name}]")
        el(rec, "parent-name", text="[Extract]")
        el(rec, "remote-alias", text=c.name)
        el(rec, "ordinal", text=str(i))
        el(rec, "local-type", text=c.datatype)
        el(rec, "aggregation", text="Count" if c.datatype == "string" else "Sum")
        el(rec, "contains-null", text="true")


def datasource_xml(parent, s: Source):
    header = next(csv.reader((DATA_DIR / f"{s.name}.csv").open(encoding="utf-8")))
    assert header == [c.name for c in s.cols], f"{s.name}.csv columns {header} != model"
    ds = el(parent, "datasource", {"caption": s.caption, "inline": "true", "name": s.ds, "version": VERSION})
    conn = el(ds, "connection", {"class": "federated"})
    if HYPER_DIRECT:
        # Connect straight to the packaged .hyper file (what Tableau does when you open a
        # .hyper). Tableau Public accepts this as an extract-based data source.
        named = el(el(conn, "named-connections"), "named-connection",
                   {"caption": s.name, "name": f"hyper.{s.name}"})
        el(named, "connection", {"authentication": "auth-none", "author-locale": "en_US", "class": "hyper",
                                 "dbname": f"{PACKAGED_EXTRACTS}/{s.name}.hyper", "default-settings": "yes",
                                 "schema": "Extract", "sslmode": "", "tablename": "Extract",
                                 "username": "tableau_internal_user"})
        el(conn, "relation", {"connection": f"hyper.{s.name}", "name": "Extract",
                              "table": "[Extract].[Extract]", "type": "table"})
        md = el(conn, "metadata-records")
        for i, c in enumerate(s.cols):
            rec = el(md, "metadata-record", {"class": "column"})
            el(rec, "remote-name", text=c.name)
            el(rec, "remote-type", text=str(REMOTE_TYPES[c.datatype]))
            el(rec, "local-name", text=f"[{c.name}]")
            el(rec, "parent-name", text="[Extract]")
            el(rec, "remote-alias", text=c.name)
            el(rec, "ordinal", text=str(i))
            el(rec, "local-type", text=c.datatype)
            el(rec, "aggregation", text="Count" if c.datatype == "string" else "Sum")
            el(rec, "contains-null", text="true")
        el(ds, "aliases", {"enabled": "yes"})
        for c in s.cols + s.calcs:
            column_xml(ds, c)
        return
    named = el(el(conn, "named-connections"), "named-connection",
               {"caption": s.name, "name": f"textscan.{s.name}"})
    el(named, "connection", {"class": "textscan", "directory": PACKAGED_DIR, "filename": f"{s.name}.csv",
                             "password": "", "server": ""})
    rel = el(conn, "relation", {"connection": f"textscan.{s.name}", "name": f"{s.name}.csv",
                                "table": f"[{s.name}#csv]", "type": "table"})
    cols = el(rel, "columns", {"character-set": "UTF-8", "header": "yes", "locale": "en_US", "separator": ","})
    for i, c in enumerate(s.cols):
        el(cols, "column", {"datatype": c.datatype, "name": c.name, "ordinal": i})
    md = el(conn, "metadata-records")
    for i, c in enumerate(s.cols):
        rec = el(md, "metadata-record", {"class": "column"})
        el(rec, "remote-name", text=c.name)
        el(rec, "remote-type", text=str(REMOTE_TYPES[c.datatype]))
        el(rec, "local-name", text=f"[{c.name}]")
        el(rec, "parent-name", text=f"[{s.name}.csv]")
        el(rec, "remote-alias", text=c.name)
        el(rec, "ordinal", text=str(i))
        el(rec, "local-type", text=c.datatype)
        el(rec, "aggregation", text="Count" if c.datatype == "string" else "Sum")
        el(rec, "contains-null", text="true")
        if c.datatype == "string":
            el(rec, "collation", {"flag": "0", "name": "LEN_RUS"})
    # Schema order: connection, aliases, column*, ..., extract, layout, ...
    el(ds, "aliases", {"enabled": "yes"})
    for c in s.cols + s.calcs:
        column_xml(ds, c)
    if os.getenv("TABLEAU_NO_EXTRACT") != "1":  # debugging switch
        extract_xml(ds, s)


def parameters_xml(parent):
    ds = el(parent, "datasource", {"hasconnection": "false", "inline": "true", "name": "Parameters", "version": VERSION})
    params_xml(ds)


def params_xml(parent):
    role = el(parent, "column", {"caption": "Role", "datatype": "string", "name": "[Parameter 1]",
                                 "param-domain-type": "list", "role": "measure", "type": "nominal",
                                 "value": f'"{ROLE_LABELS[0]}"'})
    el(role, "calculation", {"class": "tableau", "formula": f'"{ROLE_LABELS[0]}"'})
    members = el(role, "members")
    for label in ROLE_LABELS:
        el(members, "member", {"value": f'"{label}"'})
    fy = el(parent, "column", {"caption": "Fiscal Year", "datatype": "integer", "name": "[Parameter 2]",
                               "param-domain-type": "list", "role": "measure", "type": "quantitative",
                               "value": str(FISCAL_YEARS[0][0])})
    el(fy, "calculation", {"class": "tableau", "formula": str(FISCAL_YEARS[0][0])})
    aliases = el(fy, "aliases")
    for value, alias in FISCAL_YEARS:
        el(aliases, "alias", {"key": str(value), "value": alias})
    members = el(fy, "members")
    for value, alias in FISCAL_YEARS:
        el(members, "member", {"alias": alias, "value": str(value)})


def worksheet_xml(parent, sh: Sheet):
    s = SOURCES[sh.source]
    ws = el(parent, "worksheet", {"name": sh.name})
    title = el(el(ws, "layout-options"), "title")
    el(el(title, "formatted-text"), "run", {"bold": "true", "fontsize": "13"}, sh.title)

    table = el(ws, "table")
    view = el(table, "view")
    # The first data source listed is the sheet's primary one, so the real data comes first;
    # with Parameters first, Tableau treats the sheet as having no valid data source.
    dss = el(view, "datasources")
    el(dss, "datasource", {"caption": s.caption, "name": s.ds})
    el(dss, "datasource", {"name": "Parameters"})

    deps = el(view, "datasource-dependencies", {"datasource": s.ds})
    for c in s.cols + s.calcs:
        column_xml(deps, c)
    used = set(sh.rows + sh.cols + sh.tooltips) | {f"none:{f}:nk" for f in sh.filters}
    for key in (sh.text, sh.color, *(sh.sort or ())):
        if key:
            used.add(key)
    for key in sorted(used):
        deriv, col, suffix = instance_parts(key)
        el(deps, "column-instance", {"column": f"[{col}]", "derivation": DERIVATIONS[deriv],
                                     "name": f"[{key}]", "pivot": "key", "type": TYPES[suffix]})

    params = el(view, "datasource-dependencies", {"datasource": "Parameters"})
    params_xml(params)

    for f in sh.filters:
        filt = el(view, "filter", {"class": "categorical", "column": f"[{s.ds}].[none:{f}:nk]"})
        el(filt, "groupfilter", {"function": "member", "level": f"[none:{f}:nk]", "member": "true",
                                 f"{{{USER_NS}}}ui-domain": "relevant",
                                 f"{{{USER_NS}}}ui-enumeration": "inclusive",
                                 f"{{{USER_NS}}}ui-marker": "enumerate"})
    if sh.sort:
        dim, measure = sh.sort
        # Tableau's schema: <sort class='computed' .../>, between the filters and slices.
        el(view, "sort", {"class": "computed", "column": f"[{s.ds}].[{dim}]", "direction": "DESC",
                          "using": f"[{s.ds}].[{measure}]"})
    slices = el(view, "slices")
    for f in sh.filters:
        el(slices, "column", text=f"[{s.ds}].[none:{f}:nk]")
    el(view, "aggregation", {"value": "true"})

    el(table, "style")
    pane = el(el(table, "panes"), "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
    el(el(pane, "view"), "breakdown", {"value": "auto"})
    el(pane, "mark", {"class": sh.mark})
    enc = el(pane, "encodings")
    if sh.color:
        el(enc, "color", {"column": f"[{s.ds}].[{sh.color}]"})
    if sh.text:
        el(enc, "text", {"column": f"[{s.ds}].[{sh.text}]"})
    for t in sh.tooltips:
        el(enc, "tooltip", {"column": f"[{s.ds}].[{t}]"})
    if sh.text:
        rule = el(el(pane, "style"), "style-rule", {"element": "mark"})
        el(rule, "format", {"attr": "mark-labels-show", "value": "true"})
    el(table, "rows", text=" / ".join(f"[{s.ds}].[{r}]" for r in sh.rows))
    el(table, "cols", text=" / ".join(f"[{s.ds}].[{c}]" for c in sh.cols))


class Ids:
    def __init__(self):
        self.n = 0

    def __call__(self) -> int:
        self.n += 1
        return self.n


def dashboard_xml(parent, name: str, subtitle: str, sheets: list[tuple[str, int]]):
    db = el(parent, "dashboard", {"name": name})
    el(db, "style")
    el(db, "size", {"maxheight": "820", "maxwidth": "1200", "minheight": "820", "minwidth": "1200"})
    ids = Ids()
    zones = el(db, "zones")
    root = el(zones, "zone", {"h": 100000, "id": ids(), "type-v2": "layout-basic", "w": 100000, "x": 0, "y": 0})
    col = el(root, "zone", {"h": 100000, "id": ids(), "param": "vert", "type-v2": "layout-flow", "w": 100000, "x": 0, "y": 0})

    header = el(col, "zone", {"h": 9000, "id": ids(), "type-v2": "text", "w": 100000, "x": 0, "y": 0})
    ft = el(header, "formatted-text")
    el(ft, "run", {"bold": "true", "fontsize": "18"}, "H-1B Tech Jobs Tracker")
    el(ft, "run", {"fontsize": "11"}, f"\n{subtitle}. Source: U.S. DOL LCA disclosure data + USCIS H-1B Employer Data Hub.")

    controls = el(col, "zone", {"h": 7000, "id": ids(), "param": "horz", "type-v2": "layout-flow", "w": 100000, "x": 0, "y": 9000})
    el(controls, "zone", {"h": 7000, "id": ids(), "mode": "compact", "param": PARAM_ROLE, "type-v2": "paramctrl",
                          "w": 50000, "x": 0, "y": 9000})
    el(controls, "zone", {"h": 7000, "id": ids(), "mode": "compact", "param": PARAM_FY, "type-v2": "paramctrl",
                          "w": 50000, "x": 50000, "y": 9000})

    body = el(col, "zone", {"h": 84000, "id": ids(), "param": "horz", "type-v2": "layout-flow", "w": 100000, "x": 0, "y": 16000})
    x = 0
    for sheet, width in sheets:
        el(body, "zone", {"h": 84000, "id": ids(), "name": sheet, "w": width, "x": x, "y": 16000})
        x += width


def windows_xml(parent):
    wins = el(parent, "windows", {"source-height": "30"})
    show_sheets = os.getenv("TABLEAU_SHOW_SHEETS") == "1"  # debugging: worksheets as visible tabs, first
    def sheet_window(sh, hidden):
        attrs = {"class": "worksheet", "name": sh.name}
        attrs.update({"hidden": "true"} if hidden else {"maximized": "true"})
        w = el(wins, "window", attrs)
        cards = el(w, "cards")
        left = el(el(cards, "edge", {"name": "left"}), "strip", {"size": "160"})
        for card in ("pages", "filters", "marks"):
            el(left, "card", {"type": card})
        top = el(cards, "edge", {"name": "top"})
        for card in ("columns", "rows", "title"):
            el(el(top, "strip", {"size": "2147483647"}), "card", {"type": card})

    focus = os.getenv("TABLEAU_FOCUS", DASHBOARDS[0][0])  # Tableau opens on the last window
    for name, _, sheets in sorted(DASHBOARDS, key=lambda d: d[0] == focus):
        w = el(wins, "window", {"class": "dashboard", "maximized": "true", "name": name})
        vps = el(w, "viewpoints")
        for sheet, _ in sheets:
            el(vps, "viewpoint", {"name": sheet})
        el(w, "active", {"id": "-1"})
    for sh in ([] if show_sheets else SHEETS):
        sheet_window(sh, hidden=True)
    if show_sheets:  # last window is the one Tableau opens on (TABLEAU_FOCUS picks which)
        focus = os.getenv("TABLEAU_FOCUS", SHEETS[0].name)
        for sh in sorted(SHEETS, key=lambda x: x.name == focus):
            sheet_window(sh, hidden=False)


def build() -> Path:
    ET.register_namespace("user", USER_NS)
    wb = ET.Element("workbook", {"original-version": VERSION, "source-build": SOURCE_BUILD,
                                 "source-platform": "win", "version": VERSION})
    prefs = el(wb, "preferences")
    el(prefs, "preference", {"name": "ui.encoding.shelf.height", "value": "24"})
    el(prefs, "preference", {"name": "ui.shelf.height", "value": "26"})

    dss = el(wb, "datasources")
    parameters_xml(dss)
    for s in SOURCES.values():
        datasource_xml(dss, s)

    wss = el(wb, "worksheets")
    for sh in SHEETS:
        worksheet_xml(wss, sh)

    dbs = el(wb, "dashboards")
    for name, subtitle, sheets in DASHBOARDS:
        dashboard_xml(dbs, name, subtitle, sheets)

    windows_xml(wb)

    ET.indent(wb, space="  ")
    xml = ET.tostring(wb, encoding="unicode")
    OUT_TWB.write_text("<?xml version='1.0' encoding='utf-8' ?>\n" + xml, encoding="utf-8")

    extracts = [build_extract(s) for s in SOURCES.values()]
    with zipfile.ZipFile(OUT_TWBX, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(OUT_TWB, OUT_TWB.name)
        for csv_path in sorted(DATA_DIR.glob("*.csv")):
            z.write(csv_path, f"{PACKAGED_DIR}/{csv_path.name}")
        for hyper_path in extracts:
            z.write(hyper_path, f"{PACKAGED_EXTRACTS}/{hyper_path.name}")
    return OUT_TWBX


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
