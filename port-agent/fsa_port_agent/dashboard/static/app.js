// FSA Port Agent — dashboard glue.
// No libraries. SVG charts built by hand. Polls the local API.

const STATE_COLORS = {
  BUILDS:      "var(--s-BUILDS)",
  CLEANED:     "var(--s-CLEANED)",
  MATCHED_TWW: "var(--s-MATCHED_TWW)",
  SIG_MATCHED: "var(--s-SIG_MATCHED)",
  TRIAGED:     "var(--s-TRIAGED)",
  FAILED:      "var(--s-FAILED)",
  UNKNOWN:     "var(--s-UNKNOWN)",
};

const TAG_COLORS = {
  LEAF:         "#3fb950",
  CONSTRUCTOR:  "#bc8cff",
  VTABLE_THUNK: "#79c0ff",
  INTERNAL:     "#d29922",
  MSL:          "#39c5cf",
};

// ---------- API helpers ----------
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

const fmt = n => (n ?? 0).toLocaleString();
const hex = a => "0x" + a.toString(16).toUpperCase().padStart(8, "0");

// ---------- SVG chart: pie ----------
function renderPie(hostId, data, opts = {}) {
  const host = document.getElementById(hostId);
  host.innerHTML = "";
  const total = data.reduce((s, d) => s + d.value, 0);
  if (total === 0) {
    host.innerHTML = `<div style="color:var(--fg-dim); padding: 60px 0;">no data</div>`;
    return;
  }
  const size = 200, r = 78, ir = 40, cx = size / 2, cy = size / 2;
  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size });

  let angle = -Math.PI / 2;
  data.forEach(d => {
    if (d.value <= 0) return;
    const a2 = angle + (d.value / total) * Math.PI * 2;
    const large = (a2 - angle) > Math.PI ? 1 : 0;
    const x1 = cx + r * Math.cos(angle),  y1 = cy + r * Math.sin(angle);
    const x2 = cx + r * Math.cos(a2),     y2 = cy + r * Math.sin(a2);
    const xi2 = cx + ir * Math.cos(a2),   yi2 = cy + ir * Math.sin(a2);
    const xi1 = cx + ir * Math.cos(angle), yi1 = cy + ir * Math.sin(angle);
    const d_ = [
      `M${x1},${y1}`,
      `A${r},${r} 0 ${large} 1 ${x2},${y2}`,
      `L${xi2},${yi2}`,
      `A${ir},${ir} 0 ${large} 0 ${xi1},${yi1}`,
      "Z",
    ].join(" ");
    const path = svgEl("path", { d: d_, fill: d.color });
    path.appendChild(svgEl("title", {}, `${d.label}: ${d.value} (${(d.value/total*100).toFixed(1)}%)`));
    svg.appendChild(path);
    angle = a2;
  });

  // center label
  const labelBig = svgEl("text", {
    x: cx, y: cy - 2, "text-anchor": "middle",
    "font-family": "ui-monospace, monospace", "font-size": 20,
    fill: "var(--fg)", "font-weight": 600,
  });
  labelBig.textContent = fmt(total);
  svg.appendChild(labelBig);
  const labelSm = svgEl("text", {
    x: cx, y: cy + 14, "text-anchor": "middle",
    "font-family": "ui-monospace, monospace", "font-size": 10,
    fill: "var(--fg-muted)",
  });
  labelSm.textContent = opts.centerLabel || "total";
  svg.appendChild(labelSm);

  host.appendChild(svg);
}

// ---------- SVG chart: horizontal bar ----------
function renderBars(hostId, data) {
  const host = document.getElementById(hostId);
  host.innerHTML = "";
  if (!data.length) {
    host.innerHTML = `<div style="color:var(--fg-dim); padding: 60px 0;">no data</div>`;
    return;
  }
  const w = 260, barH = 22, gap = 8, padL = 110;
  const h = data.length * (barH + gap) + 10;
  const max = Math.max(...data.map(d => d.value)) || 1;
  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, width: "100%", height: h });

  data.forEach((d, i) => {
    const y = i * (barH + gap) + 4;
    const bw = Math.max(1, (d.value / max) * (w - padL - 40));

    const label = svgEl("text", {
      x: padL - 8, y: y + barH / 2 + 4, "text-anchor": "end",
      "font-family": "ui-monospace, monospace", "font-size": 11,
      fill: "var(--fg-muted)",
    });
    label.textContent = d.label;
    svg.appendChild(label);

    const bg = svgEl("rect", {
      x: padL, y, width: w - padL - 40, height: barH,
      fill: "var(--bg-card-2)", stroke: "var(--border)", "stroke-width": 1, rx: 3,
    });
    svg.appendChild(bg);

    const bar = svgEl("rect", {
      x: padL, y, width: bw, height: barH,
      fill: d.color || "var(--accent)", rx: 3,
    });
    bar.appendChild(svgEl("title", {}, `${d.label}: ${d.value}`));
    svg.appendChild(bar);

    const num = svgEl("text", {
      x: padL + bw + 6, y: y + barH / 2 + 4,
      "font-family": "ui-monospace, monospace", "font-size": 11,
      fill: "var(--fg)",
    });
    num.textContent = fmt(d.value);
    svg.appendChild(num);
  });

  host.appendChild(svg);
}

function svgEl(tag, attrs = {}, text) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}

// ---------- Address strip ----------
function renderStrip(data) {
  const host = document.getElementById("address-strip");
  host.innerHTML = "";
  if (!data.bins.length) {
    host.innerHTML = `<div style="color:var(--fg-dim); padding: 10px; font-size: 11px;">no data — run triage first</div>`;
    return;
  }
  data.bins.forEach(b => {
    const cell = document.createElement("div");
    cell.className = "strip-cell " + (b.state ? "s-" + b.state : "");
    if (!b.state) cell.style.background = "var(--bg-card-2)";
    cell.title = b.count ? `${b.count} fn (${b.state || "empty"})` : "";
    host.appendChild(cell);
  });
  document.getElementById("strip-lo").textContent = hex(data.min_addr);
  document.getElementById("strip-hi").textContent = hex(data.max_addr);
}

// ---------- Renderers ----------
function renderGate4(g4) {
  const pct = Math.min(100, g4.pct);
  document.getElementById("gate4-bar").style.width = pct + "%";
  document.getElementById("gate4-matched").textContent = fmt(g4.matched_tww);
  document.getElementById("gate4-total").textContent = fmt(g4.dol_total);
  document.getElementById("gate4-pct").textContent = pct.toFixed(1);
  document.getElementById("gate4-threshold-val").textContent = fmt(g4.threshold);
  const v = document.getElementById("gate4-verdict");
  v.className = "verdict " + (g4.passing ? "pass" : "fail");
  v.textContent = g4.passing ? "PASSING" : "NOT YET";
}

function renderBigNumbers(snapshot) {
  const host = document.getElementById("big-numbers");
  const s = snapshot.state_counts;
  const done = (s.CLEANED||0) + (s.MATCHED_TWW||0) + (s.SIG_MATCHED||0) + (s.BUILDS||0);
  const items = [
    { n: snapshot.total_functions, l: "fns in DB" },
    { n: done,                     l: "progressing" },
    { n: s.MATCHED_TWW || 0,       l: "TWW matched" },
    { n: s.FAILED || 0,            l: "failed" },
  ];
  host.innerHTML = items.map(i =>
    `<div class="big-num"><div class="n">${fmt(i.n)}</div><div class="l">${i.l}</div></div>`
  ).join("");
}

function renderQueue(q) {
  const host = document.getElementById("queue-grid");
  host.innerHTML = "";
  for (const [kind, info] of Object.entries(q)) {
    const el = document.createElement("div");
    el.className = "queue-item";
    const idsPreview = info.pending_ids.slice(0, 4).join(" ");
    el.innerHTML = `
      <div class="name">${kind}</div>
      <div class="stats">${info.pending} pending · ${info.with_responses} responses · ${info.done_files} archived</div>
      ${info.pending_ids.length ? `<div class="pending-ids">${idsPreview}${info.pending_ids.length > 4 ? " …" : ""}</div>` : ""}
    `;
    host.appendChild(el);
  }
}

function renderFunctions(rows) {
  const tbody = document.querySelector("#fn-table-el tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="color:var(--fg-dim);padding:20px;text-align:center;">no functions match</td></tr>`;
    return;
  }
  rows.forEach(r => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="addr">${hex(r.addr)}</td>
      <td>${r.name || "—"}</td>
      <td>${fmt(r.size || 0)}</td>
      <td>${r.tag || "—"}</td>
      <td class="state-${r.state}">${r.state}</td>
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
  for (const s of Object.keys(snapshot.state_counts)) sSel.add(new Option(s, s));
  for (const t of Object.keys(snapshot.tag_counts)) tSel.add(new Option(t, t));
  sSel.value = curS; tSel.value = curT;
}

// ---------- Job runner UI ----------
let activeJobId = null;
let jobPollTimer = null;

async function runAction(action) {
  const buttons = document.querySelectorAll(".actions button");
  buttons.forEach(b => b.disabled = true);
  const statusEl = document.getElementById("job-status");
  const logEl = document.getElementById("job-log");
  statusEl.className = "verdict running";
  statusEl.textContent = action;
  logEl.textContent = "";

  try {
    const r = await api("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    activeJobId = r.job_id;
    pollJob();
  } catch (e) {
    logEl.textContent = "failed to start: " + e.message;
    statusEl.className = "verdict failed";
    statusEl.textContent = "error";
    buttons.forEach(b => b.disabled = false);
  }
}

async function pollJob() {
  if (!activeJobId) return;
  clearTimeout(jobPollTimer);
  try {
    const j = await api("/api/jobs/" + activeJobId);
    const logEl = document.getElementById("job-log");
    logEl.textContent = (j.log || []).join("\n");
    logEl.scrollTop = logEl.scrollHeight;

    const statusEl = document.getElementById("job-status");
    statusEl.className = "verdict " + j.status;
    statusEl.textContent = j.status + (j.returncode !== null ? ` (rc=${j.returncode})` : "");

    if (j.status === "done" || j.status === "failed") {
      document.querySelectorAll(".actions button").forEach(b => b.disabled = false);
      activeJobId = null;
      // Re-fetch everything — a job likely changed state.
      loadAll();
      return;
    }
  } catch (e) {
    console.error("poll:", e);
  }
  jobPollTimer = setTimeout(pollJob, 750);
}

// ---------- Load & refresh cycle ----------
async function loadAll() {
  try {
    const [snapshot, strip, queue, fns] = await Promise.all([
      api("/api/state"),
      api("/api/address_strip"),
      api("/api/queue"),
      api("/api/functions?limit=50"),
    ]);

    // db status chip
    const chip = document.getElementById("db-status");
    chip.textContent = snapshot.db_exists
      ? `state.db · ${fmt(snapshot.total_functions)} fns`
      : "state.db · not yet created";
    chip.className = "status-chip " + (snapshot.db_exists ? "ok" : "warn");

    renderGate4(snapshot.gate4);
    renderBigNumbers(snapshot);
    populateFilters(snapshot);

    const pieData = Object.entries(snapshot.state_counts)
      .map(([k, v]) => ({ label: k, value: v, color: STATE_COLORS[k] || "var(--fg-dim)" }));
    renderPie("chart-states", pieData);

    const legend = document.getElementById("legend-states");
    legend.innerHTML = pieData.map(d =>
      `<span><span class="sw" style="background:${d.color}"></span>${d.label} · ${fmt(d.value)}</span>`
    ).join("");

    const barData = Object.entries(snapshot.tag_counts)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ label: k, value: v, color: TAG_COLORS[k] || "var(--accent)" }));
    renderBars("chart-tags", barData);

    renderStrip(strip);
    renderQueue(queue);
    renderFunctions(fns);

    document.getElementById("last-refresh").textContent =
      "refreshed " + new Date().toLocaleTimeString();
  } catch (e) {
    console.error("loadAll:", e);
  }
}

async function refreshFns() {
  const s = document.getElementById("filter-state").value;
  const t = document.getElementById("filter-tag").value;
  const qs = new URLSearchParams({ limit: "100" });
  if (s) qs.set("state", s);
  if (t) qs.set("tag", t);
  const fns = await api("/api/functions?" + qs.toString());
  renderFunctions(fns);
}

// ---------- Explainer toggle ----------
const EXPLAINERS_KEY = "fsa_pa_explainers";

function applyExplainerState(on) {
  document.body.dataset.explainers = on ? "on" : "off";
}

function initExplainerToggle() {
  const saved = localStorage.getItem(EXPLAINERS_KEY);
  applyExplainerState(saved !== "off");
  const btn = document.getElementById("toggle-explainers");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const nowOn = document.body.dataset.explainers !== "on";
    applyExplainerState(nowOn);
    localStorage.setItem(EXPLAINERS_KEY, nowOn ? "on" : "off");
  });
}

// ---------- Boot ----------
document.addEventListener("DOMContentLoaded", () => {
  initExplainerToggle();

  document.querySelectorAll(".actions button").forEach(btn => {
    btn.addEventListener("click", () => runAction(btn.dataset.action));
  });
  document.getElementById("refresh-fns").addEventListener("click", refreshFns);
  document.getElementById("filter-state").addEventListener("change", refreshFns);
  document.getElementById("filter-tag").addEventListener("change", refreshFns);

  loadAll();
  // Lightweight poll every 5s for live feel; jobs poll faster on their own.
  setInterval(loadAll, 5000);
});
