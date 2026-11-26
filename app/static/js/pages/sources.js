// Sources page (docs/ui-mockups/04-sources-admin.html). FR-01, FR-02.
// Presentation only: loads /api/v1/sources, renders, and posts what the person does. Who may
// edit, who may approve and what a valid source looks like are decided by the services.

import { del, get, post } from "../api.js";
import { askText, closeDrawer, confirmModal, diffGridHtml, escapeHtml, formatDate, formatDateTime, openDrawer, toast } from "../ui.js";

const $ = (id) => document.getElementById(id);
const state = { views: [], selected: new Set(), bulk: null };
const keyOf = (v) => `${v.source.id}|${v.pending_version.version}`;

const TYPES = [
  ["RSS", "RSS feed + HTML listing"],
  ["HTML_LIST", "HTML listing"],
  ["STRUCTURED_DATA", "Official data file"],
];
const ENGINES = [
  ["http", "Standard"],
  ["browser", "Browser"],
];
const PRIORITIES = [
  ["NORMAL", "Normal"],
  ["URGENT", "Urgent"],
];
const SELECTORS = [
  ["row", "Row selector"],
  ["title", "Title selector"],
  ["link", "Link selector"],
  ["date", "Date selector"],
  ["header_row", "Heading rows (dates and section names)"],
  ["ref_no_regex", "Reference no. pattern (regex)"],
];

const badge = (tone, label) => `<span class="b b-${tone}">${escapeHtml(label)}</span>`;
const view = (id) => state.views.find((v) => v.source.id === id);

// ---------- Loading ----------
async function load() {
  state.views = await get("/sources");
  renderPending();
  renderRegister();
}

// ---------- Pending approval ----------
function pendingCard(v, index, many) {
  const p = v.pending_version;
  const s = v.source;
  const diff = diffGridHtml(v.pending_changes, `Before${s.current_version ? ` (v${s.current_version})` : ""}`, `After (v${p.version})`);
  return (
    `<section class="card pad-card stack gap-3 card-pending" aria-label="Pending approval" data-source="${escapeHtml(s.id)}">` +
    '<div class="row row-between gap-2"><div class="row gap-2">' +
    (many
      ? `<input type="checkbox" data-select="${escapeHtml(keyOf(v))}" aria-label="Select ${escapeHtml(s.authority)} ${escapeHtml(s.page_name)}"${state.selected.has(keyOf(v)) ? " checked" : ""}>`
      : "") +
    `${badge("mod", "Pending approval")}<h2 class="section-title-sm">${escapeHtml(s.authority)} · ${escapeHtml(s.page_name)} — version ${p.version}</h2></div>` +
    `<span class="muted text-13">Requested by ${escapeHtml(p.requested_by)} · ${escapeHtml(formatDateTime(p.ts))}</span></div>` +
    (many ? `<details><summary>Show before and after</summary>${diff}</details>` : diff) +
    '<div class="row gap-2">' +
    `<button type="button" class="btn btn-g" data-approve="${index}"${v.can_decide ? "" : " disabled"}>Approve</button>` +
    `<button type="button" class="btn btn-d" data-reject="${index}"${v.can_decide ? "" : " disabled"}>Reject</button>` +
    `<button type="button" class="btn btn-o" data-test="${escapeHtml(s.id)}">Test this source</button></div>` +
    (v.decide_note ? `<div class="muted text-13" role="note">${escapeHtml(v.decide_note)}</div>` : "") +
    "</section>"
  );
}

function renderPending() {
  const pending = state.views.filter((v) => v.pending_version);
  const many = pending.length > 1;
  const live = new Set(pending.map(keyOf));
  for (const k of [...state.selected]) if (!live.has(k)) state.selected.delete(k);
  $("so-pending").innerHTML = pending.map((v) => pendingCard(v, state.views.indexOf(v), many)).join("");
  renderBulkBar(pending, many);
}

// ---------- Selected pending sources: approve, reject, test ----------
function renderBulkBar(pending, many) {
  const bar = $("so-bulkbar");
  bar.hidden = !many;
  if (!many) return;
  const uploaders = [...new Set(pending.map((v) => v.pending_version.requested_by))];
  const n = state.selected.size;
  bar.innerHTML =
    '<div class="row gap-2 wrap">' +
    uploaders.map((u) => `<button type="button" class="btn btn-o" data-select-by="${escapeHtml(u)}">Select all by ${escapeHtml(u)}</button>`).join("") +
    `<button type="button" class="btn btn-o" data-select-none${n ? "" : " disabled"}>Clear selection</button></div>` +
    '<div class="row gap-2 wrap">' +
    `<span class="muted text-13" aria-live="polite">${n} selected</span>` +
    `<button type="button" class="btn btn-g" data-bulk="approve"${n ? "" : " disabled"}>Approve selected</button>` +
    `<button type="button" class="btn btn-d" data-bulk="reject"${n ? "" : " disabled"}>Reject selected</button>` +
    `<button type="button" class="btn btn-o" data-bulk="test"${n ? "" : " disabled"}>Test selected</button></div>`;
}

function selectedItems() {
  return [...state.selected].map((k) => {
    const [source_id, version] = k.split("|");
    return { source_id, version: Number(version) };
  });
}

const sourceName = (id) => {
  const v = view(id);
  return v ? `${v.source.authority} · ${v.source.page_name}` : id;
};

async function bulkDecide(kind) {
  const items = selectedItems();
  if (!items.length) return;
  let body = { items };
  if (kind === "reject") {
    const reason = await askText({
      title: `Reject ${items.length} change${items.length === 1 ? "" : "s"}?`,
      intro: "The current versions stay in use. The reason is recorded in the audit log for each source.",
      label: "Reason for rejecting",
      confirmLabel: "Reject selected",
      danger: true,
    });
    if (!reason) return;
    body = { items, reason };
  }
  let results;
  try {
    results = await post(`/sources/bulk-decision/${kind}`, body);
  } catch {
    return;
  }
  const failed = results.filter((r) => !r.ok);
  const done = results.length - failed.length;
  toast(`${done} ${kind === "approve" ? "approved" : "rejected"}${failed.length ? `, ${failed.length} not changed` : ""}.`, { type: failed.length ? "err" : "ok" });
  const names = Object.fromEntries(failed.map((r) => [r.source_id, sourceName(r.source_id)]));
  state.selected = new Set(failed.map((r) => `${r.source_id}|${r.version}`));
  await load();
  if (failed.length) {
    const root = $("so-test");
    root.hidden = false;
    root.innerHTML =
      '<h2 class="section-title-sm">Not changed</h2><ul>' +
      failed.map((r) => `<li><strong>${escapeHtml(names[r.source_id])}</strong>: ${escapeHtml(r.message)}</li>`).join("") +
      "</ul>";
    root.scrollIntoView({ block: "nearest" });
  }
}

// One source at a time. The server keeps the pace (2 s apart, one request per website at a time,
// circuit breaker) and never works around a verification page: such a source shows "Blocked".
async function testSelected() {
  const items = selectedItems();
  if (!items.length) return;
  const root = $("so-test");
  root.hidden = false;
  const rows = [];
  const paint = (current) => {
    root.innerHTML =
      '<h2 class="section-title-sm">Test selected (dry run, nothing saved)</h2>' +
      `<p class="text-14" aria-live="polite">${current ? `Testing ${rows.length + 1} of ${items.length}: ${escapeHtml(sourceName(current))}…` : `Finished ${items.length} test${items.length === 1 ? "" : "s"}.`}</p>` +
      '<div class="table-wrap"><table><thead><tr><th scope="col">Source</th><th scope="col">Result</th><th scope="col">What happened</th></tr></thead><tbody>' +
      rows.map((r) => `<tr><td>${escapeHtml(sourceName(r.id))}</td><td>${r.badge}</td><td>${escapeHtml(r.message)}</td></tr>`).join("") +
      "</tbody></table></div>";
  };
  for (const item of items) {
    paint(item.source_id);
    root.scrollIntoView({ block: "nearest" });
    try {
      const out = await post(`/sources/${encodeURIComponent(item.source_id)}/test`, undefined, { query: { polite: true }, silent: true });
      const label = out.status === "OK" ? badge("ok", `OK · ${out.rows.length} rows`) : out.status === "BLOCKED" ? badge("urg", "Blocked") : badge("urg", "Problem");
      rows.push({ id: item.source_id, badge: label, message: `${out.fetch_engine === "browser" ? "Browser" : "Standard"} engine. ${out.message}` });
    } catch (e) {
      rows.push({ id: item.source_id, badge: badge("urg", "Problem"), message: e.message });
    }
  }
  paint(null);
}

// ---------- Bulk add ----------
function openBulk() {
  state.bulk = null;
  const root = $("so-bulk");
  root.hidden = false;
  root.innerHTML =
    '<div class="row row-between gap-2"><h2 class="section-title-sm">Bulk add sources</h2>' +
    '<button type="button" class="btn btn-o" data-bulk-close>Close</button></div>' +
    '<p class="text-14">Upload a CSV or Excel (.xlsx) file with a header row, or paste rows. Up to 50 rows. Nothing is saved until you choose “Save valid rows for approval”, and a different person must approve each source.</p>' +
    '<form class="form" id="so-bulk-form" novalidate><div class="form-grid-2">' +
    '<div class="field"><label for="bulk-file">File (.csv or .xlsx)</label><input id="bulk-file" type="file" accept=".csv,.xlsx"></div>' +
    '<div class="field"><label for="bulk-text">Or paste rows (with the header row)</label><textarea id="bulk-text" rows="4" spellcheck="false"></textarea></div></div>' +
    '<div class="row gap-2"><button type="submit" class="btn btn-p">Check rows</button>' +
    '<a href="/api/v1/sources/bulk-import/template.csv" download="sources-template.csv">Download template</a></div></form>' +
    '<div id="so-bulk-preview" class="stack gap-3"></div>';
  root.scrollIntoView({ block: "nearest" });
}

const STATUS_BADGE = { OK: badge("ok", "OK"), WARNING: badge("mod", "Warning"), ERROR: badge("urg", "Error") };

function renderPreview(p) {
  state.bulk = p;
  const savable = p.ok + p.warnings;
  $("so-bulk-preview").innerHTML =
    `<p class="text-14" aria-live="polite"><strong>${p.rows.length} rows</strong> in ${escapeHtml(p.file_name)}: ${p.ok} OK, ${p.warnings} with a warning, ${p.errors} with an error.</p>` +
    '<div class="table-wrap"><table><thead><tr><th scope="col">Row</th><th scope="col">Authority · page</th><th scope="col">URL</th><th scope="col">Status</th><th scope="col">Reason</th></tr></thead><tbody>' +
    p.rows
      .map(
        (r) =>
          `<tr><td>${r.row}</td><td>${escapeHtml(r.authority)} · ${escapeHtml(r.page_name)}</td><td><code>${escapeHtml(r.url)}</code></td><td>${STATUS_BADGE[r.status]}</td>` +
          `<td>${r.reasons.length ? `<ul>${r.reasons.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul>` : "—"}</td></tr>`,
      )
      .join("") +
    "</tbody></table></div>" +
    '<div class="row gap-2 wrap">' +
    `<button type="button" class="btn btn-g" data-bulk-save${savable ? "" : " disabled"}>Save ${savable} valid row${savable === 1 ? "" : "s"} for approval</button>` +
    (p.errors ? '<button type="button" class="btn btn-o" data-bulk-errors>Download errors.csv</button>' : "") +
    "</div>";
}

async function previewBulk(form) {
  const fd = new FormData();
  const file = form.querySelector("#bulk-file").files[0];
  if (file) fd.append("file", file);
  else fd.append("text", form.querySelector("#bulk-text").value);
  try {
    renderPreview(await post("/sources/bulk-import/preview", fd));
  } catch {
    $("so-bulk-preview").innerHTML = "";
  }
}

async function saveBulk() {
  const p = state.bulk;
  if (!p) return;
  try {
    const out = await post("/sources/bulk-import/save", { file_name: p.file_name, rows: p.rows.map((r) => r.raw) });
    toast(`${out.saved} source${out.saved === 1 ? "" : "s"} saved as version 1. Waiting for approval${out.skipped ? `; ${out.skipped} skipped` : ""}.`, { type: "ok" });
    $("so-bulk").hidden = true;
    state.bulk = null;
    await load();
  } catch {
    /* api() already showed the reason */
  }
}

async function downloadErrors() {
  const p = state.bulk;
  if (!p) return;
  const bad = p.rows.filter((r) => r.status === "ERROR").map((r) => r.raw);
  const response = await fetch("/api/v1/sources/bulk-import/errors.csv", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows: bad }),
  });
  if (!response.ok) return void toast("Could not make errors.csv.", { type: "err" });
  const url = URL.createObjectURL(await response.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: "errors.csv" });
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------- Register ----------
function renderRegister() {
  const active = state.views.filter((v) => v.source.active).length;
  const planned = state.views.filter((v) => v.source.planned).length;
  $("so-count").textContent = `${active} active in POC · ${planned} planned`;
  $("so-rows").innerHTML = state.views
    .map((v, i) => {
      const s = v.source;
      const urgent = s.priority === "URGENT";
      const status = s.active ? badge("ok", "Active") : s.planned ? badge("grey", v.status_label) : badge("mod", v.status_label);
      const version = s.current_version
        ? `v${s.current_version}${v.pending_version ? ` <span class="muted">(v${v.pending_version.version} pending)</span>` : ""}`
        : v.pending_version
          ? `<span class="muted">v${v.pending_version.version} pending</span>`
          : "—";
      const actions = s.planned
        ? `<a href="#" data-edit="${i}">Set up</a>`
        : `<a href="#" data-edit="${i}">Edit</a> · <a href="#" data-test="${escapeHtml(s.id)}">Test</a> · <a href="#" class="link-danger" data-delete="${i}">Delete</a>`;
      return (
        `<tr><td class="fw-500">${escapeHtml(s.authority)} · ${escapeHtml(s.page_name)}</td>` +
        `<td>${escapeHtml(s.planned && !s.url ? "To be confirmed by probe" : v.how_we_read_it)}</td>` +
        `<td>${urgent ? badge("urg", "Urgent") : "Normal"}</td><td>${version}</td><td>${status}</td><td>${actions}</td></tr>`
      );
    })
    .join("");
}

// ---------- Add / edit form ----------
const lines = (list) => (list || []).join("\n");
const splitLines = (text) =>
  text
    .split(/\r?\n/)
    .map((t) => t.trim())
    .filter(Boolean);
const opt = (value, label, current) => `<option value="${value}"${value === current ? " selected" : ""}>${escapeHtml(label)}</option>`;

function formHtml(s, isNew) {
  const cfg = s.adapter_config || {};
  const sel = cfg.selectors || {};
  return (
    `<div class="field"><label for="f-auth">Authority</label><input id="f-auth" name="authority" value="${escapeHtml(s.authority)}" required></div>` +
    `<div class="field"><label for="f-page">Page name</label><input id="f-page" name="page_name" value="${escapeHtml(s.page_name)}" required></div>` +
    `<div class="field"><label for="f-url">Page URL</label><input id="f-url" name="url" type="url" value="${escapeHtml(s.url)}" placeholder="https://" required></div>` +
    `<div class="field"><label for="f-rss">RSS URL (optional)</label><input id="f-rss" name="rss_url" type="url" value="${escapeHtml(s.rss_url || "")}" placeholder="https://"></div>` +
    `<div class="field"><label for="f-type">How we read it</label><select id="f-type" name="type">${TYPES.map(([v, l]) => opt(v, l, s.type)).join("")}</select></div>` +
    '<fieldset><legend>Where to find the items on the page</legend><div class="form-grid-2">' +
    SELECTORS.map(
      ([key, label]) =>
        `<div class="field"><label for="f-sel-${key}">${label}</label><input id="f-sel-${key}" data-sel="${key}" value="${escapeHtml(sel[key] || "")}" autocomplete="off"></div>`,
    ).join("") +
    `<div class="field"><label for="f-datefmt">Date format</label><input id="f-datefmt" name="date_format" value="${escapeHtml(cfg.date_format || "")}" placeholder="%d.%m.%Y" autocomplete="off"></div></div>` +
    `<label class="check"><input type="checkbox" name="date_from_header_row"${cfg.date_from_header_row ? " checked" : ""}> Dates and section names are heading rows between the items</label></fieldset>` +
    `<div class="field"><label for="f-inc">Include keywords (one per line)</label><textarea id="f-inc" name="include_keywords" rows="3">${escapeHtml(lines(s.include_keywords))}</textarea></div>` +
    `<div class="field"><label for="f-exc">Exclude keywords (one per line)</label><textarea id="f-exc" name="exclude_keywords" rows="3">${escapeHtml(lines(s.exclude_keywords))}</textarea></div>` +
    `<div class="field"><label for="f-pri">Priority</label><select id="f-pri" name="priority">${PRIORITIES.map(([v, l]) => opt(v, l, s.priority)).join("")}</select></div>` +
    '<fieldset id="f-engine-box"><legend>How the page is fetched</legend>' +
    `<div class="field"><label for="f-engine">Fetch with</label><select id="f-engine" name="fetch_engine">${ENGINES.map(([v, l]) => opt(v, l, s.fetch_engine || "http")).join("")}</select>` +
    '<span class="muted text-13">Standard reads the page as the website sends it. Browser opens it in a browser first, for pages that fill their list with JavaScript. The selectors above work the same way for both. Never switched automatically.</span></div>' +
    '<div id="f-browser-opts"><div class="form-grid-2">' +
    `<div class="field"><label for="f-wait">Wait for this element</label><input id="f-wait" name="wait_for_selector" value="${escapeHtml(s.wait_for_selector || "")}" placeholder="Default: the row selector" autocomplete="off"></div>` +
    `<div class="field"><label for="f-timeout">Wait up to (seconds)</label><input id="f-timeout" name="wait_timeout_s" type="number" min="1" max="60" step="1" value="${escapeHtml(String(s.wait_timeout_s || 15))}"></div></div></div></fieldset>` +
    (isNew
      ? '<label class="check"><input type="checkbox" checked disabled> Active once approved</label>'
      : `<label class="check"><input type="checkbox" name="active"${s.active ? " checked" : ""}> Active</label>`) +
    '<p class="muted text-13">Saving creates a new version. It only takes effect when a different person approves it.</p>' +
    '<div class="drawer-actions"><button type="submit" class="btn btn-g btn-fill">Save for approval</button>' +
    '<button type="button" class="btn btn-o btn-fill" data-cancel>Cancel</button></div>'
  );
}

function readForm(form, base) {
  const fd = new FormData(form);
  const text = (k) => (fd.get(k) ?? "").toString().trim();
  const selectors = { ...((base.adapter_config || {}).selectors || {}) };
  for (const input of form.querySelectorAll("[data-sel]")) {
    const v = input.value.trim();
    if (v) selectors[input.dataset.sel] = v;
    else delete selectors[input.dataset.sel];
  }
  const adapter = { ...(base.adapter_config || {}) };
  if (Object.keys(selectors).length) adapter.selectors = selectors;
  else delete adapter.selectors;
  if (text("date_format")) adapter.date_format = text("date_format");
  else delete adapter.date_format;
  if (fd.get("date_from_header_row") === "on") adapter.date_from_header_row = true;
  else delete adapter.date_from_header_row;
  const browser = text("type") === "HTML_LIST" && text("fetch_engine") === "browser";
  return {
    authority: text("authority"),
    page_name: text("page_name"),
    url: text("url"),
    rss_url: text("rss_url") || null,
    type: text("type"),
    adapter_config: adapter,
    include_keywords: splitLines(text("include_keywords")),
    exclude_keywords: splitLines(text("exclude_keywords")),
    priority: text("priority"),
    // The server decides what is valid; the form only never sends "browser" for a page type that
    // cannot use it (the box is hidden then).
    fetch_engine: browser ? "browser" : "http",
    wait_for_selector: browser ? text("wait_for_selector") || null : null,
    wait_timeout_s: browser ? Number(text("wait_timeout_s")) || 15 : 15,
  };
}

function openForm(v) {
  const isNew = !v;
  const base = v
    ? v.source
    : { authority: "", page_name: "", url: "", rss_url: "", type: "HTML_LIST", adapter_config: {}, include_keywords: [], exclude_keywords: [], priority: "NORMAL", fetch_engine: "http", wait_for_selector: null, wait_timeout_s: 15, active: false };
  const form = document.createElement("form");
  form.className = "form";
  form.noValidate = true;
  form.innerHTML = formHtml(base, isNew);
  openDrawer({
    title: isNew ? "Add source" : `Edit ${base.authority} · ${base.page_name}`,
    subtitle: isNew ? "New source" : `Version ${base.current_version || "–"}`,
    content: form,
  });
  form.addEventListener("click", (e) => {
    if (e.target instanceof Element && e.target.closest("[data-cancel]")) closeDrawer();
  });
  // "Fetch with" only exists for an HTML listing; the wait fields only for the Browser engine.
  // (Hidden fields are ignored by readForm, so a changed dropdown never leaves a stale setting.)
  const syncEngineBox = () => {
    const isHtml = form.elements.type.value === "HTML_LIST";
    form.querySelector("#f-engine-box").hidden = !isHtml;
    form.querySelector("#f-browser-opts").hidden = form.elements.fetch_engine.value !== "browser";
  };
  form.elements.type.addEventListener("change", syncEngineBox);
  form.elements.fetch_engine.addEventListener("change", syncEngineBox);
  syncEngineBox();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const values = readForm(form, base);
    try {
      if (isNew) {
        const out = await post("/sources", values);
        toast(`${out.source.authority} · ${out.source.page_name} saved as version 1. Waiting for approval.`, { type: "ok" });
      } else {
        const out = await post(`/sources/${encodeURIComponent(base.id)}/versions`, { ...values, active: form.elements.active ? form.elements.active.checked : base.active });
        toast(`Saved as version ${out.version}. Waiting for approval by a different person.`, { type: "ok" });
      }
      closeDrawer();
      await load();
    } catch {
      /* api() already showed the reason */
    }
  });
}

// ---------- Approve, reject, test ----------
async function decide(index, kind) {
  const v = state.views[index];
  const p = v.pending_version;
  try {
    if (kind === "approve") {
      await post(`/sources/${encodeURIComponent(v.source.id)}/versions/${p.version}/approve`);
      toast(`Version ${p.version} of ${v.source.authority} approved.`, { type: "ok" });
    } else {
      const reason = await askText({
        title: "Reject this change?",
        intro: "The current version stays in use. The reason is recorded in the audit log.",
        label: "Reason for rejecting",
        confirmLabel: "Reject",
        danger: true,
      });
      if (!reason) return;
      await post(`/sources/${encodeURIComponent(v.source.id)}/versions/${p.version}/reject`, { reason });
      toast(`Version ${p.version} of ${v.source.authority} rejected.`, { type: "ok" });
    }
    await load();
  } catch {
    /* api() already showed the reason */
  }
}

async function testSource(id) {
  let out;
  try {
    out = await post(`/sources/${encodeURIComponent(id)}/test`);
  } catch {
    return;
  }
  const v = view(id);
  const ok = out.status === "OK";
  const rows = out.rows.length
    ? '<div class="table-wrap"><table><thead><tr><th scope="col">Title</th><th scope="col">Link</th><th scope="col">Date shown</th><th scope="col">Reference no.</th></tr></thead><tbody>' +
      out.rows
        .map(
          (r) =>
            `<tr><td>${escapeHtml(r.title)}</td><td><code>${escapeHtml(r.link)}</code></td><td>${escapeHtml(formatDate(r.displayed_date))}</td><td>${escapeHtml(r.ref_no || "–")}</td></tr>`,
        )
        .join("") +
      "</tbody></table></div>"
    : "";
  const root = $("so-test");
  root.hidden = false;
  root.innerHTML =
    '<div class="row row-between gap-2">' +
    `<h2 class="section-title-sm">Test result · ${escapeHtml(v ? `${v.source.authority} · ${v.source.page_name}` : id)} (dry run, nothing saved)</h2>` +
    `${badge(ok ? "ok" : "urg", ok ? `Parsed ${out.rows.length} rows in ${out.duration_seconds} s` : `${out.status === "BLOCKED" ? "Blocked" : "Problem"} after ${out.duration_seconds} s`)}</div>` +
    `<p class="text-14"><strong>Fetched with:</strong> ${out.fetch_engine === "browser" ? "Browser" : "Standard"}. ${escapeHtml(out.message)}</p>${rows}` +
    '<p class="muted text-13">Check the rows look right before approving. If the site shows a verification page, the test stops and reports “Blocked”.</p>';
  root.scrollIntoView({ block: "nearest" });
}

// ---------- Delete ----------
async function deleteSource(view) {
  const s = view.source;
  const ok = await confirmModal({
    title: `Delete ${s.authority} · ${s.page_name}?`,
    body: "This proposes removing the source. A different person must approve it. It then stops being crawled and leaves this list, but its past runs, items and snapshots are kept.",
    confirmLabel: "Propose delete",
    danger: true,
  });
  if (!ok) return;
  try {
    await del(`/sources/${encodeURIComponent(s.id)}`);
  } catch {
    return; // api() already showed the error
  }
  toast("Delete proposed. It waits for a different person to approve it.", { type: "ok" });
  await load();
}

// ---------- Wiring ----------
document.addEventListener("click", (e) => {
  const t = e.target instanceof Element ? e.target : null;
  if (!t) return;
  if (t.closest("#so-add")) return void openForm(null);
  if (t.closest("#so-bulk-open")) return void openBulk();
  if (t.closest("[data-bulk-close]")) return void ($("so-bulk").hidden = true);
  if (t.closest("[data-bulk-save]")) return void saveBulk();
  if (t.closest("[data-bulk-errors]")) return void downloadErrors();
  const by = t.closest("[data-select-by]");
  if (by) {
    for (const v of state.views) if (v.pending_version && v.pending_version.requested_by === by.dataset.selectBy) state.selected.add(keyOf(v));
    return void renderPending();
  }
  if (t.closest("[data-select-none]")) {
    state.selected.clear();
    return void renderPending();
  }
  const bulk = t.closest("[data-bulk]");
  if (bulk) return void (bulk.dataset.bulk === "test" ? testSelected() : bulkDecide(bulk.dataset.bulk));
  const edit = t.closest("[data-edit]");
  if (edit) {
    e.preventDefault();
    return void openForm(state.views[Number(edit.dataset.edit)]);
  }
  const test = t.closest("[data-test]");
  if (test) {
    e.preventDefault();
    return void testSource(test.dataset.test);
  }
  const remove = t.closest("[data-delete]");
  if (remove) {
    e.preventDefault();
    return void deleteSource(state.views[Number(remove.dataset.delete)]);
  }
  const approve = t.closest("[data-approve]");
  if (approve) return void decide(Number(approve.dataset.approve), "approve");
  const reject = t.closest("[data-reject]");
  if (reject) return void decide(Number(reject.dataset.reject), "reject");
});

document.addEventListener("change", (e) => {
  const box = e.target instanceof Element ? e.target.closest("[data-select]") : null;
  if (!box) return;
  if (box.checked) state.selected.add(box.dataset.select);
  else state.selected.delete(box.dataset.select);
  renderBulkBar(state.views.filter((v) => v.pending_version), true);
});

document.addEventListener("submit", (e) => {
  if (e.target instanceof Element && e.target.id === "so-bulk-form") {
    e.preventDefault();
    previewBulk(e.target);
  }
});

load().catch(() => {
  /* api() already showed the error */
});
