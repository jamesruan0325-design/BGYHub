// BGYHub Admin – Purelymail Mailboxes. All server data is inserted with textContent (never innerHTML).
"use strict";

const CSRF = document.querySelector('meta[name="csrf-token"]').content;
const $ = (id) => document.getElementById(id);
let currentPlan = null;
let currentJob = null;
let pollTimer = null;

function el(tag, opts = {}, ...children) {
  const node = document.createElement(tag);
  if (opts.className) node.className = opts.className;
  if (opts.text !== undefined) node.textContent = opts.text;
  if (opts.title) node.title = opts.title;
  for (const c of children) if (c) node.append(c);
  return node;
}

function toast(message, isError = false) {
  const t = $("toast");
  t.textContent = message;
  t.classList.toggle("toast-error", isError);
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, isError ? 8000 : 4000);
}

async function reauth() {
  const dlg = $("dlg-reauth");
  $("reauth-password").value = "";
  $("reauth-error").hidden = true;
  dlg.showModal();
  return new Promise((resolve) => {
    $("reauth-form").onsubmit = async (ev) => {
      ev.preventDefault();
      try {
        await api("POST", "/api/reauth", { password: $("reauth-password").value }, false);
        dlg.close();
        resolve(true);
      } catch (e) {
        $("reauth-error").textContent = e.message;
        $("reauth-error").hidden = false;
      }
    };
    dlg.onclose = () => resolve(false);
  });
}

async function request(method, url, body, allowReauth = true) {
  const opts = { method, headers: { "X-CSRF-Token": CSRF }, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  if (resp.status === 401) { window.location = "/login"; throw new Error("Locked"); }
  if (resp.status === 428 && allowReauth) {
    if (await reauth()) return request(method, url, body, false);
    throw new Error("Cancelled");
  }
  return resp;
}

async function api(method, url, body, allowReauth = true) {
  const resp = await request(method, url, body, allowReauth);
  let data = {};
  try { data = await resp.json(); } catch (_) { /* empty body */ }
  if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status})`);
  return data;
}

async function download(url, fallbackName) {
  try {
    const resp = await request("GET", url);
    if (!resp.ok) {
      let msg = `Download failed (${resp.status})`;
      try { msg = (await resp.json()).error || msg; } catch (_) { /* not JSON */ }
      throw new Error(msg);
    }
    const disp = resp.headers.get("Content-Disposition") || "";
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disp);
    const name = match ? decodeURIComponent(match[1]) : fallbackName;
    const blobUrl = URL.createObjectURL(await resp.blob());
    const a = el("a");
    a.href = blobUrl;
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 10000);
  } catch (e) {
    if (e.message !== "Cancelled") toast(e.message, true);
  }
}

// ------------------------------------------------------------------ status + banners

function banner(kind, text, buttonLabel, onClick) {
  const b = el("div", { className: `alert alert-${kind}` }, el("span", { text }));
  if (buttonLabel) {
    const btn = el("button", { className: "btn", text: buttonLabel });
    btn.type = "button";
    btn.onclick = onClick;
    b.append(btn);
  }
  $("banners").append(b);
}

let lastStatus = null;
async function loadStatus() {
  const s = await api("GET", "/api/status");
  lastStatus = s;
  $("st-token").textContent = s.token_configured ? "✅ Configured" : "❌ Missing";
  const t = s.tracking;
  if (!t.configured) $("st-tracking").textContent = "⚠️ Not selected";
  else if (!t.valid) $("st-tracking").textContent = "❌ Problem";
  else $("st-tracking").textContent = `✅ ${t.path.split("/").pop()} (${t.rows} rows)`;
  $("st-tracking").title = t.path || "";
  $("st-pending").textContent = s.excel_pending ? `⚠️ ${s.excel_pending}` : "0";

  $("banners").replaceChildren();
  if (!s.token_configured) {
    banner("error", "The Purelymail API token is missing. Put PURELYMAIL_API_TOKEN=… in purelymail/.env and restart the app.");
  }
  if (!t.configured) {
    banner("warn", "Choose your BGYHub email tracking workbook so new mailboxes are recorded in it.", "Choose workbook", openSettings);
  } else if (!t.valid) {
    banner("error", `Tracking workbook problem: ${t.reason}`, "Settings", openSettings);
  } else if (t.open_in_excel) {
    banner("warn", "The tracking workbook is open in Excel. Close it before creating or resetting, otherwise it can't be updated.");
  }
  if (s.excel_pending) {
    banner("warn", `${s.excel_pending} change(s) are waiting to be written to the tracking workbook.`, "Retry Excel update", retryExcel);
  }
  if (s.csv_import.available) {
    banner("info", `Found ${s.csv_import.candidates.length} mailbox(es) created earlier from Terminal (${s.csv_import.candidates.join(", ")}). Import them so their passwords go into the workbook and they can be reset here.`,
      "Import now", runImport);
  }
  $("btn-create").disabled = !!s.batch_running;
}

// ------------------------------------------------------------------ mailbox list + reset

const KIND_LABEL = {
  managed: "Managed by this app", protected: "Protected 🔒", excluded: "Excluded from numbering 🔒",
  other: "Not managed", unknown: "⚠️ Creation unconfirmed",
};

async function loadMailboxes() {
  const body = $("mailboxes");
  try {
    const { mailboxes } = await api("GET", "/api/mailboxes");
    body.replaceChildren();
    if (!mailboxes.length) body.append(el("tr", {}, el("td", { text: "No mailboxes found.", className: "muted" })));
    for (const m of mailboxes) {
      const tr = el("tr");
      tr.append(el("td", { text: m.email, className: "mono" }));
      tr.append(el("td", { text: KIND_LABEL[m.kind] || m.kind, className: `kind kind-${m.kind}` }));
      tr.append(el("td", { text: m.created_at ? `${m.created_at.slice(0, 10)} (${m.source})` : "—" }));
      tr.append(el("td", { text: m.in_workbook ? (m.workbook_has_password ? "✅ with password" : "✅ (no password)") : "—" }));
      const actions = el("td", { className: "actions" });
      if (m.can_reset) {
        const b = el("button", { className: "btn btn-small", text: "Reset Password" });
        b.type = "button";
        b.onclick = () => openReset(m.email);
        actions.append(b);
      }
      tr.append(actions);
      body.append(tr);
    }
  } catch (e) {
    body.replaceChildren(el("tr", {}, el("td", { text: e.message, className: "error" })));
  }
}

function openReset(email) {
  $("reset-email").textContent = email;
  $("reset-confirm").value = "";
  $("reset-error").hidden = true;
  $("reset-ask").hidden = false;
  $("reset-done").hidden = true;
  $("reset-go").disabled = true;
  $("reset-confirm").oninput = () => {
    $("reset-go").disabled = $("reset-confirm").value.trim().toLowerCase() !== email;
  };
  $("reset-go").onclick = async () => {
    $("reset-go").disabled = true;
    $("reset-go").textContent = "Resetting…";
    try {
      const r = await api("POST", `/api/mailboxes/${encodeURIComponent(email)}/reset-password`,
        { confirm: $("reset-confirm").value });
      $("reset-ask").hidden = true;
      $("reset-done").hidden = false;
      $("reset-password").textContent = r.password;
      if (r.ok) {
        $("reset-result").textContent = `✅ New password for ${email} (shown once; it is also in the tracking workbook):`;
        $("reset-excel").textContent = excelLine(r.excel);
      } else {
        $("reset-result").textContent = `⚠️ ${r.message}`;
        $("reset-excel").textContent = "";
      }
      loadStatus();
      loadMailboxes();
    } catch (e) {
      $("reset-error").textContent = e.message;
      $("reset-error").hidden = false;
    } finally {
      $("reset-go").textContent = "Reset password";
    }
  };
  $("reset-copy").onclick = async () => {
    await navigator.clipboard.writeText($("reset-password").textContent);
    toast("Copied");
  };
  $("dlg-reset").onclose = () => { $("reset-password").textContent = ""; };
  $("dlg-reset").showModal();
}

// ------------------------------------------------------------------ preview + create

async function preview() {
  $("preview").hidden = true;
  const count = parseInt($("count").value, 10);
  $("btn-preview").disabled = true;
  try {
    currentPlan = await api("POST", "/api/preview", { count });
    $("preview-summary").textContent = currentPlan.highest_existing
      ? `Highest existing numbered mailbox: ${currentPlan.highest_existing}. These ${currentPlan.emails.length} will be created:`
      : `These ${currentPlan.emails.length} will be created:`;
    $("preview-list").replaceChildren(...currentPlan.emails.map((e) => el("li", { text: e })));
    $("btn-create").textContent = `Create ${currentPlan.emails.length} mailbox${currentPlan.emails.length > 1 ? "es" : ""}`;
    $("preview").hidden = false;
  } catch (e) {
    toast(e.message, true);
  } finally {
    $("btn-preview").disabled = false;
  }
}

function confirmCreate() {
  if (!currentPlan) return;
  const n = currentPlan.emails.length;
  $("confirm-text").textContent = `${n} new mailbox${n > 1 ? "es" : ""} will be created on Purelymail:`;
  $("confirm-list").replaceChildren(...currentPlan.emails.map((e) => el("li", { text: e })));
  $("confirm-ok").textContent = `Create ${n}`;
  const dlg = $("dlg-confirm");
  dlg.onclose = () => { if (dlg.returnValue === "ok") startCreate(); };
  dlg.returnValue = "";
  dlg.showModal();
}

async function startCreate() {
  const plan = currentPlan;
  currentPlan = null;
  $("preview").hidden = true;
  try {
    const { job_id } = await api("POST", "/api/create", { plan_id: plan.plan_id });
    currentJob = job_id;
    $("job").hidden = false;
    poll();
  } catch (e) {
    toast(e.message, true);
  }
}

const STATUS_LABEL = {
  queued: "Queued", working: "Working…", created: "✅ Created", failed: "❌ Failed",
  unknown: "⚠️ Unknown", skipped: "↷ Skipped", not_attempted: "— Not attempted",
};

function excelLine(x) {
  if (!x) return "";
  const icon = { ok: "✅", failed: "❌", not_configured: "⚠️", conflict: "⚠️", pending: "⏳" }[x.state] || "";
  let text = `Excel tracking file: ${icon} ${x.message}`;
  if (x.backup) text += ` · backup: ${x.backup.split("/").pop()}`;
  return text;
}

function renderJob(job) {
  const c = job.counts || {};
  const counters = [
    ["Created", c.created || 0, "ok"], ["Failed", c.failed || 0, "bad"], ["Unknown", c.unknown || 0, "warn"],
    ["Skipped", c.skipped || 0, ""], ["Not attempted", c.not_attempted || 0, ""],
  ];
  if (job.state === "running") counters.push(["Remaining", (c.queued || 0) + (c.working || 0), ""]);
  $("job-counters").replaceChildren(...counters.map(([label, n, cls]) =>
    el("div", { className: `counter ${n ? cls : ""}` }, el("span", { className: "counter-n", text: String(n) }),
      el("span", { className: "counter-label", text: label }))));
  const x = job.excel || {};
  $("job-excel").textContent = job.state === "running" ? excelLine(x) || "Excel tracking file: ⏳ waiting" : excelLine(x);
  $("job-excel").className = `excel-line excel-${x.state || "pending"}`;
  $("job-items").replaceChildren(...job.items.map((it) => el("tr", {},
    el("td", { text: it.email, className: "mono" }),
    el("td", { text: STATUS_LABEL[it.status] || it.status, className: `st-${it.status}` }),
    el("td", { text: it.detail || "", className: "muted" }))));
  const done = job.state === "done";
  $("btn-export").hidden = !(done && c.created);
  $("btn-retry-excel").hidden = !(done && x.state && x.state !== "ok");
}

async function poll() {
  clearTimeout(pollTimer);
  try {
    const job = await api("GET", `/api/jobs/${currentJob}`);
    renderJob(job);
    if (job.state === "running") {
      pollTimer = setTimeout(poll, 1000);
    } else {
      loadStatus();
      loadMailboxes();
    }
  } catch (e) {
    toast(e.message, true);
    pollTimer = setTimeout(poll, 3000);
  }
}

async function retryExcel() {
  try {
    const r = await api("POST", "/api/excel/sync");
    toast(excelLine(r), r.state !== "ok");
    if (currentJob && !$("job").hidden) $("job-excel").textContent = excelLine(r);
    loadStatus();
    loadMailboxes();
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------------------------ settings + import

async function openSettings() {
  const t = (lastStatus && lastStatus.tracking) || {};
  $("settings-current").textContent = t.configured ? `Current: ${t.path}${t.valid ? "" : " (" + t.reason + ")"}` : "No workbook selected yet.";
  $("settings-error").hidden = true;
  $("candidates").replaceChildren();
  const imp = lastStatus ? lastStatus.csv_import : null;
  if (imp && imp.available) {
    $("import-text").textContent = `credentials.csv has ${imp.candidates.length} mailbox(es) not yet managed here: ${imp.candidates.join(", ")}.`;
    $("btn-import").hidden = false;
  } else {
    $("import-text").textContent = imp && imp.done ? `Import done (${imp.done.slice(0, 16).replace("T", " ")}).` : "Nothing to import.";
    $("btn-import").hidden = true;
  }
  $("dlg-settings").showModal();
}

async function selectWorkbook(path) {
  try {
    const info = await api("POST", "/api/tracking/select", { path });
    toast(`Using ${info.path.split("/").pop()}`);
    $("settings-current").textContent = `Current: ${info.path}`;
    await loadStatus();
    loadMailboxes();
    if (lastStatus.excel_pending) retryExcel();
  } catch (e) {
    $("settings-error").textContent = e.message;
    $("settings-error").hidden = false;
  }
}

async function findCandidates() {
  $("btn-find").disabled = true;
  $("btn-find").textContent = "Searching Desktop, Documents, Downloads, iCloud Drive…";
  try {
    const { candidates } = await api("GET", "/api/tracking/candidates");
    const list = $("candidates");
    list.replaceChildren();
    if (!candidates.length) list.append(el("li", { text: "No matching workbook found. Paste its path below.", className: "muted" }));
    for (const c of candidates) {
      const btn = el("button", { className: "btn btn-small", text: "Use this" });
      btn.type = "button";
      btn.onclick = () => selectWorkbook(c.path);
      list.append(el("li", {}, el("div", {}, el("strong", { text: c.path.split("/").pop() }),
        el("div", { className: "muted small", text: `${c.path} · ${c.rows} rows · modified ${c.mtime.replace("T", " ")}` })), btn));
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    $("btn-find").disabled = false;
    $("btn-find").textContent = "Find my tracking workbook";
  }
}

async function runImport() {
  try {
    const r = await api("POST", "/api/import-csv");
    toast(`Imported ${r.imported.length} mailbox(es). ${excelLine(r.excel)}`, r.excel.state !== "ok");
    $("dlg-settings").close();
    loadStatus();
    loadMailboxes();
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------------------------ wiring

document.addEventListener("DOMContentLoaded", () => {
  $("btn-preview").onclick = preview;
  $("count").addEventListener("keydown", (e) => { if (e.key === "Enter") preview(); });
  $("count").addEventListener("input", () => { $("preview").hidden = true; currentPlan = null; });
  $("btn-create").onclick = confirmCreate;
  $("btn-refresh").onclick = () => { loadStatus(); loadMailboxes(); };
  $("btn-export").onclick = () => download(`/api/jobs/${currentJob}/export.csv`, "bgyhub-mailboxes.csv");
  $("btn-retry-excel").onclick = retryExcel;
  $("btn-settings").onclick = openSettings;
  $("btn-find").onclick = findCandidates;
  $("btn-use-path").onclick = () => selectWorkbook($("manual-path").value.trim());
  $("btn-import").onclick = runImport;
  $("btn-open-excel").onclick = async () => {
    try { await api("POST", "/api/tracking/open"); toast("Opening… close Excel again before creating or resetting."); }
    catch (e) { toast(e.message, true); }
  };
  $("btn-download-excel").onclick = () => download("/api/tracking/download", "tracking.xlsx");
  $("btn-lock").onclick = async () => { await api("POST", "/api/lock"); window.location = "/login"; };
  $("btn-quit").onclick = async () => {
    if (!window.confirm("Stop BGYHub Mailbox Admin? You can start it again from the Desktop icon.")) return;
    await api("POST", "/api/quit");
    document.body.replaceChildren(el("main", { className: "page" }, el("h1", { text: "BGYHub Mailbox Admin has stopped." }),
      el("p", { className: "muted", text: "You can close this tab." })));
  };
  loadStatus().catch((e) => toast(e.message, true));
  loadMailboxes();
});
