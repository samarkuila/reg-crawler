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
  root.innerHTML = shown
    .map(
      (a) =>
        '<div class="alert">' +
        `<span class="b ${a.severity === "urgent" ? "b-urg" : "b-grey"}">${escapeHtml(KIND_LABEL[a.kind] || a.kind)}</span>` +
        `<div class="alert-body">${escapeHtml(a.message)}</div></div>`,
    )
    .join("");
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

const button = document.getElementById("run-now");
if (button) button.addEventListener("click", runNow);
loadAlerts();
window.addEventListener("run-started", () => setTimeout(loadAlerts, 1500));
