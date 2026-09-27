"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const token = document.querySelector('meta[name="review-token"]').content;
  const fields = {
    invoice_number: $("invoice-number"), supplier: $("supplier"),
    invoice_date: $("invoice-date"), currency: $("currency"), total: $("total"),
  };
  const fieldLabels = {invoice_number: "Invoice number", supplier: "Supplier", invoice_date: "Invoice date", currency: "Currency", total: "Total"};
  const hardErrors = {
    unreadable_pdf: "This PDF could not be read. Inspect the original file, then reject this record and import a readable replacement.",
    encrypted_pdf: "This PDF is password protected. Reject this record and import an unlocked copy to extract its details.",
    no_text: "At least one page has no selectable text. This version cannot approve scans or partially unreadable documents. Reject this record and import a selectable-text copy.",
  };
  const state = {batch: null, batches: [], selected: null, filter: "all", busy: false, baseline: ""};
  const encode = encodeURIComponent;
  const batchURL = () => `/api/batches/${encode(state.batch.id)}`;
  const selectedRecord = () => state.batch?.records.find((record) => record.id === state.selected);
  const visibleRecords = () => (state.batch?.records || []).filter((record) => state.filter === "all" || record.status === state.filter);
  const values = () => Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value]));
  const editorSnapshot = () => JSON.stringify({fields: values(), note: $("review-note").value});
  const dirty = () => Boolean(selectedRecord() && state.baseline && editorSnapshot() !== state.baseline);

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function feedback(message, kind = "") {
    $("feedback").textContent = message;
    $("feedback").className = `feedback ${kind}`;
    $("feedback").setAttribute("role", kind === "error" ? "alert" : "status");
  }

  function allowDiscard() {
    return !dirty() || window.confirm("You have unsaved changes to this invoice. Discard them and continue?");
  }

  function dateTime(value) {
    if (!value) return "";
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
  }

  function amount(value) {
    const parts = String(value).split(".");
    return parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (parts.length > 1 ? `.${parts[1]}` : "");
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.method === "POST") headers.set("X-Review-Token", token);
    const response = await fetch(path, {...options, headers, credentials: "same-origin"});
    let data;
    try { data = await response.json(); }
    catch { throw new Error("The server returned an unreadable response. Your edits are still here; try again."); }
    if (!response.ok) throw new Error(data.error || "The request could not be completed. Your edits are still here.");
    return data;
  }

  function post(path, body) {
    return api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  }

  function renderControls() {
    const record = selectedRecord();
    const pending = record?.status === "pending";
    const blocked = Boolean(record && hardErrors[record.code]);
    $("pdf-files").disabled = state.busy;
    $("upload-button").disabled = state.busy || !$("pdf-files").files.length;
    $("demo-button").disabled = state.busy;
    $("batch-select").disabled = state.busy || !state.batches.length;
    $("status-filter").disabled = state.busy;
    $("approve-ready-button").disabled = state.busy || !state.batch?.counts.ready;
    $("export-button").disabled = state.busy || !state.batch || state.batch.counts.pending !== 0;
    $("approve-button").hidden = !pending;
    $("reject-button").hidden = !pending;
    $("reopen-button").hidden = !record || pending;
    $("approve-button").disabled = state.busy || !pending || blocked;
    $("reject-button").disabled = state.busy || !pending;
    $("reopen-button").disabled = state.busy || !record || pending;
    for (const input of Object.values(fields)) {
      input.disabled = state.busy || !pending || blocked;
    }
    $("review-note").disabled = state.busy || !pending;
    $("unsaved-notice").hidden = !dirty();
    $("reset-edits").disabled = state.busy;
    for (const button of $("document-list").querySelectorAll("button")) button.disabled = state.busy;
    $("review-workspace").setAttribute("aria-busy", String(state.busy));
  }

  async function run(operation, loadingMessage) {
    if (state.busy) return;
    state.busy = true;
    renderControls();
    feedback(loadingMessage, "loading");
    try { await operation(); }
    catch (error) { feedback(error.message || "The request failed. Please try again.", "error"); }
    finally { state.busy = false; renderControls(); }
  }

  function renderBatchOptions() {
    $("batch-select").replaceChildren();
    if (!state.batches.length) $("batch-select").append(element("option", "", "No batches yet"));
    for (const batch of state.batches) {
      const count = batch.records?.length ?? ((batch.counts?.pending || 0) + (batch.counts?.approved || 0) + (batch.counts?.rejected || 0));
      const option = element("option", "", `${dateTime(batch.created_at) || "Saved batch"} · ${count} files · ${batch.id.slice(0, 6)}`);
      option.value = batch.id;
      $("batch-select").append(option);
    }
    if (state.batch) $("batch-select").value = state.batch.id;
  }

  function applyBatch(batch, preferred = state.selected) {
    state.batch = batch;
    const index = state.batches.findIndex((item) => item.id === batch.id);
    if (index < 0) state.batches.unshift(batch);
    else state.batches[index] = batch;
    const records = visibleRecords();
    state.selected = records.some((record) => record.id === preferred) ? preferred : records[0]?.id || null;
    try { localStorage.setItem("invoice-review:selectedBatch", batch.id); } catch { /* Storage may be unavailable in private browsing. */ }
    renderBatchOptions();
    renderOverview();
    renderDocuments();
    renderEditor();
    renderExports();
    renderControls();
  }

  function renderOverview() {
    const batch = state.batch;
    $("batch-overview").hidden = !batch;
    $("empty-workspace").hidden = Boolean(batch);
    $("review-workspace").hidden = !batch;
    $("exports-panel").hidden = !batch;
    if (!batch) return;
    $("batch-meta").textContent = `Imported ${dateTime(batch.created_at)}. Review decisions are saved locally.`;
    $("batch-revision").textContent = `${batch.records.length} documents · Revision ${batch.revision}`;
    for (const status of ["pending", "approved", "rejected"]) $(`${status}-count`).textContent = batch.counts[status];
    $("spending").replaceChildren();
    for (const [currency, summary] of Object.entries(batch.summary?.currencies || {})) {
      const chip = element("div", "spend-chip");
      chip.append(element("span", "", currency), element("strong", "", amount(summary.total)));
      $("spending").append(chip);
    }
    if (!$("spending").children.length) $("spending").append(element("span", "small-note", "No approved spending yet."));
    $("ready-hint").hidden = false;
    $("ready-hint").textContent = batch.counts.ready
      ? `${batch.counts.ready} pending invoices passed automatic validation. You can approve them together after checking the documents.`
      : "Resolve the flagged records individually. Every decision is recorded in the batch history.";
  }

  function renderDocuments() {
    $("document-list").replaceChildren();
    const records = visibleRecords();
    $("document-count").textContent = state.batch?.records.length || 0;
    $("no-matches").hidden = Boolean(records.length);
    for (const record of records) {
      const button = element("button", "document-button");
      button.type = "button";
      button.setAttribute("aria-current", String(record.id === state.selected));
      button.append(element("strong", "", record.source_file));
      button.append(element("span", "document-subtitle", record.fields?.supplier || (record.code ? "Needs attention" : "Invoice record")));
      button.append(element("span", `status-badge ${record.status}`, record.status === "pending" ? "Pending review" : record.status === "approved" ? "Approved" : "Rejected"));
      button.addEventListener("click", () => {
        if (state.busy || state.selected === record.id || !allowDiscard()) return;
        state.selected = record.id;
        renderDocuments();
        renderEditor();
        renderControls();
      });
      $("document-list").append(button);
    }
  }

  function renderEditor() {
    const record = selectedRecord();
    $("record-detail").hidden = !record;
    $("empty-selection").hidden = Boolean(record);
    if (!record) { state.baseline = ""; $("pdf-preview").removeAttribute("src"); return; }
    $("record-status").className = `status-badge ${record.status}`;
    $("record-status").textContent = record.status === "pending" ? "Pending" : record.status === "approved" ? "Approved" : "Rejected";
    $("record-source").textContent = record.source_file;
    $("record-layout").textContent = record.layout ? `Record layout: ${record.layout}` : "No supported layout detected";
    $("record-lock-hint").hidden = record.status === "pending";
    const pdf = `${batchURL()}/pdf/${encode(record.id)}`;
    $("open-pdf").href = pdf;
    $("open-pdf").setAttribute("aria-label", `Open original PDF: ${record.source_file}`);
    const blocked = Boolean(hardErrors[record.code]);
    $("pdf-preview").hidden = blocked;
    $("preview-unavailable").hidden = !blocked;
    if (blocked) {
      $("pdf-preview").removeAttribute("src");
      $("preview-unavailable-text").textContent = hardErrors[record.code];
    } else if ($("pdf-preview").getAttribute("src") !== pdf) $("pdf-preview").src = pdf;
    $("pdf-preview").title = `Invoice PDF: ${record.source_file}`;
    $("issue-box").hidden = !record.code;
    $("issue-title").textContent = record.code ? record.code.replaceAll("_", " ") : "";
    $("issue-message").textContent = record.message || "";
    for (const [field, input] of Object.entries(fields)) input.value = record.fields?.[field] ?? "";
    $("review-note").value = record.note || "";
    $("original-fields").replaceChildren();
    for (const [field, label] of Object.entries(fieldLabels)) {
      $("original-fields").append(element("dt", "", label), element("dd", "", record.original_fields?.[field] || "Not extracted"));
    }
    $("extracted-text").textContent = record.extracted_text || "No document text is available.";
    $("decision-history").replaceChildren();
    const history = record.history || [];
    $("history-title").textContent = `Decision history${history.length ? ` (${history.length})` : ""}`;
    $("no-history").hidden = Boolean(history.length);
    for (const decision of [...history].reverse()) {
      const item = element("li");
      const time = element("time", "", dateTime(decision.timestamp));
      if (decision.timestamp) time.dateTime = decision.timestamp;
      const action = {approve: "Approved", reject: "Rejected", reopen: "Reopened"}[decision.action] || decision.action;
      item.append(element("strong", "", action), time, element("p", "", decision.note || "No note added."));
      $("decision-history").append(item);
    }
    state.baseline = editorSnapshot();
  }

  function renderExports() {
    if (!state.batch) return;
    const pending = state.batch.counts.pending;
    $("export-hint").textContent = pending
      ? `${pending} invoice${pending === 1 ? " is" : "s are"} still pending. Approve or reject every record to create a report.`
      : "Create a saved report of approved invoices, with rejected records and review decisions included for traceability.";
    $("export-list").replaceChildren();
    const snapshots = state.batch.exports || [];
    $("no-exports").hidden = Boolean(snapshots.length);
    for (const snapshot of [...snapshots].reverse()) {
      const old = snapshot.revision !== state.batch.revision;
      const card = element("article", `export-snapshot${old ? " old" : ""}`);
      const heading = element("div", "snapshot-heading");
      heading.append(element("strong", "", `Report snapshot · Revision ${snapshot.revision}`), element("span", "", dateTime(snapshot.created_at)));
      card.append(heading, element("p", "snapshot-label", old ? "Earlier revision — later review decisions are not included." : "Current revision — matches the saved review decisions."));
      const links = element("nav", "export-links");
      links.setAttribute("aria-label", `Downloads for revision ${snapshot.revision}`);
      for (const [name, label] of [["dashboard.html", "View dashboard ↗"], ["invoices.csv", "Invoice CSV"], ["spend_report.xlsx", "Excel workbook"], ["review.csv", "Rejected records"], ["review_decisions.json", "Decision audit"], ["download.zip", "Download all (.zip)"]]) {
        const link = element("a", "", label);
        link.href = `${batchURL()}/exports/${encode(snapshot.id)}/${name}`;
        if (name === "dashboard.html") { link.target = "_blank"; link.rel = "noopener"; }
        links.append(link);
      }
      card.append(links);
      $("export-list").append(card);
    }
  }

  $("pdf-files").addEventListener("change", renderControls);
  $("upload-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("pdf-files").files.length || !allowDiscard()) return;
    const form = new FormData();
    for (const file of $("pdf-files").files) form.append("files", file);
    run(async () => {
      const batch = await api("/api/batches", {method: "POST", body: form});
      state.filter = "all"; $("status-filter").value = "all";
      applyBatch(batch, null);
      $("upload-form").reset();
      feedback(`Imported ${batch.records.length} invoices. Review each record before exporting.`, "success");
    }, "Importing and extracting your invoices…");
  });

  $("demo-button").addEventListener("click", () => {
    if (!allowDiscard()) return;
    run(async () => {
      const batch = await post("/api/demo", {});
      state.filter = "all"; $("status-filter").value = "all";
      applyBatch(batch, null);
      feedback("Demo batch ready. These fictional invoices include intentional problems for you to review.", "success");
    }, "Preparing the fictional demo invoices…");
  });

  $("batch-select").addEventListener("change", (event) => {
    const id = event.target.value;
    if (!allowDiscard()) { event.target.value = state.batch?.id || ""; return; }
    run(async () => {
      try {
        const batch = await api(`/api/batches/${encode(id)}`);
        state.filter = "all"; $("status-filter").value = "all";
        applyBatch(batch, null);
        feedback("Saved batch loaded. Your previous review decisions are available below.");
      } catch (error) { $("batch-select").value = state.batch?.id || ""; throw error; }
    }, "Loading the saved batch…");
  });

  $("status-filter").addEventListener("change", (event) => {
    if (!allowDiscard()) { event.target.value = state.filter; return; }
    state.filter = event.target.value;
    applyBatch(state.batch);
  });
  $("review-form").addEventListener("input", renderControls);
  $("reset-edits").addEventListener("click", () => { renderEditor(); renderControls(); });

  async function recordAction(action) {
    const record = selectedRecord();
    if (!record || state.busy || (action === "approve" && hardErrors[record.code])) return;
    const edited = values();
    const note = $("review-note").value;
    const changed = Object.entries(edited).some(([key, value]) => value !== (record.fields?.[key] ?? ""));
    if (action === "approve" && !$("review-form").reportValidity()) return;
    if ((action === "reject" || (action === "approve" && (changed || record.code))) && !note.trim()) {
      feedback(action === "reject" ? "Add a decision note explaining why this invoice should be rejected." : "Add a decision note explaining your corrections or why this flagged invoice should be approved.", "error");
      $("review-note").focus(); return;
    }
    await run(async () => {
      const batch = await post(`${batchURL()}/records/${encode(record.id)}`, {action, fields: edited, note, revision: state.batch.revision});
      applyBatch(batch, record.id);
      const message = {approve: "Approved. This invoice now contributes to spending.", reject: "Rejected. This invoice is excluded from spending.", reopen: "Reopened for review. Check its details before approving again."}[action];
      feedback(message, "success");
    }, "Saving your review decision…");
  }

  $("review-form").addEventListener("submit", (event) => { event.preventDefault(); recordAction("approve"); });
  $("reject-button").addEventListener("click", () => recordAction("reject"));
  $("reopen-button").addEventListener("click", () => recordAction("reopen"));
  $("approve-ready-button").addEventListener("click", () => {
    if (!allowDiscard()) return;
    const count = state.batch.counts.ready;
    if (!window.confirm(`Approve ${count} pending invoices that passed automatic validation? Their saved extracted values will be included in spending.`)) return;
    run(async () => {
      applyBatch(await post(`${batchURL()}/approve-ready`, {revision: state.batch.revision}));
      feedback(`Approved ${count} validated invoices. Flagged documents still need an individual decision.`, "success");
    }, "Approving validated invoices…");
  });

  $("export-button").addEventListener("click", () => {
    run(async () => {
      const result = await post(`${batchURL()}/export`, {revision: state.batch.revision});
      state.batch.exports = state.batch.exports || [];
      if (!state.batch.exports.some((item) => item.id === result.export_id)) state.batch.exports.push({id: result.export_id, revision: result.revision});
      renderExports();
      try { applyBatch(await api(batchURL())); }
      catch {
        feedback("The report snapshot was created, but the batch could not be refreshed. Your download links are available below; reload the page to refresh the saved batch.", "error");
        return;
      }
      feedback(`Report snapshot created for revision ${result.revision}. Open the dashboard or download the reports below.`, "success");
      $("exports-panel").scrollIntoView({behavior: "auto", block: "nearest"});
    }, "Creating the approved report snapshot…");
  });

  window.addEventListener("beforeunload", (event) => { if (dirty()) { event.preventDefault(); event.returnValue = ""; } });
  run(async () => {
    const response = await api("/api/batches");
    state.batches = response.batches;
    renderBatchOptions();
    let saved;
    try { saved = localStorage.getItem("invoice-review:selectedBatch"); } catch { /* The server remains the source of saved batches. */ }
    const id = state.batches.find((batch) => batch.id === saved)?.id || state.batches[0]?.id;
    if (id) {
      applyBatch(await api(`/api/batches/${encode(id)}`), null);
      feedback("Saved batch loaded. Continue reviewing or import a new batch.");
    } else feedback("Upload your invoices or try the demo to get started.");
  }, "Loading saved batches…");
})();
