let evtSource = null;
let queuedAlumni = 0;
let activeStage = null;
let streamFinished = false;
let queryTimer = null;
let queryStartedAt = 0;
let queryExpectedMs = 5000;

const log = document.getElementById("log");
const crawlBtn = document.getElementById("crawlBtn");
const parseBtn = document.getElementById("parseBtn");
const stopBtn = document.getElementById("stopBtn");
const summaryCard = document.getElementById("summaryCard");

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function formatClassYear(value) {
  const text = String(value || "").trim();
  const match = text.match(/^T'?(\d{2})$/i);
  if (match) return `T'${match[1]}`;
  return text;
}

function appendLog(text, cls = "log-event") {
  const div = document.createElement("div");
  div.className = cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function clampPercent(pct) {
  if (!Number.isFinite(pct)) return 0;
  return Math.min(100, Math.max(0, pct));
}

function setProgress(barId, pctId, pct) {
  const safePct = clampPercent(pct);
  document.getElementById(barId).style.width = `${safePct}%`;
  document.getElementById(pctId).textContent = `${Math.round(safePct)}%`;
}

function resetProgress(stageName) {
  setProgress("overallBar", "overallPct", 0);
  setProgress("alumBar", "alumPct", 0);
  setProgress("pageBar", "pagePct", 0);
  document.getElementById("overallMeta").textContent = `${stageName} idle`;
  document.getElementById("alumMeta").textContent = "Current alum: none";
  document.getElementById("pageMeta").textContent = "Current page: none";
  summaryCard.hidden = true;
  summaryCard.textContent = "";
}

function setButtons(running) {
  crawlBtn.disabled = running;
  parseBtn.disabled = running;
  stopBtn.disabled = !running;
}

async function loadAlumniCount() {
  const queueCount = document.getElementById("queueCount");
  try {
    const response = await fetch("/alumni-count");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    queuedAlumni = Number(data.count) || 0;
    queueCount.textContent = `${formatNumber(queuedAlumni)} alumni queued`;
    document.getElementById("overallMeta").textContent =
      `Idle - ${formatNumber(queuedAlumni)} seed alumni`;
  } catch (error) {
    queueCount.textContent = "Alumni queue unavailable";
    appendLog(`Could not load alumni count: ${error.message}`, "log-error");
  } finally {
    crawlBtn.disabled = false;
    parseBtn.disabled = false;
  }
}

async function startStage(stage) {
  if (evtSource) evtSource.close();
  activeStage = stage;
  streamFinished = false;
  log.innerHTML = "";
  resetProgress(stage === "crawl" ? "Crawl" : "Parse");
  setButtons(true);

  const response = await fetch(`/${stage}/start`, { method: "POST" });
  if (!response.ok) {
    appendLog(`${stage} start failed: HTTP ${response.status}`, "log-error");
    stopStage();
    return;
  }

  const startData = await response.json();
  appendLog(`${stage} ${startData.status}`, "log-ok");
  evtSource = new EventSource(`/${stage}/stream`);
  evtSource.onmessage = (event) => {
    const ev = JSON.parse(event.data);
    handleStageEvent(ev);
  };
  evtSource.onerror = () => {
    if (!streamFinished) appendLog("[stream closed]", "log-error");
    stopStage();
  };
}

function handleStageEvent(ev) {
  if (activeStage === "crawl") {
    handleCrawlEvent(ev);
    return;
  }
  handleParseEvent(ev);
}

function setOverallFromEvent(ev, noun) {
  const done = Number(ev.overall_done ?? ev.page_done ?? 0);
  const total = Number(ev.overall_total ?? ev.page_total ?? 0);
  setProgress("overallBar", "overallPct", total ? (done / total) * 100 : 0);
  document.getElementById("overallMeta").textContent =
    `${formatNumber(done)}/${formatNumber(total)} ${noun}`;
}

function setPageFromEvent(ev) {
  const pageIndex = Number(ev.page_index ?? 0);
  const pageTotal = Number(ev.page_total ?? 0);
  setProgress("pageBar", "pagePct", pageTotal ? (pageIndex / pageTotal) * 100 : 0);
  document.getElementById("pageMeta").textContent = ev.url
    ? `${formatNumber(pageIndex)}/${formatNumber(pageTotal)} - ${ev.url}`
    : `${formatNumber(pageIndex)}/${formatNumber(pageTotal)} pages`;
}

function handleCrawlEvent(ev) {
  switch (ev.kind) {
    case "crawl_start":
      setOverallFromEvent(ev, "alumni");
      appendLog(`crawl started for ${formatNumber(ev.overall_total)} alumni`, "log-ok");
      break;
    case "alum_start":
      setProgress("alumBar", "alumPct", 0);
      setProgress("pageBar", "pagePct", 0);
      document.getElementById("alumMeta").textContent =
        `${ev.name} ${formatClassYear(ev.class_year)}`;
      appendLog(`> ${ev.name} ${formatClassYear(ev.class_year)}`, "log-event");
      break;
    case "page_fetched":
      setProgress("alumBar", "alumPct", 70);
      setPageFromEvent(ev);
      appendLog(`  fetched ${ev.url}`, "log-event");
      break;
    case "page_skipped":
      setPageFromEvent(ev);
      appendLog(`  skipped ${ev.url} (${ev.reason})`, "log-event");
      break;
    case "page_failed":
      setPageFromEvent(ev);
      appendLog(`  failed ${ev.url}`, "log-error");
      break;
    case "alum_done":
      setProgress("alumBar", "alumPct", 100);
      setOverallFromEvent(ev, "alumni");
      appendLog(`done ${ev.name} (${formatNumber(ev.pages_fetched)} new pages)`, "log-ok");
      break;
    case "done":
      finishStage(ev, "crawl complete", "alumni");
      break;
  }
}

function handleParseEvent(ev) {
  switch (ev.kind) {
    case "parse_start":
      setOverallFromEvent(ev, "pages");
      appendLog(`parse started for ${formatNumber(ev.page_total)} pages`, "log-ok");
      break;
    case "alum_start":
      setProgress("alumBar", "alumPct", 0);
      setProgress("pageBar", "pagePct", 0);
      document.getElementById("alumMeta").textContent = ev.name;
      appendLog(`> ${ev.name}`, "log-event");
      break;
    case "page_parsed":
      setPageFromEvent(ev);
      setProgress("alumBar", "alumPct", ev.page_total ? (ev.page_index / ev.page_total) * 100 : 0);
      setOverallFromEvent(ev, "pages");
      appendLog(
        `  parsed ${ev.url} keep=${ev.verdict_counts.keep} uncertain=${ev.verdict_counts.uncertain} drop=${ev.verdict_counts.drop}`,
        "log-event",
      );
      break;
    case "alum_done":
      setProgress("alumBar", "alumPct", 100);
      setOverallFromEvent(ev, "pages");
      appendLog(`done ${ev.name}`, "log-ok");
      break;
    case "done":
      finishStage(ev, "parse complete", "pages");
      break;
  }
}

function finishStage(ev, label, noun) {
  if (ev.error) appendLog(`ERROR: ${ev.error}`, "log-error");
  setOverallFromEvent(ev, noun);
  setProgress("alumBar", "alumPct", 100);
  streamFinished = true;
  summaryCard.hidden = false;
  summaryCard.textContent =
    `${label}: ${formatNumber(ev.overall_done ?? 0)}/${formatNumber(ev.overall_total ?? 0)} ${noun}`;
  appendLog(label, "log-ok");
  stopStage();
}

function stopStage() {
  if (evtSource) {
    evtSource.close();
    evtSource = null;
  }
  activeStage = null;
  setButtons(false);
}

function selectedMode() {
  const checked = document.querySelector('input[name="queryMode"]:checked');
  return checked ? checked.value : "strict";
}

function startQueryProgress(mode) {
  const progress = document.getElementById("queryProgress");
  queryExpectedMs = mode === "deep" ? 20000 : 5000;
  queryStartedAt = Date.now();
  progress.classList.add("active");
  setProgress("queryBar", "queryPct", 0);
  if (queryTimer) clearInterval(queryTimer);
  queryTimer = setInterval(() => {
    const elapsed = Date.now() - queryStartedAt;
    setProgress("queryBar", "queryPct", Math.min(90, (elapsed / queryExpectedMs) * 90));
  }, 120);
}

function finishQueryProgress() {
  const progress = document.getElementById("queryProgress");
  if (queryTimer) clearInterval(queryTimer);
  queryTimer = null;
  setProgress("queryBar", "queryPct", 100);
  setTimeout(() => {
    progress.classList.remove("active");
    setProgress("queryBar", "queryPct", 0);
  }, 450);
}

async function runQuery() {
  const input = document.getElementById("question");
  const answer = document.getElementById("answer");
  const question = input.value.trim();
  const mode = selectedMode();
  if (!question) {
    answer.textContent = "Please enter a question.";
    return;
  }

  answer.textContent = "Querying...";
  startQueryProgress(mode);
  try {
    const response = await fetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    answer.textContent = data.answer;
  } catch (error) {
    answer.textContent = `Query failed: ${error.message}`;
  } finally {
    finishQueryProgress();
  }
}

crawlBtn.addEventListener("click", () => startStage("crawl"));
parseBtn.addEventListener("click", () => startStage("parse"));
stopBtn.addEventListener("click", stopStage);
document.getElementById("queryBtn").addEventListener("click", runQuery);
document.getElementById("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter") runQuery();
});

loadAlumniCount();
