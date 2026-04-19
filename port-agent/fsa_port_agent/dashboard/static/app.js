// FSA Port Agent dashboard glue.
// No libraries. Vanilla JS. Polls local-only API at /api/*.

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const STATE_ORDER = [
  "BUILDS", "CLEANED", "MATCHED_TWW", "SIG_MATCHED",
  "TRIAGED", "FAILED", "PERMANENT_FAIL", "UNKNOWN",
];
const STATE_COLORS = {
  BUILDS:         "var(--s-BUILDS)",
  CLEANED:        "var(--s-CLEANED)",
  MATCHED_TWW:    "var(--s-MATCHED_TWW)",
  SIG_MATCHED:    "var(--s-SIG_MATCHED)",
  TRIAGED:        "var(--s-TRIAGED)",
  FAILED:         "var(--s-FAILED)",
  PERMANENT_FAIL: "var(--s-PERMANENT_FAIL)",
  UNKNOWN:        "var(--s-UNKNOWN)",
};
const STATE_TIPS = {
  BUILDS:         "compiles under Emscripten as well as cc -fsyntax-only",
  CLEANED:        "m2c output rewritten to compilable C and passed the gate",
  MATCHED_TWW:    "byte-identical body imported from a donor source tree",
  SIG_MATCHED:    "signature matched via heuristics (not byte-matched)",
  TRIAGED:        "row in DB, call graph known, no source yet",
  FAILED:         "last cleanup attempt failed (retryable at higher tier)",
  PERMANENT_FAIL: "exhausted retries — parked",
  UNKNOWN:        "not yet seen",
};
// Display override — backend state string stays MATCHED_TWW for compatibility,
// but the UI just shows "MATCHED". Everything else renders its own key.
const STATE_LABELS = {
  MATCHED_TWW: "MATCHED",
};
const stateLabel = s => STATE_LABELS[s] || s;
const TAG_COLORS = {
  LEAF:         "#88d97a",
  CONSTRUCTOR:  "#bc8cff",
  VTABLE_THUNK: "#79c0ff",
  INTERNAL:     "#f2c058",
  MSL:          "#5fd3d9",
};
const ACTION_META = {
  triage:               { label: "Run Triage",               cls: "primary", tip: "Scan asm splits, populate state.db, build the call graph." },
  triage_limit_200:     { label: "Triage (limit 200)",       cls: "",        tip: "Small smoke-test triage." },
  import_dry:           { label: "Match sweep (dry-run)",    cls: "",        tip: "Count byte-identical matches against donor sources without writing." },
  import_real:          { label: "Match sweep (real)",       cls: "warn",    tip: "Compile donor sources, byte-match, copy hits into src/. Long run." },
  import_limit_20:      { label: "Match sweep (limit 20)",   cls: "ghost",   tip: "Sample sweep — dry-only." },
  cleanup_prepare_10:   { label: "Prepare 10 cleanup",       cls: "",        tip: "Enqueue 10 cleanup prompts (leaf-first topo order)." },
  cleanup_prepare_50:   { label: "Prepare 50 cleanup",       cls: "",        tip: "Enqueue 50 cleanup prompts." },
  cleanup_apply:        { label: "Apply cleanup responses",  cls: "primary", tip: "Splice Claude Code's .response.c files into seg_*.c; mark CLEANED." },
  cleanup_status:       { label: "Queue status",             cls: "ghost",   tip: "Print queue counts — no side effects." },
  cleanup_retry_failed: { label: "Retry FAILED cleanup",     cls: "",        tip: "Re-enqueue FAILED rows at next tier (cheap→expensive→opus)." },
  hal:                  { label: "HAL scaffold",             cls: "",        tip: "Write Emscripten-compatible stubs for GX/AX/PAD/DVD/OSThread." },
  build_check:          { label: "Build-check (all)",        cls: "",        tip: "cc -fsyntax-only across every seg_*.c." },
  build_check_limit_50: { label: "Build-check 50 segs",      cls: "",        tip: "cc -fsyntax-only across first 50 seg_*.c." },
  build_prepare_10:     { label: "Prepare 10 fix_build",     cls: "",        tip: "Enqueue 10 FIX_BUILD prompts from compile errors." },
  build_apply:          { label: "Apply fix_build diffs",    cls: "primary", tip: "Splice unified-diff responses back into source." },
  build_status:         { label: "Build status",             cls: "ghost",   tip: "Print build-check tallies." },
};

const fmt = n => (n ?? 0).toLocaleString();
const hex = a => "0x" + a.toString(16).toUpperCase().padStart(8, "0");
const svgEl = (tag, attrs = {}, text) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
};

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------
function initTabs() {
  const tabs  = document.querySelectorAll(".tab");
  const panes = document.querySelectorAll(".tab-panel");
  const activate = (name) => {
    tabs.forEach(t => t.setAttribute("aria-selected", t.dataset.tab === name ? "true" : "false"));
    panes.forEach(p => p.classList.toggle("active", p.dataset.panel === name));
    if (name === "treemap") refreshTreemap();
    // SVG bars pick size from clientWidth; hidden panels read 0. Re-render
    // after the tab becomes visible so chart widths match the layout.
    if (name === "pipeline" || name === "overview") requestAnimationFrame(loadAll);
    // persist in URL hash for bookmarks / refresh
    history.replaceState(null, "", "#" + name);
  };
  tabs.forEach(t => t.addEventListener("click", () => activate(t.dataset.tab)));
  const initial = (location.hash || "#overview").slice(1);
  if (document.querySelector(`.tab[data-tab="${initial}"]`)) activate(initial);
}

// ---------------------------------------------------------------------------
// SVG: donut
// ---------------------------------------------------------------------------
function renderDonut(hostId, data, centerLabel = "total") {
  const host = document.getElementById(hostId);
  host.innerHTML = "";
  const total = data.reduce((s, d) => s + d.value, 0);
  if (total === 0) { host.innerHTML = `<div class="muted small" style="padding: 40px 0;">no data</div>`; return; }
  const size = 180, r = 72, ir = 42, cx = size / 2, cy = size / 2;
  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size });
  let angle = -Math.PI / 2;
  for (const d of data) {
    if (d.value <= 0) continue;
    const a2 = angle + (d.value / total) * Math.PI * 2;
    const large = (a2 - angle) > Math.PI ? 1 : 0;
    const x1 = cx + r * Math.cos(angle),  y1 = cy + r * Math.sin(angle);
    const x2 = cx + r * Math.cos(a2),     y2 = cy + r * Math.sin(a2);
    const xi2 = cx + ir * Math.cos(a2),   yi2 = cy + ir * Math.sin(a2);
    const xi1 = cx + ir * Math.cos(angle), yi1 = cy + ir * Math.sin(angle);
    const path = svgEl("path", {
      d: [`M${x1},${y1}`, `A${r},${r} 0 ${large} 1 ${x2},${y2}`,
          `L${xi2},${yi2}`, `A${ir},${ir} 0 ${large} 0 ${xi1},${yi1}`, "Z"].join(" "),
      fill: d.color,
    });
    path.appendChild(svgEl("title", {}, `${d.label}: ${d.value} (${(d.value/total*100).toFixed(1)}%)`));
    svg.appendChild(path);
    angle = a2;
  }
  const big = svgEl("text", { x: cx, y: cy - 2, "text-anchor": "middle",
    "font-family": "var(--mono)", "font-size": 18, fill: "var(--fg)", "font-weight": 600 });
  big.textContent = fmt(total);
  const small = svgEl("text", { x: cx, y: cy + 14, "text-anchor": "middle",
    "font-family": "var(--mono)", "font-size": 10, fill: "var(--fg-muted)" });
  small.textContent = centerLabel;
  svg.appendChild(big); svg.appendChild(small);
  host.appendChild(svg);
}

// ---------------------------------------------------------------------------
// SVG: horizontal bars
// ---------------------------------------------------------------------------
function renderBars(hostId, data, opts = {}) {
  const host = document.getElementById(hostId);
  host.innerHTML = "";
  if (!data.length) { host.innerHTML = `<div class="muted small" style="padding: 24px 0;">no data</div>`; return; }
  // match viewBox to the container's actual pixel width — no stretching.
  // clamp to a minimum of 260 so very narrow panels don't collapse labels.
  const w = Math.max(260, Math.floor(host.clientWidth || 360));
  const barH = 20, gap = 7, padL = Math.min(140, Math.max(90, Math.floor(w * 0.28))), padR = 48;
  const h = data.length * (barH + gap) + 6;
  const max = opts.max || Math.max(...data.map(d => d.value)) || 1;
  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, width: w, height: h });

  data.forEach((d, i) => {
    const y = i * (barH + gap) + 2;
    const bw = Math.max(0.5, (d.value / max) * (w - padL - padR));
    const label = svgEl("text", {
      x: padL - 6, y: y + barH / 2 + 3.5, "text-anchor": "end",
      "font-family": "var(--mono)", "font-size": 10.5, fill: "var(--fg-muted)",
    });
    label.textContent = d.label;
    svg.appendChild(label);

    svg.appendChild(svgEl("rect", {
      x: padL, y, width: w - padL - padR, height: barH,
      fill: "var(--bg-card-2)", stroke: "var(--border)", "stroke-width": 1, rx: 3,
    }));
    const bar = svgEl("rect", {
      x: padL, y, width: bw, height: barH,
      fill: d.color || "var(--accent)", rx: 3,
    });
    bar.appendChild(svgEl("title", {}, `${d.label}: ${fmt(d.value)}${opts.unit || ""}`));
    svg.appendChild(bar);

    const num = svgEl("text", {
      x: padL + bw + 5, y: y + barH / 2 + 3.5,
      "font-family": "var(--mono)", "font-size": 10.5, fill: "var(--fg)",
    });
    num.textContent = opts.formatter ? opts.formatter(d.value) : fmt(d.value);
    svg.appendChild(num);
  });
  host.appendChild(svg);
}

// ---------------------------------------------------------------------------
// Stacked progress bar (new chart type — one row, layered segments)
// ---------------------------------------------------------------------------
function renderProgressStack(hostId, stateCounts, total) {
  const host = document.getElementById(hostId);
  host.innerHTML = "";
  const stackOrder = ["BUILDS", "CLEANED", "MATCHED_TWW", "SIG_MATCHED", "TRIAGED", "FAILED", "PERMANENT_FAIL", "UNKNOWN"];
  const present = stackOrder
    .map(s => ({ state: s, n: stateCounts[s] || 0 }))
    .filter(d => d.n > 0);
  const w = 600, h = 42;
  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, width: "100%", height: h, preserveAspectRatio: "none" });
  let x = 0;
  present.forEach(d => {
    const bw = (d.n / Math.max(1, total)) * w;
    const rect = svgEl("rect", {
      x, y: 4, width: bw, height: h - 20,
      fill: `var(--s-${d.state})`,
    });
    rect.appendChild(svgEl("title", {}, `${stateLabel(d.state)}: ${fmt(d.n)} (${(100*d.n/total).toFixed(1)}%)`));
    svg.appendChild(rect);
    // labels on segments wide enough
    if (bw > 48) {
      const t = svgEl("text", {
        x: x + 4, y: h - 8, "font-family": "var(--mono)", "font-size": 9,
        fill: "#0b0d12", "font-weight": 600,
      });
      t.textContent = `${stateLabel(d.state)} ${fmt(d.n)}`;
      svg.appendChild(t);
    }
    x += bw;
  });
  host.appendChild(svg);
  // legend row below
  const lg = document.createElement("div");
  lg.className = "legend";
  lg.style.marginTop = "6px";
  lg.innerHTML = present.map(d =>
    `<span><span class="swatch" style="background:var(--s-${d.state})"></span>${stateLabel(d.state)} · ${fmt(d.n)}</span>`
  ).join("");
  host.appendChild(lg);
}

// ---------------------------------------------------------------------------
// DOL address strip
// ---------------------------------------------------------------------------
function renderStrip(data) {
  const host = document.getElementById("address-strip");
  host.innerHTML = "";
  if (!data.bins || !data.bins.length) {
    host.innerHTML = `<div class="muted small" style="padding: 10px;">no data — run triage first</div>`;
    return;
  }
  for (const b of data.bins) {
    const cell = document.createElement("div");
    cell.className = "strip-cell " + (b.state ? "s-" + b.state : "s-UNKNOWN");
    cell.title = b.count ? `${b.count} fn (${b.state || "empty"})` : "";
    host.appendChild(cell);
  }
  document.getElementById("strip-lo").textContent = hex(data.min_addr);
  document.getElementById("strip-hi").textContent = hex(data.max_addr);

  // state legend under the strip
  const lg = document.getElementById("state-legend");
  lg.innerHTML = STATE_ORDER.map(s =>
    `<span data-tip="${STATE_TIPS[s] || ""}"><span class="sw s-${s}"></span>${stateLabel(s)}</span>`
  ).join("");
}

// ---------------------------------------------------------------------------
// KPI row
// ---------------------------------------------------------------------------
function renderKPIs(snapshot) {
  const s = snapshot.state_counts || {};
  const total = snapshot.total_functions || 0;
  const dolTotal = (snapshot.dol_total || 5981);
  const matched = (s.CLEANED||0) + (s.MATCHED_TWW||0) + (s.SIG_MATCHED||0) + (s.BUILDS||0);
  const remaining = (s.UNKNOWN||0) + (s.TRIAGED||0);
  const failed = (s.FAILED||0) + (s.PERMANENT_FAIL||0);
  const matchedPct = total ? (100 * matched / total) : 0;
  const remainingPct = total ? (100 * remaining / total) : 0;

  document.getElementById("kpi-total").textContent = fmt(total);
  document.getElementById("kpi-total-sub").textContent = `/ ${fmt(dolTotal)} dol`;

  document.getElementById("kpi-progressing").textContent = fmt(matched);
  document.getElementById("kpi-progressing-sub").textContent =
    total ? `${matchedPct.toFixed(1)}% of fns` : "—";
  const pbar = document.getElementById("kpi-progress-bar");
  if (pbar) pbar.style.width = matchedPct.toFixed(2) + "%";

  document.getElementById("kpi-cleaned").textContent = fmt(s.CLEANED || 0);

  document.getElementById("kpi-remaining").textContent = fmt(remaining);
  document.getElementById("kpi-remaining-sub").textContent =
    total ? `${remainingPct.toFixed(1)}% left` : "—";

  document.getElementById("kpi-failed").textContent = fmt(failed);
  document.getElementById("kpi-failed-sub").textContent =
    `${s.FAILED||0} retry · ${s.PERMANENT_FAIL||0} parked`;
}

// ---------------------------------------------------------------------------
// Queue + mini joblist
// ---------------------------------------------------------------------------
function renderQueue(q, hostId) {
  const host = document.getElementById(hostId);
  host.innerHTML = "";
  const entries = Object.entries(q);
  if (!entries.length) {
    host.innerHTML = `<div class="muted small">empty</div>`;
    return;
  }
  for (const [kind, info] of entries) {
    const el = document.createElement("div");
    el.className = "queue-item";
    const idsPreview = (info.pending_ids || []).slice(0, 3).join(" ");
    el.innerHTML = `
      <div class="name">${kind}</div>
      <div class="stats">${info.pending} pending · ${info.with_responses} responses · ${info.done_files} done</div>
      ${idsPreview ? `<div class="pending-ids">${idsPreview}</div>` : ""}
    `;
    host.appendChild(el);
  }
}

function renderJobsMini(jobs) {
  const host = document.getElementById("overview-jobs");
  host.innerHTML = "";
  if (!jobs.length) { host.innerHTML = `<div class="muted small">no jobs run yet</div>`; return; }
  const recent = jobs.slice(0, 5);
  for (const j of recent) {
    const ago = j.started_at ? ((Date.now()/1000 - j.started_at) | 0) + "s ago" : "";
    const row = document.createElement("div");
    row.className = "mj-row";
    const action = (j.cmd || []).slice(j.cmd.indexOf("--phase") + 1).join(" ");
    row.innerHTML = `
      <span class="mj-id">${j.id}</span>
      <span class="mj-status ${j.status}">${j.status}${j.returncode !== null ? `:${j.returncode}` : ""}</span>
      <span class="grow">${action}</span>
      <span>${ago}</span>`;
    host.appendChild(row);
  }
}

// ---------------------------------------------------------------------------
// Cleanup stats (pipeline tab)
// ---------------------------------------------------------------------------
function renderCleanupStats(s) {
  const TIER_COLORS = {
    cheap:     "var(--s-TRIAGED)",
    expensive: "var(--s-SIG_MATCHED)",
    opus:      "var(--s-CLEANED)",
    unknown:   "var(--fg-dim)",
  };
  const tierRows = Object.entries(s.per_tier || {})
    .sort((a, b) => (b[1].total || 0) - (a[1].total || 0))
    .map(([tier, c]) => ({
      label: `${tier} ${c.CLEANED || 0}/${c.total || 0}`,
      value: c.success_pct || 0,
      color: TIER_COLORS[tier] || "var(--accent)",
    }));
  renderBars("cleanup-tier-chart", tierRows, { max: 100, unit: "%" });

  const errRows = Object.entries(s.error_buckets || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ label: k, value: v, color: "var(--s-FAILED)" }));
  renderBars("cleanup-error-chart", errRows);

  const cx = s.context_availability || {};
  const ctxRows = [
    { label: "reference", value: cx.has_tww_ref    || 0 },
    { label: "callees",   value: cx.has_callees    || 0 },
    { label: "callers",   value: cx.has_callers    || 0 },
    { label: "nearby",    value: cx.has_nearby     || 0 },
    { label: "strings",   value: cx.has_strings    || 0 },
    { label: "m2c_error", value: cx.has_m2c_error  || 0 },
  ].map(r => ({ ...r, color: "var(--accent)" }));
  renderBars("cleanup-ctx-chart", ctxRows, { max: cx.total || 1 });

  const t = s.totals || {};
  const totalsHost = document.getElementById("cleanup-totals");
  totalsHost.innerHTML = `
    <span class="pill">attempts · ${fmt(t.attempts || 0)}</span>
    <span class="pill ok">cleaned · ${fmt(t.cleaned || 0)}</span>
    <span class="pill warn">lex-fail · ${fmt(t.failed_lex || 0)}</span>
    <span class="pill warn">compile-fail · ${fmt(t.failed_compile || 0)}</span>
    <span class="pill bad">permanent · ${fmt(t.permanent_fail || 0)}</span>
  `;
  const batchHost = document.getElementById("cleanup-batch-list");
  if (!s.batches || !s.batches.length) {
    batchHost.innerHTML = "<em>(no batches yet — run Prepare 10/50 cleanup)</em>";
  } else {
    batchHost.innerHTML = s.batches.slice().reverse().map(b => {
      const ts = b.generated_at_unix ? new Date(b.generated_at_unix * 1000).toLocaleString() : "";
      const tiers = b.tiers || {};
      return `<div class="batch-row">
        <code>${b.batch_id}</code>
        <span class="dim">${b.task_count} tasks · cheap=${tiers.cheap||0} exp=${tiers.expensive||0} opus=${tiers.opus||0}</span>
        <span class="dim">${ts}</span>
      </div>`;
    }).join("");
  }
}

// ---------------------------------------------------------------------------
// Functions table
// ---------------------------------------------------------------------------
let fnPage = 0;
const FN_PAGE_SIZE = 100;

function renderFunctions(rows) {
  const tbody = document.querySelector("#fn-table-el tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted" style="padding:20px;text-align:center;">no functions match</td></tr>`;
    return;
  }
  rows.forEach(r => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="addr">${hex(r.addr)}</td>
      <td>${r.name || "—"}</td>
      <td>${fmt(r.size || 0)}</td>
      <td>${r.tag || "—"}</td>
      <td class="state-${r.state}">${stateLabel(r.state)}</td>
      <td>${r.unit || "—"}</td>
    `;
    tbody.appendChild(tr);
  });
}

function populateFilters(snapshot) {
  const sSel = document.getElementById("filter-state");
  const tSel = document.getElementById("filter-tag");
  const curS = sSel.value, curT = tSel.value;
  const clearAfterAll = sel => { while (sel.options.length > 1) sel.remove(1); };
  clearAfterAll(sSel); clearAfterAll(tSel);
  for (const s of Object.keys(snapshot.state_counts || {})) sSel.add(new Option(s, s));
  for (const t of Object.keys(snapshot.tag_counts || {}))   tSel.add(new Option(t, t));
  sSel.value = curS; tSel.value = curT;
}

async function refreshFns() {
  const s = document.getElementById("filter-state").value;
  const t = document.getElementById("filter-tag").value;
  const nameFilter = document.getElementById("filter-name").value.trim();
  const qs = new URLSearchParams({
    limit: String(FN_PAGE_SIZE),
    offset: String(fnPage * FN_PAGE_SIZE),
  });
  if (s) qs.set("state", s);
  if (t) qs.set("tag", t);
  if (nameFilter) qs.set("q", nameFilter);
  const rows = await api("/api/functions?" + qs);
  renderFunctions(rows);
  document.getElementById("fn-count").textContent = `page ${fnPage + 1} · ${rows.length} rows`;
  document.getElementById("fn-page-label").textContent = String(fnPage + 1);
  // disable next-page when we got fewer than a full page of rows
  const nextBtn = document.getElementById("fn-next");
  nextBtn.disabled = rows.length < FN_PAGE_SIZE;
  document.getElementById("fn-prev").disabled = fnPage === 0;
}

// ---------------------------------------------------------------------------
// Actions bar — rendered from /api/actions (allow-list)
// ---------------------------------------------------------------------------
async function renderActions() {
  const host = document.getElementById("actions-grid");
  host.innerHTML = "";
  let actions;
  try {
    const r = await api("/api/actions");
    actions = r.actions || [];
  } catch { host.innerHTML = `<div class="muted small">actions unavailable</div>`; return; }
  for (const name of actions) {
    const meta = ACTION_META[name] || { label: name, cls: "", tip: "" };
    const btn = document.createElement("button");
    btn.className = "btn " + meta.cls;
    btn.dataset.action = name;
    btn.textContent = meta.label;
    if (meta.tip) btn.title = meta.tip;
    btn.addEventListener("click", () => runAction(name));
    host.appendChild(btn);
  }
}

// ---------------------------------------------------------------------------
// Job runner
// ---------------------------------------------------------------------------
let activeJobId = null;
let jobPollTimer = null;

async function runAction(action) {
  const buttons = document.querySelectorAll("#actions-grid button");
  buttons.forEach(b => b.disabled = true);
  setJobStatus("running", action, "");
  document.getElementById("job-log").textContent = "";
  document.getElementById("log-drawer").classList.remove("collapsed");
  try {
    const r = await api("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    activeJobId = r.job_id;
    pollJob();
  } catch (e) {
    document.getElementById("job-log").textContent = "failed to start: " + e.message;
    setJobStatus("failed", "error", "");
    buttons.forEach(b => b.disabled = false);
  }
}

function setJobStatus(status, label, rc) {
  const el = document.getElementById("job-status");
  el.className = "verdict " + status;
  el.textContent = status + (rc ? " " + rc : "");
  document.getElementById("job-cmd").textContent = label;
  const mini = document.getElementById("kpi-job-status");
  mini.className = "kpi-value small";
  mini.textContent = status;
  mini.classList.toggle("ok",   status === "done");
  mini.classList.toggle("bad",  status === "failed");
  mini.classList.toggle("warn", status === "running");
  document.getElementById("kpi-job-sub").textContent = label || "—";
}

async function pollJob() {
  if (!activeJobId) return;
  clearTimeout(jobPollTimer);
  try {
    const j = await api("/api/jobs/" + activeJobId);
    const logEl = document.getElementById("job-log");
    logEl.textContent = (j.log || []).join("\n");
    if (document.getElementById("follow-tail").checked) {
      logEl.scrollTop = logEl.scrollHeight;
    }
    const label = (j.cmd || []).slice((j.cmd || []).indexOf("--phase") + 1).join(" ");
    setJobStatus(j.status, label, j.returncode !== null ? `(rc=${j.returncode})` : "");
    if (j.status === "done" || j.status === "failed") {
      document.querySelectorAll("#actions-grid button").forEach(b => b.disabled = false);
      activeJobId = null;
      loadAll();
      return;
    }
  } catch (e) { console.error("poll:", e); }
  jobPollTimer = setTimeout(pollJob, 750);
}

// ---------------------------------------------------------------------------
// Treemap (squarified, Bruls/Huijing/van Wijk 2000)
// ---------------------------------------------------------------------------
let treemapData = null;            // raw groups from /api/treemap
let treemapLayout = null;          // computed [{name, x, y, w, h, ...}]
let treemapHover = null;
const tmCanvas = () => document.getElementById("treemap-canvas");

async function refreshTreemap() {
  if (!treemapData) {
    try {
      const r = await api("/api/treemap");
      treemapData = r.groups || [];
    } catch { return; }
  }
  drawTreemap();
}

function layoutTreemap(items, bounds) {
  // Classic squarified treemap — operate on a mutable sub-rectangle.
  items = items.slice();
  items.sort((a, b) => b.size - a.size);
  const sum = items.reduce((s, x) => s + x.size, 0);
  if (sum <= 0 || items.length === 0) return [];

  const rect = { x: bounds.x, y: bounds.y, w: bounds.w, h: bounds.h };
  const area = rect.w * rect.h;
  // normalize sizes to area
  items.forEach(x => x._area = x.size / sum * area);

  const result = [];
  let row = [];
  let i = 0;
  while (i < items.length) {
    const side = Math.min(rect.w, rect.h);
    const rowWithNext = row.concat([items[i]]);
    const wCur  = worst(row, side);
    const wNext = worst(rowWithNext, side);
    if (row.length === 0 || wNext <= wCur) {
      row = rowWithNext;
      i++;
    } else {
      layoutRow(row, rect, result);
      row = [];
    }
  }
  if (row.length) layoutRow(row, rect, result);
  return result;

  function worst(r, side) {
    if (!r.length) return Infinity;
    const s = r.reduce((a, b) => a + b._area, 0);
    const s2 = side * side;
    const ss = s * s;
    let rMin = Infinity, rMax = 0;
    for (const it of r) { if (it._area < rMin) rMin = it._area; if (it._area > rMax) rMax = it._area; }
    return Math.max((s2 * rMax) / ss, ss / (s2 * rMin));
  }
  function layoutRow(r, rect, out) {
    const rowSum = r.reduce((a, b) => a + b._area, 0);
    if (rect.w >= rect.h) {
      // carve off a vertical slab of width = rowSum / rect.h
      const slabW = rowSum / rect.h;
      let y = rect.y;
      for (const it of r) {
        const h = it._area / slabW;
        out.push({ ...it, x: rect.x, y, w: slabW, h });
        y += h;
      }
      rect.x += slabW; rect.w -= slabW;
    } else {
      const slabH = rowSum / rect.w;
      let x = rect.x;
      for (const it of r) {
        const w = it._area / slabH;
        out.push({ ...it, x, y: rect.y, w, h: slabH });
        x += w;
      }
      rect.y += slabH; rect.h -= slabH;
    }
  }
}

function treemapFilteredData() {
  let data = treemapData || [];
  const filter = document.getElementById("tm-filter").value.trim().toLowerCase();
  const hide = document.getElementById("tm-hide-unassigned").checked;
  if (hide) data = data.filter(g => g.kind !== "page");
  if (filter) {
    const terms = filter.split(/\s+/);
    data = data.filter(g => terms.every(t => g.name.toLowerCase().includes(t)));
  }
  const sizeBy = document.querySelector('input[name="tm-size"]:checked')?.value || "count";
  return data.map(g => ({ ...g, size: sizeBy === "bytes" ? g.bytes : g.total }))
             .filter(g => g.size > 0);
}

function drawTreemap() {
  const canvas = tmCanvas();
  if (!canvas) return;
  const ratio = window.devicePixelRatio || 1;
  const { width: cw, height: ch } = canvas.getBoundingClientRect();
  if (cw === 0 || ch === 0) return;  // panel hidden
  const rw = Math.round(cw * ratio), rh = Math.round(ch * ratio);
  if (canvas.width !== rw || canvas.height !== rh) {
    canvas.width = rw; canvas.height = rh;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  ctx.fillStyle = "#050710";
  ctx.fillRect(0, 0, cw, ch);

  const items = treemapFilteredData();
  treemapLayout = layoutTreemap(items, { x: 0, y: 0, w: cw, h: ch });

  const style = getComputedStyle(document.body);
  const colorFor = (state) => style.getPropertyValue("--s-" + state).trim() || "#333";

  ctx.font = "10px ui-monospace, monospace";
  ctx.textBaseline = "top";
  for (const it of treemapLayout) {
    const base = colorFor(it.dominant);
    // radial gradient — brighter at top-left, darker at bottom-right; overall
    // lightness scales with progress_pct (inspired by decomp.dev).
    const cx = it.x + it.w * 0.4, cy = it.y + it.h * 0.4;
    const r0 = (it.w + it.h) * 0.1, r1 = (it.w + it.h) * 0.5;
    const grad = ctx.createRadialGradient(cx, cy, r0, cx, cy, r1);
    const boost = Math.min(0.25, (it.progress_pct || 0) / 100 * 0.25);
    grad.addColorStop(0, shade(base, +0.08 + boost));
    grad.addColorStop(1, shade(base, -0.3));
    ctx.fillStyle = grad;
    ctx.fillRect(it.x, it.y, it.w, it.h);
    // border
    ctx.strokeStyle = "rgba(0,0,0,0.6)";
    ctx.lineWidth = 1;
    ctx.strokeRect(it.x + 0.5, it.y + 0.5, Math.max(0, it.w - 1), Math.max(0, it.h - 1));
    // label if large enough
    if (it.w > 70 && it.h > 22) {
      const name = shortName(it.name);
      ctx.fillStyle = "rgba(0,0,0,0.85)";
      ctx.fillText(name, it.x + 6, it.y + 5);
      ctx.fillStyle = "rgba(255,255,255,0.78)";
      ctx.fillText(name, it.x + 5, it.y + 4);
      if (it.h > 40) {
        const sub = `${fmt(it.total)} fns · ${it.progress_pct}%`;
        ctx.fillStyle = "rgba(0,0,0,0.7)";
        ctx.fillText(sub, it.x + 5, it.y + 18);
      }
    }
  }
  // hover outline
  if (treemapHover) {
    const h = treemapHover;
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#fff";
    ctx.strokeRect(h.x + 1, h.y + 1, h.w - 2, h.h - 2);
  }

  // legend under canvas
  const lg = document.getElementById("treemap-legend");
  lg.innerHTML = STATE_ORDER.map(s =>
    `<span><span class="sw s-${s}"></span>${stateLabel(s)}</span>`
  ).join("");
}

function shortName(n) {
  // path/to/file.cpp → file.cpp (primary) — keep full on tooltip
  const slash = n.lastIndexOf("/");
  return slash >= 0 ? n.slice(slash + 1) : n;
}

function shade(hex, amt) {
  // accepts #rrggbb or rgb/hsl — fall back to darken via rgba overlay
  const c = hex.trim();
  if (/^#([0-9a-f]{6})$/i.test(c)) {
    const n = parseInt(c.slice(1), 16);
    let r = (n >> 16) & 0xff, g = (n >> 8) & 0xff, b = n & 0xff;
    r = Math.max(0, Math.min(255, r + amt * 255));
    g = Math.max(0, Math.min(255, g + amt * 255));
    b = Math.max(0, Math.min(255, b + amt * 255));
    return `rgb(${r|0},${g|0},${b|0})`;
  }
  return c;
}

function initTreemapInteractions() {
  const canvas = tmCanvas();
  if (!canvas) return;
  const tip = document.getElementById("treemap-tooltip");
  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    let hit = null;
    if (treemapLayout) {
      for (const it of treemapLayout) {
        if (mx >= it.x && mx <= it.x + it.w && my >= it.y && my <= it.y + it.h) { hit = it; break; }
      }
    }
    if (hit !== treemapHover) { treemapHover = hit; drawTreemap(); }
    if (hit) {
      const stateBarHtml = STATE_ORDER
        .filter(s => (hit.states || {})[s])
        .map(s => `<span style="flex:${hit.states[s]};background:var(--s-${s})"></span>`).join("");
      tip.innerHTML = `
        <div class="tt-title">${hit.name}</div>
        <div class="tt-row"><span>functions</span><b>${fmt(hit.total)}</b></div>
        <div class="tt-row"><span>bytes</span><b>${fmt(hit.bytes)}</b></div>
        <div class="tt-row"><span>dominant</span><b style="color:var(--s-${hit.dominant})">${stateLabel(hit.dominant)}</b></div>
        ${hit.ceiling && hit.ceiling !== hit.dominant ? `<div class="tt-row"><span>furthest</span><b style="color:var(--s-${hit.ceiling})">${stateLabel(hit.ceiling)}</b></div>` : ""}
        <div class="tt-row"><span>progress</span><b>${hit.progress_pct}%</b></div>
        <div class="tt-bar">${stateBarHtml}</div>`;
      tip.hidden = false;
      // clamp into viewport
      const tw = 260;
      let tx = mx + 14, ty = my + 14;
      if (tx + tw > rect.width) tx = mx - tw - 14;
      if (ty + 110 > rect.height) ty = my - 120;
      tip.style.left = tx + "px"; tip.style.top = ty + "px";
    } else {
      tip.hidden = true;
    }
  });
  canvas.addEventListener("mouseleave", () => {
    treemapHover = null;
    tip.hidden = true;
    drawTreemap();
  });
  window.addEventListener("resize", () => drawTreemap());
  document.getElementById("tm-filter").addEventListener("input", drawTreemap);
  document.getElementById("tm-hide-unassigned").addEventListener("change", drawTreemap);
  document.querySelectorAll('input[name="tm-size"]').forEach(r =>
    r.addEventListener("change", drawTreemap)
  );
}

// ---------------------------------------------------------------------------
// Log drawer
// ---------------------------------------------------------------------------
function initDrawer() {
  const drawer = document.getElementById("log-drawer");
  const syncPad = () => {
    // body's bottom padding must equal whatever the drawer's effective height
    // is, or the last grid row gets hidden under a fixed drawer.
    const h = drawer.classList.contains("collapsed") ? 32 : 220;
    document.body.style.paddingBottom = h + "px";
  };
  document.getElementById("drawer-toggle").addEventListener("click", () => {
    drawer.classList.toggle("collapsed");
    syncPad();
  });
  syncPad();
}

// ---------------------------------------------------------------------------
// Load cycle
// ---------------------------------------------------------------------------
async function loadAll() {
  try {
    const [snapshot, strip, queue, cleanupStats, jobs] = await Promise.all([
      api("/api/state"),
      api("/api/address_strip"),
      api("/api/queue"),
      api("/api/cleanup_stats").catch(() => null),
      api("/api/jobs").catch(() => ({ jobs: [] })),
    ]);

    const chip = document.getElementById("db-status");
    chip.textContent = snapshot.db_exists
      ? `state.db · ${fmt(snapshot.total_functions)} fns`
      : "state.db · not yet created";
    chip.className = "status-chip " + (snapshot.db_exists ? "ok" : "warn");

    renderKPIs(snapshot);
    populateFilters(snapshot);

    const stateData = STATE_ORDER
      .map(k => ({ label: k, value: snapshot.state_counts[k] || 0, color: STATE_COLORS[k] }))
      .filter(d => d.value > 0);
    renderDonut("chart-states", stateData, "total");
    document.getElementById("legend-states").innerHTML = stateData.map(d =>
      `<span data-tip="${STATE_TIPS[d.label] || ""}">
         <span class="swatch" style="background:${d.color}"></span>${stateLabel(d.label)} · ${fmt(d.value)}
       </span>`
    ).join("");

    const barData = Object.entries(snapshot.tag_counts || {})
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ label: k, value: v, color: TAG_COLORS[k] || "var(--accent)" }));
    renderBars("chart-tags", barData);

    renderProgressStack("chart-progress-stack", snapshot.state_counts, snapshot.total_functions || 1);

    renderStrip(strip);
    renderQueue(queue, "queue-grid");
    renderQueue(queue, "overview-queue");
    renderJobsMini(jobs.jobs || []);
    if (cleanupStats) renderCleanupStats(cleanupStats);

    // only refetch treemap when its tab is the active one — it's a full-table scan
    if (document.querySelector('.tab-panel[data-panel="treemap"].active')) {
      try {
        const tm = await api("/api/treemap");
        treemapData = tm.groups || [];
        drawTreemap();
      } catch {}
    }

    document.getElementById("last-refresh").textContent =
      "refreshed " + new Date().toLocaleTimeString();
  } catch (e) {
    console.error("loadAll:", e);
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initDrawer();
  initTreemapInteractions();
  renderActions();

  document.getElementById("refresh-fns").addEventListener("click", () => { fnPage = 0; refreshFns(); });
  document.getElementById("filter-state").addEventListener("change", () => { fnPage = 0; refreshFns(); });
  document.getElementById("filter-tag").addEventListener("change", () => { fnPage = 0; refreshFns(); });
  document.getElementById("filter-name").addEventListener("input", () => { refreshFns(); });
  document.getElementById("fn-prev").addEventListener("click", () => { if (fnPage > 0) { fnPage--; refreshFns(); } });
  document.getElementById("fn-next").addEventListener("click", () => { fnPage++; refreshFns(); });

  loadAll();
  refreshFns();
  setInterval(loadAll, 5000);

  // debounced resize — re-fit SVG bar widths to the new container width
  let resizeT = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(loadAll, 150);
  });
});
