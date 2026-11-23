// Daily review page (docs/ui-mockups/01-daily-review.html). FR-15 … FR-21.
// Presentation only: loads from /api/v1, renders, and posts what the reviewer does.
// Every rule (who may decide, what is required, what counts as "edited") lives in the services.

import { ApiError, get, post } from "../api.js";
import { askText, escapeHtml, formatDate, formatDateTime, parseDisplayDate, toast } from "../ui.js";

// ---------- Labels (display text only) ----------
const CHANGE_BADGES = {
  URGENT: ["b-urg", "Urgent"],
  NEW: ["b-new", "New"],
  MODIFIED: ["b-mod", "Modified"],
  BACKDATED: ["b-back", "Backdated"],
  REAPPEARED: ["b-grey", "Reappeared"],
  RSS_ONLY: ["b-mod", "RSS only"],
  HTML_ONLY: ["b-mod", "HTML only"],
};
const STATUS_PILLS = [
  ["NOT_APPLICABLE", "Not applicable", "dot-na"],
  ["ACTION_OVER", "Action over", "dot-done"],
  ["IN_PROGRESS", "In progress", "dot-progress"],
  ["NOT_AS_PER_PLAN", "Not as per plan", "dot-late"],
  ["YET_TO_START", "Yet to start", "dot-todo"],
];
const TREATMENTS = [
  ["INFO_ONLY", "Information only"],
  ["OTHER_FUNCTION", "Other function (SME)"],
  ["FUTURE_POTENTIAL", "Future / potential"],
  ["IMPLEMENTATION_REQUIRED", "Implementation required"],
  ["NOT_RELEVANT", "Not relevant"],
];
const APPLICABILITY = [
  ["YES", "Yes"],
  ["NO", "No"],
  ["TBC", "TBC"],
];
const LOW_PREVIEW = 3;

// ---------- State ----------
const $ = (id) => document.getElementById(id);
const cycleSelect = document.querySelector('[data-role="cycle-select"]');
const state = {
  cycle: cycleSelect ? cycleSelect.value : "",
  data: null, // /items response
  health: null, // /source-checks response (null when the cycle has no run)
  sme: [],
  filters: { authority: "", type: "", q: "" },
  selectedId: null,
  detail: null,
  cursorId: null,
  checked: new Set(),
  showAllLow: false,
  busy: false,
};

// ---------- Small helpers ----------
const badge = ([cls, label]) => `<span class="b ${cls}">${escapeHtml(label)}</span>`;
const short = (sha) => (sha ? `${sha.slice(0, 4)}…${sha.slice(-4)}` : "");
const hhmm = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime())
    ? `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
    : "";
};
const items = () => (state.data ? state.data.items : []);
const attentionItems = () => items().filter((i) => i.section === "attention");
const byId = (id) => items().find((i) => i.id === id);

// ---------- Loading ----------
async function load() {
  const query = { cycle: state.cycle || undefined };
  const [data, health, sme] = await Promise.all([
    get("/items", query),
    get("/source-checks", query, { silent: true }).catch((e) => {
      if (!(e instanceof ApiError && e.status === 404)) toast(e.message, { type: "err" });
      return null; // 404 = no run for this cycle yet
    }),
    state.sme.length ? Promise.resolve(state.sme) : get("/sme-functions"),
  ]);
  state.data = data;
  state.health = health;
  state.sme = sme;
  state.checked = new Set([...state.checked].filter((id) => byId(id)));
  renderAll();
}

async function reload() {
  try {
    await load();
  } catch {
    /* api() already showed the error */
  }
}

// ---------- Rendering: header, health, counts ----------
function renderAll() {
  renderSub();
  renderHealth();
  renderCounts();
  renderAuthorityFilter();
  renderTable();
  renderManual();
  renderLow();
  if (state.selectedId && !byId(state.selectedId)) closePanel();
}

function renderSub() {
  const h = state.health;
  const label = h ? h.cycle_label : "No cycle yet";
  let text = `${label} cycle`;
  if (h && h.last_run_started_at) {
    const end = hhmm(h.last_run_ended_at);
    text += ` · Last run ${hhmm(h.last_run_started_at)}${end ? `–${end}` : ""}`;
  }
  $("rv-sub").textContent = text;
  $("rv-email-link").href = state.data && state.data.cycle ? `/email?cycle=${encodeURIComponent(state.data.cycle)}` : "/email";
}

function renderHealth() {
  const h = state.health;
  const root = $("rv-health");
  if (!h || !h.run_id) {
    root.innerHTML =
      '<div class="section-title-sm">No crawl has run for this cycle yet</div>' +
      '<div class="muted text-14">Items appear here after the first run.</div>';
    return;
  }
  const needs = h.attention.filter((c) => c.needs_acknowledgement);
  const done = h.attention.filter((c) => !c.needs_acknowledgement);
  const okTexts = [...new Set(h.checks.filter((c) => c.status === "OK").map((c) => `${c.authority} · OK`))];
  let html =
    '<div class="row row-between gap-3">' +
    `<div class="section-title-sm">${escapeHtml(h.headline)}</div>` +
    '<a href="/health" class="text-14">View source health</a></div>';
  for (const c of needs) {
    html +=
      '<div class="alert">' +
      `<span class="b b-urg">${escapeHtml(c.status_label)}</span>` +
      `<div class="alert-body"><strong>${escapeHtml(c.authority)} · ${escapeHtml(c.page_name)}</strong>` +
      `${c.error_detail ? ` — ${escapeHtml(c.error_detail)}` : ""}</div>` +
      `<button type="button" class="btn btn-d" data-manual-check="${escapeHtml(c.id)}">Mark checked manually</button></div>`;
  }
  const chips =
    okTexts.map((t) => `<span class="b b-ok">${escapeHtml(t)}</span>`).join("") +
    done
      .map(
        (c) =>
          `<span class="b b-grey" title="${escapeHtml(c.manual_check ? c.manual_check.note : "")}">` +
          `${escapeHtml(c.authority)} · checked manually${c.manual_check ? ` by ${escapeHtml(c.manual_check.user)}` : ""}</span>`,
      )
      .join("");
  if (chips) html += `<div class="row gap-2">${chips}</div>`;
  root.innerHTML = html;
}

function renderCounts() {
  const c = state.data.counts;
  const tile = (label, value, cls = "") =>
    `<div class="card count"><div class="count-label">${label}</div><div class="count-value ${cls}">${value}</div></div>`;
  $("rv-counts").innerHTML =
    tile("New", c.new, "count-new") +
    tile("Modified", c.modified) +
    tile("Backdated", c.backdated, "count-back") +
    tile("Urgent", c.urgent, "count-urg") +
    tile("Needs manual fetch", c.needs_manual_fetch, "count-mod") +
    tile("Decided", `${c.decided} <small>of ${c.total}</small>`, "count-ok");
}

// ---------- Rendering: needs-attention table ----------
function renderAuthorityFilter() {
  const select = $("f-auth");
  const authorities = [...new Set(items().map((i) => i.authority))].sort();
  const current = state.filters.authority;
  select.innerHTML =
    '<option value="">All authorities</option>' +
    authorities.map((a) => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join("");
  select.value = authorities.includes(current) ? current : "";
  state.filters.authority = select.value;
}

function visibleAttention() {
  const { authority, type, q } = state.filters;
  const needle = q.trim().toLowerCase();
  return attentionItems().filter(
    (i) =>
      (!authority || i.authority === authority) &&
      (!type || i.change_type === type) &&
      (!needle || i.title.toLowerCase().includes(needle)),
  );
}

function itemRow(i) {
  const badges = i.badges.filter((b) => CHANGE_BADGES[b]).map((b) => badge(CHANGE_BADGES[b])).join("");
  const sub = [i.note, i.relevance_reason].filter(Boolean).join(" · ");
  const urgent = i.priority === "URGENT";
  const cls = [i.id === state.selectedId ? "is-selected" : "", i.id === state.cursorId ? "is-cursor" : ""]
    .filter(Boolean)
    .join(" ");
  const stateTone = i.decided ? "b-ok" : i.section === "manual_fetch" ? "b-mod" : i.state_label === "In review" ? "b-new" : "b-grey";
  return (
    `<tr data-id="${escapeHtml(i.id)}"${cls ? ` class="${cls}"` : ""}>` +
    '<td><div class="cell-stack">' +
    (badges ? `<div class="cell-badges">${badges}</div>` : "") +
    `<a href="#item-${escapeHtml(i.id)}" data-open="${escapeHtml(i.id)}"${i.id === state.selectedId ? ' aria-current="true"' : ""}>${escapeHtml(i.title)}</a>` +
    (sub ? `<span class="muted text-13">${escapeHtml(sub)}</span>` : "") +
    "</div></td>" +
    `<td>${escapeHtml(i.authority)}</td>` +
    `<td>${escapeHtml(formatDate(i.displayed_date))}</td>` +
    `<td><span class="b b-grey">${escapeHtml(i.channel_label)}</span></td>` +
    `<td><div class="text-13">${escapeHtml(i.relevance_label)}</div>` +
    `<div class="meter${urgent ? " meter-urg" : ""}" style="--pct:${Math.max(0, Math.min(100, i.relevance_score))}%"><span></span></div></td>` +
    `<td>${badge([stateTone, i.state_label])}</td></tr>`
  );
}

function renderTable() {
  const rows = visibleAttention();
  const total = attentionItems().length;
  const body = $("rv-rows");
  if (!rows.length) {
    const text = total
      ? "No items match these filters. Clear the filters to see all items."
      : "Nothing needs attention in this cycle.";
    body.innerHTML = `<tr><td colspan="6"><div class="empty"><div class="empty-text">${text}</div></div></td></tr>`;
    return;
  }
  body.innerHTML = rows.map(itemRow).join("");
}

// ---------- Rendering: manual fetch + low relevance ----------
function renderManual() {
  const list = items().filter((i) => i.section === "manual_fetch");
  $("rv-manual").innerHTML = list
    .map(
      (i) =>
        '<section class="card pad-sm row gap-3" aria-label="Needs manual fetch">' +
        '<span class="b b-mod">Needs manual fetch</span>' +
        `<div class="grow basis-300 text-14"><strong class="fw-500">${escapeHtml(i.authority)} · ${escapeHtml(i.title)}</strong> — ` +
        "the document download was blocked. Download it in your browser and upload it here.</div>" +
        `<button type="button" class="btn btn-o" data-upload="${escapeHtml(i.id)}">Upload document</button></section>`,
    )
    .join("");
}

function renderLow() {
  const root = $("rv-low");
  const all = items().filter((i) => i.section === "low_relevance");
  if (!all.length) {
    root.hidden = true;
    return;
  }
  root.hidden = false;
  const sorted = [...all].sort((a, b) => Number(a.decided) - Number(b.decided));
  const open = all.filter((i) => !i.decided).length;
  const shown = state.showAllLow ? sorted : sorted.slice(0, LOW_PREVIEW);
  const selectable = shown.filter((i) => !i.decided);
  const allChecked = selectable.length > 0 && selectable.every((i) => state.checked.has(i.id));
  const count = [...state.checked].filter((id) => byId(id) && !byId(id).decided).length;
  const rows = shown
    .map((i) =>
      i.decided
        ? `<div class="row gap-2 text-14 muted"><span class="b b-grey">Dismissed</span> ${escapeHtml(i.authority)} · ${escapeHtml(i.title)}</div>`
        : '<div class="row gap-2">' +
          `<label class="check grow"><input type="checkbox" data-low="${escapeHtml(i.id)}"${state.checked.has(i.id) ? " checked" : ""}> ` +
          `${escapeHtml(i.authority)} · ${escapeHtml(i.title)}</label>` +
          `<a href="#item-${escapeHtml(i.id)}" class="text-14" data-open="${escapeHtml(i.id)}">Review</a></div>`,
    )
    .join("");
  root.innerHTML =
    '<div class="row row-between gap-3"><div>' +
    `<h2 class="section-title-sm">Probably not relevant <span class="muted fw-400">· ${open} ${open === 1 ? "item" : "items"}</span></h2>` +
    '<div class="muted text-13">Shown, never hidden. Ranked low by keyword rules. Nothing is dismissed until you choose to.</div></div>' +
    '<div class="row gap-2">' +
    (all.length > LOW_PREVIEW
      ? `<button type="button" class="btn btn-o" id="low-toggle" aria-expanded="${state.showAllLow}">${state.showAllLow ? "Show fewer" : `Show all ${all.length}`}</button>`
      : "") +
    `<button type="button" class="btn btn-d" id="low-dismiss"${count ? "" : " disabled"}>Dismiss selected${count ? ` (${count})` : ""}</button></div></div>` +
    (selectable.length
      ? `<label class="check"><input type="checkbox" id="low-all"${allChecked ? " checked" : ""}> Select all shown</label>`
      : "") +
    `<div class="stack gap-1">${rows}</div>`;
}

// ---------- Review panel ----------
const statusDot = (value) => STATUS_PILLS.find((p) => p[0] === value)?.[2] ?? "";

function evidenceHtml(detail) {
  const i = detail.item;
  const lines = [
    `First seen ${escapeHtml(formatDateTime(i.first_seen_at))} · ${escapeHtml(i.channel_label)}` +
      (i.change_type ? ` · ${escapeHtml(CHANGE_BADGES[i.change_type]?.[1] ?? i.change_type)}` : ""),
  ];
  const snap = detail.snapshots[detail.snapshots.length - 1];
  const doc = [...detail.documents].reverse().find((d) => d.sha256);
  const hashes = [];
  if (snap) hashes.push(`Snapshot SHA-256 ${escapeHtml(short(snap.sha256))}`);
  if (doc) hashes.push(`Document SHA-256 ${escapeHtml(short(doc.sha256))}`);
  if (hashes.length) lines.push(hashes.join(" · "));
  for (const d of detail.documents) {
    if (d.fetch_status === "MANUAL") {
      lines.push(
        `Document manually supplied by ${escapeHtml(d.uploaded_by || "a reviewer")}` +
          `${d.fetched_at ? ` on ${escapeHtml(formatDateTime(d.fetched_at))}` : ""}` +
          `${d.filename ? ` (${escapeHtml(d.filename)})` : ""}`,
      );
    }
  }
  const last = detail.latest_decision;
  if (last) {
    lines.push(
      `${last.is_draft ? "Draft saved" : "Last decision"} by ${escapeHtml(last.reviewer)} on ${escapeHtml(formatDateTime(last.decided_at))}`,
    );
  }
  return (
    '<div class="evidence"><div class="evidence-title">Source evidence</div>' +
    lines.map((l) => `<div>${l}</div>`).join("") +
    `<a href="/history?item=${encodeURIComponent(i.id)}">Open full traceability</a></div>`
  );
}

function panelHtml(detail) {
  const i = detail.item;
  const p = detail.prefill;
  const d = detail.latest_decision;
  const pick = (saved, proposed) => (d && saved != null ? saved : proposed);
  const ref = pick(d?.ref_no_and_date, p.ref_no_and_date) ?? "";
  const authority = pick(d?.authority, p.authority) ?? "";
  const issue = formatDate(pick(d?.issue_date, p.issue_date));
  const effective = formatDate(pick(d?.effective_date, p.effective_date));
  const applicability = d?.applicability ?? "";
  const status = d?.status_colour ?? "YET_TO_START";
  const impact = d?.impact_text ?? i.summary ?? "";
  const treatment = d?.treatment && d.treatment !== "NOT_RELEVANT" ? d.treatment : "";
  const sme = d?.sme_function ?? "";
  const include = d ? d.include_in_update : true;
  const option = (val, label, sel) => `<option value="${val}"${sel === val ? " selected" : ""}>${escapeHtml(label)}</option>`;
  const pills = STATUS_PILLS.map(
    ([val, label, dot]) =>
      `<label class="pill"><input type="radio" name="status_colour" value="${val}"${status === val ? " checked" : ""}>` +
      `<span class="dot ${dot}"></span>${label}</label>`,
  ).join("");

  return (
    '<div class="drawer-head"><div>' +
    `<div class="muted text-13">Reviewing · ${escapeHtml(i.internal_ref)}</div>` +
    `<h2 class="drawer-title" id="rv-panel-title" tabindex="-1">${escapeHtml(i.title)}</h2></div>` +
    '<button type="button" class="btn btn-o btn-icon" id="rv-close" aria-label="Close panel">' +
    '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button></div>' +
    '<form class="drawer-body form" id="rv-form" novalidate>' +
    `<div class="field"><label for="r-ref">Reference No. and date</label><input id="r-ref" name="ref_no_and_date" value="${escapeHtml(ref)}"></div>` +
    '<div class="form-grid-2">' +
    `<div class="field"><label for="r-auth">Authority</label><input id="r-auth" name="authority" value="${escapeHtml(authority)}"></div>` +
    `<div class="field"><label for="r-app">Applicability</label><select id="r-app" name="applicability"><option value="">Choose…</option>${APPLICABILITY.map(([v, l]) => option(v, l, applicability)).join("")}</select></div>` +
    `<div class="field"><label for="r-iss">Issue date</label><input id="r-iss" name="issue_date" value="${escapeHtml(issue)}" placeholder="DD.MM.YYYY" inputmode="numeric" aria-describedby="r-iss-err"><div class="field-error" id="r-iss-err" role="alert" hidden></div></div>` +
    `<div class="field"><label for="r-eff">Effective date</label><input id="r-eff" name="effective_date" value="${escapeHtml(effective)}" placeholder="DD.MM.YYYY" inputmode="numeric" aria-describedby="r-eff-err"><div class="field-error" id="r-eff-err" role="alert" hidden></div></div></div>` +
    (p.effective_date_rule ? `<div class="muted text-13">Effective date: ${escapeHtml(p.effective_date_rule)}</div>` : "") +
    (p.applicability_hint ? `<div class="muted text-13">Suggested applicability: ${escapeHtml(p.applicability_hint.text)} (from “${escapeHtml(p.applicability_hint.phrase)}”). Please check; you decide.</div>` : "") +
    `<div class="field"><label for="r-link">Guidelines link</label><input id="r-link" value="${escapeHtml(i.canonical_url)}" readonly></div>` +
    `<fieldset><legend>Status</legend><div class="pill-row">${pills}</div></fieldset>` +
    `<div class="field"><label for="r-imp">Impact on TVSCS</label><textarea id="r-imp" name="impact_text" rows="5">${escapeHtml(impact)}</textarea></div>` +
    '<div class="form-grid-2">' +
    `<div class="field"><label for="r-tr">Treatment</label><select id="r-tr" name="treatment"><option value="">Choose…</option>${TREATMENTS.map(([v, l]) => option(v, l, treatment)).join("")}</select></div>` +
    `<div class="field"><label for="r-sme">SME function</label><select id="r-sme" name="sme_function"><option value="">None</option>${state.sme.map((f) => option(f.id, f.name, sme)).join("")}</select></div></div>` +
    `<label class="check"><input type="checkbox" name="include_in_update"${include ? " checked" : ""}> Include in today’s update</label>` +
    evidenceHtml(detail) +
    '<div class="drawer-actions">' +
    '<button type="button" class="btn btn-g btn-fill" data-act="accept">Accept</button>' +
    '<button type="button" class="btn btn-o btn-fill" data-act="save">Save</button>' +
    '<button type="button" class="btn btn-d btn-fill" data-act="dismiss">Dismiss</button></div></form>'
  );
}

async function openPanel(id, { focus = true } = {}) {
  let detail;
  try {
    detail = await get(`/items/${encodeURIComponent(id)}`);
  } catch {
    return;
  }
  state.selectedId = id;
  state.cursorId = id;
  state.detail = detail;
  const panel = $("rv-panel");
  panel.innerHTML = panelHtml(detail);
  panel.hidden = false;
  renderTable();
  if (focus) $("rv-panel-title").focus();
}

function closePanel() {
  if (!state.selectedId) return;
  const id = state.selectedId;
  state.selectedId = null;
  state.detail = null;
  $("rv-panel").hidden = true;
  $("rv-panel").innerHTML = "";
  renderTable();
  document.querySelector(`#rv-rows [data-open="${CSS.escape(id)}"]`)?.focus();
}

function readForm() {
  const form = $("rv-form");
  const fd = new FormData(form);
  const text = (k) => (fd.get(k) ?? "").toString().trim() || null;
  const errors = [];
  const dateField = (name, inputId) => {
    const raw = (fd.get(name) ?? "").toString();
    const iso = parseDisplayDate(raw);
    const err = $(`${inputId}-err`);
    const input = $(inputId);
    if (iso === undefined) {
      err.textContent = "Use the format DD.MM.YYYY, for example 20.11.2026.";
      err.hidden = false;
      input.setAttribute("aria-invalid", "true");
      errors.push(input);
      return null;
    }
    err.hidden = true;
    input.removeAttribute("aria-invalid");
    return iso;
  };
  const payload = {
    ref_no_and_date: text("ref_no_and_date"),
    authority: text("authority"),
    applicability: text("applicability"),
    issue_date: dateField("issue_date", "r-iss"),
    effective_date: dateField("effective_date", "r-eff"),
    status_colour: text("status_colour"),
    impact_text: text("impact_text"),
    treatment: text("treatment"),
    sme_function: text("sme_function"),
    include_in_update: fd.get("include_in_update") === "on",
  };
  return { payload, errors };
}

async function submitDecision(action) {
  if (state.busy || !state.selectedId) return;
  const id = state.selectedId;
  const title = byId(id)?.title ?? "item";
  let payload;
  if (action === "dismiss") {
    const reason = await askText({
      title: "Dismiss this item?",
      intro: "The item stays in the audit trail and can be found in item history. It will not appear in the daily update.",
      label: "Reason for dismissing",
      confirmLabel: "Dismiss",
      danger: true,
    });
    if (!reason) return;
    payload = { action, dismiss_reason: reason };
  } else {
    const { payload: values, errors } = readForm();
    if (errors.length) {
      errors[0].focus();
      return;
    }
    payload = { action, ...values };
  }
  state.busy = true;
  setActionsDisabled(true);
  try {
    const out = await post(`/items/${encodeURIComponent(id)}/decision`, payload);
    const edited = Object.keys(out.decision.fields_edited || {}).length;
    const verb = { accept: "Accepted", save: "Draft saved", dismiss: "Dismissed" }[action];
    const extra = edited ? ` ${edited} ${edited === 1 ? "field" : "fields"} edited from the pre-filled values.` : "";
    toast(`${verb}: ${title}.${extra}`, { type: "ok" });
    await load();
    if (action === "save") await openPanel(id, { focus: false });
    else closePanel();
  } catch {
    /* api() already showed the error */
  } finally {
    state.busy = false;
    setActionsDisabled(false);
  }
}

function setActionsDisabled(disabled) {
  document.querySelectorAll("#rv-form [data-act]").forEach((b) => {
    b.disabled = disabled;
  });
}

// ---------- Actions outside the panel ----------
async function markChecked(checkId) {
  const note = await askText({
    title: "Mark as checked manually",
    intro: "Open the source in your own browser and look for new items. Then record what you did.",
    label: "Note (what you checked)",
    confirmLabel: "Mark checked",
  });
  if (!note) return;
  try {
    await post(`/source-checks/${encodeURIComponent(checkId)}/manual-check`, { note });
    toast("Recorded: source checked manually.", { type: "ok" });
    await load();
  } catch {
    /* api() already showed the error */
  }
}

let uploadTarget = null;
async function uploadChosen(file) {
  const id = uploadTarget;
  uploadTarget = null;
  if (!id || !file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const doc = await post(`/items/${encodeURIComponent(id)}/upload`, fd);
    toast(`Document manually supplied by ${doc.uploaded_by || "you"}: ${doc.filename || file.name}.`, { type: "ok" });
    await load();
    await openPanel(id, { focus: false });
  } catch {
    /* api() already showed the error */
  }
}

async function bulkDismiss() {
  const ids = [...state.checked].filter((id) => byId(id) && !byId(id).decided);
  if (!ids.length) return;
  const reason = await askText({
    title: `Dismiss ${ids.length} ${ids.length === 1 ? "item" : "items"}?`,
    intro: "One reason is recorded for all of them. They stay in the audit trail and in item history.",
    label: "Reason for dismissing",
    confirmLabel: "Dismiss selected",
    danger: true,
  });
  if (!reason) return;
  try {
    const out = await post("/items/bulk-dismiss", { item_ids: ids, reason });
    toast(`Dismissed ${out.dismissed.length} ${out.dismissed.length === 1 ? "item" : "items"}.`, { type: "ok" });
    state.checked.clear();
    await load();
  } catch {
    /* api() already showed the error */
  }
}

// ---------- Keyboard ----------
function moveCursor(step) {
  const rows = visibleAttention();
  if (!rows.length) return;
  const at = rows.findIndex((r) => r.id === state.cursorId);
  const next = rows[Math.max(0, Math.min(rows.length - 1, at < 0 ? (step > 0 ? 0 : rows.length - 1) : at + step))];
  state.cursorId = next.id;
  renderTable();
  const link = document.querySelector(`#rv-rows [data-open="${CSS.escape(next.id)}"]`);
  link?.focus();
  link?.closest("tr")?.scrollIntoView({ block: "nearest" });
}

function onKey(e) {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
  if (document.querySelector("dialog[open]")) return; // modals handle their own keys (Esc closes)
  const t = e.target;
  const typing = t instanceof HTMLElement && t.matches("input, textarea, select, [contenteditable]");
  if (e.key === "Escape") {
    if (state.selectedId) {
      e.preventDefault();
      closePanel();
    }
    return;
  }
  if (typing) return;
  const key = e.key.toLowerCase();
  if (key === "j" || key === "k") {
    e.preventDefault();
    moveCursor(key === "j" ? 1 : -1);
  } else if (e.key === "Enter") {
    if (t instanceof HTMLElement && t.matches("a, button")) return; // native activation
    const id = state.cursorId ?? visibleAttention()[0]?.id;
    if (id) {
      e.preventDefault();
      openPanel(id);
    }
  } else if (key === "a" || key === "d") {
    e.preventDefault();
    if (!state.selectedId) {
      toast("Open an item first (J / K to move, Enter to open).");
      return;
    }
    submitDecision(key === "a" ? "accept" : "dismiss");
  }
}

// ---------- Wiring ----------
document.addEventListener("click", (e) => {
  const target = e.target instanceof Element ? e.target : null;
  if (!target) return;
  const open = target.closest("[data-open]");
  if (open) {
    e.preventDefault();
    openPanel(open.dataset.open);
    return;
  }
  const row = target.closest("#rv-rows tr[data-id]");
  if (row && !target.closest("a, button, input, select, textarea")) {
    openPanel(row.dataset.id);
    return;
  }
  const manual = target.closest("[data-manual-check]");
  if (manual) return void markChecked(manual.dataset.manualCheck);
  const upload = target.closest("[data-upload]");
  if (upload) {
    uploadTarget = upload.dataset.upload;
    $("rv-file").click();
    return;
  }
  const act = target.closest("#rv-form [data-act]");
  if (act) return void submitDecision(act.dataset.act);
  if (target.closest("#rv-close")) return closePanel();
  if (target.closest("#low-toggle")) {
    state.showAllLow = !state.showAllLow;
    renderLow();
    $("low-toggle")?.focus();
    return;
  }
  if (target.closest("#low-dismiss")) return void bulkDismiss();
});

document.addEventListener("change", (e) => {
  const t = e.target;
  if (!(t instanceof HTMLInputElement) && !(t instanceof HTMLSelectElement)) return;
  if (t.id === "rv-file") {
    const file = t.files && t.files[0];
    t.value = "";
    uploadChosen(file);
  } else if (t.dataset.low) {
    if (t.checked) state.checked.add(t.dataset.low);
    else state.checked.delete(t.dataset.low);
    renderLow();
    document.querySelector(`[data-low="${CSS.escape(t.dataset.low)}"]`)?.focus();
  } else if (t.id === "low-all") {
    const shown = items().filter((i) => i.section === "low_relevance" && !i.decided);
    const visible = state.showAllLow ? shown : shown.slice(0, LOW_PREVIEW);
    for (const i of visible) {
      if (t.checked) state.checked.add(i.id);
      else state.checked.delete(i.id);
    }
    renderLow();
    $("low-all")?.focus();
  } else if (t.id === "f-auth") {
    state.filters.authority = t.value;
    renderTable();
  } else if (t.id === "f-type") {
    state.filters.type = t.value;
    renderTable();
  } else if (t === cycleSelect) {
    const url = new URL(location.href);
    url.searchParams.set("cycle", t.value);
    location.href = url.toString();
  }
});

$("f-q").addEventListener("input", (e) => {
  state.filters.q = e.target.value;
  renderTable();
});
$("rv-form")?.addEventListener("submit", (e) => e.preventDefault());
$("rv-panel").addEventListener("submit", (e) => e.preventDefault());
document.addEventListener("keydown", onKey);

reload();
