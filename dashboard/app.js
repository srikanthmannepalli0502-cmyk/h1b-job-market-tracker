"use strict";

const ROLE_LABELS = {
  data_analyst: "Data Analyst",
  bi_analyst: "BI Analyst / Engineer",
  data_engineer: "Data Engineer",
  analytics_engineer: "Analytics Engineer",
  data_scientist: "Data Scientist",
  ml_engineer: "ML Engineer",
  ai_engineer: "AI Engineer / Scientist",
  cybersecurity: "Cybersecurity",
  cloud_engineer: "Cloud Engineer / Architect",
  devops_engineer: "DevOps / SRE / Platform",
  software_engineer: "Software Engineer",
  business_analyst: "Business Analyst",
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const PAGE_SIZE = 25;

const state = { role: "data_analyst", fy: null, search: "", shown: PAGE_SIZE };
let DATA = null;

const $ = (id) => document.getElementById(id);
const fmtInt = (n) => Math.round(n).toLocaleString("en-US");
const fmtMoney = (n) => (n == null ? "–" : "$" + Math.round(n / 1000).toLocaleString("en-US") + "K");
const fmtMoneyFull = (n) => (n == null ? "–" : "$" + Math.round(n).toLocaleString("en-US"));
const roleLabel = (r) => ROLE_LABELS[r] || r;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** Turn {"cols":[...],"rows":[[...]]} into a list of objects. */
function fromColumnar(payload) {
  return payload.rows.map((row) => Object.fromEntries(payload.cols.map((c, i) => [c, row[i]])));
}

async function loadJson(name) {
  const res = await fetch(`data/${name}.json`);
  if (!res.ok) throw new Error(`${name}.json: HTTP ${res.status}`);
  return res.json();
}

/* ---------- labels for partial years ---------- */

function coverageLabel(c) {
  if (c.is_complete) return `FY${c.fiscal_year}`;
  const first = new Date(c.first_decision + "T00:00:00");
  const last = new Date(c.last_decision + "T00:00:00");
  return `FY${c.fiscal_year} (${MONTHS[first.getMonth()]}–${MONTHS[last.getMonth()]} only)`;
}

/* ---------- tooltip (hover + keyboard focus) ---------- */

const tooltip = $("tooltip");

function showTip(target, html) {
  tooltip.innerHTML = html;
  tooltip.hidden = false;
  const r = target.getBoundingClientRect();
  const tw = tooltip.offsetWidth;
  const left = Math.min(Math.max(8, r.left + r.width / 2 - tw / 2), window.innerWidth - tw - 8);
  const top = r.top - tooltip.offsetHeight - 8;
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top < 8 ? r.bottom + 8 : top}px`;
}

function hideTip() { tooltip.hidden = true; }

function bindTips(container) {
  container.querySelectorAll("[data-tip]").forEach((el) => {
    const show = () => showTip(el, el.dataset.tip);
    el.addEventListener("mouseenter", show);
    el.addEventListener("focus", show);
    el.addEventListener("mouseleave", hideTip);
    el.addEventListener("blur", hideTip);
  });
}

/* ---------- small helpers ---------- */

/** Round, evenly spaced ticks whose first and last values enclose [min, max]. */
function niceTicks(min, max, count = 4) {
  const span = max - min || 1;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks = [];
  for (let i = 0; start + i * step <= end + step * 1e-9; i++) {
    ticks.push(Number((start + i * step).toPrecision(12)));
  }
  return ticks;
}

function table(headers, rows) {
  const head = headers.map((h) => `<th scope="col"${h.num ? ' class="num"' : ""}>${esc(h.label)}</th>`).join("");
  const body = rows.map((r) => `<tr>${r.map((v, i) => `<td${headers[i].num ? ' class="num"' : ""}>${esc(v)}</td>`).join("")}</tr>`).join("");
  return `<table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

/* ---------- tiles ---------- */

function renderTiles() {
  const { role, fy } = state;
  const all = DATA.wages.find((w) => w.fiscal_year === fy && w.role_family === role && w.wage_level === "ALL" && w.worksite_state === "ALL");
  const cov = DATA.meta.coverage.find((c) => c.fiscal_year === fy);

  const total = DATA.meta.totals.find((t) => t.fiscal_year === fy && t.role_family === role);
  $("tile-apps").textContent = total ? fmtInt(total.certified) : "–";
  $("tile-apps-note").textContent = total
    ? `${roleLabel(role)}, ${coverageLabel(cov)} · ${fmtInt(total.employers)} employers`
    : "";
  $("tile-wage").textContent = all ? fmtMoney(all.p50) : "–";
  $("tile-wage-note").textContent = all
    ? `Middle 50%: ${fmtMoney(all.p25)} – ${fmtMoney(all.p75)} · full-time roles`
    : "";

  const t = DATA.trend.rows.find((r) => r.role_family === role);
  const tile = $("tile-trend");
  tile.className = "tile-value";
  if (t && t.prior > 0) {
    const pct = (t.current / t.prior - 1) * 100;
    tile.textContent = `${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct).toFixed(1)}%`;
    tile.classList.add(pct >= 0 ? "delta-up" : "delta-down");
    $("tile-trend-note").textContent = `${fmtInt(t.current)} vs ${fmtInt(t.prior)}, ${trendPeriod()}`;
  } else {
    tile.textContent = "–";
    $("tile-trend-note").textContent = "";
  }
}

function trendPeriod() {
  const m = DATA.trend.months;
  // Fiscal year order: Oct..Sep
  const order = [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9].filter((x) => m.includes(x));
  const fy = DATA.trend.current_fy;
  return `${MONTHS[order[0] - 1]}–${MONTHS[order[order.length - 1] - 1]} of FY${fy} vs FY${fy - 1}`;
}

/* ---------- sponsors table ---------- */

function sponsorRows() {
  const q = state.search.trim().toLowerCase();
  return DATA.employers
    .filter((e) => e.fy === state.fy && e.role === state.role)
    .filter((e) => !q || e.name.toLowerCase().includes(q))
    .sort((a, b) => b.apps - a.apps || a.name.localeCompare(b.name));
}

function renderSponsors() {
  const rows = sponsorRows();
  const max = rows.length ? rows[0].apps : 1;
  const cov = DATA.meta.coverage.find((c) => c.fiscal_year === state.fy);
  $("sponsors-sub").textContent =
    `${roleLabel(state.role)} · ${coverageLabel(cov)} · certified H-1B LCAs. ` +
    `Employers with fewer than ${DATA.meta.min_employer_apps} applications in these roles across all years are not listed.`;

  const tbody = $("sponsors-table").querySelector("tbody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">No employers match.</td></tr>`;
  } else {
    tbody.innerHTML = rows.slice(0, state.shown).map((e) => `
      <tr>
        <td>${esc(e.name)}</td>
        <td>${esc(e.hq || "")}</td>
        <td class="num"><span class="bar-cell"><span class="inline-bar" style="width:${Math.max(2, (e.apps / max) * 80)}px"></span>${fmtInt(e.apps)}</span></td>
        <td class="num">${fmtInt(e.entry)}</td>
        <td class="num">${fmtInt(e.new_hires)}</td>
        <td class="num">${fmtMoneyFull(e.median_wage)}</td>
      </tr>`).join("");
  }
  $("sponsors-count").textContent = `Showing ${Math.min(state.shown, rows.length)} of ${fmtInt(rows.length)} employers`;
  $("show-more").hidden = state.shown >= rows.length;
}

/* ---------- salary by wage level (range bars) ---------- */

function renderWages() {
  const levels = ["I", "II", "III", "IV"];
  const rows = levels
    .map((lvl) => DATA.wages.find((w) => w.fiscal_year === state.fy && w.role_family === state.role && w.wage_level === lvl && w.worksite_state === "ALL"))
    .filter(Boolean);
  const el = $("wage-chart");
  if (!rows.length) { el.innerHTML = `<p class="empty">Not enough data for this selection.</p>`; $("wage-table").innerHTML = ""; return; }

  const lo = Math.min(...rows.map((r) => r.p25));
  const hi = Math.max(...rows.map((r) => r.p75));
  const ticks = niceTicks(lo, hi, 4);
  const min = ticks[0], max = ticks[ticks.length - 1];
  const x = (v) => ((v - min) / (max - min)) * 100;

  el.innerHTML = rows.map((r) => {
    const tip = `<strong>Level ${r.wage_level}</strong>Median ${fmtMoneyFull(r.p50)}<br><span class="muted">25th–75th: ${fmtMoneyFull(r.p25)} – ${fmtMoneyFull(r.p75)}<br>${fmtInt(r.applications)} applications</span>`;
    return `
      <div class="chart-row">
        <div class="row-label">Level ${r.wage_level}</div>
        <div class="plot" tabindex="0" data-tip="${esc(tip)}" aria-label="Level ${r.wage_level}: median ${fmtMoneyFull(r.p50)}">
          ${ticks.map((t) => `<span class="gridline" style="left:${x(t)}%"></span>`).join("")}
          <span class="range" style="left:${x(r.p25)}%;width:${x(r.p75) - x(r.p25)}%"></span>
          <span class="median-tick" style="left:${x(r.p50)}%"></span>
        </div>
        <div class="row-value">${fmtMoney(r.p50)}</div>
      </div>`;
  }).join("") + `
    <div class="axis-row"><span></span><div class="axis">${ticks.map((t) => `<span style="left:${x(t)}%">${fmtMoney(t)}</span>`).join("")}</div><span></span></div>`;
  bindTips(el);

  $("wage-table").innerHTML = table(
    [{ label: "Level" }, { label: "Applications", num: true }, { label: "25th pct", num: true }, { label: "Median", num: true }, { label: "75th pct", num: true }],
    rows.map((r) => [r.wage_level, fmtInt(r.applications), fmtMoneyFull(r.p25), fmtMoneyFull(r.p50), fmtMoneyFull(r.p75)]),
  );
}

/* ---------- year-over-year by role (diverging bars) ---------- */

function renderTrend() {
  const rows = DATA.trend.rows
    .filter((r) => r.prior > 0)
    .map((r) => ({ ...r, pct: (r.current / r.prior - 1) * 100 }))
    .sort((a, b) => b.pct - a.pct);
  $("trend-sub").textContent = `Certified H-1B applications, ${trendPeriod()} (same months, so the partial year compares fairly).`;

  // Symmetric around zero so growth and decline bars are visually comparable.
  // Scale to the second-largest change: a single outlier (e.g. +139%) would
  // otherwise squash every other bar to a few pixels. Bars past the axis are
  // drawn broken at the edge; their real value stays in the label and table.
  const mags = rows.map((r) => Math.abs(r.pct)).sort((a, b) => b - a);
  const scaleTo = mags.length > 2 && mags[0] > 2 * mags[1] ? mags[1] * 1.15 : mags[0];
  const lim = Math.max(...niceTicks(0, Math.max(10, scaleTo), 2));
  const ticks = niceTicks(-lim, lim, 4);
  const clamp = (v) => Math.max(-lim, Math.min(lim, v));
  const x = (v) => ((clamp(v) + lim) / (2 * lim)) * 100;

  const el = $("trend-chart");
  el.innerHTML = rows.map((r) => {
    const selected = r.role_family === state.role;
    const left = r.pct >= 0 ? x(0) : x(r.pct);
    const width = Math.abs(x(r.pct) - x(0));
    const sign = r.pct >= 0 ? "▲" : "▼";
    const tip = `<strong>${esc(roleLabel(r.role_family))}</strong>${sign} ${Math.abs(r.pct).toFixed(1)}%<br><span class="muted">${fmtInt(r.current)} vs ${fmtInt(r.prior)}</span>`;
    return `
      <div class="chart-row">
        <div class="row-label${selected ? " is-selected" : ""}">${esc(roleLabel(r.role_family))}</div>
        <div class="plot" tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(roleLabel(r.role_family))}: ${r.pct >= 0 ? "up" : "down"} ${Math.abs(r.pct).toFixed(1)} percent">
          ${ticks.map((t) => `<span class="gridline" style="left:${x(t)}%"></span>`).join("")}
          <span class="zero" style="left:${x(0)}%"></span>
          <span class="bar ${r.pct >= 0 ? "pos" : "neg"}${selected ? "" : " muted"}${Math.abs(r.pct) > lim ? " clipped" : ""}" style="left:${left}%;width:${Math.max(width, 0.5)}%"></span>
        </div>
        <div class="row-value">${sign} ${Math.abs(r.pct).toFixed(1)}%</div>
      </div>`;
  }).join("") + `
    <div class="axis-row"><span></span><div class="axis">${ticks.map((t) => `<span style="left:${x(t)}%">${t > 0 ? "+" : t < 0 ? "−" : ""}${Math.abs(t)}%</span>`).join("")}</div><span></span></div>`;
  bindTips(el);

  $("trend-table").innerHTML = table(
    [{ label: "Role" }, { label: `FY${DATA.trend.current_fy - 1}`, num: true }, { label: `FY${DATA.trend.current_fy}`, num: true }, { label: "Change", num: true }],
    rows.map((r) => [roleLabel(r.role_family), fmtInt(r.prior), fmtInt(r.current), `${r.pct >= 0 ? "+" : ""}${r.pct.toFixed(1)}%`]),
  );
}

/* ---------- top states (bars) ---------- */

function renderStates() {
  const rows = DATA.states
    .filter((s) => s.fiscal_year === state.fy && s.role_family === state.role)
    .sort((a, b) => b.apps - a.apps)
    .slice(0, 10);
  const el = $("state-chart");
  if (!rows.length) { el.innerHTML = `<p class="empty">Not enough data for this selection.</p>`; $("state-table").innerHTML = ""; return; }

  const ticks = niceTicks(0, rows[0].apps, 4);
  const max = ticks[ticks.length - 1];
  const x = (v) => (v / max) * 100;
  el.innerHTML = rows.map((s) => {
    const tip = `<strong>${esc(s.state)}</strong>${fmtInt(s.apps)} certified applications<br><span class="muted">Median salary ${fmtMoneyFull(s.median_wage)}</span>`;
    return `
      <div class="chart-row">
        <div class="row-label">${esc(s.state)}</div>
        <div class="plot" tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(s.state)}: ${fmtInt(s.apps)} applications">
          ${ticks.map((t) => `<span class="gridline" style="left:${x(t)}%"></span>`).join("")}
          <span class="bar pos" style="left:0;width:${x(s.apps)}%"></span>
        </div>
        <div class="row-value">${fmtInt(s.apps)}</div>
      </div>`;
  }).join("") + `
    <div class="axis-row"><span></span><div class="axis">${ticks.map((t) => `<span style="left:${x(t)}%">${fmtInt(t)}</span>`).join("")}</div><span></span></div>`;
  bindTips(el);

  $("state-table").innerHTML = table(
    [{ label: "State" }, { label: "Applications", num: true }, { label: "Median salary", num: true }],
    rows.map((s) => [s.state, fmtInt(s.apps), fmtMoneyFull(s.median_wage)]),
  );
}

/* ---------- wiring ---------- */

function renderAll() {
  hideTip();
  renderTiles();
  renderSponsors();
  renderWages();
  renderTrend();
  renderStates();
}

function setupFilters() {
  const roleSel = $("role-filter");
  roleSel.innerHTML = Object.entries(DATA.meta.role_groups).map(([group, roles]) =>
    `<optgroup label="${esc(group)}">${roles.map((r) => `<option value="${r}">${esc(roleLabel(r))}</option>`).join("")}</optgroup>`,
  ).join("");
  roleSel.value = state.role;

  const fySel = $("fy-filter");
  const years = [...DATA.meta.coverage].sort((a, b) => b.fiscal_year - a.fiscal_year);
  fySel.innerHTML = years.map((c) => `<option value="${c.fiscal_year}">${esc(coverageLabel(c))}</option>`).join("");
  // Default to the most recent complete year.
  state.fy = (years.find((c) => c.is_complete) || years[0]).fiscal_year;
  fySel.value = String(state.fy);

  roleSel.addEventListener("change", () => { state.role = roleSel.value; state.shown = PAGE_SIZE; renderAll(); });
  fySel.addEventListener("change", () => { state.fy = Number(fySel.value); state.shown = PAGE_SIZE; renderAll(); });
  $("employer-search").addEventListener("input", (e) => { state.search = e.target.value; state.shown = PAGE_SIZE; renderSponsors(); });
  $("show-more").addEventListener("click", () => { state.shown += PAGE_SIZE; renderSponsors(); });
  window.addEventListener("scroll", hideTip, { passive: true });
}

async function main() {
  try {
    const [meta, trend, wages, employers, states] = await Promise.all(
      ["meta", "trend", "wages", "employers", "states"].map(loadJson),
    );
    DATA = { meta, trend, wages: fromColumnar(wages), employers: fromColumnar(employers), states: fromColumnar(states) };
  } catch (err) {
    $("coverage").textContent = `Could not load data (${err.message}).`;
    return;
  }
  const cov = DATA.meta.coverage.map(coverageLabel).join(", ");
  const updated = new Date(DATA.meta.generated_at).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  $("coverage").textContent = `Data loaded: ${cov}. Updated ${updated}.`;
  setupFilters();
  renderAll();
}

main();
