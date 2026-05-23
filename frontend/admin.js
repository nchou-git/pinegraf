const loginCard = document.getElementById("login-card");
const adminCard = document.getElementById("admin-card");
const pwInput = document.getElementById("pw");
const loginErr = document.getElementById("login-err");
const loginBtn = document.getElementById("login-btn");
const logoutBtn = document.getElementById("logout-btn");
const crawlBtn = document.getElementById("crawl-btn");
const previewBtn = document.getElementById("preview-btn");
const parseBtn = document.getElementById("parse-btn");
const stopBtn = document.getElementById("stop-btn");
const logEl = document.getElementById("log");
const progressWrap = document.getElementById("progress-wrap");
const progressLabel = document.getElementById("progress-label");
const progressCount = document.getElementById("progress-count");
const progressFill = document.getElementById("progress-fill");
const progressUsage = document.getElementById("progress-usage");
const previewResult = document.getElementById("preview-result");
const usageTotal = document.getElementById("usage-total");
const usageModels = document.getElementById("usage-models");
const parseFilterForm = document.getElementById("parse-filter-form");
const parseUrlPattern = document.getElementById("parse-url-pattern");
const parseKeywords = document.getElementById("parse-keywords");
const parseLimit = document.getElementById("parse-limit");

let evtSource = null;
let activeStage = null;

// ---------- log helpers ----------

function appendLog(text, cls = "log-evt") {
  const div = document.createElement("div");
  div.className = cls;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function showProgress(stage) {
  progressWrap.hidden = false;
  progressLabel.textContent = stage === "crawl" ? "Crawling" : "Parsing";
  progressCount.textContent = "0 / ?";
  progressUsage.textContent = "";
  progressFill.style.width = "0%";
  progressFill.classList.remove("done", "err");
}

function updateProgress(ev) {
  const done = ev.overall_done ?? ev.page_done;
  const total = ev.overall_total ?? ev.page_total;
  if (total && done !== undefined) {
    const pct = Math.min(100, Math.round((done / total) * 100));
    progressFill.style.width = pct + "%";
    progressCount.textContent = `${done} / ${total}`;
  }
  if (ev.kind === "done") {
    progressFill.style.width = "100%";
    progressFill.classList.add(ev.error ? "err" : "done");
    progressLabel.textContent = ev.error ? "Failed" : "Complete";
  }
}

function formatNumber(value) {
  return new Intl.NumberFormat("en-US").format(value ?? 0);
}

function formatMoney(value) {
  return `$${Number(value ?? 0).toFixed(4)}`;
}

function updateRunUsage(ev) {
  progressUsage.textContent =
    `${formatNumber(ev.calls)} calls | ${formatNumber(ev.total_tokens)} tokens | ` +
    `${formatMoney(ev.dollars)}`;
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
    case "alum_start":
      return `> ${ev.name}`;
    case "alum_done":
      return `done ${ev.name}`;
    case "done":
      return ev.error
        ? `ERROR: ${ev.error}`
        : `complete: ${ev.overall_done ?? 0}/${ev.overall_total ?? 0} fetched=${ev.fetched_total ?? "?"}`;
    default:
      return JSON.stringify(ev);
  }
}

// ---------- auth ----------

function showLogin() {
  loginCard.hidden = false;
  adminCard.hidden = true;
  pwInput.value = "";
  pwInput.focus();
}

function showAdmin() {
  loginCard.hidden = true;
  adminCard.hidden = false;
  loadUsageSummary();
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
        res.status === 401 || res.status === 403
          ? "Wrong password."
          : `HTTP ${res.status}`;
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

// ---------- pipeline ----------

function setRunning(running) {
  crawlBtn.disabled = running;
  previewBtn.disabled = running;
  parseBtn.disabled = running;
  stopBtn.disabled = !running;
}

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
    const data = await res.json();
    usageTotal.textContent =
      `${formatNumber(data.totals.calls)} calls | ` +
      `${formatNumber(data.totals.total_tokens)} tokens | ${formatMoney(data.totals.dollars)}`;
    const dollarsByModel = (data.by_day_model || []).reduce((acc, row) => {
      acc[row.model] = (acc[row.model] || 0) + row.dollars;
      return acc;
    }, {});
    usageModels.textContent = Object.entries(dollarsByModel)
      .map(([model, dollars]) => `${model}: ${formatMoney(dollars)}`)
      .join(" | ");
  } catch {
    usageTotal.textContent = "usage unavailable";
  }
}

async function startStage(stage) {
  if (evtSource) evtSource.close();
  logEl.innerHTML = "";
  activeStage = stage;
  setRunning(true);
  showProgress(stage);

  try {
    const fetchOptions = { method: "POST" };
    if (stage === "parse") {
      fetchOptions.headers = { "Content-Type": "application/json" };
      fetchOptions.body = JSON.stringify(parseFilterPayload());
    }
    const res = await fetch(`/admin/${stage}/start`, fetchOptions);
    if (res.status === 401) {
      showLogin();
      return;
    }
    if (!res.ok) {
      appendLog(`${stage} start failed: HTTP ${res.status}`, "log-err");
      setRunning(false);
      return;
    }
    const start = await res.json();
    appendLog(`${stage} ${start.status}`, "log-ok");

    evtSource = new EventSource(`/admin/${stage}/stream`);
    evtSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const summary = formatEvent(data);
      const cls = data.error || data.kind === "page_error" ? "log-err" : "log-evt";
      appendLog(summary, cls);
      if (data.kind === "usage_tick") updateRunUsage(data);
      updateProgress(data);
      if (data.kind === "done") {
        loadUsageSummary();
        stopStream();
      }
    };
    evtSource.onerror = () => {
      appendLog("[stream closed]", "log-evt");
      stopStream();
    };
  } catch (err) {
    appendLog(`error: ${err.message}`, "log-err");
    setRunning(false);
  }
}

function stopStream() {
  if (evtSource) {
    evtSource.close();
    evtSource = null;
  }
  activeStage = null;
  setRunning(false);
}

// ---------- bindings ----------

loginBtn.addEventListener("click", login);
pwInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") login();
});
logoutBtn.addEventListener("click", logout);
crawlBtn.addEventListener("click", () => startStage("crawl"));
previewBtn.addEventListener("click", previewParse);
parseBtn.addEventListener("click", () => startStage("parse"));
stopBtn.addEventListener("click", stopStream);
parseFilterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  previewParse();
});

checkAuth();
