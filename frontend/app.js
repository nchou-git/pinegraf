// ---------- tabs ----------

const tabs = document.querySelectorAll(".tab");
const panels = {
  lookup: document.getElementById("panel-lookup"),
  research: document.getElementById("panel-research"),
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
    `;
    lkResults.appendChild(row);
  }
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
    answerEl.textContent = data.answer;
  } catch (err) {
    answerEl.textContent = `Research failed: ${err.message}`;
  }
}

document.getElementById("rs-go").addEventListener("click", runResearch);
document.getElementById("rs-question").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runResearch();
});

// load empty state
runLookup();
