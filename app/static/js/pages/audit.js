// Audit log page. FR-28. Read only: filters call /api/v1/audit/view; before/after arrive from the
// server already turned into readable key/value rows.

import { get } from "../api.js";
import { escapeHtml, formatDateTime, parseDisplayDate } from "../ui.js";

const $ = (id) => document.getElementById(id);
let optionsFilled = false;

function readDate(id) {
  const input = $(id);
  const err = $(`${id}-err`);
  const iso = parseDisplayDate(input.value);
  if (iso === undefined) {
    err.textContent = "Use DD.MM.YYYY, for example 20.11.2026.";
    err.hidden = false;
    input.setAttribute("aria-invalid", "true");
    return { error: input };
  }
  err.hidden = true;
  input.removeAttribute("aria-invalid");
  return { value: iso };
}

function query() {
  const from = readDate("a-from");
  const to = readDate("a-to");
  if (from.error || to.error) {
    (from.error || to.error).focus();
    return null;
  }
  return { actor: $("a-actor").value, entity: $("a-entity").value, action: $("a-action").value, from: from.value, to: to.value };
}

function fill(select, values, first) {
  const current = select.value;
  select.innerHTML = `<option value="">${first}</option>` + values.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  select.value = current;
}

const kvTable = (rows, title) =>
  `<div class="au-kv"><div class="au-kv-title">${title}</div>` +
  (rows.length
    ? `<dl>${rows.map((r) => `<dt>${escapeHtml(r.key)}</dt><dd>${escapeHtml(r.value)}</dd>`).join("")}</dl>`
    : '<div class="muted text-13">Nothing recorded.</div>') +
  "</div>";

function rowHtml(e) {
  return (
    `<tr><td class="au-when">${escapeHtml(formatDateTime(e.ts))}</td><td>${escapeHtml(e.actor)}</td>` +
    `<td>${escapeHtml(e.action_label)}</td><td>${escapeHtml(e.entity)} <code>${escapeHtml(e.entity_id)}</code></td>` +
    `<td><details class="au-details"><summary>Show before and after</summary>` +
    `<div class="au-grid">${kvTable(e.before, "Before")}${kvTable(e.after, "After")}</div></details></td></tr>`
  );
}

let allRows = [];
let shownRows = 50;

function paintRows() {
  const more = allRows.length - shownRows;
  $("au-rows").innerHTML = allRows.length
    ? allRows.slice(0, shownRows).map(rowHtml).join("") +
      (more > 0
        ? `<tr><td colspan="5"><button type="button" class="btn btn-o" id="au-more">Show 50 more (${more} left)</button></td></tr>`
        : "")
    : '<tr><td colspan="5"><div class="empty"><div class="empty-text">No events match these filters.</div></div></td></tr>';
}

document.addEventListener("click", (e) => {
  if (e.target instanceof Element && e.target.closest("#au-more")) {
    shownRows += 50;
    paintRows();
  }
});

async function load() {
  const q = query();
  if (!q) return;
  let page;
  try {
    page = await get("/audit/view", q);
  } catch {
    return; // api() already showed the error
  }
  if (!optionsFilled) {
    fill($("a-actor"), page.options.actors, "Anyone");
    fill($("a-entity"), page.options.entities, "Anything");
    fill($("a-action"), page.options.actions, "Any action");
    optionsFilled = true;
  }
  $("au-count").textContent = `${page.total} ${page.total === 1 ? "event" : "events"}, newest first`;
  shownRows = 50;
  allRows = page.rows;
  paintRows();
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v) params.set(k, v);
  $("au-export").href = `/api/v1/audit/export.csv${params.toString() ? `?${params}` : ""}`;
}

$("au-form").addEventListener("submit", (e) => {
  e.preventDefault();
  load();
});
$("au-clear").addEventListener("click", () => {
  $("au-form").reset();
  load();
});

load();
