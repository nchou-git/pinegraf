let evtSource = null;
let queuedAlumni = 0;
let totalAlumKnown = 0;
let alumDoneOverall = 0;
let currentClass = null;
let currentClassTotal = 0;
let currentClassDone = 0;
let currentAlumStages = 0;
let streamFinished = false;
let classesSeen = new Set();

const STAGES_PER_ALUM = 10; // rough estimate for the alum bar
const log = document.getElementById("log");
const researchBtn = document.getElementById("researchBtn");
const stopBtn = document.getElementById("stopBtn");
const summaryCard = document.getElementById("summaryCard");

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function appendLog(text, cls = "stage") {
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

function setOverallProgress(done, total) {
  alumDoneOverall = Number.isFinite(Number(done)) ? Number(done) : alumDoneOverall;
  totalAlumKnown = Number.isFinite(Number(total)) ? Number(total) : totalAlumKnown;
  const pct = totalAlumKnown ? (alumDoneOverall / totalAlumKnown) * 100 : 0;
  setProgress("overallBar", "overallPct", pct);
  document.getElementById("overallMeta").textContent =
    `${formatNumber(alumDoneOverall)}/${formatNumber(totalAlumKnown)} alumni`;
}

function setClassProgress(done, total) {
  currentClassDone = Number.isFinite(Number(done)) ? Number(done) : currentClassDone;
  currentClassTotal = Number.isFinite(Number(total)) ? Number(total) : currentClassTotal;
  const pct = currentClassTotal ? (currentClassDone / currentClassTotal) * 100 : 0;
  setProgress("classBar", "classPct", pct);
  document.getElementById("classMeta").textContent = currentClass
    ? `Current class: ${currentClass} (${formatNumber(currentClassDone)}/${formatNumber(currentClassTotal)})`
    : "Current class: none";
}

function setAlumProgress(pct) {
  setProgress("alumBar", "alumPct", pct);
}

function resetProgress() {
  totalAlumKnown = queuedAlumni;
  alumDoneOverall = 0;
  currentClass = null;
  currentClassTotal = 0;
  currentClassDone = 0;
  currentAlumStages = 0;
  classesSeen = new Set();
  setOverallProgress(0, queuedAlumni);
  setClassProgress(0, 0);
  setAlumProgress(0);
  document.getElementById("alumMeta").textContent = "Current alum: none";
  summaryCard.hidden = true;
  summaryCard.textContent = "";
}

async function loadAlumniCount() {
  const queueCount = document.getElementById("queueCount");

  try {
    const response = await fetch("/alumni-count");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    queuedAlumni = Number(data.count) || 0;
    totalAlumKnown = queuedAlumni;
    queueCount.textContent = `${formatNumber(queuedAlumni)} alumni queued`;
    setOverallProgress(0, queuedAlumni);
  } catch (error) {
    queueCount.textContent = "Alumni queue unavailable";
    document.getElementById("overallMeta").textContent = "Queue count unavailable";
    appendLog(`Could not load alumni count: ${error.message}`, "error");
  } finally {
    researchBtn.disabled = false;
  }
}

function updateOverallFromEvent(ev) {
  const done = ev.overall_done ?? alumDoneOverall;
  const total = ev.overall_total ?? totalAlumKnown;
  setOverallProgress(done, total);
}

function formatAlumMeta(ev) {
  const depth = Number.isFinite(Number(ev.depth)) ? Number(ev.depth) : 0;
  const via = ev.discovered_via ? `, via ${ev.discovered_via}` : "";
  return `Current alum: ${ev.name} (depth ${depth}${via})`;
}

function startResearch() {
  if (evtSource) evtSource.close();
  log.innerHTML = "";
  streamFinished = false;
  resetProgress();
  document.getElementById("overallMeta").textContent =
    `Starting... 0/${formatNumber(totalAlumKnown)} alumni`;
  researchBtn.disabled = true;
  stopBtn.disabled = false;

  evtSource = new EventSource("/research/stream");

  evtSource.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    handleEvent(ev);
  };
  evtSource.onerror = () => {
    if (!streamFinished) {
      appendLog("[stream closed]", "error");
    }
    stopResearch();
  };
}

async function showSummary() {
  summaryCard.hidden = false;
  summaryCard.textContent = "Building summary...";

  try {
    const [connectionsResponse, projectsResponse] = await Promise.all([
      fetch("/connections"),
      fetch("/projects"),
    ]);
    const connectionsData = connectionsResponse.ok
      ? await connectionsResponse.json()
      : { connections: [] };
    const projectsData = projectsResponse.ok ? await projectsResponse.json() : { projects: [] };
    const connections = Array.isArray(connectionsData.connections)
      ? connectionsData.connections.length
      : 0;
    const projects = Array.isArray(projectsData.projects) ? projectsData.projects.length : 0;

    summaryCard.textContent =
      `✓ Researched ${formatNumber(alumDoneOverall)} alumni across ` +
      `${formatNumber(classesSeen.size)} classes. Found ${formatNumber(connections)} ` +
      `connections, ${formatNumber(projects)} projects.`;
  } catch (error) {
    summaryCard.textContent =
      `✓ Researched ${formatNumber(alumDoneOverall)} alumni across ` +
      `${formatNumber(classesSeen.size)} classes. Summary counts unavailable.`;
    appendLog(`Could not load summary counts: ${error.message}`, "error");
  }
}

function handleEvent(ev) {
  switch (ev.kind) {
    case "class_start":
      currentClass = ev.class_year;
      classesSeen.add(ev.class_year);
      currentClassTotal = ev.count;
      currentClassDone = ev.done || 0;
      setClassProgress(currentClassDone, currentClassTotal);
      updateOverallFromEvent(ev);
      appendLog(`> class ${ev.class_year}: ${ev.count} alumni`, "class-start");
      break;
    case "alum_start":
      currentAlumStages = 0;
      setAlumProgress(0);
      document.getElementById("alumMeta").textContent = formatAlumMeta(ev);
      appendLog(`  - ${formatAlumMeta(ev).replace("Current alum: ", "")}`, "stage");
      break;
    case "discovered":
      updateOverallFromEvent(ev);
      if (ev.class_year === currentClass) {
        currentClassTotal = ev.total_in_class || currentClassTotal + 1;
        setClassProgress(currentClassDone, currentClassTotal);
      }
      appendLog(
        `    discovered ${ev.name} (${ev.class_year}) via ${ev.discovered_via}`,
        "stage",
      );
      break;
    case "stage":
      currentAlumStages++;
      setAlumProgress(Math.min(95, (currentAlumStages / STAGES_PER_ALUM) * 100));
      appendLog(`    ${ev.name}: ${ev.stage}${ev.url ? " - " + ev.url : ""}`, "stage");
      break;
    case "alum_done":
      setAlumProgress(100);
      setClassProgress(ev.done_in_class ?? currentClassDone + 1, ev.total_in_class);
      updateOverallFromEvent(ev);
      appendLog(`  done ${ev.name}`, "alum-done");
      break;
    case "class_done":
      if (ev.total_in_class) {
        currentClassTotal = ev.total_in_class;
      }
      currentClassDone = ev.total_done ?? currentClassDone;
      currentClass = ev.class_year;
      classesSeen.add(ev.class_year);
      setClassProgress(currentClassDone, currentClassTotal);
      updateOverallFromEvent(ev);
      appendLog(`< class ${ev.class_year} done (${ev.total_done} alumni)`, "class-done");
      break;
    case "done":
      if (ev.error) appendLog(`ERROR: ${ev.error}`, "error");
      updateOverallFromEvent(ev);
      appendLog("research complete", "alum-done");
      document.getElementById("overallMeta").textContent =
        `Done - ${formatNumber(alumDoneOverall)}/${formatNumber(totalAlumKnown)} alumni`;
      streamFinished = true;
      showSummary();
      stopResearch();
      break;
  }
}

function stopResearch() {
  if (evtSource) {
    evtSource.close();
    evtSource = null;
  }
  researchBtn.disabled = false;
  stopBtn.disabled = true;
}

async function runQuery() {
  const input = document.getElementById("question");
  const answer = document.getElementById("answer");
  const question = input.value.trim();
  if (!question) {
    answer.textContent = "Please enter a question.";
    return;
  }
  answer.textContent = "Querying...";
  const response = await fetch("/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  const data = await response.json();
  answer.textContent = data.answer;
}

researchBtn.addEventListener("click", startResearch);
stopBtn.addEventListener("click", stopResearch);
document.getElementById("queryBtn").addEventListener("click", runQuery);
document.getElementById("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter") runQuery();
});

loadAlumniCount();
