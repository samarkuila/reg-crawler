// Database admin page. Browse, edit and delete rows of any table, and reset.
// JS only calls /api/v1/admin/db and renders: the server checks the role, the locked tables and
// every value, and writes the audit event.

import { get, post, put } from "../api.js";
import { closeDrawer, escapeHtml, openDrawer, openModal, toast } from "../ui.js";

const $ = (id) => document.getElementById(id);
const PAGE = 50;
let current = null; // { table, page, offset, q }

const show = (v) => (v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));
const keyOf = (page, row) => Object.fromEntries(page.columns.filter((c) => c.primary_key).map((c) => [c.name, row[c.name]]));

async function loadTables() {
  let tables;
  try {
    tables = await get("/admin/db/tables");
  } catch {
    $("db-tables").innerHTML = '<li class="muted pad-card">The table list could not be loaded.</li>';
    return;
  }
  $("db-tables").innerHTML = tables
    .map(
      (t) =>
        `<li><button type="button" class="db-table-btn" data-table="${escapeHtml(t.name)}"` +
        `${current && current.table === t.name ? ' aria-current="true"' : ""}>` +
        `<span>${escapeHtml(t.name)}</span><span class="muted text-13">${t.rows}${t.editable ? "" : " · read only"}</span></button></li>`,
    )
    .join("");
}

function render(page) {
  $("db-title").textContent = page.table;
  const from = page.total ? current.offset + 1 : 0;
  const to = Math.min(current.offset + PAGE, page.total);
  $("db-count").textContent = `${from}–${to} of ${page.total} ${page.total === 1 ? "row" : "rows"}`;
  $("db-note").hidden = !page.reason;
  $("db-note").textContent = page.reason || "";
  $("db-add").hidden = !page.editable;
  $("db-prev").disabled = current.offset === 0;
  $("db-next").disabled = to >= page.total;
  $("db-head").innerHTML =
    "<tr>" +
    page.columns.map((c) => `<th scope="col">${escapeHtml(c.name)}${c.primary_key ? " (key)" : ""}</th>`).join("") +
    (page.editable ? '<th scope="col">Actions</th>' : "") +
    "</tr>";
  const span = page.columns.length + (page.editable ? 1 : 0);
  $("db-rows").innerHTML = page.rows.length
    ? page.rows
        .map(
          (row, i) =>
            "<tr>" +
            page.columns.map((c) => `<td class="db-cell" title="${escapeHtml(show(row[c.name]))}">${escapeHtml(show(row[c.name]))}</td>`).join("") +
            (page.editable
              ? `<td class="db-actions"><button type="button" class="btn btn-o" data-edit="${i}">Edit</button> ` +
                `<button type="button" class="btn btn-d" data-delete="${i}">Delete</button></td>`
              : "") +
            "</tr>",
        )
        .join("")
    : `<tr><td colspan="${span}"><div class="empty"><div class="empty-text">No rows.</div></div></td></tr>`;
}

async function openTable(table, offset = 0, q = "") {
  current = { table, offset, q, page: null };
  let page;
  try {
    page = await get(`/admin/db/tables/${encodeURIComponent(table)}`, { limit: PAGE, offset, q });
  } catch {
    return;
  }
  current.page = page;
  render(page);
  loadTables();
}

function control(c, id, text, long, locked) {
  if (c.choices) {
    const options = (c.nullable ? ['<option value=""></option>'] : []).concat(
      c.choices.map((v) => `<option value="${escapeHtml(v)}"${v === text ? " selected" : ""}>${escapeHtml(v)}</option>`),
    );
    return `<select id="${id}"${locked ? " disabled" : ""}>${options.join("")}</select>`;
  }
  return long
    ? `<textarea id="${id}" rows="4"${locked ? " readonly" : ""}></textarea>`
    : `<input id="${id}"${locked ? " readonly" : ""}>`;
}

function addForm(page) {
  const form = document.createElement("form");
  form.className = "stack gap-3";
  form.noValidate = true;
  for (const c of page.columns) {
    const id = `db-n-${c.name}`;
    form.insertAdjacentHTML(
      "beforeend",
      `<div class="field"><label for="${id}">${escapeHtml(c.name)} <span class="muted text-13">${escapeHtml(c.type)}${c.primary_key ? ", key (required)" : c.nullable ? ", optional" : ""}</span></label>${control(c, id, "", false, false)}</div>`,
    );
  }
  form.insertAdjacentHTML(
    "beforeend",
    '<div class="drawer-actions"><button type="submit" class="btn btn-p">Add row</button><button type="button" class="btn btn-o" data-cancel>Cancel</button></div>',
  );
  form.querySelector("[data-cancel]").addEventListener("click", closeDrawer);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const values = {};
    for (const c of page.columns) values[c.name] = form.querySelector(`#${CSS.escape(`db-n-${c.name}`)}`).value;
    try {
      await post(`/admin/db/tables/${encodeURIComponent(page.table)}/rows`, { values });
    } catch {
      return;
    }
    closeDrawer();
    toast("Row added and written to the audit log.", { type: "ok" });
    openTable(current.table, current.offset, current.q);
  });
  return form;
}

function editForm(page, row) {
  const form = document.createElement("form");
  form.className = "stack gap-3";
  form.noValidate = true;
  const initial = {};
  for (const c of page.columns) {
    const value = row[c.name];
    initial[c.name] = show(value);
    const id = `db-f-${c.name}`;
    const long = typeof value === "object" || initial[c.name].length > 60;
    const field = control(c, id, initial[c.name], long, c.primary_key);
    form.insertAdjacentHTML(
      "beforeend",
      `<div class="field"><label for="${id}">${escapeHtml(c.name)} <span class="muted text-13">${escapeHtml(c.type)}${c.primary_key ? ", key" : ""}</span></label>${field}` +
        (c.nullable && !c.primary_key
          ? `<label class="text-13"><input type="checkbox" id="${id}-null"${value === null ? " checked" : ""}> Empty (null)</label>`
          : "") +
        "</div>",
    );
    form.querySelector(`#${CSS.escape(id)}`).value = initial[c.name];
  }
  form.insertAdjacentHTML(
    "beforeend",
    '<div class="drawer-actions"><button type="submit" class="btn btn-p">Save</button><button type="button" class="btn btn-o" data-cancel>Cancel</button></div>',
  );
  form.querySelector("[data-cancel]").addEventListener("click", closeDrawer);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const changes = {};
    for (const c of page.columns) {
      if (c.primary_key) continue;
      const nullBox = form.querySelector(`#${CSS.escape(`db-f-${c.name}-null`)}`);
      const text = form.querySelector(`#${CSS.escape(`db-f-${c.name}`)}`).value;
      if (nullBox && nullBox.checked) {
        if (row[c.name] !== null) changes[c.name] = null;
      } else if (text !== initial[c.name] || row[c.name] === null) {
        changes[c.name] = text;
      }
    }
    if (!Object.keys(changes).length) {
      toast("Nothing was changed.");
      return;
    }
    try {
      await put(`/admin/db/tables/${encodeURIComponent(page.table)}/rows`, { key: keyOf(page, row), changes });
    } catch {
      return;
    }
    closeDrawer();
    toast("Row saved and written to the audit log.", { type: "ok" });
    openTable(current.table, current.offset, current.q);
  });
  return form;
}

async function deleteRow(page, row) {
  const key = keyOf(page, row);
  const body = document.createElement("div");
  body.className = "stack gap-3";
  body.innerHTML =
    `<p class="text-14">Key: <code>${escapeHtml(JSON.stringify(key))}</code>. The row is removed for good. The audit log keeps a copy of what it held.</p>` +
    '<label class="pill"><input type="checkbox" id="db-cascade" checked> Also delete the rows that point to this one</label>' +
    '<p class="muted text-13">If other rows (for example alerts or versions) still point to it, the database refuses to delete it. This removes those first. Write-once tables are never touched, and the delete stops if one of them points here.</p>';
  const result = await openModal({
    title: `Delete this row from ${page.table}?`,
    body,
    buttons: [
      { label: "Cancel", value: false, kind: "o" },
      { label: "Delete row", value: true, kind: "d" },
    ],
  });
  if (result !== true) return;
  const cascade = body.querySelector("#db-cascade").checked;
  let out;
  try {
    out = await post(`/admin/db/tables/${encodeURIComponent(page.table)}/rows/delete`, { key, cascade });
  } catch {
    return;
  }
  toast(`${out.deleted} ${out.deleted === 1 ? "row" : "rows"} deleted and written to the audit log.`, { type: "ok" });
  openTable(current.table, current.offset, current.q);
}

async function resetDb() {
  const body = document.createElement("div");
  body.className = "stack gap-2";
  body.innerHTML =
    "<p class=\"text-14\">This removes every source, item, run, document, snapshot and audit event. The old database and stored files are moved to <code>data/backups</code> first. " +
    "Users and the default settings are kept so you can still sign in.</p>" +
    '<div class="field"><label for="db-confirm">Type RESET to confirm</label><input id="db-confirm" autocomplete="off"></div>';
  const result = await openModal({
    title: "Reset the database?",
    body,
    buttons: [
      { label: "Cancel", value: false, kind: "o" },
      { label: "Reset database", value: true, kind: "d" },
    ],
  });
  if (result !== true) return;
  try {
    const out = await post("/admin/db/reset", { confirm: body.querySelector("#db-confirm").value });
    toast(`Database reset. The old data is in ${out.backup}`, { type: "ok" });
  } catch {
    return;
  }
  current = null;
  $("db-title").textContent = "Pick a table";
  $("db-count").textContent = "";
  $("db-head").innerHTML = "";
  $("db-rows").innerHTML = "";
  loadTables();
}

$("db-tables").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-table]");
  if (btn) {
    $("db-q").value = "";
    openTable(btn.dataset.table);
  }
});
$("db-rows").addEventListener("click", (e) => {
  const edit = e.target.closest("[data-edit]");
  const remove = e.target.closest("[data-delete]");
  if (!current || !current.page) return;
  if (edit) {
    const row = current.page.rows[Number(edit.dataset.edit)];
    openDrawer({ title: `Edit row in ${current.page.table}`, content: editForm(current.page, row) });
  } else if (remove) {
    deleteRow(current.page, current.page.rows[Number(remove.dataset.delete)]);
  }
});
$("db-search").addEventListener("submit", (e) => {
  e.preventDefault();
  if (current) openTable(current.table, 0, $("db-q").value.trim());
});
$("db-clear").addEventListener("click", () => {
  $("db-q").value = "";
  if (current) openTable(current.table);
});
$("db-prev").addEventListener("click", () => current && openTable(current.table, Math.max(0, current.offset - PAGE), current.q));
$("db-next").addEventListener("click", () => current && openTable(current.table, current.offset + PAGE, current.q));
$("db-add").addEventListener("click", () => {
  if (current && current.page) openDrawer({ title: `Add row to ${current.page.table}`, content: addForm(current.page) });
});
$("db-reset").addEventListener("click", resetDb);

loadTables();
