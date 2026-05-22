const loginCard = document.getElementById("login-card");
const adminCard = document.getElementById("admin-card");
const pwInput = document.getElementById("pw");
const loginErr = document.getElementById("login-err");
const loginBtn = document.getElementById("login-btn");
const logoutBtn = document.getElementById("logout-btn");
const crawlBtn = document.getElementById("crawl-btn");
const parseBtn = document.getElementById("parse-btn");
const stopBtn = document.getElementById("stop-btn");
const logEl = document.getElementById("log");

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
  parseBtn.disabled = running;
  stopBtn.disabled = !running;
}

async function startStage(stage) {
  if (evtSource) evtSource.close();
  logEl.innerHTML = "";
  activeStage = stage;
  setRunning(true);

  try {
    const res = await fetch(`/admin/${stage}/start`, { method: "POST" });
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
      if (data.kind === "done") stopStream();
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
parseBtn.addEventListener("click", () => startStage("parse"));
stopBtn.addEventListener("click", stopStream);

checkAuth();
