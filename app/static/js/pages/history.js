// Item history page (docs/ui-mockups/05-item-history.html). FR-27.
// Presentation only: the six steps, their wording and their links are built by the history service.

import { get } from "../api.js";
import { escapeHtml, parseDisplayDate } from "../ui.js";

const $ = (id) => document.getElementById(id);
const state = { hits: [], selected: null };

const badge = (b) => (b ? ` <span class="b b-${b.tone}">${escapeHtml(b.label)}</span>` : "");

// ---------- Search ----------
function readDate(inputId) {
  const input = $(inputId);
  const err = $(`${inputId}-err`);
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

async function search({ prefer = null } = {}) {
  const from = readDate("h-f");
  const to = readDate("h-t");
  if (from.error || to.error) return void (from.error || to.error).focus();
  let data;
  try {
    data = await get("/history/search", {
      q: $("h-q").value.trim(),
      authority: $("h-a").value,
      from: from.value,
      to: to.value,
    });
  } catch {
    return; // api() already showed the error
  }
  state.hits = data.hits;
  fillAuthorities(data.authorities);
  renderResults(data.total);
  const wanted = prefer ?? (data.hits.some((h) => h.id === state.selected) ? state.selected : null);
  if (wanted) return void (await select(wanted)); // an item named in the link is shown even if the search misses it
  state.selected = null;
  renderTrail(null);
  if (data.hits.length) await select(data.hits[0].id);
}

function fillAuthorities(list) {
  const select = $("h-a");
  if (select.options.length > 1) return;
  const current = select.value;
  select.innerHTML = '<option value="">All</option>' + list.map((a) => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join("");
  select.value = current;
}

function renderResults(total) {
  $("hi-count").textContent = `${total} ${total === 1 ? "result" : "results"}`;
  $("hi-list").innerHTML = state.hits.length
    ? state.hits
        .map(
          (h) =>
            `<a href="#${escapeHtml(h.id)}" class="hi-hit${h.id === state.selected ? " is-selected" : ""}" data-id="${escapeHtml(h.id)}"${h.id === state.selected ? ' aria-current="true"' : ""}>` +
            `<span class="hi-hit-title">${escapeHtml(h.title)}</span>` +
            `<span class="muted text-13">${escapeHtml(h.authority)} · ${escapeHtml(h.internal_ref)} · ${escapeHtml(h.note)}</span></a>`,
        )
        .join("")
    : '<div class="empty"><div class="empty-text">No items match. Clear a filter and try again.</div></div>';
}

// ---------- Trail ----------
async function select(id) {
  let trail;
  try {
    trail = await get(`/history/${encodeURIComponent(id)}/trail`);
  } catch {
    return;
  }
  state.selected = id;
  renderResults(state.hits.length);
  renderTrail(trail);
}

function stepHtml(s, last) {
  const hashes = s.hashes.length
    ? `<div class="muted text-13">${s.hashes.map((h) => `${escapeHtml(h.label)} <code title="${escapeHtml(h.sha256)}">${escapeHtml(h.short)}</code>`).join(" → ")}${s.links.length ? " · " : ""}${linksHtml(s)}</div>`
    : s.links.length
      ? `<div class="text-14">${linksHtml(s)}</div>`
      : "";
  return (
    `<li class="step${s.present ? "" : " step-pending"}"><div class="step-rail"><div class="step-num">${s.number}</div><div class="step-line${last ? " is-last" : ""}"></div></div>` +
    `<div class="step-body"><div class="fw-500">${escapeHtml(s.title)}${badge(s.badge)}</div>` +
    `<div class="text-14">${escapeHtml(s.text)}</div>` +
    (s.note ? `<div class="muted text-13">${escapeHtml(s.note)}</div>` : "") +
    `${hashes}</div></li>`
  );
}

function linksHtml(s) {
  return s.links
    .map((l) => `<a href="${escapeHtml(l.href)}"${l.href.startsWith("/api/") ? ' target="_blank" rel="noopener"' : ""}>${escapeHtml(l.label)}</a>`)
    .join(" · ");
}

function renderTrail(trail) {
  const root = $("hi-trail");
  const link = $("hi-export");
  if (!trail) {
    root.innerHTML = '<div class="empty"><div class="empty-text">Choose an item to see its chain.</div></div>';
    link.href = "#";
    link.setAttribute("aria-disabled", "true");
    return;
  }
  link.href = `/api/v1/history/${encodeURIComponent(trail.item_id)}/trail.csv`;
  link.setAttribute("aria-disabled", "false");
  root.innerHTML =
    `<h2 class="section-title">Traceability · ${escapeHtml(trail.internal_ref)}</h2>` +
    `<div class="muted text-14">${escapeHtml(trail.title)}</div>` +
    `<ol class="steps">${trail.steps.map((s, i) => stepHtml(s, i === trail.steps.length - 1)).join("")}</ol>`;
}

// ---------- Wiring ----------
$("hi-form").addEventListener("submit", (e) => {
  e.preventDefault();
  search();
});
$("hi-list").addEventListener("click", (e) => {
  const a = e.target instanceof Element ? e.target.closest("[data-id]") : null;
  if (!a) return;
  e.preventDefault();
  select(a.dataset.id);
});
$("hi-export").addEventListener("click", (e) => {
  if ($("hi-export").getAttribute("aria-disabled") === "true") e.preventDefault();
});

// /history?item=<id> (from the review page) opens that item; ?q= pre-fills the search.
const params = new URLSearchParams(location.search);
if (params.get("q")) $("h-q").value = params.get("q");
search({ prefer: params.get("item") });
