"use strict";

const state = {
  me: null,
  stats: null,
  selectedSource: "all",
  directoryPage: 1,
  directoryFilters: { q: "", org: "", class_year: "" },
  sourcesCache: null,
};

const TAB_DEFS = [
  { id: "directory", label: "Directory", icon: "ti-list-search" },
  { id: "ask", label: "Ask", icon: "ti-message-question" },
  { id: "graph", label: "Graph", icon: "ti-vector-triangle" },
  { id: "sources", label: "Sources", icon: "ti-database" },
];

const SOURCE_KINDS = [
  {
    id: "domain",
    kind: "domain",
    label: "Webcrawl a sitemap",
    icon: "ti-world",
    description: "Crawl a public website via sitemap.xml.",
    fields: [
      {
        name: "identifier",
        label: "Domain or sitemap URL",
        placeholder: "tuck.dartmouth.edu",
        required: true,
      },
    ],
  },
  {
    id: "structured-file",
    kind: "file",
    label: "Upload structured data",
    icon: "ti-table-import",
    description: "Upload xlsx or csv records.",
    hint: "Spreadsheet rows become records. Columns become attributes.",
    fields: [
      {
        name: "file",
        label: "File (xlsx, csv)",
        type: "file",
        accept: ".xlsx,.csv",
        required: true,
      },
    ],
  },
  {
    id: "unstructured-file",
    kind: "file",
    label: "Upload unstructured data",
    icon: "ti-file-text",
    description: "Upload text, markdown, or pdf documents.",
    hint: "Documents are chunked and extracted via the LLM pipeline.",
    fields: [
      {
        name: "file",
        label: "File (txt, pdf, md)",
        type: "file",
        accept: ".txt,.md,.pdf",
        required: true,
      },
    ],
  },
];

const STAT_CARDS = [
  {
    key: "documents",
    label: "Documents",
    ariaLabel: "What is a document?",
    definition:
      "Articles, files, or records fetched and cleaned. One xlsx row or one news article = one document.",
  },
  {
    key: "claims",
    label: "Claims",
    ariaLabel: "What is a claim?",
    definition:
      "Structured statements extracted from documents. One document typically yields 5–30 claims.",
  },
  {
    key: "entities",
    label: "Entities",
    ariaLabel: "What is an entity?",
    definition:
      "Distinct people, organizations, or projects that claims refer to. One person mentioned across 20 documents is still one entity.",
  },
  {
    key: "sources",
    label: "Sources",
    ariaLabel: "What is a source?",
    definition:
      "Registered ingestion endpoints (files, sitemaps, APIs) that produce documents.",
  },
];

document.addEventListener("DOMContentLoaded", init);

async function init() {
  await Promise.all([loadMe(), loadStats()]);
  renderTopbar();
  window.addEventListener("hashchange", renderRoute);
  renderRoute();
}

async function loadMe() {
  try {
    state.me = await getJSON("/api/me");
  } catch (_) {
    state.me = {
      is_admin: false,
      workspace: {
        slug: "tuck",
        display_name: "Workspace",
        tagline: "",
      },
      admin_login_url: "/admin/login",
      admin_logout_url: "/admin/logout",
    };
  }
}

async function loadStats() {
  try {
    state.stats = await getJSON("/api/stats");
  } catch (_) {
    state.stats = {};
  }
}

function renderTopbar() {
  ensureBrandHomeLink();
  const sub = document.getElementById("brand-sub");
  const workspaceName = state.me?.workspace?.display_name || "Workspace";
  const entityCount = state.stats?.entities || 0;
  const peopleLabel = entityCount === 1 ? "person" : "people";
  sub.innerHTML = `<span>${escapeHtml(workspaceName)} · ${formatNumber(entityCount)} ${peopleLabel}</span>`;

  const tabs = TAB_DEFS;
  const nav = document.getElementById("nav-pills");
  const activeTab = currentTab();
  nav.innerHTML =
    tabs
      .map(
        (tab) =>
          `<a class="nav-pill ${activeTab === tab.id ? "active" : ""}" data-tab="${tab.id}" href="#${tab.id}"><i class="ti ${tab.icon}" aria-hidden="true"></i>${escapeHtml(tab.label)}</a>`,
      )
      .join("") +
    `<span class="user-menu-anchor">
       <button class="avatar" id="user-avatar" type="button" aria-label="Open user menu" title="${escapeAttr(state.me?.is_admin ? "admin" : "user")}">${state.me?.is_admin ? "AD" : "NC"}</button>
     </span>`;
  byId("user-avatar").onclick = toggleUserMenu;
}

function ensureBrandHomeLink() {
  const brand = document.querySelector(".brand");
  if (!brand || brand.querySelector(".brand-home")) return;
  const link = document.createElement("a");
  link.className = "brand-home";
  link.href = "#directory";
  link.setAttribute("aria-label", "Go to Directory");
  while (brand.firstChild) link.appendChild(brand.firstChild);
  brand.appendChild(link);
}

function toggleUserMenu(event) {
  event.stopPropagation();
  const anchor = event.currentTarget.closest(".user-menu-anchor");
  const existing = anchor.querySelector(".menu");
  if (existing) {
    existing.remove();
    return;
  }
  document.querySelectorAll(".menu").forEach((menu) => menu.remove());
  const workspaceName = state.me?.workspace?.display_name || "Workspace";
  const menu = document.createElement("div");
  menu.className = "menu user-menu";
  menu.innerHTML = `
    <div class="menu-header">Workspace: ${escapeHtml(workspaceName)}</div>
    ${
      state.me?.is_admin
        ? `<button class="menu-item" data-act="admin-logout"><i class="ti ti-logout" aria-hidden="true"></i> Sign out of admin</button>`
        : `<button class="menu-item" data-act="admin-login"><i class="ti ti-login" aria-hidden="true"></i> Sign in as admin</button>`
    }
  `;
  anchor.appendChild(menu);
  menu.querySelector("[data-act]").onclick = (clickEvent) => {
    clickEvent.stopPropagation();
    const action = clickEvent.currentTarget.dataset.act;
    if (action === "admin-login") {
      window.location.href = state.me?.admin_login_url || "/admin/login";
      return;
    }
    adminLogout(clickEvent);
  };
  setTimeout(() => {
    document.addEventListener(
      "click",
      function onAway() {
        menu.remove();
        document.removeEventListener("click", onAway);
      },
      { once: true },
    );
  }, 0);
}

function currentTab() {
  const route = location.hash.replace(/^#/, "") || "directory";
  return route.split("/")[0];
}

function renderRoute() {
  const route = location.hash.replace(/^#/, "") || "directory";
  const [tab, ...rest] = route.split("/");
  if (tab === "admin") {
    history.replaceState(null, "", "#sources");
    renderTopbar();
    return renderSources([]);
  }
  renderTopbar();
  if (tab === "ask") return renderAsk();
  if (tab === "graph") return renderGraph(rest[0]);
  if (tab === "sources") return renderSources(rest);
  return renderDirectory();
}

/* ───── Directory ───── */

async function renderDirectory() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="page-content">
      <section class="toolbar">
        <label class="input-with-icon">
          <i class="ti ti-search icon" aria-hidden="true"></i>
          <input id="dir-q" placeholder="Name" value="${escapeAttr(state.directoryFilters.q)}" />
        </label>
        <input id="dir-org" placeholder="Organization" value="${escapeAttr(state.directoryFilters.org)}" />
        <input id="dir-class" placeholder="Class year" value="${escapeAttr(state.directoryFilters.class_year)}" />
        <button class="btn-primary" id="dir-search">
          <i class="ti ti-search" aria-hidden="true"></i> Search
        </button>
      </section>
      <section class="chip-strip" id="chip-strip">
        <span class="chip-strip-label">Sources</span>
        <span class="chip-strip-label">Loading…</span>
      </section>
      <section class="results" id="results">
        <div class="empty-state"><i class="ti ti-loader" aria-hidden="true"></i><div>Loading…</div></div>
      </section>
      <div class="pagination" id="pagination"></div>
    </div>
  `;
  const onSearch = () => {
    state.directoryFilters = {
      q: byId("dir-q").value,
      org: byId("dir-org").value,
      class_year: byId("dir-class").value,
    };
    state.directoryPage = 1;
    loadDirectory();
  };
  byId("dir-search").onclick = onSearch;
  ["dir-q", "dir-org", "dir-class"].forEach((id) => {
    byId(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") onSearch();
    });
  });
  await loadSourceChips();
  await loadDirectory();
}

async function loadSourceChips() {
  const strip = byId("chip-strip");
  try {
    const data = await getJSON("/api/sources");
    state.sourcesCache = data.sources || [];
    const active = state.sourcesCache.filter((s) => s.status !== "archived");
    const chips = [
      { id: "all", label: "All" },
      ...active.map((s) => ({
        id: s.identifier,
        label: s.display_name || s.identifier,
      })),
    ];
    strip.innerHTML =
      `<span class="chip-strip-label">Sources</span>` +
      chips
        .map(
          (c) =>
            `<button class="chip ${state.selectedSource === c.id ? "active" : ""}" data-source="${escapeAttr(c.id)}">${escapeHtml(c.label)}</button>`,
        )
        .join("") +
      `<span class="result-count" id="result-count"></span>`;
    strip.querySelectorAll(".chip").forEach((chip) => {
      chip.onclick = () => {
        state.selectedSource = chip.dataset.source;
        state.directoryPage = 1;
        loadSourceChips();
        loadDirectory();
      };
    });
  } catch (e) {
    strip.innerHTML =
      `<span class="chip-strip-label">Sources</span><span class="chip-strip-label">Unable to load</span>`;
  }
}

async function loadDirectory() {
  const params = new URLSearchParams({
    q: state.directoryFilters.q || "",
    org: state.directoryFilters.org || "",
    class_year: state.directoryFilters.class_year || "",
    source: state.selectedSource === "all" ? "" : state.selectedSource,
    page: String(state.directoryPage),
  });
  const results = byId("results");
  try {
    const data = await getJSON(`/api/directory?${params.toString()}`);
    const total = data.total || 0;
    const totalPages = Math.max(1, Math.ceil(total / (data.page_size || 25)));
    const countLabel = byId("result-count");
    if (countLabel) {
      countLabel.textContent = total
        ? `${total} result${total === 1 ? "" : "s"} · page ${data.page} of ${totalPages}`
        : "no results";
    }
    if (!data.results.length) {
      const entitiesTotal = state.stats?.entities || 0;
      results.innerHTML = entitiesTotal
        ? `<div class="empty-state"><i class="ti ti-search-off" aria-hidden="true"></i><div>No matches. Try a different name or organization.</div></div>`
        : `<div class="empty-state"><i class="ti ti-database-off" aria-hidden="true"></i><div>No data yet. ${state.me?.is_admin ? "Go to <a href=\"#sources\">Sources</a> to add and ingest data." : "Sign in as admin to ingest sources."}</div></div>`;
      byId("pagination").innerHTML = "";
      return;
    }
    results.innerHTML = data.results.map(directoryRow).join("");
    results.querySelectorAll(".entity-row").forEach((row) => {
      row.onclick = (e) => {
        if (e.target.closest(".source-badge,.conflict-pill")) return;
        location.hash = `#graph/${row.dataset.entityId}`;
      };
    });
    renderPagination(data.page, totalPages);
  } catch (e) {
    results.innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle" aria-hidden="true"></i><div>Unable to load directory: ${escapeHtml(e.message)}</div></div>`;
  }
}

function directoryRow(row) {
  const initials = (row.canonical_name || "")
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0] || "")
    .join("")
    .toUpperCase();
  const hasConflict = (row.conflict_count || 0) > 0;
  const sourceMix = row.source_mix || {};
  const sourceLabels = state.sourcesCache || [];
  const labelFor = (identifier) => {
    const found = sourceLabels.find((s) => s.identifier === identifier);
    return found ? found.display_name || found.identifier : identifier;
  };
  const sourceCount = Object.keys(sourceMix).length;
  const badges = Object.entries(sourceMix)
    .map(
      ([name, count]) =>
        `<span class="source-badge">${escapeHtml(labelFor(name))}${count > 1 ? ` ×${count}` : ""}</span>`,
    )
    .join("");
  const conflictPill = hasConflict
    ? `<span class="conflict-pill"><i class="ti ti-alert-triangle" aria-hidden="true"></i>${row.conflict_count} conflict${row.conflict_count === 1 ? "" : "s"}</span>`
    : "";
  const isCrawlOnly = sourceCount === 0;
  const crawlPill = isCrawlOnly
    ? `<span class="mention-only-pill">Mentioned only</span>`
    : "";
  const classYear =
    (row.primary_attributes && row.primary_attributes.class_year) || "";
  return `
    <article class="entity-row ${hasConflict ? "has-conflict" : ""} ${isCrawlOnly ? "crawl-only" : ""}" data-entity-id="${escapeAttr(row.entity_id)}">
      <div class="avatar-circle">${escapeHtml(initials || "??")}</div>
      <div>
        <div class="row-name-line">
          <span class="row-name">${escapeHtml(row.canonical_name || "Unknown")}</span>
          ${classYear ? `<span class="row-meta">${escapeHtml(String(classYear))}</span>` : ""}
          ${conflictPill}
          ${crawlPill}
        </div>
        <div class="row-bio">${escapeHtml(rowBio(row))}</div>
      </div>
      <div class="row-source-badges">${badges}</div>
    </article>`;
}

function rowBio(row) {
  const attrs = row.primary_attributes || {};
  const parts = [];
  if (attrs.current_title) parts.push(String(attrs.current_title));
  if (attrs.current_employer) parts.push(String(attrs.current_employer));
  if (!parts.length && row.kind) parts.push(`${capitalize(row.kind)}`);
  if (row.connection_count) {
    parts.push(`${row.connection_count} connection${row.connection_count === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}

function renderPagination(page, totalPages) {
  const pag = byId("pagination");
  if (totalPages <= 1) {
    pag.innerHTML = "";
    return;
  }
  pag.innerHTML = `
    <button class="btn-secondary" ${page <= 1 ? "disabled" : ""}>Previous</button>
    <button class="btn-secondary accent" ${page >= totalPages ? "disabled" : ""}>Next</button>
  `;
  const [prev, next] = pag.querySelectorAll("button");
  prev.onclick = () => {
    state.directoryPage = Math.max(1, page - 1);
    loadDirectory();
  };
  next.onclick = () => {
    state.directoryPage = page + 1;
    loadDirectory();
  };
}

/* ───── Ask ───── */

function renderAsk() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <section class="ask-question-box">
      <i class="ti ti-message-question icon" aria-hidden="true"></i>
      <textarea id="ask-input" placeholder="Ask about people, projects, or organizations…" rows="1"></textarea>
      <button class="btn-primary" id="ask-submit"><i class="ti ti-send" aria-hidden="true"></i> Ask</button>
    </section>
    <div id="ask-result"></div>
  `;
  const input = byId("ask-input");
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${input.scrollHeight}px`;
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask();
    }
  });
  byId("ask-submit").onclick = ask;
  input.focus();
}

async function ask() {
  const input = byId("ask-input");
  const question = input.value.trim();
  if (!question) return;
  const result = byId("ask-result");
  result.innerHTML = `
    <div class="ask-answer">
      <div class="ask-answer-label"><i class="ti ti-sparkles" aria-hidden="true"></i><span>Answer</span></div>
      <div class="ask-answer-text" id="answer-text"><span class="muted">Thinking…</span></div>
    </div>
    <div class="ask-citations" id="ask-citations-wrap" style="display:none">
      <div class="ask-citations-label">Sources</div>
      <div id="ask-citations"></div>
    </div>
  `;
  const answerText = byId("answer-text");
  answerText.textContent = "";
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      events.forEach((event) => {
        const line = event.split("\n").find((p) => p.startsWith("data: "));
        if (!line) return;
        let payload;
        try {
          payload = JSON.parse(line.slice(6));
        } catch (_) {
          return;
        }
        if (payload.kind === "token") {
          answerText.textContent += payload.text;
        } else if (payload.kind === "citations") {
          renderCitations(payload.citations || []);
        }
      });
    }
    if (!answerText.textContent.trim()) {
      answerText.innerHTML = `<span class="muted">No answer could be generated.</span>`;
    }
  } catch (e) {
    answerText.innerHTML = `<span class="muted">Unable to get an answer: ${escapeHtml(e.message)}</span>`;
  }
}

function renderCitations(citations) {
  if (!citations.length) return;
  const wrap = byId("ask-citations-wrap");
  const list = byId("ask-citations");
  wrap.style.display = "";
  list.innerHTML = citations
    .map(
      (c, i) => `
      <div class="ask-citation">
        <span class="num">[${i + 1}]</span>
        <span class="muted small">${escapeHtml(c.quote || c.source_id || "source")}</span>
        <i class="ti ti-external-link" aria-hidden="true"></i>
      </div>`,
    )
    .join("");
}

/* ───── Graph ───── */

async function renderGraph(entityId) {
  const app = document.getElementById("app");
  if (!entityId) {
    app.innerHTML = `
      <div class="graph-empty">
        <h2>Open a graph view</h2>
        <p class="muted">Find a person, organization, or project to see its connections.</p>
        <div class="search-row">
          <input id="graph-search" placeholder="Search for a person, organization, or project" />
          <button class="btn-primary" id="graph-search-go"><i class="ti ti-search" aria-hidden="true"></i> Find</button>
        </div>
        <div id="graph-results"></div>
      </div>
    `;
    const go = async () => {
      const q = byId("graph-search").value.trim();
      if (!q) return;
      const data = await getJSON(`/api/directory?q=${encodeURIComponent(q)}&page_size=20`);
      const out = byId("graph-results");
      if (!data.results.length) {
        out.innerHTML = `<div class="empty-state"><i class="ti ti-search-off" aria-hidden="true"></i><div>No matches.</div></div>`;
        return;
      }
      out.innerHTML = data.results.map(directoryRow).join("");
      out.querySelectorAll(".entity-row").forEach((row) => {
        row.onclick = () => (location.hash = `#graph/${row.dataset.entityId}`);
      });
    };
    byId("graph-search-go").onclick = go;
    byId("graph-search").addEventListener("keydown", (e) => {
      if (e.key === "Enter") go();
    });
    return;
  }
  app.innerHTML = `
    <nav class="breadcrumb">
      <a href="#directory">Directory</a>
      <i class="ti ti-chevron-right" aria-hidden="true"></i>
      <span id="bc-name">Loading…</span>
    </nav>
    <div id="entity-panel"></div>
  `;
  try {
    const data = await getJSON(`/api/entity/${entityId}`);
    byId("bc-name").textContent = data.identity.canonical_name;
    renderEntityPanel(data);
  } catch (e) {
    byId("entity-panel").innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load: ${escapeHtml(e.message)}</div></div>`;
  }
}

function renderEntityPanel(data) {
  const initials = (data.identity.canonical_name || "")
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0] || "")
    .join("")
    .toUpperCase();
  const subtitleParts = [];
  const attrs = data.primary_attributes || {};
  if (attrs.current_title) subtitleParts.push(attrs.current_title);
  if (attrs.current_employer) subtitleParts.push(attrs.current_employer);
  if (!subtitleParts.length) subtitleParts.push(capitalize(data.identity.kind || "entity"));
  const hasConflict = (data.conflict_count || 0) > 0;
  const conflictPill = hasConflict
    ? `<span class="conflict-pill"><i class="ti ti-alert-triangle"></i>${data.conflict_count} conflict${data.conflict_count === 1 ? "" : "s"}</span>`
    : "";
  byId("entity-panel").innerHTML = `
    <div class="entity-hero">
      <div class="avatar-big">${escapeHtml(initials || "??")}</div>
      <div style="flex:1">
        <h1>${escapeHtml(data.identity.canonical_name)}</h1>
        ${attrs.class_year ? `<div class="subtitle">${escapeHtml(String(attrs.class_year))}</div>` : ""}
        <div class="subtitle">${escapeHtml(subtitleParts.join(" · "))}</div>
        <div class="meta">
          <span><strong>${data.connections.length}</strong> connections</span>
          <span><strong>${data.claim_count || 0}</strong> claims</span>
          ${conflictPill}
        </div>
      </div>
    </div>
    <section class="graph-split">
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">Connections</div>
          <div class="muted small">${data.connections.length} total</div>
        </div>
        <svg id="graph-svg" class="graph-svg"></svg>
        <div class="graph-legend">
          <span class="swatch"><span class="legend-person"></span>person</span>
          <span class="swatch"><span class="legend-org"></span>organization</span>
          <span class="swatch"><span class="legend-project"></span>project</span>
          <span class="swatch" style="margin-left:auto"><svg width="20" height="3"><line x1="0" y1="1.5" x2="20" y2="1.5" stroke="#00693E" stroke-width="2"/></svg> verified edge</span>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">Selected claim</div>
        </div>
        <div id="claim-panel">
          <div class="muted small">Click an edge or a connection to inspect the supporting evidence.</div>
        </div>
      </div>
    </section>
  `;
  drawGraph(data);
}

function drawGraph(data) {
  if (typeof d3 === "undefined") return;
  const svg = d3.select("#graph-svg");
  const node = svg.node();
  if (!node) return;
  const width = node.clientWidth || 600;
  const height = node.clientHeight || 360;
  svg.attr("viewBox", `0 0 ${width} ${height}`);
  svg.selectAll("*").remove();

  const nodes = [
    { id: data.identity.entity_id, name: data.identity.canonical_name, kind: data.identity.kind, focus: true },
  ];
  const links = (data.connections || []).map((conn) => {
    nodes.push({ id: conn.neighbor_id, name: conn.neighbor_name, kind: conn.neighbor_kind });
    return {
      source: data.identity.entity_id,
      target: conn.neighbor_id,
      predicates: conn.predicates,
      confidence: conn.confidence,
      evidence_count: conn.evidence_count,
      is_resolved: conn.is_resolved,
    };
  });

  if (!links.length) {
    svg
      .append("text")
      .attr("x", width / 2)
      .attr("y", height / 2)
      .attr("text-anchor", "middle")
      .attr("fill", "#888888")
      .attr("font-size", "13")
      .text("No connections yet.");
    return;
  }

  const sim = d3
    .forceSimulation(nodes)
    .force(
      "link",
      d3
        .forceLink(links)
        .id((d) => d.id)
        .distance(130),
    )
    .force("charge", d3.forceManyBody().strength(-280))
    .force("center", d3.forceCenter(width / 2, height / 2));

  const link = svg
    .append("g")
    .selectAll("line")
    .data(links)
    .enter()
    .append("line")
    .attr("stroke", (d) => (d.is_resolved === false ? "#888888" : "#00693E"))
    .attr("stroke-dasharray", (d) => (d.is_resolved === false ? "4 3" : null))
    .attr("stroke-width", (d) => Math.max(1, (d.confidence || 0.5) * 3))
    .attr("opacity", (d) => 0.4 + 0.6 * (d.confidence || 0.5))
    .attr("cursor", "pointer")
    .on("click", (_e, d) => loadClaimForEdge(d));

  const nodeG = svg
    .append("g")
    .selectAll("g")
    .data(nodes)
    .enter()
    .append("g")
    .attr("cursor", (d) => (d.focus ? "default" : "pointer"))
    .call(
      d3
        .drag()
        .on("start", (event, d) => {
          if (!event.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on("drag", (event, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on("end", (event, d) => {
          if (!event.active) sim.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        }),
    );

  nodeG.each(function (d) {
    const g = d3.select(this);
    const isOrg = d.kind === "org";
    const isProject = d.kind === "project";
    if (isOrg || isProject) {
      g.append("rect")
        .attr("x", d.focus ? -22 : -16)
        .attr("y", d.focus ? -22 : -16)
        .attr("width", d.focus ? 44 : 32)
        .attr("height", d.focus ? 44 : 32)
        .attr("rx", 4)
        .attr("fill", isProject ? "white" : d.focus ? "#00693E" : "white")
        .attr("stroke", "#00693E")
        .attr("stroke-width", 2);
    } else {
      g.append("circle")
        .attr("r", d.focus ? 22 : 16)
        .attr("fill", d.focus ? "#00693E" : "white")
        .attr("stroke", "#00693E")
        .attr("stroke-width", 2);
    }
    g.append("text")
      .attr("y", d.focus ? 38 : 32)
      .attr("text-anchor", "middle")
      .attr("font-size", d.focus ? 12 : 11)
      .attr("fill", "#1A1A1A")
      .attr("font-weight", d.focus ? 500 : 400)
      .text(d.name || "");
  });

  nodeG.on("click", (_e, d) => {
    if (d.focus) return;
    location.hash = `#graph/${d.id}`;
  });

  sim.on("tick", () => {
    link
      .attr("x1", (d) => d.source.x)
      .attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x)
      .attr("y2", (d) => d.target.y);
    nodeG.attr("transform", (d) => `translate(${d.x},${d.y})`);
  });
}

function loadClaimForEdge(edge) {
  const panel = byId("claim-panel");
  const subject = edge.source.name || edge.source;
  const object = edge.target.name || edge.target;
  panel.innerHTML = `
    <div class="claim-statement">
      <span class="entity">${escapeHtml(subject)}</span>
      <span class="predicate">${escapeHtml((edge.predicates || []).join(", "))}</span>
      <span class="entity">${escapeHtml(object)}</span>
    </div>
    <div class="claim-meta">
      <span><strong>${edge.evidence_count || 0}</strong> evidence rows</span>
      <span>·</span>
      <span>${Math.round((edge.confidence || 0) * 100)}% corroborated</span>
    </div>
    <div class="muted small">Click the names in this graph to drill into each entity.</div>
  `;
}

/* ───── Sources ───── */

async function renderSources(parts) {
  if (parts[0]) {
    return renderSourceDetail(parts[0], parts[1]);
  }
  const app = document.getElementById("app");
  const adminActions = state.me?.is_admin
    ? `<div class="source-card-actions">
         <button class="btn-primary" id="add-source"><i class="ti ti-plus"></i> Add source</button>
         <button class="btn-primary" id="run-pipeline"><i class="ti ti-player-play"></i> Run pipeline</button>
       </div>`
    : "";
  app.innerHTML = `
    <div class="stats-grid" id="sources-stats">${statCards(state.stats || {})}</div>
    <div class="section-header">
      <div>
        <h1>Sources</h1>
        <div class="subtitle" id="sources-summary">Loading…</div>
      </div>
      ${adminActions}
    </div>
    <div class="sources-list" id="sources-list">
      <div class="empty-state"><i class="ti ti-loader" aria-hidden="true"></i><div>Loading…</div></div>
    </div>
    ${
      state.me?.is_admin
        ? `<details class="conflicts" open>
             <summary class="conflicts-header">
               <div class="panel-title">Conflicts</div>
               <span class="conflicts-count-pill" id="conflict-count">0 unresolved</span>
             </summary>
             <div id="conflicts-body"><div class="muted small">Loading…</div></div>
           </details>`
        : ""
    }
  `;
  if (state.me?.is_admin) {
    byId("add-source").onclick = openAddSourceModal;
    byId("run-pipeline").onclick = runFullPipeline;
  }
  setupStatInfoButtons();
  await Promise.all([
    loadSourcesStats(),
    loadSourcesList(),
    state.me?.is_admin ? loadAdminConflicts() : Promise.resolve(),
  ]);
}

async function loadSourcesStats() {
  const statsGrid = byId("sources-stats");
  try {
    const stats = await getJSON("/api/stats");
    state.stats = stats;
    statsGrid.innerHTML = statCards(stats);
    setupStatInfoButtons();
    renderTopbar();
  } catch (e) {
    statsGrid.innerHTML = `<div class="muted small">Unable to load stats: ${escapeHtml(e.message)}</div>`;
  }
}

function statCards(stats) {
  return STAT_CARDS.map(
    (card) => `
      <div class="stat-card">
        <div class="stat-card-head">
          <div class="label">${escapeHtml(card.label)}</div>
          <button class="stat-info" aria-label="${escapeAttr(card.ariaLabel)}" data-term="${escapeAttr(card.key)}"><i class="ti ti-info-circle" aria-hidden="true"></i></button>
        </div>
        <div class="value">${formatNumber(stats?.[card.key])}</div>
      </div>`,
  ).join("");
}

function setupStatInfoButtons() {
  document.querySelectorAll(".stat-info").forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation();
      const card = button.closest(".stat-card");
      const existing = card?.querySelector(".stat-tooltip");
      closeStatTooltips();
      if (!card || existing) return;
      const def = STAT_CARDS.find((item) => item.key === button.dataset.term)?.definition;
      if (!def) return;
      const tooltip = document.createElement("div");
      tooltip.className = "menu stat-tooltip";
      tooltip.textContent = def;
      card.appendChild(tooltip);
      setTimeout(() => document.addEventListener("click", closeStatTooltips, { once: true }), 0);
    };
  });
}

function closeStatTooltips() {
  document.querySelectorAll(".stat-tooltip").forEach((tooltip) => tooltip.remove());
}

async function loadSourcesList() {
  try {
    const data = await getJSON("/api/sources");
    const sources = data.sources || [];
    state.sourcesCache = sources;
    const active = sources.filter((s) => s.status === "active");
    const paused = sources.filter((s) => s.status === "paused");
    const archived = sources.filter((s) => s.status === "archived");
    byId("sources-summary").textContent =
      `${active.length} active · ${paused.length} paused · ${archived.length} archived`;
    const list = byId("sources-list");
    if (!sources.length) {
      list.innerHTML = state.me?.is_admin
        ? `<div class="empty-state">
             <i class="ti ti-database-off" aria-hidden="true"></i>
             <div>No sources yet. Click 'Add source' to add one.</div>
             <button class="btn-primary" id="empty-add-source"><i class="ti ti-plus" aria-hidden="true"></i> Add source</button>
           </div>`
        : `<div class="empty-state"><i class="ti ti-database-off" aria-hidden="true"></i><div>No sources yet.</div></div>`;
      const emptyAddSource = byId("empty-add-source");
      if (emptyAddSource) emptyAddSource.onclick = openAddSourceModal;
      return;
    }
    list.innerHTML = sources
      .filter((s) => s.status !== "archived")
      .map((s, i) => sourceCard(s, i + 1))
      .join("");
    list.querySelectorAll(".source-card").forEach((card) => {
      const id = card.dataset.sourceId;
      card.onclick = (e) => {
        if (e.target.closest("button")) return;
        location.hash = `#sources/${id}`;
      };
      const crawl = card.querySelector("[data-action=crawl]");
      const parse = card.querySelector("[data-action=parse]");
      const resume = card.querySelector("[data-action=resume]");
      const menuBtn = card.querySelector("[data-action=menu]");
      if (crawl) crawl.onclick = (e) => { e.stopPropagation(); runSourceAction(id, "crawl"); };
      if (parse) parse.onclick = (e) => { e.stopPropagation(); runSourceAction(id, "parse"); };
      if (resume) resume.onclick = (e) => {
        e.stopPropagation();
        updateSourceStatus(id, "active");
      };
      if (menuBtn) menuBtn.onclick = (e) => {
        e.stopPropagation();
        toggleMenu(card, id);
      };
    });
  } catch (e) {
    byId("sources-list").innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load sources: ${escapeHtml(e.message)}</div></div>`;
  }
}

function sourceCard(source, index) {
  const paused = source.status === "paused";
  const indexStr = String(index).padStart(2, "0");
  const meta = sourceMetaLine(source);
  const actions = paused
    ? `<button class="btn-primary" data-action="resume" style="padding:6px 10px;font-size:12px"><i class="ti ti-player-play"></i> Resume</button>`
    : `<button class="btn-source" data-action="crawl"><i class="ti ti-download"></i> Crawl</button>
       <button class="btn-source" data-action="parse"><i class="ti ti-cpu"></i> Parse</button>`;
  const menuButton = state.me?.is_admin
    ? `<button class="btn-source icon-only" data-action="menu" aria-label="More"><i class="ti ti-dots"></i></button>`
    : "";
  return `
    <article class="source-card ${paused ? "paused" : ""}" data-source-id="${escapeAttr(source.id)}">
      <div class="source-card-head">
        <div class="source-card-identity">
          <span class="source-card-index">${indexStr}</span>
          <i class="ti ${source.icon_hint || "ti-database"} source-card-icon"></i>
          <div>
            <div class="source-card-name">${escapeHtml(source.display_name || source.identifier)}</div>
            <div class="source-card-meta">${escapeHtml(meta)}</div>
          </div>
        </div>
        <span class="status-pill ${source.status}">${capitalize(source.status)}</span>
      </div>
      <div class="source-card-foot">
        <div class="source-stats">
          <span><strong>${source.coverage.documents}</strong> docs</span>
          <span><strong>${source.coverage.claims}</strong> claims</span>
          <span class="muted">${source.last_run_at ? `last run ${timeAgo(source.last_run_at)}` : "never run"}</span>
        </div>
        ${state.me?.is_admin ? `<div class="source-card-actions">${actions}${menuButton}</div>` : ""}
      </div>
    </article>
  `;
}

function sourceMetaLine(source) {
  const kindLabel =
    {
      domain: "Sitemap",
      file: "Manual upload",
    }[source.kind] || source.kind;
  return `${kindLabel} · ${source.identifier} · trust ${formatTrust(source.trust_weight)}`;
}

function formatTrust(value) {
  if (value == null) return "—";
  return Number(value).toFixed(2);
}

function toggleMenu(card, sourceId) {
  const existing = card.querySelector(".menu");
  if (existing) {
    existing.remove();
    return;
  }
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  const source = (state.sourcesCache || []).find((s) => s.id === sourceId);
  const isPaused = source && source.status === "paused";
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.innerHTML = `
    <button class="menu-item" data-act="rename"><i class="ti ti-pencil"></i> Rename</button>
    <button class="menu-item" data-act="trust"><i class="ti ti-percentage"></i> Edit trust</button>
    ${source && source.kind === "file" ? `<button class="menu-item" data-act="download"><i class="ti ti-download"></i> Download original</button>` : ""}
    ${isPaused ? "" : `<button class="menu-item" data-act="pause"><i class="ti ti-player-pause"></i> Pause</button>`}
    <button class="menu-item danger" data-act="archive"><i class="ti ti-archive"></i> Archive</button>
  `;
  card.querySelector(".source-card-actions").appendChild(menu);
  menu.querySelectorAll("button").forEach((b) => {
    b.onclick = (e) => {
      e.stopPropagation();
      menu.remove();
      handleMenuAction(b.dataset.act, sourceId);
    };
  });
  setTimeout(() => {
    document.addEventListener(
      "click",
      function onAway() {
        menu.remove();
        document.removeEventListener("click", onAway);
      },
      { once: true },
    );
  }, 0);
}

async function handleMenuAction(action, sourceId) {
  const source = (state.sourcesCache || []).find((s) => s.id === sourceId);
  if (!source) return;
  if (action === "rename") {
    const name = prompt("New display name", source.display_name || source.identifier);
    if (!name) return;
    await patchSource(sourceId, { display_name: name });
    toast("Renamed.", "success");
    loadSourcesList();
  } else if (action === "trust") {
    const value = prompt("Trust weight (0.0–1.0)", String(source.trust_weight ?? 0.5));
    if (!value) return;
    const n = Number(value);
    if (Number.isNaN(n) || n < 0 || n > 1) {
      toast("Trust must be between 0 and 1.", "error");
      return;
    }
    await patchSource(sourceId, { trust_weight: n });
    toast("Trust updated.", "success");
    loadSourcesList();
  } else if (action === "pause") {
    await patchSource(sourceId, { status: "paused" });
    toast("Source paused.", "success");
    loadSourcesList();
  } else if (action === "archive") {
    if (!confirm("Archive this source? Data is preserved; no new ingestion will run.")) return;
    await fetch(`/admin/sources/${sourceId}`, { method: "DELETE" });
    toast("Source archived.", "success");
    loadSourcesList();
  } else if (action === "download") {
    window.location.href = `/api/sources/${sourceId}/download`;
  }
}

async function patchSource(id, body) {
  await fetch(`/admin/sources/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function updateSourceStatus(id, status) {
  await patchSource(id, { status });
  toast(status === "active" ? "Source resumed." : `Source ${status}.`, "success");
  loadSourcesList();
}

async function runSourceAction(sourceId, action) {
  try {
    const res = await fetch(`/admin/sources/${sourceId}/${action}`, { method: "POST" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || res.statusText);
    }
    toast(
      action === "crawl" ? "Crawl started." : "Parse started in background.",
      "success",
    );
    loadStats();
  } catch (e) {
    toast(`${action} failed: ${e.message}`, "error");
  }
}

/* ───── Source detail ───── */

async function renderSourceDetail(sourceId, tab) {
  const activeTab = tab || "documents";
  const app = document.getElementById("app");
  app.innerHTML = `
    <nav class="breadcrumb">
      <a href="#sources">Sources</a>
      <i class="ti ti-chevron-right"></i>
      <span id="bc-source-name">Loading…</span>
    </nav>
    <div id="source-detail-head"></div>
    <div class="tabs-sub" id="source-detail-tabs">
      <button class="tab-sub ${activeTab === "documents" ? "active" : ""}" data-tab="documents">Documents</button>
      <button class="tab-sub ${activeTab === "runs" ? "active" : ""}" data-tab="runs">Runs</button>
      <button class="tab-sub ${activeTab === "config" ? "active" : ""}" data-tab="config">Config</button>
    </div>
    <div class="tab-content" id="source-tab-content">
      <div class="empty-state"><i class="ti ti-loader"></i><div>Loading…</div></div>
    </div>
  `;
  byId("source-detail-tabs")
    .querySelectorAll(".tab-sub")
    .forEach((b) => {
      b.onclick = () => (location.hash = `#sources/${sourceId}/${b.dataset.tab}`);
    });
  try {
    const detail = await getJSON(`/api/sources/${sourceId}`);
    byId("bc-source-name").textContent = detail.display_name || detail.identifier;
    renderSourceDetailHead(detail);
    if (activeTab === "documents") renderSourceDocuments(sourceId);
    else if (activeTab === "runs") renderSourceRuns(detail);
    else renderSourceConfig(detail);
  } catch (e) {
    byId("source-tab-content").innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load: ${escapeHtml(e.message)}</div></div>`;
  }
}

function renderSourceDetailHead(source) {
  const head = byId("source-detail-head");
  head.innerHTML = `
    <div class="entity-hero">
      <i class="ti ${source.icon_hint || "ti-database"}" style="font-size:32px;color:var(--green);width:52px;height:52px;display:flex;align-items:center;justify-content:center;background:var(--green-tint);border-radius:50%"></i>
      <div style="flex:1">
        <h1>${escapeHtml(source.display_name || source.identifier)}</h1>
        <div class="subtitle">${escapeHtml(sourceMetaLine(source))}</div>
        <div class="meta">
          <span><strong>${source.coverage.documents}</strong> documents</span>
          <span><strong>${source.coverage.claims}</strong> claims</span>
          ${source.coverage.conflicts ? `<span class="conflict-pill"><i class="ti ti-alert-triangle"></i>${source.coverage.conflicts} conflicts</span>` : ""}
          <span class="muted">Created ${escapeHtml(formatDate(source.created_at))}</span>
        </div>
      </div>
      ${
        state.me?.is_admin
          ? `<div class="source-card-actions">
               <button class="btn-source" data-action="crawl"><i class="ti ti-download"></i> Crawl</button>
               <button class="btn-source" data-action="parse"><i class="ti ti-cpu"></i> Parse</button>
             </div>`
          : ""
      }
    </div>
  `;
  if (state.me?.is_admin) {
    head.querySelector("[data-action=crawl]").onclick = () => runSourceAction(source.id, "crawl");
    head.querySelector("[data-action=parse]").onclick = () => runSourceAction(source.id, "parse");
  }
}

async function renderSourceDocuments(sourceId) {
  const wrap = byId("source-tab-content");
  wrap.innerHTML = `<div class="empty-state"><i class="ti ti-loader"></i><div>Loading documents…</div></div>`;
  try {
    const data = await getJSON(`/api/sources/${sourceId}/documents?page=1&page_size=50`);
    if (!data.results.length) {
      wrap.innerHTML = `<div class="empty-state"><i class="ti ti-file-off"></i><div>No documents yet. Crawl this source to start.</div></div>`;
      return;
    }
    wrap.innerHTML = `
      <table class="docs-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Fetched</th>
            <th class="num">Words</th>
            <th class="num">Chunks</th>
            <th class="num">Claims</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${data.results
            .map(
              (d) => `
            <tr>
              <td>
                <div class="cell-truncate">${escapeHtml(d.title || d.url || "")}</div>
                <div class="muted small cell-truncate">${escapeHtml(d.url || "")}</div>
              </td>
              <td class="muted small">${escapeHtml(timeAgo(d.fetched_at))}</td>
              <td class="num">${d.word_count}</td>
              <td class="num">${d.chunks}</td>
              <td class="num">${d.claims_extracted}</td>
              <td><button class="btn-ghost" data-doc="${escapeAttr(d.document_id)}"><i class="ti ti-eye"></i> View</button></td>
            </tr>`,
            )
            .join("")}
        </tbody>
      </table>
      <div class="muted small" style="padding:12px">${data.total} total · showing ${data.results.length}</div>
    `;
    wrap.querySelectorAll("[data-doc]").forEach((b) => {
      b.onclick = () => openDocumentModal(b.dataset.doc);
    });
  } catch (e) {
    wrap.innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load: ${escapeHtml(e.message)}</div></div>`;
  }
}

async function openDocumentModal(documentId) {
  openModal(`<div class="empty-state"><i class="ti ti-loader"></i><div>Loading…</div></div>`);
  try {
    const data = await getJSON(`/api/document/${documentId}`);
    const claimsHtml = (data.claims_raw || [])
      .map(
        (c) => `<li><strong>${escapeHtml(c.subject_text)}</strong> ${escapeHtml(c.predicate)} ${escapeHtml(c.object_text || "")}</li>`,
      )
      .join("");
    openModal(`
      <div class="modal-header">
        <div>
          <div class="modal-title">${escapeHtml(data.title || data.url || "Document")}</div>
          <div class="modal-subtitle">${escapeHtml(data.url || "")}</div>
        </div>
        <button class="modal-close" onclick="closeModal()" aria-label="Close">×</button>
      </div>
      <div class="doc-viewer">
        <div class="muted small">${data.word_count || 0} words · ${data.chunks?.length || 0} chunks · ${data.claims_raw?.length || 0} extracted claims</div>
        <pre>${escapeHtml((data.cleaned_text || "").slice(0, 5000))}${(data.cleaned_text || "").length > 5000 ? "\n\n…(truncated)" : ""}</pre>
        ${claimsHtml ? `<div><div class="muted small" style="margin-bottom:6px">Extracted claims</div><ul style="padding-left:18px;font-size:13px">${claimsHtml}</ul></div>` : ""}
      </div>
    `);
  } catch (e) {
    openModal(`<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load: ${escapeHtml(e.message)}</div></div>`);
  }
}

function renderSourceRuns(detail) {
  const wrap = byId("source-tab-content");
  if (!detail.runs.length) {
    wrap.innerHTML = `<div class="empty-state"><i class="ti ti-history-off"></i><div>No runs yet.</div></div>`;
    return;
  }
  wrap.innerHTML = `
    <table class="runs-table">
      <thead>
        <tr>
          <th>Kind</th>
          <th>Status</th>
          <th>Started</th>
          <th>Finished</th>
          <th class="num">Stats</th>
          <th>Error</th>
        </tr>
      </thead>
      <tbody>
        ${detail.runs
          .map(
            (r) => `
          <tr>
            <td>${escapeHtml(r.kind)}</td>
            <td><span class="status-pill ${r.status === "complete" ? "active" : r.status === "running" ? "running" : "paused"}">${escapeHtml(r.status)}</span></td>
            <td class="muted small">${escapeHtml(timeAgo(r.started_at))}</td>
            <td class="muted small">${r.finished_at ? escapeHtml(timeAgo(r.finished_at)) : "—"}</td>
            <td class="num muted small">${r.stats ? escapeHtml(JSON.stringify(r.stats)) : "—"}</td>
            <td class="muted small">${escapeHtml(r.error_message || "—")}</td>
          </tr>`,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function renderSourceConfig(detail) {
  const wrap = byId("source-tab-content");
  const adminOnly = !state.me?.is_admin;
  wrap.innerHTML = `
    <div class="panel" style="margin-top:0">
      <div class="modal-body">
        <label class="field">
          <span class="field-label">Display name</span>
          <input id="cfg-name" value="${escapeAttr(detail.display_name || "")}" ${adminOnly ? "disabled" : ""} />
        </label>
        <div class="field-row">
          <label class="field">
            <span class="field-label">Identifier</span>
            <input value="${escapeAttr(detail.identifier)}" disabled />
            <span class="field-hint">Identifier cannot be changed after creation.</span>
          </label>
          <label class="field">
            <span class="field-label">Trust weight</span>
            <input id="cfg-trust" type="number" step="0.05" min="0" max="1" value="${escapeAttr(String(detail.trust_weight ?? 0.5))}" ${adminOnly ? "disabled" : ""} />
          </label>
        </div>
        <label class="field">
          <span class="field-label">Notes</span>
          <textarea id="cfg-notes" rows="3" ${adminOnly ? "disabled" : ""}>${escapeHtml(stripStatusLine(detail.notes || ""))}</textarea>
        </label>
        ${
          !adminOnly
            ? `<div style="display:flex;justify-content:flex-end;gap:8px"><button class="btn-primary" id="cfg-save"><i class="ti ti-device-floppy"></i> Save</button></div>`
            : ""
        }
      </div>
    </div>
  `;
  if (!adminOnly) {
    byId("cfg-save").onclick = async () => {
      const body = {
        display_name: byId("cfg-name").value.trim() || null,
        trust_weight: Number(byId("cfg-trust").value),
        notes: byId("cfg-notes").value.trim() || null,
      };
      await patchSource(detail.id, body);
      toast("Saved.", "success");
      renderSourceDetail(detail.id, "config");
    };
  }
}

function stripStatusLine(notes) {
  if (notes.startsWith("status:")) {
    const idx = notes.indexOf("\n");
    return idx >= 0 ? notes.slice(idx + 1) : "";
  }
  return notes;
}

/* ───── Add source modal ───── */

let modalKind = "domain";

function openAddSourceModal() {
  modalKind = "domain";
  renderAddSourceModal();
}

function renderAddSourceModal() {
  const selected = SOURCE_KINDS.find((k) => k.id === modalKind) || SOURCE_KINDS[0];
  const trustDefault = selected.kind === "file" ? 0.95 : 0.9;
  openModal(`
    <div class="modal-header">
      <div>
        <div class="modal-title">Add source</div>
        <div class="modal-subtitle">Add a crawlable sitemap or upload a file.</div>
      </div>
      <button class="modal-close" onclick="closeModal()" aria-label="Close">×</button>
    </div>
    <div class="modal-body">
      <div>
        <div class="field-label" style="margin-bottom:6px">Kind</div>
        <div class="kind-grid">
          ${SOURCE_KINDS.map(
            (k) => `
            <button type="button" class="kind-card ${k.id === modalKind ? "selected" : ""}" data-kind="${k.id}">
              <i class="ti ${k.icon} icon"></i>
              <div>
                <div class="label">${escapeHtml(k.label)}</div>
                <div class="description">${escapeHtml(k.description)}</div>
              </div>
            </button>`,
          ).join("")}
        </div>
      </div>
      <label class="field">
        <span class="field-label">Display name</span>
        <input id="new-name" placeholder="e.g. Tuck news" />
        <span class="field-hint">You can rename this anytime.</span>
      </label>
      ${selected.fields
        .map((f) => {
          if (f.type === "file") {
            return `<label class="field">
                <span class="field-label">${escapeHtml(f.label)}</span>
                <input id="new-${f.name}" type="file" accept="${escapeAttr(f.accept || "")}" />
              </label>`;
          }
          return `<label class="field">
              <span class="field-label">${escapeHtml(f.label)}</span>
              <input id="new-${f.name}" placeholder="${escapeAttr(f.placeholder || "")}" />
            </label>`;
        })
        .join("")}
      ${selected.hint ? `<div class="field-hint">${escapeHtml(selected.hint)}</div>` : ""}
      <label class="field">
        <span class="field-label">Trust weight</span>
        <input id="new-trust" type="number" step="0.05" min="0" max="1" value="${trustDefault}" />
        <span class="field-hint">How much to weight evidence from this source. 1.0 = ground truth.</span>
      </label>
    </div>
    <div class="modal-footer">
      <button class="btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn-primary" id="new-submit"><i class="ti ti-plus"></i> Add source</button>
    </div>
  `);
  document.querySelectorAll(".kind-card").forEach((card) => {
    card.onclick = () => {
      modalKind = card.dataset.kind;
      renderAddSourceModal();
    };
  });
  byId("new-submit").onclick = submitAddSource;
}

async function submitAddSource() {
  const selected = SOURCE_KINDS.find((k) => k.id === modalKind) || SOURCE_KINDS[0];
  const kind = selected.kind;
  const display_name = byId("new-name").value.trim();
  const trust = Number(byId("new-trust").value || 0.75);
  if (!display_name) {
    toast("Display name is required.", "error");
    return;
  }
  try {
    if (kind === "file") {
      const input = byId("new-file");
      if (!input.files || !input.files[0]) {
        toast("Pick a file to upload.", "error");
        return;
      }
      const form = new FormData();
      form.append("display_name", display_name);
      form.append("trust_weight", String(trust));
      form.append("file", input.files[0]);
      const res = await fetch("/admin/sources/upload", { method: "POST", body: form });
      if (!res.ok) throw new Error(`${res.status}`);
    } else {
      const identifier = byId("new-identifier").value.trim();
      if (!identifier) {
        toast("Identifier is required.", "error");
        return;
      }
      const body = {
        kind,
        identifier,
        trust_weight: trust,
        display_name,
      };
      const res = await fetch("/admin/sources", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || res.statusText);
      }
    }
    closeModal();
    toast("Source added.", "success");
    await Promise.all([loadSourcesStats(), loadSourcesList()]);
  } catch (e) {
    toast(`Add failed: ${e.message}`, "error");
  }
}

/* ───── Source conflicts ───── */

async function loadAdminConflicts() {
  try {
    const data = await getJSON("/admin/conflicts");
    const rows = data.results || [];
    byId("conflict-count").textContent = `${data.total || 0} unresolved`;
    if (!rows.length) {
      byId("conflicts-body").innerHTML = `<div class="muted small">No conflicts. Sources agree (or there is no data yet).</div>`;
      return;
    }
    byId("conflicts-body").innerHTML = rows
      .map(
        (c) => `
      <div class="conflict-row">
        <div class="stmt"><strong>Conflict ${escapeHtml(c.id.slice(0, 8))}</strong></div>
        <div class="versus">
          <span>Claim A: ${escapeHtml(c.claim_a_id.slice(0, 8))}</span>
          <span>vs</span>
          <span>Claim B: ${escapeHtml(c.claim_b_id.slice(0, 8))}</span>
        </div>
        <div style="margin-top:8px;display:flex;gap:6px">
          <button class="btn-source" data-resolve="${escapeAttr(c.id)}" data-side="claim_a_wins">Pick A</button>
          <button class="btn-source" data-resolve="${escapeAttr(c.id)}" data-side="claim_b_wins">Pick B</button>
          <button class="btn-ghost" data-resolve="${escapeAttr(c.id)}" data-side="both_valid_distinct">Both valid</button>
        </div>
      </div>`,
      )
      .join("");
    byId("conflicts-body")
      .querySelectorAll("[data-resolve]")
      .forEach((b) => {
        b.onclick = async () => {
          const id = b.dataset.resolve;
          const resolution = b.dataset.side;
          await fetch(`/admin/conflicts/${id}/resolve`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ resolution }),
          });
          toast("Resolved.", "success");
          loadAdminConflicts();
        };
      });
  } catch (e) {
    byId("conflicts-body").innerHTML = `<div class="muted small">Unable to load conflicts: ${escapeHtml(e.message)}</div>`;
  }
}

async function runFullPipeline() {
  if (
    !confirm(
      "Run the full pipeline against all sources? This may take several minutes and incurs API costs.",
    )
  )
    return;
  try {
    const sources = state.sourcesCache || (await getJSON("/api/sources")).sources || [];
    let started = 0;
    for (const s of sources.filter((x) => x.status === "active")) {
      const res = await fetch(`/admin/sources/${s.id}/crawl`, { method: "POST" });
      if (res.ok) started += 1;
    }
    toast(`${started} source crawl${started === 1 ? "" : "s"} started.`, "success");
  } catch (e) {
    toast(`Failed: ${e.message}`, "error");
  }
}

/* ───── Admin auth ───── */

async function adminLogout(event) {
  event.preventDefault();
  const tab = currentTab();
  await fetch("/admin/logout", { method: "POST" });
  await Promise.all([loadMe(), loadStats()]);
  renderTopbar();
  if (tab === "admin") {
    location.hash = "#sources";
  } else if (tab === "sources") {
    await renderRoute();
  }
  return false;
}

/* ───── Modal + toast ───── */

function openModal(html) {
  const root = document.getElementById("modal-root");
  root.innerHTML = `<div class="modal-overlay" onclick="closeModalOnBackdrop(event)"><div class="modal" onclick="event.stopPropagation()">${html}</div></div>`;
}

function closeModal() {
  document.getElementById("modal-root").innerHTML = "";
}

function closeModalOnBackdrop(event) {
  if (event.target.classList.contains("modal-overlay")) closeModal();
}

function toast(message, level) {
  const root = document.getElementById("toast-root");
  const el = document.createElement("div");
  el.className = `toast ${level || ""}`;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

/* ───── Utilities ───── */

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(body || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function byId(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  if (value == null) return "";
  return String(value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[c],
  );
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function capitalize(value) {
  if (!value) return "";
  return value[0].toUpperCase() + value.slice(1);
}

function formatNumber(n) {
  if (n == null) return "0";
  return Number(n).toLocaleString();
}

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString();
  } catch (_) {
    return iso;
  }
}

function timeAgo(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = (Date.now() - then) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

window.closeModal = closeModal;
window.closeModalOnBackdrop = closeModalOnBackdrop;
