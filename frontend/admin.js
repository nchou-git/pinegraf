const loginCard = document.getElementById("login-card");
const adminCard = document.getElementById("admin-card");
const pwInput = document.getElementById("pw");
const loginErr = document.getElementById("login-err");
const loginBtn = document.getElementById("login-btn");
const logoutBtn = document.getElementById("logout-btn");
const runPipelineBtn = document.getElementById("run-pipeline-btn");
const cancelPipelineBtn = document.getElementById("cancel-pipeline-btn");
const crawlBtn = document.getElementById("crawl-btn");
const previewBtn = document.getElementById("preview-btn");
const parseBtn = document.getElementById("parse-btn");
const auditBtn = document.getElementById("audit-btn");
const stopBtn = document.getElementById("stop-btn");
const resetExtractionBtn = document.getElementById("reset-extraction-btn");
const logEl = document.getElementById("log");
const progressWrap = document.getElementById("progress-wrap");
const progressLabel = document.getElementById("progress-label");
const progressCount = document.getElementById("progress-count");
const progressFill = document.getElementById("progress-fill");
const progressUsage = document.getElementById("progress-usage");
const previewResult = document.getElementById("preview-result");
const auditResult = document.getElementById("audit-result");
const usageTotal = document.getElementById("usage-total");
const usageModels = document.getElementById("usage-models");
const parseFilterForm = document.getElementById("parse-filter-form");
const parseUrlPattern = document.getElementById("parse-url-pattern");
const parseKeywords = document.getElementById("parse-keywords");
const parseLimit = document.getElementById("parse-limit");
const pipelineBanner = document.getElementById("pipeline-banner");
const pipelineStatusLine = document.getElementById("pipeline-status-line");
const statPagesCrawled = document.getElementById("stat-pages-crawled");
const statPagesParsed = document.getElementById("stat-pages-parsed");
const statEntities = document.getElementById("stat-entities");
const statConnections = document.getElementById("stat-connections");
const dbState = document.getElementById("db-state");
const USAGE_POLL_INTERVAL_MS = 3000;
const USAGE_IDLE_STOP_MS = 30000;

let evtSource = null;
let activePipelineRunId = null;
let cancelRequested = false;
let maxPipelineCostUsd = 10;
let currentStageLabel = "";
let estimatedNextRunCost = 0;
let usagePollTimer = null;
let usageIdleTimer = null;
let usagePollingActive = false;
let lastRenderedUsageDollars = null;

// ---------- formatting ----------

function formatNumber(value) {
  return new Intl.NumberFormat("en-US").format(value ?? 0);
}

function formatMoney(value) {
  return `$${Number(value ?? 0).toFixed(4)}`;
}

function relativeTime(value) {
  if (!value) return "unknown";
  const elapsed = Math.max(0, Date.now() - new Date(value).getTime());
  const minutes = Math.floor(elapsed / 60000);
  if (minutes < 1) return "just now";
  if (minutes === 1) return "1 min ago";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours === 1) return "1 hour ago";
  return `${hours} hours ago`;
}

function appendLog(text, cls = "log-evt") {
  const div = document.createElement("div");
  div.className = cls;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatusLine(text, isError = false) {
  pipelineStatusLine.hidden = !text;
  pipelineStatusLine.textContent = text;
  pipelineStatusLine.classList.toggle("err", Boolean(isError));
}

function setBanner(text) {
  pipelineBanner.textContent = `Status: ${text}`;
}

function setRunning(running) {
  runPipelineBtn.disabled = running;
  runPipelineBtn.textContent = running ? "Pipeline running" : "Run pipeline";
  cancelPipelineBtn.hidden = !running;
  crawlBtn.disabled = running;
  previewBtn.disabled = running;
  parseBtn.disabled = running;
  auditBtn.disabled = running;
  stopBtn.disabled = !running;
  if (running) startUsagePolling();
  else markUsageIdle();
}

function showProgress(label) {
  currentStageLabel = label;
  progressWrap.hidden = false;
  progressLabel.textContent = label;
  progressCount.textContent = "";
  progressUsage.textContent = "";
  progressFill.style.width = "0%";
  progressFill.classList.remove("done", "err");
  setBanner(`Pipeline running: ${label}`);
}

function updateProgress(ev) {
  const done = ev.overall_done ?? ev.page_done;
  const total = ev.overall_total ?? ev.page_total;
  if (total && done !== undefined) {
    const pct = Math.min(100, Math.round((done / total) * 100));
    progressFill.style.width = `${pct}%`;
    progressCount.textContent = `${formatNumber(done)} / ${formatNumber(total)}`;
  }
  if (ev.kind === "done") {
    progressFill.style.width = "100%";
    progressFill.classList.add(ev.error ? "err" : "done");
    progressLabel.textContent = ev.error ? `${currentStageLabel} failed` : `${currentStageLabel} complete`;
  }
}

function updateRunUsage(ev) {
  progressUsage.textContent =
    `${formatNumber(ev.calls)} calls | ${formatNumber(ev.total_tokens)} tokens | ` +
    `${formatMoney(ev.dollars)}`;
}

function renderUsageResources(data) {
  const totals = data.totals || data;
  const dollars = Number(totals.dollars ?? 0);
  usageTotal.textContent =
    `${formatNumber(totals.calls)} calls | ` +
    `${formatNumber(totals.total_tokens)} tokens | ${formatMoney(dollars)}`;
  const byModel =
    data.by_model ||
    (data.by_day_model || []).reduce((acc, row) => {
      acc[row.model] = (acc[row.model] || 0) + row.dollars;
      return acc;
    }, {});
  usageModels.textContent = Object.entries(byModel)
    .map(([model, modelDollars]) => `${model}: ${formatMoney(modelDollars)}`)
    .join(" | ");
  if (lastRenderedUsageDollars !== null && dollars > lastRenderedUsageDollars) {
    usageTotal.classList.remove("flash");
    void usageTotal.offsetWidth;
    usageTotal.classList.add("flash");
  }
  lastRenderedUsageDollars = dollars;
}

function startUsagePolling() {
  if (document.hidden) return;
  clearTimeout(usageIdleTimer);
  usageIdleTimer = null;
  if (usagePollingActive) return;
  usagePollingActive = true;
  pollUsageLive();
  usagePollTimer = window.setInterval(pollUsageLive, USAGE_POLL_INTERVAL_MS);
}

function markUsageIdle() {
  clearTimeout(usageIdleTimer);
  usageIdleTimer = window.setTimeout(stopUsagePolling, USAGE_IDLE_STOP_MS);
}

function stopUsagePolling() {
  usagePollingActive = false;
  if (usagePollTimer !== null) {
    window.clearInterval(usagePollTimer);
    usagePollTimer = null;
  }
  if (usageIdleTimer !== null) {
    window.clearTimeout(usageIdleTimer);
    usageIdleTimer = null;
  }
}

async function pollUsageLive() {
  if (!usagePollingActive || document.hidden) return;
  try {
    const res = await fetch("/admin/usage/live");
    if (res.status === 401) {
      stopUsagePolling();
      showLogin();
      return;
    }
    if (!res.ok) return;
    renderUsageResources(await res.json());
  } catch {
    return;
  }
}

function formatEvent(ev) {
  switch (ev.kind) {
    case "crawl_start":
      return `crawl_start (max ${ev.max_pages ?? "?"} pages)`;
    case "sitemap_fetch":
      return `sitemap: ${ev.url}`;
    case "sitemap_failed":
      return `sitemap failed: ${ev.url} ${ev.status ?? ev.error ?? ""}`;
    case "crawl_planned":
      return `planned ${ev.overall_total} URLs`;
    case "page_fetched":
      return `fetched ${ev.url}`;
    case "page_skipped":
      return `skipped ${ev.url} (${ev.reason})`;
    case "page_failed":
      return `failed ${ev.url}`;
    case "page_error":
      return `ERROR ${ev.url}: ${ev.error}`;
    case "parse_start":
      return `parse_start: ${ev.page_total ?? "?"} pages`;
    case "page_parsed":
      return `parsed ${ev.url}`;
    case "chunk_done":
      return `chunk ${ev.chunk_index ?? "?"} parsed ${ev.url}`;
    case "chunk_skipped_cache":
      return `chunk ${ev.chunk_index ?? "?"} cache ${ev.url}`;
    case "chunk_skipped_triage":
      return `chunk ${ev.chunk_index ?? "?"} skipped ${ev.url}`;
    case "chunk_escalated":
      return `chunk ${ev.chunk_index ?? "?"} escalated ${ev.url}`;
    case "rate_limit_pause":
      return `rate_limit_pause ${ev.pause_seconds ?? "?"}s`;
    case "usage_tick":
      return `usage ${formatNumber(ev.calls)} calls ${formatMoney(ev.dollars)}`;
    case "done":
      return ev.error
        ? `ERROR: ${ev.error}`
        : `complete: ${ev.overall_done ?? 0}/${ev.overall_total ?? 0}`;
    default:
      return JSON.stringify(ev);
  }
}

// ---------- auth and dashboard ----------

function showLogin() {
  loginCard.hidden = false;
  adminCard.hidden = true;
  pwInput.value = "";
  pwInput.focus();
}

async function showAdmin() {
  loginCard.hidden = true;
  adminCard.hidden = false;
  await refreshDashboard();
  loadUsageSummary();
  loadLastAudit();
  loadDbState();
}

async function checkAuth() {
  try {
    const res = await fetch("/admin/me");
    const data = await res.json();
    if (data.authenticated) showAdmin();
    else showLogin();
  } catch {
    showLogin();
  }
}

async function login() {
  loginErr.textContent = "";
  loginBtn.disabled = true;
  try {
    const res = await fetch("/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pwInput.value }),
    });
    if (!res.ok) {
      loginErr.textContent =
        res.status === 401 || res.status === 403 ? "Wrong password." : `HTTP ${res.status}`;
      return;
    }
    showAdmin();
  } catch (err) {
    loginErr.textContent = err.message;
  } finally {
    loginBtn.disabled = false;
  }
}

async function logout() {
  await fetch("/admin/logout", { method: "POST" });
  showLogin();
}

async function refreshDashboard() {
  try {
    const res = await fetch("/admin/stats");
    if (res.status === 401) {
      showLogin();
      return null;
    }
    if (!res.ok) return null;
    const stats = await res.json();
    statPagesCrawled.textContent = formatNumber(stats.pages_crawled);
    statPagesParsed.textContent = formatNumber(stats.pages_parsed);
    statEntities.textContent = formatNumber(stats.entities);
    statConnections.textContent = formatNumber(stats.connections);
    maxPipelineCostUsd = Number(stats.max_pipeline_cost_usd ?? 10);
    estimatedNextRunCost = Number(stats.estimated_next_run_cost ?? 0);
    renderPipelineBanner(stats.pipeline_run);
    return stats;
  } catch {
    setBanner("Ready");
    return null;
  }
}

function renderPipelineBanner(run) {
  const costText = `Next run estimate: ${formatMoney(estimatedNextRunCost)}`;
  if (run && run.status === "running") {
    setBanner(
      currentStageLabel
        ? `Pipeline running: ${currentStageLabel} | ${costText}`
        : `Pipeline running | ${costText}`,
    );
    setRunning(true);
    return;
  }
  setRunning(false);
  if (run && run.status === "complete") {
    setBanner(`Pipeline complete (last run: ${relativeTime(run.finished_at)}) | ${costText}`);
  } else {
    setBanner(`Ready | ${costText}`);
  }
}

// ---------- one-button pipeline ----------

async function apiPost(path, body = null) {
  const options = { method: "POST" };
  if (body !== null) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  if (res.status === 401) {
    showLogin();
    throw new Error("Admin session expired");
  }
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.detail || `HTTP ${res.status}`);
  }
  return await res.json();
}

async function runPipeline() {
  const stats = await refreshDashboard();
  if (stats?.pipeline_run?.status === "running") {
    setStatusLine("Pipeline is already running", true);
    setRunning(true);
    return;
  }
  const crawlMax = stats?.crawl_max_pages ?? "?";
  const cost = Number(stats?.max_pipeline_cost_usd ?? maxPipelineCostUsd).toFixed(2);
  const confirmed = window.confirm(
    `This will fetch up to ${crawlMax} pages and may spend up to roughly $${cost} on OpenAI. Continue?`,
  );
  if (!confirmed) return;

  cancelRequested = false;
  logEl.innerHTML = "";
  setStatusLine("Starting pipeline...");
  setRunning(true);
  progressWrap.hidden = false;

  try {
    const run = await apiPost("/admin/pipeline/run/start");
    activePipelineRunId = run.id;
    await runStreamingStage("crawl", "Crawling...", "/admin/crawl/start", "/admin/crawl/stream");
    await assertWithinBudget();
    await runStreamingStage("parse", "Parsing...", "/admin/parse/start", "/admin/parse/stream", {});
    await assertWithinBudget();
    await runSimpleStage("Reconciling...", "/admin/reconcile/run");
    await assertWithinBudget();
    await runSimpleStage("Auditing...", "/admin/audit/run", { sample_size: 30 });
    await finishPipeline("complete");
    setStatusLine("Pipeline complete");
    setBanner("Pipeline complete (last run: just now)");
  } catch (err) {
    const status = cancelRequested ? "canceled" : "failed";
    if (activePipelineRunId) {
      await finishPipeline(status, err.message).catch(() => {});
    }
    progressFill.classList.add("err");
    setStatusLine(cancelRequested ? "Pipeline canceled" : `Pipeline failed: ${err.message}`, true);
    setBanner(cancelRequested ? "Ready" : "Ready");
  } finally {
    activePipelineRunId = null;
    stopStream();
    setRunning(false);
    await refreshDashboard();
    loadUsageSummary();
  }
}

async function finishPipeline(status, errorMessage = "") {
  if (!activePipelineRunId) return;
  await apiPost(`/admin/pipeline/run/${activePipelineRunId}/finish`, {
    status,
    error_message: errorMessage,
  });
}

async function assertWithinBudget() {
  const stats = await refreshDashboard();
  const dollars = Number(stats?.running_llm_dollars ?? 0);
  if (dollars > maxPipelineCostUsd) {
    await cancelPipeline();
    throw new Error(`Pipeline cost exceeded ${formatMoney(maxPipelineCostUsd)}`);
  }
}

async function runStreamingStage(stage, label, startPath, streamPath, body = null) {
  showProgress(label);
  const start = await apiPost(startPath, body);
  appendLog(`${stage} ${start.status}`, "log-ok");
  if (start.status === "already_running") throw new Error("Pipeline is already running");
  await waitForStream(streamPath);
}

function waitForStream(streamPath) {
  return new Promise((resolve, reject) => {
    if (evtSource) evtSource.close();
    evtSource = new EventSource(streamPath);
    let settled = false;
    const settle = (fn, value) => {
      if (settled) return;
      settled = true;
      stopStream();
      fn(value);
    };
    evtSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const cls = data.error || data.kind === "page_error" ? "log-err" : "log-evt";
      appendLog(formatEvent(data), cls);
      if (data.kind === "usage_tick") {
        updateRunUsage(data);
        if (Number(data.dollars ?? 0) > maxPipelineCostUsd) {
          cancelPipeline();
          settle(reject, new Error(`Pipeline cost exceeded ${formatMoney(maxPipelineCostUsd)}`));
          return;
        }
      }
      updateProgress(data);
      if (data.kind === "done") {
        if (data.error) settle(reject, new Error(data.error));
        else settle(resolve);
      }
    };
    evtSource.onerror = () => {
      if (cancelRequested) settle(reject, new Error("Pipeline canceled"));
      else settle(reject, new Error("Progress stream closed"));
    };
  });
}

async function runSimpleStage(label, path, body = null) {
  showProgress(label);
  progressCount.textContent = "";
  progressFill.style.width = "15%";
  const result = await apiPost(path, body);
  appendLog(`${label.replace("...", "")} ${result.status ?? "ok"}`, "log-ok");
  progressFill.style.width = "100%";
  progressFill.classList.add("done");
}

async function cancelPipeline() {
  cancelRequested = true;
  setStatusLine("Pipeline is canceling...");
  if (evtSource) evtSource.close();
  await fetch("/admin/crawl/stop", { method: "POST" }).catch(() => {});
  await fetch("/admin/parse/stop", { method: "POST" }).catch(() => {});
  await fetch("/admin/pipeline/run/cancel", { method: "POST" }).catch(() => {});
}

async function loadDbState() {
  try {
    const res = await fetch("/admin/db");
    if (!res.ok) return;
    const data = await res.json();
    dbState.innerHTML = Object.entries(data.tables || {})
      .map(([table, count]) => `<div>${table}: ${formatNumber(count)}</div>`)
      .join("");
    dbState.hidden = false;
  } catch {
    dbState.hidden = true;
  }
}

async function resetExtractionData() {
  const typed = window.prompt('Type "RESET" to clear extraction data.');
  if (typed !== "RESET") {
    setStatusLine("Reset canceled");
    return;
  }
  try {
    const result = await apiPost("/admin/reset/extraction", { confirmation: typed });
    setStatusLine("Extraction data reset");
    appendLog(`reset extraction data ${result.status}`, "log-ok");
    await refreshDashboard();
    await loadDbState();
  } catch (err) {
    setStatusLine(`Reset failed: ${err.message}`, true);
  }
}

function stopStream() {
  if (evtSource) {
    evtSource.close();
    evtSource = null;
  }
}

// ---------- advanced controls ----------

function parseFilterPayload() {
  const payload = {};
  const urlPattern = parseUrlPattern.value.trim();
  const keywords = parseKeywords.value
    .split(",")
    .map((keyword) => keyword.trim())
    .filter(Boolean);
  const limit = Number.parseInt(parseLimit.value, 10);
  if (urlPattern) payload.url_pattern = urlPattern;
  if (keywords.length) payload.keywords = keywords;
  if (Number.isFinite(limit) && limit > 0) payload.limit = limit;
  return payload;
}

async function previewParse() {
  previewBtn.disabled = true;
  previewResult.hidden = true;
  previewResult.textContent = "";
  try {
    const res = await fetch("/admin/parse/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parseFilterPayload()),
    });
    if (res.status === 401) {
      showLogin();
      return;
    }
    if (!res.ok) {
      previewResult.textContent = `Preview failed: HTTP ${res.status}`;
      previewResult.hidden = false;
      return;
    }
    const data = await res.json();
    previewResult.textContent =
      `${formatNumber(data.page_count)} pages | ` +
      `${formatNumber(data.total_estimated_tokens)} tokens | ` +
      `${formatMoney(data.estimated_dollar_cost)} | ` +
      `${formatNumber(data.estimated_wall_clock_seconds)}s`;
    previewResult.hidden = false;
  } catch (err) {
    previewResult.textContent = `Preview failed: ${err.message}`;
    previewResult.hidden = false;
  } finally {
    previewBtn.disabled = false;
  }
}

async function loadUsageSummary() {
  try {
    const res = await fetch("/admin/usage/summary");
    if (!res.ok) return;
    renderUsageResources(await res.json());
  } catch {
    usageTotal.textContent = "usage unavailable";
  }
}

function renderAudit(audit) {
  auditResult.innerHTML = "";
  if (!audit) {
    auditResult.hidden = true;
    return;
  }
  const summary = audit.diff_summary || {};
  const heading = document.createElement("div");
  heading.className = "audit-heading";
  heading.textContent =
    `audit ${audit.id} | sample ${audit.sample_size} | ` +
    `jaccard ${Number(summary.global_jaccard ?? 0).toFixed(2)}`;
  auditResult.appendChild(heading);

  const table = document.createElement("table");
  table.className = "audit-table";
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  ["page", "thrifty", "frontier", "jaccard"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  (summary.per_page || []).forEach((row) => {
    const tr = document.createElement("tr");
    [
      row.page || row.raw_page_id || "",
      row.thrifty_count ?? 0,
      row.frontier_count ?? 0,
      Number(row.jaccard ?? 0).toFixed(2),
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = String(value);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  auditResult.appendChild(table);
  auditResult.hidden = false;
}

async function loadLastAudit() {
  try {
    const res = await fetch("/admin/audit/last");
    if (!res.ok) return;
    const data = await res.json();
    renderAudit(data.audit);
  } catch {
    auditResult.hidden = true;
  }
}

async function runAudit() {
  setRunning(true);
  showProgress("Auditing...");
  auditResult.hidden = false;
  auditResult.textContent = "audit running";
  try {
    const data = await apiPost("/admin/audit/run", { sample_size: 30 });
    renderAudit(data);
  } catch (err) {
    auditResult.textContent = `Audit failed: ${err.message}`;
  } finally {
    setRunning(false);
  }
}

async function startStage(stage) {
  logEl.innerHTML = "";
  setRunning(true);
  const label = stage === "crawl" ? "Crawling..." : "Parsing...";
  try {
    await runStreamingStage(
      stage,
      label,
      `/admin/${stage}/start`,
      `/admin/${stage}/stream`,
      stage === "parse" ? parseFilterPayload() : null,
    );
    loadUsageSummary();
  } catch (err) {
    appendLog(`error: ${err.message}`, "log-err");
  } finally {
    setRunning(false);
    refreshDashboard();
  }
}

// ---------- bindings ----------

loginBtn.addEventListener("click", login);
pwInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") login();
});
logoutBtn.addEventListener("click", logout);
runPipelineBtn.addEventListener("click", runPipeline);
cancelPipelineBtn.addEventListener("click", cancelPipeline);
crawlBtn.addEventListener("click", () => startStage("crawl"));
previewBtn.addEventListener("click", previewParse);
parseBtn.addEventListener("click", () => startStage("parse"));
auditBtn.addEventListener("click", runAudit);
stopBtn.addEventListener("click", cancelPipeline);
resetExtractionBtn.addEventListener("click", resetExtractionData);
window.addEventListener("beforeunload", stopUsagePolling);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopUsagePolling();
  } else if (runPipelineBtn.disabled) {
    startUsagePolling();
  }
});
parseFilterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  previewParse();
});

checkAuth();
