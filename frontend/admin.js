const loginCard = document.getElementById("login-card");
const adminCard = document.getElementById("admin-card");
const pwInput = document.getElementById("pw");
const loginErr = document.getElementById("login-err");
const loginBtn = document.getElementById("login-btn");
const logoutBtn = document.getElementById("logout-btn");
const crawlBtn = document.getElementById("crawl-btn");
const parseBtn = document.getElementById("parse-btn");
const stopBtn = document.getElementById("stop-btn");
const queueInfo = document.getElementById("queue-info");
const logEl = document.getElementById("log");

let evtSource = null;

function appendLog(text, cls = "log-evt") {
  const div = document.createElement("div");
  div.className = cls;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function showLogin() { loginCard.hidden = false; adminCard.hidden = true; pwInput.value = ""; pwInput.focus(); }
function showAdmin() { loginCard.hidden = true; adminCard.hidden = false; refreshAlumniCount(); }

async function checkAuth() {
  try {
    const res = await fetch("/admin/me");
    const data = await res.json();
    if (data.authenticated) showAdmin(); else showLogin();
  } catch { showLogin(); }
}

async function login() {
  loginErr.textContent = ""; loginBtn.disabled = true;
  try {
    const res = await fetch("/admin/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pwInput.value }) });
    if (!res.ok) { loginErr.textContent = res.status === 401 ? "Wrong password." : `HTTP ${res.status}`; return; }
    showAdmin();
  } catch (err) { loginErr.textContent = err.message; }
  finally { loginBtn.disabled = false; }
}

async function logout() { await fetch("/admin/logout", { method: "POST" }); showLogin(); }

async function refreshAlumniCount() {
  try {
    const res = await fetch("/admin/alumni-count");
    if (res.status === 401) { showLogin(); return; }
    const data = await res.json();
    queueInfo.textContent = `${data.count} alumni queued`;
  } catch (err) { queueInfo.textContent = `queue unavailable: ${err.message}`; }
}

function setRunning(running) { crawlBtn.disabled = running; parseBtn.disabled = running; stopBtn.disabled = !running; }

async function startStage(stage) {
  if (evtSource) evtSource.close();
  logEl.innerHTML = "";
  setRunning(true);
  try {
    const res = await fetch(`/admin/${stage}/start`, { method: "POST" });
    if (res.status === 401) { showLogin(); return; }
    if (!res.ok) { appendLog(`${stage} start failed: HTTP ${res.status}`, "log-err"); setRunning(false); return; }
    const start = await res.json();
    appendLog(`${stage} ${start.status}`, "log-ok");
    evtSource = new EventSource(`/admin/${stage}/stream`);
    evtSource.onmessage = (ev) => { const data = JSON.parse(ev.data); appendLog(JSON.stringify(data), data.error ? "log-err" : "log-evt"); if (data.kind === "done") stopStream(); };
    evtSource.onerror = () => { appendLog("[stream closed]", "log-evt"); stopStream(); };
  } catch (err) { appendLog(`error: ${err.message}`, "log-err"); setRunning(false); }
}

function stopStream() { if (evtSource) { evtSource.close(); evtSource = null; } setRunning(false); }

loginBtn.addEventListener("click", login);
pwInput.addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });
logoutBtn.addEventListener("click", logout);
crawlBtn.addEventListener("click", () => startStage("crawl"));
parseBtn.addEventListener("click", () => startStage("parse"));
stopBtn.addEventListener("click", stopStream);

checkAuth();
