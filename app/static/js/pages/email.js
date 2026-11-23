// Email preview page (docs/ui-mockups/02-email-preview.html). FR-22 … FR-25, FR-32.
// Presentation only: loads the draft from /api/v1, renders it, and posts reorder / approve.
// Who may approve, what blocks approval and what the email says are decided by the services.

import { ApiError, get, post } from "../api.js";
import { escapeHtml, toast } from "../ui.js";

const $ = (id) => document.getElementById(id);
const cycleSelect = document.querySelector('[data-role="cycle-select"]');
const state = { cycle: cycleSelect ? cycleSelect.value : "", data: null, busy: false, dragId: null };

const DOT_CLASS = {
  NOT_APPLICABLE: "dot-na",
  ACTION_OVER: "dot-done",
  IN_PROGRESS: "dot-progress",
  NOT_AS_PER_PLAN: "dot-late",
  YET_TO_START: "dot-todo",
};
const STATUS_BADGE = { DRAFT: ["b-grey", "Draft"], APPROVED: ["b-ok", "Approved"], EXPORTED: ["b-ok", "Exported"] };

const ICON_OK =
  '<svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" class="em-icon"><circle cx="9" cy="9" r="8" fill="#E6F4EC"/><path d="M5 9.5l2.6 2.5L13 6.5" stroke="#0A6B34" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>';
const ICON_WARN =
  '<svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" class="em-icon"><circle cx="9" cy="9" r="8" fill="#FFF4D6"/><path d="M9 5v5M9 12.6v.4" stroke="#6B4A00" stroke-width="1.8" stroke-linecap="round"/></svg>';

const cycleQuery = () => (state.cycle ? { cycle: state.cycle } : {});

// ---------- Loading ----------
async function load() {
  try {
    state.data = await get("/daily-update/preview", cycleQuery(), { silent: true });
  } catch (e) {
    state.data = null;
    renderEmpty(e instanceof ApiError && e.status === 404 ? e.message : `Could not load the draft. ${e.message}`);
    return;
  }
  render();
}

function renderEmpty(message) {
  $("em-sub").textContent = "No draft to show";
  $("em-checks").innerHTML = `<div class="empty"><div class="empty-text">${escapeHtml(message)}</div></div>`;
  $("em-rows").innerHTML = "";
  $("em-count").textContent = "";
  for (const id of ["e-sub", "e-to", "e-cc"]) $(id).value = "";
  $("em-frame").srcdoc = "";
  setButtons();
}

// ---------- Rendering ----------
function render() {
  const d = state.data;
  const [tone, label] = STATUS_BADGE[d.status] ?? ["b-grey", d.status];
  $("em-sub").innerHTML =
    `Draft for the ${escapeHtml(cycleLabel())} cycle · Built only from reviewed items · <span class="b ${tone}">${escapeHtml(label)}</span>`;
  renderChecks();
  renderRows();
  $("e-sub").value = d.subject;
  $("e-to").value = d.to.join("; ");
  $("e-cc").value = d.cc.length ? d.cc.join("; ") : "None for this cycle";
  const frame = $("em-frame");
  frame.srcdoc = d.html;
  frame.addEventListener("load", fitFrame, { once: true });
  setButtons();
}

function cycleLabel() {
  const opt = cycleSelect && cycleSelect.selectedOptions[0];
  return opt && opt.textContent ? opt.textContent.trim() : state.data.cycle;
}

function fitFrame() {
  const frame = $("em-frame");
  const doc = frame.contentDocument;
  if (doc && doc.documentElement) frame.style.height = `${doc.documentElement.scrollHeight + 8}px`;
}

function renderChecks() {
  const d = state.data;
  const items = d.checks
    .map((c) => `<div class="em-check">${c.ok ? ICON_OK : ICON_WARN}<span>${escapeHtml(c.text)}</span></div>`)
    .join("");
  const fix = d.checks.some((c) => !c.ok)
    ? `<a class="text-14" href="/review?cycle=${encodeURIComponent(d.cycle)}">Open the daily review to fix this</a>`
    : "";
  $("em-checks").innerHTML = `<h2 class="section-title-sm">Before you approve</h2>${items}${fix}`;
}

function renderRows() {
  const d = state.data;
  const editable = d.status === "DRAFT";
  $("em-count").textContent = `${d.rows.length} included`;
  $("em-rows-hint").hidden = !editable || d.rows.length < 2;
  if (!d.rows.length) {
    $("em-rows").innerHTML =
      '<li class="empty"><div class="empty-text">No rows. The email will say there were no relevant updates.</div></li>';
    return;
  }
  $("em-rows").innerHTML = d.rows
    .map((r, i) => {
      const dot = DOT_CLASS[r.status_colour] ?? "";
      const name = `${escapeHtml(r.guideline_title)} · ${escapeHtml(r.authority)}`;
      const moves = editable
        ? `<button type="button" class="btn btn-o btn-icon em-move" data-move="-1" aria-label="Move ${escapeHtml(r.guideline_title)} up"${i === 0 ? " disabled" : ""}>↑</button>` +
          `<button type="button" class="btn btn-o btn-icon em-move" data-move="1" aria-label="Move ${escapeHtml(r.guideline_title)} down"${i === d.rows.length - 1 ? " disabled" : ""}>↓</button>`
        : "";
      return (
        `<li class="em-row" data-id="${escapeHtml(r.item_id)}"${editable ? ' draggable="true"' : ""}>` +
        `<span class="em-grip" aria-hidden="true">⋮⋮</span><span class="fw-500">${r.serial_no}</span>` +
        `<span class="grow">${name}</span>` +
        (dot ? `<span class="dot ${dot}" title="${escapeHtml(r.status_label ?? "")}" role="img" aria-label="${escapeHtml(r.status_label ?? "")}"></span>` : "") +
        `${moves}</li>`
      );
    })
    .join("");
}

function setButtons() {
  const d = state.data;
  $("em-copy").disabled = !d;
  const approve = $("em-approve");
  approve.disabled = !d || !d.can_approve || state.busy;
  approve.textContent = d && d.status !== "DRAFT" ? "Approved" : "Approve";
  const eml = $("em-eml");
  const ready = d && d.status !== "DRAFT";
  eml.setAttribute("aria-disabled", ready ? "false" : "true");
  eml.href = ready ? `/api/v1/daily-update/${encodeURIComponent(d.id)}/export.eml` : "#";
  eml.title = ready ? "" : "Approve the update first";
  $("em-xlsx").href = `/api/v1/daily-update/cycle-export.csv${state.cycle ? `?cycle=${encodeURIComponent(state.cycle)}` : ""}`;
}

// ---------- Actions ----------
async function reorder(ids) {
  if (state.busy) return;
  state.busy = true;
  try {
    state.data = await post(`/daily-update/${encodeURIComponent(state.data.id)}/reorder`, { item_ids: ids });
    render();
  } catch {
    await load(); // api() already showed the error; show what the server really has
  } finally {
    state.busy = false;
    setButtons();
  }
}

async function approve() {
  if (state.busy || !state.data || !state.data.can_approve) return;
  state.busy = true;
  setButtons();
  try {
    state.data = await post(`/daily-update/${encodeURIComponent(state.data.id)}/approve`);
    toast(`Approved by ${state.data.approved_by}. You can now download the .eml draft.`, { type: "ok" });
    render();
  } catch {
    /* api() already showed the error */
  } finally {
    state.busy = false;
    setButtons();
  }
}

async function copyHtml() {
  const html = state.data.html;
  try {
    await navigator.clipboard.writeText(html);
  } catch {
    const box = document.createElement("textarea");
    box.value = html;
    box.className = "visually-hidden";
    document.body.append(box);
    box.select();
    const ok = document.execCommand("copy");
    box.remove();
    if (!ok) return void toast("Could not copy. Download the .eml instead.", { type: "err" });
  }
  toast("HTML copied. Paste it into a new Outlook message.", { type: "ok" });
}

// ---------- Wiring ----------
document.addEventListener("click", (e) => {
  const t = e.target instanceof Element ? e.target : null;
  if (!t) return;
  if (t.closest("#em-approve")) return void approve();
  if (t.closest("#em-copy")) return void copyHtml();
  const eml = t.closest("#em-eml");
  if (eml && eml.getAttribute("aria-disabled") === "true") {
    e.preventDefault();
    toast("Approve the update first, then download the .eml draft.");
    return;
  }
  const move = t.closest("[data-move]");
  if (move) {
    const ids = state.data.rows.map((r) => r.item_id);
    const at = ids.indexOf(move.closest(".em-row").dataset.id);
    const to = at + Number(move.dataset.move);
    if (at < 0 || to < 0 || to >= ids.length) return;
    [ids[at], ids[to]] = [ids[to], ids[at]];
    reorder(ids).then(() => {
      document.querySelector(`.em-row[data-id="${CSS.escape(ids[to])}"] [data-move="${move.dataset.move}"]`)?.focus();
    });
  }
});

const rowsEl = $("em-rows");
rowsEl.addEventListener("dragstart", (e) => {
  const row = e.target.closest(".em-row");
  if (!row) return;
  state.dragId = row.dataset.id;
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", row.dataset.id);
  row.classList.add("is-dragging");
});
rowsEl.addEventListener("dragover", (e) => {
  if (state.dragId && e.target.closest(".em-row")) e.preventDefault();
});
rowsEl.addEventListener("dragend", () => {
  state.dragId = null;
  rowsEl.querySelectorAll(".is-dragging").forEach((n) => n.classList.remove("is-dragging"));
});
rowsEl.addEventListener("drop", (e) => {
  const over = e.target.closest(".em-row");
  const from = state.dragId;
  state.dragId = null;
  if (!over || !from || over.dataset.id === from) return;
  e.preventDefault();
  const ids = state.data.rows.map((r) => r.item_id);
  const target = ids.indexOf(over.dataset.id);
  ids.splice(ids.indexOf(from), 1);
  ids.splice(target, 0, from); // standard array move: lands where the target row was
  reorder(ids);
});

if (cycleSelect) {
  cycleSelect.addEventListener("change", () => {
    const url = new URL(location.href);
    url.searchParams.set("cycle", cycleSelect.value);
    location.href = url.toString();
  });
}

load();
