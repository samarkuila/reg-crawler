// On every page: the header "Run now" button and the alert banner for the latest run.
// No business rules here: the server decides who may run, whether a run is already going, and
// which alerts exist. This file only calls /api/v1 and shows what came back.
import { ApiError, get, post } from "./api.js";
import { escapeHtml, toast } from "./ui.js";

const KIND_LABEL = {
  URGENT_ITEM: "Urgent item",
  SOURCE_FAILED: "Source failed",
  SOURCE_BLOCKED: "Source blocked",
  SOURCE_ZERO_ITEMS: "No items",
  SOURCE_PARSE_ERROR: "Could not read page",
  SANITY_WARNING: "Fewer items than usual",
  RUN_PROBLEM: "Run problem",
  RUN_SKIPPED: "Run skipped",
  RUN_MISSED: "Run missed",
};
// The Daily review page already shows a strip for each failed or blocked source, with its own
// "Mark checked manually" button, so the banner leaves those out there.
const SHOWN_BY_REVIEW = new Set(["SOURCE_FAILED", "SOURCE_BLOCKED", "SOURCE_ZERO_ITEMS", "SOURCE_PARSE_ERROR"]);

export async function loadAlerts() {
  const root = document.getElementById("alert-banner");
  if (!root) return;
  let alerts;
  try {
    alerts = await get("/alerts", undefined, { silent: true });
  } catch {
    root.hidden = true;
    return;
  }
  const onReview = window.location.pathname.startsWith("/review");
  const shown = alerts.filter((a) => !(onReview && SHOWN_BY_REVIEW.has(a.kind)));
  root.hidden = shown.length === 0;
  if (!shown.length) return;
  // One short line; the full list opens on request, so it never pushes the page down.
  const count = (pred) => shown.filter(pred).length;
  const parts = [];
  const sources = count((a) => SHOWN_BY_REVIEW.has(a.kind));
  const urgent = count((a) => a.kind === "URGENT_ITEM");
  const other = shown.length - sources - urgent;
  if (sources) parts.push(`${sources} ${sources === 1 ? "source needs" : "sources need"} attention`);
  if (urgent) parts.push(`${urgent} urgent ${urgent === 1 ? "item" : "items"}`);
  if (other) parts.push(`${other} other ${other === 1 ? "notice" : "notices"}`);
  const hasUrgent = shown.some((a) => a.severity === "urgent");
  const rows = shown
    .map(
      (a) =>
        '<div class="alert">' +
        `<span class="b ${a.severity === "urgent" ? "b-urg" : "b-grey"}">${escapeHtml(KIND_LABEL[a.kind] || a.kind)}</span>` +
        `<div class="alert-body">${escapeHtml(a.message)}</div></div>`,
    )
    .join("");
  const wasOpen = root.querySelector("details")?.open;
  root.innerHTML =
    `<details class="alert-summary${hasUrgent ? " is-urgent" : ""}"${wasOpen ? " open" : ""}>` +
    `<summary><strong>${escapeHtml(parts.join(" · "))}</strong><span class="muted text-13"> — show details</span></summary>` +
    `<div class="alert-list">${rows}</div>` +
    '<div class="text-13"><a href="/health">Open Source health</a></div></details>';
}

async function runNow() {
  const button = document.getElementById("run-now");
  if (button) button.disabled = true;
  try {
    await post("/crawl/run", {});
    toast("Run started. Progress is on the Source health page.", { type: "ok" });
    window.dispatchEvent(new CustomEvent("run-started"));
  } catch (err) {
    // api() has already shown the reason (for example "A crawl is already running ...").
    if (!(err instanceof ApiError)) throw err;
  } finally {
    if (button) button.disabled = false;
  }
}

// ---------- Live crawl status (every page) ----------
// Polls GET /crawl/status: quickly while a crawl runs, slowly otherwise (so a scheduled run shows up
// too). When a run ends it tells the page ("run-finished") so the Daily review reloads its items.
const FAST_MS = 3000;
const SLOW_MS = 15000;
let wasRunning = false;
let statusTimer = null;

const hhmm = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` : "";
};

function paintStatus(s) {
  const root = document.getElementById("crawl-status");
  const run = document.getElementById("run-now");
  if (run) {
    run.disabled = s.running;
    run.textContent = s.running ? "Crawling…" : "Run now";
  }
  if (!root) return;
  root.hidden = !s.running;
  if (!s.running) return;
  const pct = s.sources_total ? Math.round((s.sources_done / s.sources_total) * 100) : 0;
  const who = s.run ? ` · started ${hhmm(s.run.started_at)} by ${escapeHtml(s.run.triggered_by)}` : "";
  const now = s.current ? ` · now reading <strong>${escapeHtml(s.current)}</strong>` : "";
  root.innerHTML =
    `<div class="row row-between gap-2"><div><span class="spin" aria-hidden="true"></span><strong>Crawl in progress</strong>${who}</div>` +
    `<a href="/health">Source health</a></div>` +
    `<div class="text-13">${escapeHtml(s.message)}${now} · ${s.sources_done} of ${s.sources_total} sources done</div>` +
    `<div class="progress" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Crawl progress"><span style="width:${pct}%"></span></div>`;
}

async function pollStatus() {
  clearTimeout(statusTimer);
  let next = SLOW_MS;
  try {
    const s = await get("/crawl/status", undefined, { silent: true });
    paintStatus(s);
    if (s.running) next = FAST_MS;
    if (wasRunning && !s.running) {
      toast("The crawl has finished. The page is showing the latest results.", { type: "ok" });
      loadAlerts();
      window.dispatchEvent(new CustomEvent("run-finished"));
    }
    wasRunning = s.running;
  } catch {
    /* the server may be restarting; try again later */
  }
  statusTimer = setTimeout(pollStatus, next);
}

const button = document.getElementById("run-now");
if (button) button.addEventListener("click", runNow);
loadAlerts();
pollStatus();
window.addEventListener("run-started", () => {
  setTimeout(loadAlerts, 1500);
  pollStatus();
});
