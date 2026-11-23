// Demo wiring for /styleguide only.
import { get } from "../api.js";
import { confirmModal, escapeHtml, formatDate, openDrawer, toast } from "../ui.js";

const on = (name, handler) => {
  const node = document.querySelector(`[data-sg="${name}"]`);
  if (node) node.addEventListener("click", handler);
};

document.querySelector('[data-sg="form"]')?.addEventListener("submit", (e) => e.preventDefault());

on("toast-info", () => toast("Run started. You can keep working."));
on("toast-ok", () => toast("Decision saved.", { type: "ok" }));
on("toast-err", () => toast("Could not save. Please try again.", { type: "err" }));
on("api-error", () => get("/styleguide-missing").catch(() => {}));

on("drawer", () => {
  const body = document.createElement("p");
  body.className = "text-14";
  body.textContent = "Overlay drawer. Press Escape, use the close button, or click outside to close.";
  openDrawer({ title: "Drawer title", subtitle: "Reviewing · TVSC-REG-2026-000214", content: body });
});

on("modal", async () => {
  const ok = await confirmModal({
    title: "Dismiss this item?",
    body: "The item stays in the audit trail and can be found in item history.",
    confirmLabel: "Dismiss",
    danger: true,
  });
  toast(ok ? "Item dismissed (demo)." : "Cancelled.", { type: ok ? "ok" : "info" });
});

document.querySelector('[data-sg="date"]').textContent = `${formatDate("2026-11-20")} / ${formatDate(new Date(2026, 10, 3))}`;
document.querySelector('[data-sg="escaped"]').textContent = escapeHtml('<b onclick="x()">Tom & "Jerry"</b>');
