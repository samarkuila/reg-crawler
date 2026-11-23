// Source health page (docs/ui-mockups/03-source-health.html). FR-26.
// Presentation only: loads from /api/v1 and renders. What counts as a mismatch, who may mark a
// source as checked and what each status means are decided by the services.

import { get, post } from "../api.js";
import { askText, escapeHtml, formatDate, openModal, toast } from "../ui.js";

const POLL_MS = 3000;
const $ = (id) => document.getElementById(id);
const state = { grid: null, run: null, timer: null };

const RUN_STATUS = { RUNNING: "in progress", COMPLETED: "completed", PARTIAL: "completed with problems", FAILED: "failed" };
const hhmm = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime())
    ? `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
    : "";
};
const badge = (tone, label) => `<span class="b b-${tone}">${escapeHtml(label)}</span>`;

// ---------- Loading ----------
async function loadGrid() {
  state.grid = await get("/source-health/grid");
  renderGrid();
  await loadRun();
}

async function loadRun() {
  const current = state.grid && state.grid.current_run;
  if (!current) {
    state.run = null;
    renderRun();
    return;
  }
  state.run = await get(`/runs/${encodeURIComponent(current.id)}`);
  renderRun();
  stopPolling();
  if (state.run.run.status === "RUNNING") {
    state.timer = setTimeout(async () => {
      try {
        await loadRun();
        if (state.run && state.run.run.status !== "RUNNING") await loadGrid();
      } catch {
        /* api() already showed the error; try again on the next tick */
        state.timer = setTimeout(loadRun, POLL_MS);
      }
    }, POLL_MS);
  }
}

function stopPolling() {
  if (state.timer) clearTimeout(state.timer);
  state.timer = null;
}

// ---------- Current run ----------
function renderRun() {
  const root = $("sh-run");
  if (!state.run) {
    root.innerHTML =
      '<div class="section-title-sm">No crawl has run yet</div><div class="muted text-14">Runs appear here once the first crawl starts.</div>';
    return;
  }
  const { run, checks, sources_total: total, sources_done: done } = state.run;
  const running = run.status === "RUNNING";
  const pct = total ? Math.round((done / total) * 100) : 0;
  const title = running
    ? `Run in progress · started ${hhmm(run.started_at)} by ${run.triggered_by}`
    : `Last run ${RUN_STATUS[run.status] ?? run.status.toLowerCase()} · ${formatDate(run.cycle_date)} ${hhmm(run.started_at)}–${hhmm(run.ended_at)} · ${run.triggered_by}`;
  const names = [...new Set(state.grid.rows.map((r) => r.authority))];
  let crawling = running;
  const chips = names
    .map((name) => {
      const mine = checks.filter((c) => c.authority === name);
      if (mine.length) {
        const problem = mine.some((c) => c.status !== "OK");
        const found = mine.reduce((n, c) => n + (c.items_found ?? 0), 0);
        return badge(problem ? "mod" : "ok", `${name} · done${problem ? " · needs attention" : ` · ${found} ${found === 1 ? "item" : "items"}`}`);
      }
      if (crawling) {
        crawling = false;
        return badge("new", `${name} · crawling…`);
      }
      return badge("grey", `${name} · waiting`);
    })
    .join("");
  root.innerHTML =
    '<div class="row row-between gap-2">' +
    `<h2 class="section-title-sm">${escapeHtml(title)}</h2>` +
    `<span class="muted text-14" aria-live="polite">${done} of ${total} sources done</span></div>` +
    `<div class="progress" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Run progress"><span style="width:${pct}%"></span></div>` +
    `<div class="row gap-2 text-13">${chips}</div>`;
}

// ---------- Grid ----------
function renderGrid() {
  const g = state.grid;
  $("sh-head").innerHTML =
    '<tr><th scope="col">Source</th><th scope="col">Channel</th>' +
    g.cycle_labels.map((l) => `<th scope="col">${escapeHtml(l)}</th>`).join("") +
    '<th scope="col">Items (latest)</th><th scope="col">Action</th></tr>';
  if (!g.rows.length) {
    $("sh-rows").innerHTML = `<tr><td colspan="${g.cycles.length + 4}"><div class="empty"><div class="empty-text">No checks have been recorded yet.</div></div></td></tr>`;
    return;
  }
  $("sh-rows").innerHTML = g.rows
    .map((r, i) => {
      const cells = r.cells.map((c) => `<td>${badge(c.tone, c.label)}</td>`).join("");
      const actions = r.actions
        .map((a) =>
          a.kind === "manual"
            ? `<button type="button" class="btn btn-d btn-sm" data-act="manual" data-row="${i}">${escapeHtml(a.label)}</button>`
            : `<a href="#" data-act="${a.kind}" data-row="${i}">${escapeHtml(a.label)}</a>`,
        )
        .join(" ");
      return (
        `<tr${r.needs_attention ? ' class="row-alert"' : ""}>` +
        `<td class="fw-500">${escapeHtml(r.authority)} · ${escapeHtml(r.page_name)}</td><td>${escapeHtml(r.channel)}</td>` +
        `${cells}<td>${escapeHtml(r.items_latest)}${missesLink(r)}</td><td><div class="row gap-2">${actions}</div></td></tr>`
      );
    })
    .join("");
}

// Luna's sweep may notice listing entries the parser missed. A count per source, with the list.
function missesLink(r) {
  if (!r.possible_misses) return "";
  return ` <a href="#" class="b b-urg" data-misses="${escapeHtml(r.source_id)}">${r.possible_misses} possible missed item${r.possible_misses === 1 ? "" : "s"}</a>`;
}

async function showMisses(sourceId) {
  const flags = (await get("/agent/flags", { source_id: sourceId })).filter((f) => f.type === "POSSIBLE_MISS");
  const body = flags.length
    ? '<ul class="plain-list text-14">' +
      flags
        .map(
          (f) =>
            `<li><strong>Possible missed item — check parser:</strong> ${escapeHtml(f.payload.title)}` +
            `${f.payload.date_text ? ` (${escapeHtml(f.payload.date_text)})` : ""}<br>` +
            `<span class="muted">\u201c${escapeHtml(f.payload.evidence_quote)}\u201d — ${escapeHtml(f.payload.why_regulatory)}</span><br>` +
            `<button type="button" class="btn btn-o" data-flag="${escapeHtml(f.id)}" data-status="RESOLVED">Resolved</button> ` +
            `<button type="button" class="btn btn-o" data-flag="${escapeHtml(f.id)}" data-status="FALSE_ALARM">False alarm</button></li>`,
        )
        .join("") +
      "</ul>"
    : '<p class="text-14">No open flags.</p>';
  await showModal(`Possible missed items · ${flags[0] ? flags[0].authority : sourceId}`, body);
}

async function resolveFlag(button) {
  await post(`/agent/flags/${encodeURIComponent(button.dataset.flag)}/resolve`, { status: button.dataset.status });
  toast("Recorded.", { type: "ok" });
  button.closest("li").remove();
  await loadGrid();
}

// ---------- Actions ----------
function showModal(title, body) {
  const el = document.createElement("div");
  el.className = "stack gap-2";
  el.innerHTML = body;
  return openModal({ title, body: el });
}

async function compare(row) {
  const out = await get("/source-health/compare", { source_id: row.source_id, cycle: state.grid.cycles.at(-1).id });
  const items = out.items.length
    ? '<div class="table-wrap"><table><thead><tr><th scope="col">Item</th><th scope="col">Date shown</th><th scope="col">Seen in</th></tr></thead><tbody>' +
      out.items
        .map(
          (i) =>
            `<tr><td>${escapeHtml(i.title)}${i.ref_no ? `<div class="muted text-13">${escapeHtml(i.ref_no)}</div>` : ""}</td>` +
            `<td>${escapeHtml(formatDate(i.displayed_date))}</td><td>${badge(i.seen_in === "Both" ? "ok" : "mod", i.seen_in)}</td></tr>`,
        )
        .join("") +
      "</tbody></table></div>"
    : '<div class="muted text-14">No items for this source in the latest cycle.</div>';
  await showModal(
    `Compare RSS vs HTML · ${out.authority} ${out.page_name}`,
    `<p class="text-14">${escapeHtml(out.message)}</p>` +
      `<div class="row gap-2 text-13">${badge("ok", `${out.both} on both`)}${badge("mod", `${out.rss_only} RSS only`)}${badge("mod", `${out.html_only} HTML only`)}</div>` +
      items,
  );
}

async function viewLog(row, action) {
  const out = await get(`/source-health/checks/${encodeURIComponent(action.check_id)}/log`);
  await showModal(
    `Log · ${out.check.authority} ${out.check.page_name} · ${out.cycle_label}`,
    `<ul class="plain-list text-14">${out.lines.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>`,
  );
}

async function markChecked(action) {
  const note = await askText({
    title: "Mark as checked manually",
    intro: "Open the source in your own browser and look for new items. Then record what you did.",
    label: "Note (what you checked)",
    confirmLabel: "Mark checked",
  });
  if (!note) return;
  await post(`/source-checks/${encodeURIComponent(action.check_id)}/manual-check`, { note });
  toast("Recorded: source checked manually.", { type: "ok" });
  await loadGrid();
}

async function startRun(sourceId) {
  try {
    await post("/crawl/run", sourceId ? { source_id: sourceId } : {});
    toast("Run started.", { type: "ok" });
    await loadGrid();
  } catch {
    /* api() already showed the reason, e.g. "not built yet" */
  }
}

async function runOne() {
  const sources = [...new Map(state.grid.rows.map((r) => [r.source_id, `${r.authority} · ${r.page_name}`])).entries()];
  const body = document.createElement("div");
  body.className = "field";
  body.innerHTML =
    '<label for="sh-pick">Source</label><select id="sh-pick">' +
    sources.map(([id, name]) => `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`).join("") +
    "</select>";
  const go = await openModal({
    title: "Run one source",
    body,
    buttons: [
      { label: "Cancel", value: false, kind: "o" },
      { label: "Run", value: true, kind: "g" },
    ],
  });
  if (go === true) await startRun(body.querySelector("select").value);
}

// ---------- Wiring ----------
document.addEventListener("click", async (e) => {
  const t = e.target instanceof Element ? e.target : null;
  if (!t) return;
  const misses = t.closest("[data-misses]");
  if (misses) {
    e.preventDefault();
    return void showMisses(misses.dataset.misses).catch(() => {});
  }
  const flag = t.closest("[data-flag]");
  if (flag) return void resolveFlag(flag).catch(() => {});
  if (t.closest("#sh-run-all")) return void startRun(null);
  if (t.closest("#sh-run-one")) return void runOne();
  const act = t.closest("[data-act]");
  if (!act || !state.grid) return;
  e.preventDefault();
  const row = state.grid.rows[Number(act.dataset.row)];
  const action = row.actions.find((a) => a.kind === act.dataset.act);
  if (!action) return;
  try {
    if (action.kind === "snapshot") window.open(`/api/v1/snapshots/${encodeURIComponent(action.snapshot_id)}/content`, "_blank", "noopener");
    else if (action.kind === "compare") await compare(row);
    else if (action.kind === "log") await viewLog(row, action);
    else if (action.kind === "manual") await markChecked(action);
  } catch {
    /* api() already showed the error */
  }
});

// The header "Run now" button is handled in runnow.js (every page); here we only refresh.
window.addEventListener("run-started", () => {
  loadGrid().catch(() => {
    /* api() already showed the error */
  });
});

loadGrid().catch(() => {
  /* api() already showed the error */
});
