const state = {
  me: { is_admin: false, workspace: { slug: "tuck", display_name: "Tuck alumni" } },
  directoryPage: 1,
  selectedSource: "ALL",
};

const tabs = ["directory", "ask", "graph", "sources"];

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response;
}

async function json(path, options = {}) {
  return (await api(path, options)).json();
}

async function init() {
  try {
    state.me = await json("/api/me");
  } catch (_) {
    state.me = { is_admin: false, workspace: { slug: "tuck", display_name: "Tuck alumni" } };
  }
  await loadStats();
  renderTabs();
  window.addEventListener("hashchange", renderRoute);
  renderRoute();
}

async function loadStats() {
  try {
    const stats = await json("/api/stats");
    document.getElementById("header-stats").textContent =
      `${stats.entities || 0} ALUMNI · ${stats.documents || 0} DOCS · ${stats.claims_raw || 0} CLAIMS`;
  } catch (_) {
    document.getElementById("header-stats").textContent = "0 ALUMNI · 0 DOCS · 0 CLAIMS";
  }
}

function renderTabs() {
  const items = state.me.is_admin ? [...tabs, "admin"] : tabs;
  document.getElementById("tabs").innerHTML = items
    .map((tab) => `<a href="#${tab}" data-tab="${tab}">${label(tab)}</a>`)
    .join("");
}

function renderRoute() {
  const route = location.hash.replace(/^#/, "") || "directory";
  const [tab, id] = route.split("/");
  document.querySelectorAll(".tabs a").forEach((link) => {
    link.classList.toggle("active", link.dataset.tab === tab);
  });
  if (tab === "ask") return renderAsk();
  if (tab === "graph") return renderGraph(id);
  if (tab === "sources") return renderSources();
  if (tab === "admin" && state.me.is_admin) return renderAdmin();
  return renderDirectory();
}

async function renderDirectory() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <section class="toolbar">
      <label>Name<input id="q" placeholder="Errik Anderson"></label>
      <label>Organization<input id="org" placeholder="Dartmouth"></label>
      <label>Class year<input id="class-year" placeholder="T'07"></label>
      <button id="search">Search</button>
    </section>
    <section class="filters">
      <div id="source-chips" class="filters"></div>
      <div class="confidence-filter">
        <label class="range-label" for="min-confidence">Min confidence</label>
        <div class="range-control">
          <input id="min-confidence" class="confidence-range" type="range" min="0" max="1" step="0.05" value="0.6">
          <span id="min-confidence-value" class="range-value">0.60</span>
        </div>
      </div>
    </section>
    <div class="pagination"><a class="button secondary" id="prev">prev</a><a class="button secondary" id="next">next</a></div>
    <section id="results" class="results"></section>`;
  document.getElementById("search").onclick = () => loadDirectory(1);
  document.getElementById("prev").onclick = () => loadDirectory(Math.max(1, state.directoryPage - 1));
  document.getElementById("next").onclick = () => loadDirectory(state.directoryPage + 1);
  setupConfidenceSlider();
  await loadSourceChips();
  await loadDirectory(state.directoryPage);
}

async function loadSourceChips() {
  const sources = await json("/api/sources");
  const names = ["ALL", ...sources.sources.map((source) => source.identifier).filter(Boolean)];
  document.getElementById("source-chips").innerHTML = names
    .map((name) => {
      const safeName = escapeHtml(name);
      return `<button class="chip ${state.selectedSource === name ? "active" : ""}" data-source="${safeName}">${safeName}</button>`;
    })
    .join("");
  document.querySelectorAll("[data-source]").forEach((button) => {
    button.onclick = () => {
      state.selectedSource = button.dataset.source;
      loadDirectory(1);
    };
  });
}

function setupConfidenceSlider() {
  const slider = document.getElementById("min-confidence");
  const label = document.getElementById("min-confidence-value");
  const update = () => {
    const value = Number(slider.value || 0);
    label.textContent = value.toFixed(2);
    slider.style.setProperty("--slider-fill", `${value * 100}%`);
  };
  slider.addEventListener("input", update);
  update();
}

async function loadDirectory(page) {
  state.directoryPage = page;
  const params = new URLSearchParams({
    q: value("q"),
    org: value("org"),
    class_year: value("class-year"),
    min_confidence: value("min-confidence") || "0.6",
    page: String(page),
    source: state.selectedSource === "ALL" ? "" : state.selectedSource,
  });
  const data = await json(`/api/directory?${params}`);
  const results = document.getElementById("results");
  if (!data.results.length) {
    results.innerHTML = `<p class="muted">No matches. Try a different name or organization.</p>`;
    return;
  }
  results.innerHTML = data.results.map(entityRow).join("");
}

function entityRow(row) {
  const initials = row.canonical_name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("");
  const confidence = Math.round((row.confidence_avg || 0) * 100);
  const sources = Object.entries(row.source_mix || {}).map(([name, count]) => `<span class="badge">${name} ×${count}</span>`).join(" ");
  return `
    <article class="entity-row">
      <div class="avatar">${initials}</div>
      <div>
        <a class="row-name" href="#graph/${row.entity_id}">${row.canonical_name}</a>
        <div class="row-subtitle">${row.kind} · ${row.connection_count} connections</div>
        <div class="meta-row">${sources || '<span class="badge">CRAWL ONLY</span>'}${row.conflict_count ? '<span class="badge conflict">conflict</span>' : ""}</div>
      </div>
      <div class="right-metrics">
        <div class="muted">${confidence}% confidence</div>
        <div class="confidence-bar"><span style="width:${confidence}%"></span></div>
      </div>
    </article>`;
}

function renderAsk() {
  document.getElementById("app").innerHTML = `
    <section class="ask-box">
      <div>
        <label>Ask<textarea id="question" placeholder="Who has worked together on healthcare projects?"></textarea></label>
        <div class="muted">Search parsed documents</div>
      </div>
      <button id="ask-button">Ask</button>
      <div id="answer" class="answer"></div>
      <div id="citations" class="source-row"></div>
      <div class="meta-row">Was this useful? <button class="secondary" id="up">thumb up</button><button class="secondary" id="down">thumb down</button></div>
    </section>`;
  document.getElementById("ask-button").onclick = ask;
}

async function ask() {
  const answer = document.getElementById("answer");
  const citations = document.getElementById("citations");
  answer.textContent = "";
  citations.textContent = "";
  const response = await api("/api/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question: value("question") }),
  });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value: chunk, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(chunk, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop();
    events.forEach((event) => {
      const line = event.split("\n").find((part) => part.startsWith("data: "));
      if (!line) return;
      const data = JSON.parse(line.slice(6));
      if (data.kind === "token") answer.textContent += data.text;
      if (data.kind === "citations") citations.innerHTML = (data.citations || []).map((c, i) => `<a>[${i + 1}]</a> ${escapeHtml(c.quote || "")}`).join("<br>");
    });
  }
}

async function renderGraph(entityId) {
  const app = document.getElementById("app");
  if (!entityId) {
    app.innerHTML = `<label>Find an entity<input id="graph-search"></label><button id="graph-go">Search</button><div id="graph-results"></div>`;
    document.getElementById("graph-go").onclick = async () => {
      const data = await json(`/api/directory?q=${encodeURIComponent(value("graph-search"))}&min_confidence=0&page_size=10`);
      document.getElementById("graph-results").innerHTML = data.results.map((row) => `<div class="source-row"><a href="#graph/${row.entity_id}" class="row-name">${row.canonical_name}</a></div>`).join("");
    };
    return;
  }
  const data = await json(`/api/entity/${entityId}`);
  app.innerHTML = `
    <section class="panel">
      <div class="hero-name">${data.identity.canonical_name}</div>
      <div class="muted">${data.identity.kind} · ${data.claim_count} claims · ${data.conflict_count} conflicts</div>
      <div class="meta-row"><button class="secondary">Verify</button><button class="secondary">Dispute</button></div>
    </section>
    <section class="split">
      <svg id="graph-svg" class="graph-svg"></svg>
      <aside id="edge-detail" class="panel"><div class="muted">Click an edge for evidence.</div></aside>
    </section>`;
  drawGraph(data);
}

function drawGraph(data) {
  const svg = d3.select("#graph-svg");
  const width = svg.node().clientWidth;
  const height = svg.node().clientHeight;
  const nodes = [{ id: data.identity.entity_id, name: data.identity.canonical_name, kind: data.identity.kind, focus: true }];
  const links = data.connections.map((conn) => {
    nodes.push({ id: conn.neighbor_id, name: conn.neighbor_name, kind: conn.neighbor_kind });
    return { source: data.identity.entity_id, target: conn.neighbor_id, confidence: conn.confidence, predicates: conn.predicates, evidence_count: conn.evidence_count };
  });
  const simulation = d3.forceSimulation(nodes).force("link", d3.forceLink(links).id((d) => d.id).distance(130)).force("charge", d3.forceManyBody().strength(-260)).force("center", d3.forceCenter(width / 2, height / 2));
  const link = svg.selectAll("line").data(links).enter().append("line").attr("stroke", "#888").attr("stroke-width", (d) => 1 + 4 * d.confidence).on("click", (_, d) => {
    document.getElementById("edge-detail").innerHTML = `<div class="entity-name">${d.predicates.join(", ")}</div><div class="muted">${Math.round(d.confidence * 100)}% confidence · ${d.evidence_count} evidence rows</div>`;
  });
  const node = svg.selectAll("g").data(nodes).enter().append("g").call(d3.drag().on("start", dragStarted).on("drag", dragged).on("end", dragEnded));
  node.append("circle").attr("r", (d) => d.focus ? 18 : 12).attr("fill", (d) => d.kind === "person" ? "#00693E" : "#ffffff").attr("stroke", "#00693E").attr("stroke-width", 2);
  node.append("text").text((d) => d.name).attr("x", 16).attr("y", 4).attr("font-size", 12);
  node.on("click", (_, d) => { if (!d.focus) location.hash = `#graph/${d.id}`; });
  simulation.on("tick", () => {
    link.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y).attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
    node.attr("transform", (d) => `translate(${d.x},${d.y})`);
  });
  function dragStarted(event, d) { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }
  function dragged(event, d) { d.fx = event.x; d.fy = event.y; }
  function dragEnded(event, d) { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }
}

async function renderSources() {
  const data = await json("/api/sources");
  document.getElementById("app").innerHTML = `
    <div class="meta-row">${state.me.is_admin ? '<button>Add source</button>' : ""}</div>
    ${data.sources.map((source) => `
      <section class="source-row">
        <div class="entity-name">${source.identifier}</div>
        <div class="muted">${source.kind} · trust ${source.trust_weight}</div>
        <div class="muted">documents ${source.coverage.documents} · claims ${source.coverage.claims} · conflicts ${source.coverage.conflicts}</div>
        ${source.runs.map((run) => `<div class="activity-row">${run.kind} · ${run.status}</div>`).join("")}
      </section>`).join("")}`;
}

async function renderAdmin() {
  const stats = await json("/admin/stats");
  document.getElementById("app").innerHTML = `
    <section class="panel"><strong>Status:</strong> idle</section>
    <section class="admin-grid">
      <div class="stat"><strong>${stats.fetches || 0}</strong>Pages crawled</div>
      <div class="stat"><strong>${stats.documents || 0}</strong>Pages parsed</div>
      <div class="stat"><strong>${stats.entities || 0}</strong>Entities</div>
      <div class="stat"><strong>${stats.entity_neighborhood || 0}</strong>Connections</div>
    </section>
    <section class="panel"><button>Run pipeline</button><div class="muted">Approximate spend depends on pending chunks.</div></section>
    <section class="panel"><h3>Conflicts queue</h3><div id="conflicts"></div></section>
    <details class="panel"><summary>Advanced</summary><button class="secondary">Reset extraction</button></details>`;
  const conflicts = await json("/admin/conflicts");
  document.getElementById("conflicts").innerHTML = conflicts.results.map((item) => `<div class="activity-row">${item.claim_a_id} / ${item.claim_b_id}</div>`).join("") || '<div class="muted">No conflicts.</div>';
}

function value(id) {
  return document.getElementById(id)?.value || "";
}

function label(tab) {
  return tab[0].toUpperCase() + tab.slice(1);
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[char]);
}

init();
