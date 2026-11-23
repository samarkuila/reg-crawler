// Sources page (docs/ui-mockups/04-sources-admin.html). FR-01, FR-02.
// Presentation only: loads /api/v1/sources, renders, and posts what the person does. Who may
// edit, who may approve and what a valid source looks like are decided by the services.

import { get, post } from "../api.js";
import { askText, closeDrawer, diffGridHtml, escapeHtml, formatDate, formatDateTime, openDrawer, toast } from "../ui.js";

const $ = (id) => document.getElementById(id);
const state = { views: [] };

const TYPES = [
  ["RSS", "RSS feed + HTML listing"],
  ["HTML_LIST", "HTML listing"],
  ["STRUCTURED_DATA", "Official data file"],
  ["BROWSER", "Browser-rendered page"],
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
function pendingCard(v, index) {
  const p = v.pending_version;
  const s = v.source;
  return (
    `<section class="card pad-card stack gap-3 card-pending" aria-label="Pending approval" data-source="${escapeHtml(s.id)}">` +
    '<div class="row row-between gap-2"><div class="row gap-2">' +
    `${badge("mod", "Pending approval")}<h2 class="section-title-sm">${escapeHtml(s.authority)} · ${escapeHtml(s.page_name)} — version ${p.version}</h2></div>` +
    `<span class="muted text-13">Requested by ${escapeHtml(p.requested_by)} · ${escapeHtml(formatDateTime(p.ts))}</span></div>` +
    diffGridHtml(v.pending_changes, `Before${s.current_version ? ` (v${s.current_version})` : ""}`, `After (v${p.version})`) +
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
  $("so-pending").innerHTML = pending.map((v) => pendingCard(v, state.views.indexOf(v))).join("");
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
        : `<a href="#" data-edit="${i}">Edit</a> · <a href="#" data-test="${escapeHtml(s.id)}">Test</a>`;
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
    `<div class="field"><label for="f-datefmt">Date format</label><input id="f-datefmt" name="date_format" value="${escapeHtml(cfg.date_format || "")}" placeholder="%d.%m.%Y" autocomplete="off"></div></div></fieldset>` +
    `<div class="field"><label for="f-inc">Include keywords (one per line)</label><textarea id="f-inc" name="include_keywords" rows="3">${escapeHtml(lines(s.include_keywords))}</textarea></div>` +
    `<div class="field"><label for="f-exc">Exclude keywords (one per line)</label><textarea id="f-exc" name="exclude_keywords" rows="3">${escapeHtml(lines(s.exclude_keywords))}</textarea></div>` +
    `<div class="field"><label for="f-pri">Priority</label><select id="f-pri" name="priority">${PRIORITIES.map(([v, l]) => opt(v, l, s.priority)).join("")}</select></div>` +
    `<label class="check"><input type="checkbox" name="requires_js"${s.requires_js ? " checked" : ""}> Needs a browser to render the page</label>` +
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
    requires_js: fd.get("requires_js") === "on",
  };
}

function openForm(v) {
  const isNew = !v;
  const base = v
    ? v.source
    : { authority: "", page_name: "", url: "", rss_url: "", type: "HTML_LIST", adapter_config: {}, include_keywords: [], exclude_keywords: [], priority: "NORMAL", requires_js: false, active: false };
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
    `<p class="text-14">${escapeHtml(out.message)}</p>${rows}` +
    '<p class="muted text-13">Check the rows look right before approving. If the site shows a verification page, the test stops and reports “Blocked”.</p>';
  root.scrollIntoView({ block: "nearest" });
}

// ---------- Wiring ----------
document.addEventListener("click", (e) => {
  const t = e.target instanceof Element ? e.target : null;
  if (!t) return;
  if (t.closest("#so-add")) return void openForm(null);
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
  const approve = t.closest("[data-approve]");
  if (approve) return void decide(Number(approve.dataset.approve), "approve");
  const reject = t.closest("[data-reject]");
  if (reject) return void decide(Number(reject.dataset.reject), "reject");
});

load().catch(() => {
  /* api() already showed the error */
});
