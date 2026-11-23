// UI helpers: toast, drawer, modal, date formatting, escapeHtml.
// Presentation only — no business logic (CLAUDE.md UI rules). ES module, no dependencies.

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Escape text for safe insertion into HTML (use whenever building markup from data). */
export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

const pad2 = (n) => String(n).padStart(2, "0");

/** Format a date as DD.MM.YYYY. Accepts "YYYY-MM-DD", ISO date-time strings or Date. Returns "" if invalid. */
export function formatDate(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string") {
    // Date-only strings are formatted without timezone conversion.
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (m) return `${m[3]}.${m[2]}.${m[1]}`;
  }
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()}`;
}

/** Format a date-time as "DD.MM.YYYY HH:MM" (24h, local time). Returns "" if invalid. */
export function formatDateTime(value) {
  if (value === null || value === undefined || value === "") return "";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return `${formatDate(d)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ---------- Toast ----------
let toastRegion = null;

function getToastRegion() {
  if (!toastRegion || !toastRegion.isConnected) {
    toastRegion = el("div", "toast-region");
    toastRegion.setAttribute("aria-live", "polite");
    document.body.append(toastRegion);
  }
  return toastRegion;
}

/**
 * Show a toast. type: "info" | "ok" | "err". Errors stay until dismissed; others auto-close.
 * The message is always inserted as text, never HTML. Returns a function that dismisses it.
 */
export function toast(message, { type = "info", timeout } = {}) {
  const node = el("div", `toast${type === "ok" ? " toast-ok" : type === "err" ? " toast-err" : ""}`);
  node.setAttribute("role", type === "err" ? "alert" : "status");
  node.append(el("div", "toast-msg", message));
  const close = el("button", "toast-close", "×");
  close.type = "button";
  close.setAttribute("aria-label", "Dismiss message");
  node.append(close);
  const remove = () => node.remove();
  close.addEventListener("click", remove);
  getToastRegion().append(node);
  const ms = timeout ?? (type === "err" ? 0 : 5000);
  if (ms > 0) setTimeout(remove, ms);
  return remove;
}

// ---------- Focus helpers ----------
const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

function trapTab(container, event) {
  if (event.key !== "Tab") return;
  const items = [...container.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

// ---------- Drawer (overlay) ----------
let activeDrawer = null;

/** Close the overlay drawer, if open, and return focus to the element that opened it. */
export function closeDrawer() {
  if (!activeDrawer) return;
  const { panel, scrim, onKey, opener } = activeDrawer;
  document.removeEventListener("keydown", onKey);
  panel.remove();
  scrim.remove();
  activeDrawer = null;
  if (opener && opener.isConnected) opener.focus();
}

/**
 * Open an overlay drawer. `content` is a Node or a plain-text string.
 * Returns the body element so callers can append forms.
 */
export function openDrawer({ title, subtitle = "", content = null } = {}) {
  closeDrawer();
  const opener = document.activeElement;
  const scrim = el("div", "drawer-scrim");
  const panel = el("aside", "card drawer is-overlay");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-labelledby", "drawer-title");

  const head = el("div", "drawer-head");
  const heading = el("div");
  if (subtitle) heading.append(el("div", "muted text-13", subtitle));
  const h = el("h2", "drawer-title", title || "");
  h.id = "drawer-title";
  heading.append(h);
  const closeBtn = el("button", "btn btn-o btn-icon");
  closeBtn.type = "button";
  closeBtn.setAttribute("aria-label", "Close panel");
  closeBtn.innerHTML =
    '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  head.append(heading, closeBtn);

  const body = el("div", "drawer-body");
  if (content instanceof Node) body.append(content);
  else if (content !== null) body.textContent = String(content);

  panel.append(head, body);
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      closeDrawer();
    } else {
      trapTab(panel, e);
    }
  };
  closeBtn.addEventListener("click", closeDrawer);
  scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", onKey);
  document.body.append(scrim, panel);
  activeDrawer = { panel, scrim, onKey, opener };
  closeBtn.focus();
  return body;
}

// ---------- Modal (native <dialog>) ----------
/**
 * Open a modal. Resolves to the value of the clicked button, or null on Esc / backdrop click.
 * buttons: [{ label, value, kind: "p"|"g"|"o"|"d" }]. `body` is a Node or plain text.
 */
export function openModal({ title, body = "", buttons = [{ label: "Close", value: true, kind: "o" }] } = {}) {
  return new Promise((resolve) => {
    const opener = document.activeElement;
    const dialog = el("dialog", "modal");
    dialog.setAttribute("aria-labelledby", "modal-title");
    const head = el("div", "modal-head");
    const h = el("h2", "modal-title", title || "");
    h.id = "modal-title";
    head.append(h);
    const bodyEl = el("div", "modal-body");
    if (body instanceof Node) bodyEl.append(body);
    else bodyEl.textContent = String(body);
    const foot = el("div", "modal-foot");
    let result = null;
    for (const b of buttons) {
      const btn = el("button", `btn btn-${b.kind || "o"}`, b.label);
      btn.type = "button";
      btn.addEventListener("click", () => {
        result = b.value;
        dialog.close();
      });
      foot.append(btn);
    }
    dialog.append(head, bodyEl, foot);
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", () => {
      dialog.remove();
      if (opener && opener.isConnected) opener.focus();
      resolve(result);
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}

/** Yes/No confirmation. Resolves true only when the confirm button is used. */
export async function confirmModal({ title, body, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false } = {}) {
  const result = await openModal({
    title,
    body,
    buttons: [
      { label: cancelLabel, value: false, kind: "o" },
      { label: confirmLabel, value: true, kind: danger ? "d" : "p" },
    ],
  });
  return result === true;
}
