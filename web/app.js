// The Hiring Leaderboard — static client. Loads exported JSON and filters it
// entirely in the browser: pick a window (days), a metric, and a function, then
// sum new_roles per company and rank.

const state = { days: 90, metric: "new", func: "All", size: "All", anchor: null };

// Company-size buckets by headcount.
function sizeBucket(headcount) {
  if (!headcount) return "Unknown";
  if (headcount < 500) return "Startup";
  if (headcount < 5000) return "Scaleup";
  return "Enterprise";
}
let DAILY = [];            // [{d, c, f, n}]
const COMPANY = new Map(); // company -> {headcount, hq, notes, ats}
const TOP_N = 15;

const $ = (id) => document.getElementById(id);
const fmt = (n) => n.toLocaleString("en-US");

async function boot() {
  const [meta, companies, daily] = await Promise.all([
    fetch("data/meta.json").then((r) => r.json()),
    fetch("data/companies.json").then((r) => r.json()),
    fetch("data/new_roles_daily.json").then((r) => r.json()),
  ]);
  DAILY = daily;
  companies.forEach((c) => COMPANY.set(c.company, c));
  state.anchor = meta.date_max; // treat the latest data date as "now"

  $("co-count").textContent = meta.total_companies;
  $("updated").textContent = "Updated " + (meta.last_snapshot || meta.date_max);

  buildFunctions(meta.functions);
  wireControls();
  wireModal();
  initTheme();
  render();
}

function buildFunctions(functions) {
  const order = ["Engineering", "Product", "Data", "Design", "Sales",
    "Marketing", "Operations", "Finance", "People", "Support", "Legal", "Other"];
  const present = order.filter((f) => functions.includes(f));
  const nav = $("functions");
  ["All", ...present].forEach((f) => {
    const b = document.createElement("button");
    b.textContent = f;
    b.dataset.func = f;
    if (f === "All") b.classList.add("active");
    b.onclick = () => {
      state.func = f;
      [...nav.children].forEach((x) => x.classList.toggle("active", x === b));
      render();
    };
    nav.appendChild(b);
  });
}

function wireControls() {
  const slider = $("days");
  slider.oninput = () => {
    state.days = +slider.value;
    syncPresets();
    render();
  };
  $("presets").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      state.days = +b.dataset.days;
      slider.value = state.days;
      syncPresets();
      render();
    };
  });
  $("metric").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      state.metric = b.dataset.metric;
      $("metric").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
      render();
    };
  });
  $("size").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      state.size = b.dataset.size;
      $("size").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
      render();
    };
  });
}

function syncPresets() {
  $("presets").querySelectorAll("button").forEach((b) =>
    b.classList.toggle("active", +b.dataset.days === state.days));
}

function windowLabel() {
  const d = state.days;
  if (d % 365 === 0 && d >= 365) return `last ${d / 365} yr`;
  if (d >= 60 && d % 30 === 0) return `last ${d / 30} mo`;
  return `last ${d} days`;
}

function cutoffDate() {
  const anchor = new Date(state.anchor + "T00:00:00Z");
  anchor.setUTCDate(anchor.getUTCDate() - state.days);
  return anchor.toISOString().slice(0, 10);
}

function aggregate() {
  const cutoff = cutoffDate();
  const wantFn = state.func;
  const totals = new Map();
  for (const row of DAILY) {
    if (row.d < cutoff) continue;
    if (wantFn !== "All" && row.f !== wantFn) continue;
    totals.set(row.c, (totals.get(row.c) || 0) + row.n);
  }
  let items = [...totals.entries()].map(([company, roles]) => {
    const meta = COMPANY.get(company) || {};
    const pct = meta.headcount ? (roles * 100) / meta.headcount : null;
    return { company, roles, pct, meta };
  });
  // Company-size filter.
  if (state.size !== "All") {
    items = items.filter((i) => sizeBucket(i.meta.headcount) === state.size);
  }
  if (state.metric === "pct") {
    items = items.filter((i) => i.pct !== null);
    items.sort((a, b) => b.pct - a.pct);
  } else {
    items.sort((a, b) => b.roles - a.roles);
  }
  return items;
}

function render() {
  $("window-label").textContent = windowLabel();
  const items = aggregate();
  const top = items.slice(0, TOP_N);

  // stats
  const windowTotal = items.reduce((s, i) => s + i.roles, 0);
  $("stats").innerHTML = [
    [fmt(windowTotal), "New roles in window"],
    [fmt(items.length), "Companies hiring"],
    [fmt(COMPANY.size), "Companies tracked"],
  ].map(([v, k]) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("");

  // board head
  $("board-title").textContent = state.func === "All" ? "Overall leaders" : `${state.func} leaders`;
  $("board-sub").textContent =
    (state.metric === "pct" ? "new roles as % of headcount · " : "new roles · ") + windowLabel();

  const board = $("board");
  const empty = $("empty");
  if (!top.length) { board.innerHTML = ""; empty.hidden = false; return; }
  empty.hidden = true;

  const metricVal = (i) => state.metric === "pct" ? i.pct : i.roles;
  const max = metricVal(top[0]) || 1;

  board.innerHTML = top.map((i, idx) => {
    const v = metricVal(i);
    const pct = Math.max(2, (v / max) * 100);
    const valStr = state.metric === "pct"
      ? `${v >= 10 ? Math.round(v) : v.toFixed(1)}<span class="unit">%</span>`
      : `${fmt(i.roles)}<span class="unit">roles</span>`;
    const meta = i.meta.notes || i.meta.hq || "";
    return `<li class="row ${idx === 0 ? "lead" : ""}" data-company="${escapeHtml(i.company)}" tabindex="0" role="button">
      <span class="rank">${String(idx + 1).padStart(2, "0")}</span>
      <div class="co">
        <div class="co-name">${escapeHtml(i.company)}</div>
        <div class="co-meta">${escapeHtml(meta)}</div>
      </div>
      <div class="track"><div class="fill" style="width:${pct.toFixed(1)}%"></div></div>
      <div class="val num">${valStr}</div>
    </li>`;
  }).join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ---------- Company drill-down (role list + seniority) ----------
let ROLES = null;                // lazy-loaded [{c,f,s,t,d,u}]
const SEN_ORDER = ["Leadership", "Staff+", "Senior", "Junior", "Intern", "Unspecified"];

async function loadRoles() {
  if (!ROLES) ROLES = await fetch("data/roles.json").then((r) => r.json());
  return ROLES;
}

function wireModal() {
  const modal = $("modal");
  const close = () => { modal.hidden = true; };
  $("modal-close").onclick = close;
  $("modal-scrim").onclick = close;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  // Event delegation: any row click opens that company's detail.
  $("board").addEventListener("click", (e) => {
    const row = e.target.closest(".row"); if (row) openDetail(row.dataset.company);
  });
  $("board").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      const row = e.target.closest(".row");
      if (row) { e.preventDefault(); openDetail(row.dataset.company); }
    }
  });
}

async function openDetail(company) {
  const modal = $("modal");
  $("m-title").textContent = company;
  $("m-context").textContent = COMPANY.get(company)?.notes || "";
  $("m-sub").textContent = "Loading…";
  $("m-senior").innerHTML = "";
  $("m-roles").innerHTML = "";
  modal.hidden = false;
  modal.querySelector(".modal-panel").scrollTop = 0;

  await loadRoles();
  const cutoff = cutoffDate();
  const wantFn = state.func;
  let list = ROLES.filter((r) =>
    r.c === company && r.d >= cutoff && (wantFn === "All" || r.f === wantFn));

  const fnLabel = wantFn === "All" ? "all functions" : wantFn;
  $("m-sub").textContent = `${list.length} new ${wantFn === "All" ? "" : wantFn + " "}role${list.length === 1 ? "" : "s"} · ${fnLabel} · ${windowLabel()}`;

  // seniority summary
  const counts = {};
  list.forEach((r) => { counts[r.s] = (counts[r.s] || 0) + 1; });
  $("m-senior").innerHTML = SEN_ORDER.filter((l) => counts[l])
    .map((l) => `<span class="sen-tag"><b>${l}</b><span class="c">${counts[l]}</span></span>`).join("")
    || `<span class="sen-tag"><span class="c">No roles in this window</span></span>`;

  // role list: senior-most first, then most recent
  list.sort((a, b) => (SEN_ORDER.indexOf(a.s) - SEN_ORDER.indexOf(b.s)) || (a.d < b.d ? 1 : -1));
  $("m-roles").innerHTML = list.map((r) => {
    const lead = r.s === "Leadership" ? " lead-lvl" : "";
    const title = r.u
      ? `<a href="${escapeHtml(r.u)}" target="_blank" rel="noopener">${escapeHtml(r.t)}</a>`
      : `<span class="rt">${escapeHtml(r.t)}</span>`;
    return `<li class="m-role">
      <span class="lvl${lead}">${r.s}</span>
      ${title}
      <span class="rd">${r.d}</span>
    </li>`;
  }).join("");
}

// theme
function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("theme-toggle").onclick = () => {
    const cur = document.documentElement.dataset.theme;
    const isDark = cur === "dark" || (!cur && matchMedia("(prefers-color-scheme: dark)").matches);
    const next = isDark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("theme", next);
  };
}

boot().catch((e) => {
  document.getElementById("board").innerHTML =
    `<p class="empty">Couldn't load data (${e}). Run the pipeline + export first.</p>`;
});
