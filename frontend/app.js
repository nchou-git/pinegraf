// ---------- tabs ----------

const tabs = document.querySelectorAll(".tab");
const panels = {
  lookup: document.getElementById("panel-lookup"),
  research: document.getElementById("panel-research"),
  connections: document.getElementById("panel-connections"),
};

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.toggle("active", t === tab));
    for (const [name, el] of Object.entries(panels)) {
      el.hidden = name !== tab.dataset.tab;
    }
  });
});

// ---------- helpers ----------

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------- Lookup ----------

const lkResults = document.getElementById("lk-results");

function renderResults(data) {
  lkResults.innerHTML = "";
  if (!data.results || data.results.length === 0) {
    lkResults.innerHTML = '<div class="empty">No matches.</div>';
    return;
  }

  const header = document.createElement("div");
  header.className = "empty";
  header.textContent = `${data.count} result${data.count === 1 ? "" : "s"}`;
  lkResults.appendChild(header);

  for (const p of data.results) {
    const row = document.createElement("div");
    row.className = "result-row";
    const role = [p.current_title, p.current_company].filter(Boolean).join(" at ");
    const past = (p.past_companies || []).slice(0, 5).join(", ");
    row.innerHTML = `
      <div class="name">
        ${esc(p.name)}
        ${p.class_year ? `<span class="meta">${esc(p.class_year)}</span>` : ""}
      </div>
      ${role ? `<div class="role">${esc(role)}</div>` : ""}
      ${past ? `<div class="meta">Past: ${esc(past)}</div>` : ""}
      ${p.bio_summary ? `<div class="meta">${esc(p.bio_summary)}</div>` : ""}
      ${
        p.sources && p.sources.length
          ? `<details class="sources"><summary>sources</summary>${renderSources(p.sources)}</details>`
          : ""
      }
    `;
    lkResults.appendChild(row);
  }
}

function renderSources(sources) {
  return sources
    .map((source) => {
      const label = [source.attribute_name, source.attribute_value].filter(Boolean).join(": ");
      const where = source.source_url
        ? `<a href="${esc(source.source_url)}" target="_blank" rel="noreferrer">${esc(source.source)}</a>`
        : esc(source.source);
      const verified = source.last_verified_at ? ` verified ${esc(source.last_verified_at)}` : "";
      return `<div class="source-line">${esc(label)} | ${where}${verified}</div>`;
    })
    .join("");
}

async function runLookup() {
  const payload = {
    name: document.getElementById("lk-name").value.trim() || null,
    company: document.getElementById("lk-company").value.trim() || null,
    class_year: document.getElementById("lk-year").value.trim() || null,
  };

  lkResults.innerHTML = '<div class="empty">Searching...</div>';

  try {
    const res = await fetch("/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    renderResults(await res.json());
  } catch (err) {
    lkResults.innerHTML = `<div class="empty">Lookup failed: ${esc(err.message)}</div>`;
  }
}

document.getElementById("lk-go").addEventListener("click", runLookup);
for (const id of ["lk-name", "lk-company", "lk-year"]) {
  document.getElementById(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") runLookup();
  });
}

// ---------- Research ----------

const answerEl = document.getElementById("answer");

function renderAnswer(markdown) {
  answerEl.innerHTML = "";
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g;
  let lastIndex = 0;
  for (const match of markdown.matchAll(pattern)) {
    answerEl.appendChild(document.createTextNode(markdown.slice(lastIndex, match.index)));
    const link = document.createElement("a");
    link.className = "citation";
    link.href = match[2];
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = match[1];
    answerEl.appendChild(link);
    lastIndex = match.index + match[0].length;
  }
  answerEl.appendChild(document.createTextNode(markdown.slice(lastIndex)));
}

async function runResearch() {
  const question = document.getElementById("rs-question").value.trim();
  if (!question) {
    answerEl.textContent = "Please enter a question.";
    return;
  }

  answerEl.textContent = "Researching...";

  try {
    const res = await fetch("/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode: "deep" }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderAnswer(data.answer);
  } catch (err) {
    answerEl.textContent = `Research failed: ${err.message}`;
  }
}

document.getElementById("rs-go").addEventListener("click", runResearch);
document.getElementById("rs-question").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runResearch();
});

// ---------- Connections ----------

const cnResults = document.getElementById("cn-results");
const cnDetail = document.getElementById("cn-detail");
let cnAdminAuthenticated = false;

async function refreshConnectionAdminState() {
  try {
    const res = await fetch("/admin/me");
    if (!res.ok) return;
    const data = await res.json();
    cnAdminAuthenticated = Boolean(data.authenticated);
  } catch (_err) {
    cnAdminAuthenticated = false;
  }
}

async function searchConnections() {
  const name = document.getElementById("cn-name").value.trim();
  if (!name) return;
  cnResults.innerHTML = '<div class="empty">Searching...</div>';
  cnDetail.innerHTML = "";
  try {
    const res = await fetch("/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cnResults.innerHTML = "";
    if (!data.results.length) {
      cnResults.innerHTML = '<div class="empty">No matches.</div>';
      return;
    }
    for (const entity of data.results) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary entity-pick";
      button.textContent = [entity.name, entity.class_year].filter(Boolean).join(" ");
      button.addEventListener("click", () => loadEntity(entity.entity_id));
      cnResults.appendChild(button);
    }
    if (data.results[0].entity_id) loadEntity(data.results[0].entity_id);
  } catch (err) {
    cnResults.innerHTML = `<div class="empty">Search failed: ${esc(err.message)}</div>`;
  }
}

async function loadEntity(entityId) {
  if (!entityId) return;
  cnDetail.innerHTML = '<div class="empty">Loading...</div>';
  try {
    await refreshConnectionAdminState();
    const debug = cnAdminAuthenticated ? "?debug=true" : "";
    const res = await fetch(`/entity/${encodeURIComponent(entityId)}${debug}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    renderEntity(await res.json());
  } catch (err) {
    cnDetail.innerHTML = `<div class="empty">Load failed: ${esc(err.message)}</div>`;
  }
}

function renderEntity(entity) {
  const consolidated = entity.consolidated || {};
  const attrs = entity.attributes || [];
  const relationships = entity.relationships || [];
  const diagnostics = entity.diagnostics || null;
  const grouped = relationships.reduce((acc, rel) => {
    (acc[rel.relationship_type] ||= []).push(rel);
    return acc;
  }, {});
  cnDetail.innerHTML = `
    <div class="entity-head">
      <div class="name">${esc(entity.name)}</div>
      <div class="meta">${esc(consolidated.class_year || "")}</div>
    </div>
    <div class="attribute-grid">
      ${["current_employer", "current_title", "location"].map((key) => (
        consolidated[key] ? `<div><span class="meta">${esc(key)}</span><br>${esc(consolidated[key])}</div>` : ""
      )).join("")}
    </div>
    <h3>Attributes</h3>
    <div class="results">
      ${attrs.map((attr) => `
        <div class="result-row">
          <div class="role">${esc(attr.attribute_name)}: ${esc(attr.attribute_value)}</div>
          <div class="meta">${esc(attr.source)}${attr.last_verified_at ? ` | verified ${esc(attr.last_verified_at)}` : ""}</div>
          ${attr.source_url ? `<a class="meta" href="${esc(attr.source_url)}" target="_blank" rel="noreferrer">${esc(attr.source_url)}</a>` : ""}
        </div>
      `).join("") || '<div class="empty">No attributes.</div>'}
    </div>
    ${renderDiagnostics(diagnostics)}
    <h3>Connected Entities</h3>
    ${Object.entries(grouped).map(([type, rels]) => `
      <div class="connection-group">
        <div class="role">${esc(type)}</div>
        ${rels.map((rel) => `
          <div class="result-row">
            <div class="name">
              ${esc(rel.connected_name)}
              ${rel.is_resolved === false ? '<span class="meta">(unresolved)</span>' : ""}
            </div>
            <div class="meta">confidence ${rel.confidence_score ?? ""}</div>
            ${rel.derivation ? `<div class="meta">${esc(rel.derivation)}</div>` : ""}
            ${rel.source_url ? `<a class="meta" href="${esc(rel.source_url)}" target="_blank" rel="noreferrer">${esc(rel.source_url)}</a>` : ""}
            ${rel.text_evidence ? `<div class="meta">${esc(rel.text_evidence)}</div>` : ""}
          </div>
        `).join("")}
      </div>
    `).join("") || '<div class="empty">No relationships.</div>'}
  `;
}

function renderDiagnostics(diagnostics) {
  if (!diagnostics) return "";
  const rows = Object.entries(diagnostics)
    .map(([key, value]) => `<div class="source-line">${esc(key)}: ${esc(value)}</div>`)
    .join("");
  return `<details class="sources"><summary>debug</summary>${rows}</details>`;
}

document.getElementById("cn-go").addEventListener("click", searchConnections);
document.getElementById("cn-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchConnections();
});

// load empty state
runLookup();
