// Settings page. FR-04: every save is a new version, audited, approved by a different person.
// Presentation only: forms read and write /api/v1/settings. The services check every value
// (formats, required fields, who may approve) and answer with a plain-language reason.

import { get, put, post } from "../api.js";
import { askText, diffGridHtml, escapeHtml, formatDateTime, toast } from "../ui.js";

const $ = (id) => document.getElementById(id);
const state = { settings: {}, order: [], tab: "schedule", canEdit: false };

const DAYS = [
  ["MON", "Mon"],
  ["TUE", "Tue"],
  ["WED", "Wed"],
  ["THU", "Thu"],
  ["FRI", "Fri"],
  ["SAT", "Sat"],
  ["SUN", "Sun"],
];
const TEMPLATE_FIELDS = [
  ["subject_pattern", "Subject pattern", "Use {date} and {cycle}", 1],
  ["greeting", "Greeting", "", 1],
  ["intro", "Introduction", "", 4],
  ["disclaimer", "Disclaimer (shown in italics)", "", 3],
  ["no_update_text", "No-update sentence", "Use {date}", 2],
  ["sign_off", "Sign-off", "", 1],
  ["sender_name", "Sender name", "", 1],
];

const val = (key) => (state.settings[key] ? state.settings[key].setting.value : undefined);
const pendingOf = (key) => state.settings[key] && state.settings[key].has_pending;
const lines = (list) => (list || []).join("\n");
const splitLines = (text) =>
  text
    .split(/\r?\n/)
    .map((t) => t.trim())
    .filter(Boolean);
const dis = (extra = "") => (state.canEdit ? extra : `${extra} disabled`);

// ---------- Sections: how each group of settings is drawn and read back ----------
const SECTIONS = [
  {
    id: "schedule",
    title: "Schedule",
    keys: ["run-times", "working-days", "holidays", "lookback-days"],
    html() {
      const t = val("run-times") || {};
      const time = (id, label, v) =>
        `<div class="field"><label for="${id}">${label}</label><input id="${id}" type="time" value="${escapeHtml(v || "")}"${dis()}></div>`;
      return (
        '<h2 class="section-title">Schedule</h2><p class="muted text-14">Times are in IST.</p>' +
        '<div class="form-grid-2">' +
        time("s-morning", "Morning run", t.morning) +
        time("s-eod", "End-of-day run", t.eod) +
        time("s-ufrom", "Hourly urgent checks from", t.urgent_hourly_from) +
        time("s-uto", "Hourly urgent checks until", t.urgent_hourly_to) +
        "</div>" +
        `<fieldset><legend>Working days</legend><div class="pill-row">${DAYS.map(
          ([k, l]) =>
            `<label class="pill"><input type="checkbox" data-day="${k}"${(val("working-days") || []).includes(k) ? " checked" : ""}${dis()}>${l}</label>`,
        ).join("")}</div></fieldset>` +
        `<div class="field"><label for="s-hol">Holidays (one date per line, DD.MM.YYYY)</label><textarea id="s-hol" rows="4"${dis()}>${escapeHtml(lines(val("holidays")))}</textarea></div>` +
        `<div class="field se-narrow"><label for="s-look">Lookback days</label><input id="s-look" type="number" min="0" max="30" value="${escapeHtml(val("lookback-days") ?? "")}"${dis()}><span class="muted text-13">Days of listings each run re-reads.</span></div>`
      );
    },
    collect(form) {
      return {
        "run-times": {
          morning: form.querySelector("#s-morning").value,
          eod: form.querySelector("#s-eod").value,
          urgent_hourly_from: form.querySelector("#s-ufrom").value,
          urgent_hourly_to: form.querySelector("#s-uto").value,
        },
        "working-days": [...form.querySelectorAll("[data-day]")].filter((c) => c.checked).map((c) => c.dataset.day),
        holidays: splitLines(form.querySelector("#s-hol").value),
        "lookback-days": Number(form.querySelector("#s-look").value),
      };
    },
  },
  {
    id: "sme",
    title: "SME routing",
    keys: ["sme-routing"],
    html() {
      return (
        '<h2 class="section-title">SME routing</h2><p class="muted text-14">When a reviewer routes an item to a function, its head is copied (CC) on the email. A function cannot be deleted, only made inactive.</p>' +
        '<div class="table-wrap"><table><thead><tr><th scope="col">Function</th><th scope="col">Head of function</th><th scope="col">Head’s email</th><th scope="col">Active</th></tr></thead>' +
        `<tbody id="se-sme">${(val("sme-routing") || []).map(smeRow).join("")}</tbody></table></div>` +
        (state.canEdit ? '<div><button type="button" class="btn btn-o" data-add="sme">Add function</button></div>' : "")
      );
    },
    collect(form) {
      return {
        "sme-routing": [...form.querySelectorAll("#se-sme tr")].map((tr) => ({
          id: tr.dataset.id || "",
          name: tr.querySelector("[data-f=name]").value,
          head_name: tr.querySelector("[data-f=head_name]").value,
          head_email: tr.querySelector("[data-f=head_email]").value,
          active: tr.querySelector("[data-f=active]").checked,
        })),
      };
    },
  },
  {
    id: "recipients",
    title: "Recipients",
    keys: ["recipients"],
    html() {
      return (
        '<h2 class="section-title">Recipients</h2><p class="muted text-14">Who the daily update is addressed to (To) and copied (CC). The system never sends it; these go on the draft you download.</p>' +
        '<div class="table-wrap"><table><thead><tr><th scope="col">Name</th><th scope="col">Email</th><th scope="col">To or CC</th><th scope="col">Active</th></tr></thead>' +
        `<tbody id="se-rcp">${(val("recipients") || []).map(rcpRow).join("")}</tbody></table></div>` +
        (state.canEdit ? '<div><button type="button" class="btn btn-o" data-add="rcp">Add recipient</button></div>' : "")
      );
    },
    collect(form) {
      return {
        recipients: [...form.querySelectorAll("#se-rcp tr")].map((tr) => ({
          id: tr.dataset.id || "",
          name: tr.querySelector("[data-f=name]").value,
          email: tr.querySelector("[data-f=email]").value,
          kind: tr.querySelector("[data-f=kind]").value,
          active: tr.querySelector("[data-f=active]").checked,
        })),
      };
    },
  },
  {
    id: "email",
    title: "Email template",
    keys: ["email-template"],
    html() {
      const t = val("email-template") || {};
      return (
        '<h2 class="section-title">Email template</h2><p class="muted text-14">The wording of the daily update. The table, colour legend and layout are fixed.</p>' +
        TEMPLATE_FIELDS.map(
          ([k, label, hint, rows]) =>
            `<div class="field"><label for="t-${k}">${label}</label>` +
            (rows > 1
              ? `<textarea id="t-${k}" data-t="${k}" rows="${rows}"${dis()}>${escapeHtml(t[k] || "")}</textarea>`
              : `<input id="t-${k}" data-t="${k}" value="${escapeHtml(t[k] || "")}"${dis()}>`) +
            (hint ? `<span class="muted text-13">${escapeHtml(hint)}</span>` : "") +
            "</div>",
        ).join("")
      );
    },
    collect(form) {
      const out = {};
      for (const el of form.querySelectorAll("[data-t]")) out[el.dataset.t] = el.value;
      return { "email-template": { ...val("email-template"), ...out } };
    },
  },
  {
    id: "reasons",
    title: "Dismiss reasons",
    keys: ["dismiss-reasons"],
    html() {
      return (
        '<h2 class="section-title">Dismiss reasons</h2><p class="muted text-14">Offered when a reviewer dismisses an item. Dismissed items are never deleted.</p>' +
        `<div class="field"><label for="d-reasons">One reason per line</label><textarea id="d-reasons" rows="8"${dis()}>${escapeHtml(lines(val("dismiss-reasons")))}</textarea></div>`
      );
    },
    collect: (form) => ({ "dismiss-reasons": splitLines(form.querySelector("#d-reasons").value) }),
  },
  {
    id: "keywords",
    title: "Relevance keywords",
    keys: ["relevance-keywords", "exclusion-keywords", "low-relevance-threshold"],
    html() {
      return (
        '<h2 class="section-title">Relevance keywords</h2><p class="muted text-14">These only change how items are ranked. An item is never hidden because of a keyword.</p>' +
        `<div class="field"><label for="k-inc">Words that rank an item higher (one per line)</label><textarea id="k-inc" rows="5"${dis()}>${escapeHtml(lines(val("relevance-keywords")))}</textarea></div>` +
        `<div class="field"><label for="k-exc">Words that rank an item lower (one per line)</label><textarea id="k-exc" rows="8"${dis()}>${escapeHtml(lines(val("exclusion-keywords")))}</textarea></div>` +
        `<div class="field se-narrow"><label for="k-thr">Low-relevance threshold (0–100)</label><input id="k-thr" type="number" min="0" max="100" value="${escapeHtml(val("low-relevance-threshold") ?? "")}"${dis()}><span class="muted text-13">Items scoring below this are collapsed in the review list, never hidden.</span></div>`
      );
    },
    collect: (form) => ({
      "relevance-keywords": splitLines(form.querySelector("#k-inc").value),
      "exclusion-keywords": splitLines(form.querySelector("#k-exc").value),
      "low-relevance-threshold": Number(form.querySelector("#k-thr").value),
    }),
  },
  {
    id: "luna",
    title: "Luna (AI helper)",
    keys: ["screening-guidance", "agent-enabled", "agent-daily-budget-inr"],
    html() {
      const guidance = val("screening-guidance");
      const text = guidance && typeof guidance === "object" ? guidance.text : guidance;
      return (
        '<h2 class="section-title">Luna (AI helper)</h2>' +
        '<p class="muted text-14">Luna reads public regulator text and suggests: how relevant an item is, and values for the review form, each with an exact quote. It never decides, hides or sends anything. The system works the same with Luna off. Luna also needs AGENT_LUNA_ENABLED=true in the environment.</p>' +
        `<label class="check"><input type="checkbox" id="l-on"${val("agent-enabled") === false ? "" : " checked"}${dis()}> Luna is on</label>` +
        `<div class="field se-narrow"><label for="l-bud">Daily budget (rupees)</label><input id="l-bud" type="number" min="0" max="100000" value="${escapeHtml(val("agent-daily-budget-inr") ?? "")}"${dis()}><span class="muted text-13">When this is used up Luna pauses for the day and items carry on without suggestions.</span></div>` +
        `<div class="field"><label for="l-guid">Screening guidance (plain English)</label><textarea id="l-guid" rows="8"${dis()}>${escapeHtml(text || "")}</textarea><span class="muted text-13">Tells Luna what counts as a regulatory update for TVS Credit. Every change is versioned and needs approval from a different person.</span></div>`
      );
    },
    collect: (form) => ({
      "screening-guidance": { ...(val("screening-guidance") || {}), text: form.querySelector("#l-guid").value.trim() },
      "agent-enabled": form.querySelector("#l-on").checked,
      "agent-daily-budget-inr": Number(form.querySelector("#l-bud").value),
    }),
  },
];

const input = (field, value, extra = "") =>
  `<input data-f="${field}" value="${escapeHtml(value ?? "")}" aria-label="${field.replace("_", " ")}"${dis(extra)}>`;

function smeRow(f = {}) {
  return (
    `<tr data-id="${escapeHtml(f.id || "")}"><td>${input("name", f.name)}</td><td>${input("head_name", f.head_name)}</td>` +
    `<td>${input("head_email", f.head_email, ' type="email"')}</td>` +
    `<td><input type="checkbox" data-f="active" aria-label="Active"${f.active === false ? "" : " checked"}${dis()}></td></tr>`
  );
}

function rcpRow(r = {}) {
  const kind = r.kind || "TO";
  return (
    `<tr data-id="${escapeHtml(r.id || "")}"><td>${input("name", r.name)}</td><td>${input("email", r.email, ' type="email"')}</td>` +
    `<td><select data-f="kind" aria-label="To or CC"${dis()}><option value="TO"${kind === "TO" ? " selected" : ""}>To</option><option value="CC"${kind === "CC" ? " selected" : ""}>CC</option></select></td>` +
    `<td><input type="checkbox" data-f="active" aria-label="Active"${r.active === false ? "" : " checked"}${dis()}></td></tr>`
  );
}

// ---------- Loading and rendering ----------
async function load() {
  const list = await get("/settings");
  state.settings = Object.fromEntries(list.map((v) => [v.setting.key, v]));
  state.order = list.map((v) => v.setting.key);
  state.canEdit = list.some((v) => v.can_edit);
  renderPending();
  renderTabs();
  renderPanel();
}

function renderPending() {
  const pending = state.order.map((k) => state.settings[k]).filter((v) => v.has_pending);
  $("se-pending").innerHTML = pending
    .map((v) => {
      const p = v.setting.pending;
      return (
        `<section class="card pad-card stack gap-3 card-pending" aria-label="Pending approval" data-key="${escapeHtml(v.setting.key)}">` +
        '<div class="row row-between gap-2"><div class="row gap-2">' +
        `<span class="b b-mod">Pending approval</span><h2 class="section-title-sm">${escapeHtml(v.title)} — version ${p.version}</h2></div>` +
        `<span class="muted text-13">Requested by ${escapeHtml(p.requested_by)} · ${escapeHtml(formatDateTime(p.requested_at))}</span></div>` +
        diffGridHtml(v.changes, `Now (v${v.setting.version})`, `After (v${p.version})`) +
        '<div class="row gap-2">' +
        `<button type="button" class="btn btn-g" data-approve="${escapeHtml(v.setting.key)}"${v.can_decide ? "" : " disabled"}>Approve</button>` +
        `<button type="button" class="btn btn-d" data-reject="${escapeHtml(v.setting.key)}"${v.can_decide ? "" : " disabled"}>Reject</button></div>` +
        (v.decide_note ? `<div class="muted text-13" role="note">${escapeHtml(v.decide_note)}</div>` : "") +
        "</section>"
      );
    })
    .join("");
}

function renderTabs() {
  $("se-tabs").innerHTML = SECTIONS.map((s) => {
    const waiting = s.keys.some(pendingOf);
    return `<button type="button" role="tab" class="tab${s.id === state.tab ? " is-on" : ""}" id="tab-${s.id}" data-tab="${s.id}" aria-selected="${s.id === state.tab}" aria-controls="se-panel">${escapeHtml(s.title)}${waiting ? ' <span class="b b-mod">Pending</span>' : ""}</button>`;
  }).join("");
}

function renderPanel() {
  const section = SECTIONS.find((s) => s.id === state.tab);
  const form = $("se-panel");
  form.setAttribute("aria-labelledby", `tab-${section.id}`);
  const waiting = section.keys.filter(pendingOf);
  const meta = section.keys
    .map((k) => state.settings[k])
    .filter(Boolean)
    .map((v) => `v${v.setting.version} · ${v.setting.updated_by}`)
    .join(" · ");
  form.innerHTML =
    section.html() +
    (waiting.length
      ? '<div class="alert"><div class="alert-body">A change is already waiting for approval (see the top of the page). Approve or reject it before saving another.</div></div>'
      : "") +
    (state.canEdit
      ? `<div class="row gap-2"><button type="submit" class="btn btn-g"${waiting.length ? " disabled" : ""}>Save for approval</button><span class="muted text-13">${escapeHtml(meta)}</span></div>`
      : '<p class="muted text-14" role="note">Your role cannot change settings. Ask an admin.</p>');
}

// ---------- Actions ----------
async function save() {
  const section = SECTIONS.find((s) => s.id === state.tab);
  const values = section.collect($("se-panel"));
  const changed = Object.entries(values).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(val(k)));
  if (!changed.length) return void toast("Nothing changed.");
  let saved = 0;
  for (const [key, value] of changed) {
    try {
      await put(`/settings/${encodeURIComponent(key)}`, { value });
      saved += 1;
    } catch {
      break; // api() showed the reason; stop so nothing is half-saved silently
    }
  }
  if (saved) toast(`Saved ${saved} ${saved === 1 ? "change" : "changes"} for approval by a different person.`, { type: "ok" });
  await load();
}

async function decide(key, kind) {
  try {
    if (kind === "approve") {
      await post(`/settings/${encodeURIComponent(key)}/approve`);
      toast(`${state.settings[key].title} approved and now in use.`, { type: "ok" });
    } else {
      const reason = await askText({
        title: "Reject this change?",
        intro: "The current value stays in use. The reason is recorded in the audit log.",
        label: "Reason for rejecting",
        confirmLabel: "Reject",
        danger: true,
      });
      if (!reason) return;
      await post(`/settings/${encodeURIComponent(key)}/reject`, { reason });
      toast(`${state.settings[key].title} change rejected.`, { type: "ok" });
    }
    await load();
  } catch {
    /* api() already showed the reason */
  }
}

// ---------- Wiring ----------
document.addEventListener("click", (e) => {
  const t = e.target instanceof Element ? e.target : null;
  if (!t) return;
  const tab = t.closest("[data-tab]");
  if (tab) {
    state.tab = tab.dataset.tab;
    renderTabs();
    renderPanel();
    return void $(`tab-${state.tab}`).focus();
  }
  const add = t.closest("[data-add]");
  if (add) {
    const body = $(add.dataset.add === "sme" ? "se-sme" : "se-rcp");
    body.insertAdjacentHTML("beforeend", add.dataset.add === "sme" ? smeRow() : rcpRow());
    return void body.lastElementChild.querySelector("input").focus();
  }
  const approve = t.closest("[data-approve]");
  if (approve) return void decide(approve.dataset.approve, "approve");
  const reject = t.closest("[data-reject]");
  if (reject) return void decide(reject.dataset.reject, "reject");
});

$("se-panel").addEventListener("submit", (e) => {
  e.preventDefault();
  save();
});

$("se-tabs").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const at = SECTIONS.findIndex((s) => s.id === state.tab);
  const next = SECTIONS[(at + (e.key === "ArrowRight" ? 1 : SECTIONS.length - 1)) % SECTIONS.length];
  state.tab = next.id;
  renderTabs();
  renderPanel();
  $(`tab-${next.id}`).focus();
});

load().catch(() => {
  /* api() already showed the error */
});
