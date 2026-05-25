"use strict";

const ASK_SESSION_KEY = "pinegraf_ask_session";
const REMOVE_SOURCE_CONFIRM =
  "Permanently remove this source? This deletes the source and all derived data (documents, claims, etc.) that came only from this source. Cannot be undone.";

const state = {
  me: null,
  stats: null,
  directoryPage: 1,
  directoryFilters: { q: "", sources: [], class_years: [], orgs: [], sort: "name_asc" },
  directoryRows: [],
  directoryOptionRows: [],
  sourcesCache: null,
  askSession: JSON.parse(sessionStorage.getItem(ASK_SESSION_KEY) || "[]"),
  graphSearch: "",
  graphSearchResults: [],
  sidebarCollapsed: sessionStorage.getItem("pinegraf.sidebarCollapsed") === "true",
};

let modalRestoreFocus = null;
let modalKeydownHandler = null;

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
    label: "Sitemap",
    icon: "ti-world",
    description: "Crawl a public website via sitemap.xml.",
    fields: [
      {
        name: "identifier",
        label: "URL or domain",
        placeholder: "tuck.dartmouth.edu",
        required: true,
      },
    ],
  },
  {
    id: "dataset",
    kind: "file",
    format: "dataset",
    label: "Dataset",
    icon: "ti-table",
    description: "Structured records. Rows become entities.",
    hint: "Structured records. Rows become entities.",
    fields: [
      {
        name: "file",
        label: "Dataset file",
        type: "file",
        accept: ".xlsx,.csv,.json,.tsv",
        required: true,
      },
    ],
  },
  {
    id: "text",
    kind: "file",
    format: "text",
    label: "General Text",
    icon: "ti-file-text",
    description: "Unstructured. Chunked and extracted by the LLM pipeline.",
    hint: "Unstructured. Chunked and extracted by the LLM pipeline.",
    fields: [
      {
        name: "file",
        label: "Text file",
        type: "file",
        accept: ".txt,.md,.pdf,.html",
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
    definition: "Example: one news article on tuck.dartmouth.edu or one row in alum_data.xlsx.",
  },
  {
    key: "claims",
    label: "Claims",
    ariaLabel: "What is a claim?",
    definition: 'Example: "Errik Anderson founded Adimab in 2007."',
  },
  {
    key: "entities",
    label: "Entities",
    ariaLabel: "What is an entity?",
    definition:
      "Example: \"Daniella Reichstetter\" and \"Daniella Reichstetter T'07\" resolve to one entity.",
  },
  {
    key: "sources",
    label: "Sources",
    ariaLabel: "What is a source?",
    definition: "Example: tuck.dartmouth.edu, alum_data.xlsx, a SerpAPI feed.",
  },
];

document.addEventListener("DOMContentLoaded", init);

async function init() {
  await Promise.all([loadMe(), loadStats()]);
  setupShell();
  renderShell();
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
  document.body.dataset.workspace = state.me?.workspace?.slug || "tuck";
}

async function loadStats() {
  try {
    state.stats = await getJSON("/api/stats");
  } catch (_) {
    state.stats = {};
  }
}

function setupShell() {
  byId("sidebar-collapse").onclick = toggleSidebar;
  byId("mobile-menu-button").onclick = openMobileSidebar;
  byId("mobile-sidebar-backdrop").onclick = closeMobileSidebar;
}

function renderShell() {
  const shell = byId("shell");
  const sidebar = byId("sidebar");
  shell.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
  shell.classList.toggle("sidebar-open", Boolean(state.mobileSidebarOpen));
  sidebar.classList.toggle("collapsed", state.sidebarCollapsed);

  const workspaceName = state.me?.workspace?.display_name || "Workspace";

  const nav = byId("sidebar-nav");
  const activeTab = currentTab();
  nav.innerHTML = TAB_DEFS
    .map(
      (tab) =>
        `<a class="nav-item ${activeTab === tab.id ? "active" : ""}" data-tab="${tab.id}" href="#${tab.id}">
           <i class="ti ${tab.icon}" aria-hidden="true"></i>
           <span>${escapeHtml(tab.label)}</span>
         </a>`,
    )
    .join("");

  const orgRole = state.me?.is_admin ? "Admin" : "Viewer";
  byId("sidebar-org-avatar").textContent = state.me?.is_admin ? "AD" : workspaceInitials(workspaceName);
  byId("sidebar-org-name").textContent = orgRole;
  byId("sidebar-org-role").textContent = workspaceName;
  byId("sidebar-org-row").onclick = toggleWorkspaceMenu;

  const collapse = byId("sidebar-collapse");
  collapse.setAttribute(
    "aria-label",
    state.sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar",
  );
  collapse.innerHTML = `<i class="ti ${state.sidebarCollapsed ? "ti-chevron-right" : "ti-chevron-left"}" aria-hidden="true"></i>`;
}

function workspaceInitials(name) {
  return String(name || "Workspace")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0] || "")
    .join("")
    .toUpperCase();
}

function toggleWorkspaceMenu(event) {
  event.stopPropagation();
  const row = byId("sidebar-org-row");
  const existing = row.querySelector(".menu");
  if (existing) {
    existing.remove();
    return;
  }
  document.querySelectorAll(".menu").forEach((menu) => menu.remove());
  const workspaceName = state.me?.workspace?.display_name || "Workspace";
  const menu = document.createElement("div");
  menu.className = "menu sidebar-org-menu";
  menu.innerHTML = `
    <div class="menu-header">${escapeHtml(workspaceName)}</div>
    ${
      state.me?.is_admin
        ? `<button class="menu-item" data-admin-action="logout"><i class="ti ti-logout" aria-hidden="true"></i> Sign out of admin</button>`
        : `<button class="menu-item" data-admin-action="login"><i class="ti ti-login" aria-hidden="true"></i> Sign in as admin</button>`
    }
  `;
  row.appendChild(menu);
  const action = menu.querySelector("[data-admin-action]");
  action.onclick = async (clickEvent) => {
    clickEvent.stopPropagation();
    menu.remove();
    if (action.dataset.adminAction === "logout") {
      await adminLogout(clickEvent);
    } else {
      location.href = state.me?.admin_login_url || "/admin/login";
    }
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

function toggleSidebar() {
  state.sidebarCollapsed = !state.sidebarCollapsed;
  sessionStorage.setItem("pinegraf.sidebarCollapsed", String(state.sidebarCollapsed));
  renderShell();
}

function openMobileSidebar() {
  state.mobileSidebarOpen = true;
  renderShell();
}

function closeMobileSidebar() {
  state.mobileSidebarOpen = false;
  renderShell();
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
    renderShell();
    return renderSources([]);
  }
  closeMobileSidebar();
  renderShell();
  if (tab === "ask") return renderAsk();
  if (tab === "graph") return renderGraph(rest[0]);
  if (tab === "sources") return renderSources(rest);
  return renderDirectory();
}

function setPageHeader({ title, subtitle = "", eyebrow = "", actions = "" }) {
  byId("page-header").innerHTML = `
    <div class="page-title-block">
      ${eyebrow ? `<div class="page-eyebrow">${eyebrow}</div>` : ""}
      <h1 id="page-title">${escapeHtml(title)}</h1>
      <div class="page-subtitle" id="page-subtitle">${escapeHtml(subtitle)}</div>
    </div>
    <div class="page-actions" id="page-actions">${actions}</div>
  `;
}

/* ───── Directory ───── */

async function renderDirectory() {
  setPageHeader({
    title: "Directory",
    subtitle: `${formatNumber(state.stats?.entities || 0)} people across ${formatNumber((state.sourcesCache || []).length)} sources`,
    actions: directoryHeaderActions(),
  });
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="directory-page">
      <section class="directory-filter-bar">
        <label class="directory-search input-with-icon">
          <i class="ti ti-search icon" aria-hidden="true"></i>
          <input class="input" id="dir-q" placeholder="Search people" value="${escapeAttr(state.directoryFilters.q)}" />
        </label>
        <div class="directory-filter" id="source-filter-wrap">
          <button class="btn-secondary filter-button" id="filter-source" type="button">
            <strong id="filter-source-label">All sources</strong>
            <i class="ti ti-chevron-down" aria-hidden="true"></i>
          </button>
        </div>
        <div class="directory-filter" id="class-filter-wrap">
          <button class="btn-secondary filter-button" id="filter-class" type="button">
            <strong id="filter-class-label">All classes</strong>
            <i class="ti ti-chevron-down" aria-hidden="true"></i>
          </button>
        </div>
        <div class="directory-filter" id="org-filter-wrap">
          <button class="btn-secondary filter-button" id="filter-org" type="button">
            <strong id="filter-org-label">All organizations</strong>
            <i class="ti ti-chevron-down" aria-hidden="true"></i>
          </button>
        </div>
        <div class="directory-filter directory-sort" id="sort-filter-wrap">
          <button class="btn-secondary filter-button" id="filter-sort" type="button">
            <span>Sort</span>
            <strong id="filter-sort-label">Name A-Z</strong>
            <i class="ti ti-chevron-down" aria-hidden="true"></i>
          </button>
        </div>
        <button class="btn-ghost reset-filters" id="reset-filters" type="button" hidden>Reset filters</button>
      </section>
      <section class="directory-results" id="results">
        <div class="empty-state"><i class="ti ti-loader" aria-hidden="true"></i><div>Loading…</div></div>
      </section>
      <div class="pagination directory-pagination" id="pagination"></div>
    </div>
  `;
  const onSearch = () => {
    state.directoryFilters.q = byId("dir-q").value.trim();
    state.directoryPage = 1;
    loadDirectory();
  };
  byId("dir-q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") onSearch();
  });
  byId("filter-source").onclick = (event) => openDirectoryFilter("source", event.currentTarget);
  byId("filter-class").onclick = (event) => openDirectoryFilter("class_year", event.currentTarget);
  byId("filter-org").onclick = (event) => openDirectoryFilter("org", event.currentTarget);
  byId("filter-sort").onclick = (event) => openDirectorySort(event.currentTarget);
  byId("reset-filters").onclick = resetDirectoryFilters;
  await loadDirectorySources();
  await loadDirectoryOptions();
  await loadDirectory();
}

function directoryHeaderActions() {
  return `
    <button class="btn-icon-only" type="button" disabled title="Coming soon" aria-label="Add person coming soon">
      <i class="ti ti-plus" aria-hidden="true"></i>
    </button>
  `;
}

async function loadDirectorySources() {
  try {
    const data = await getJSON("/api/sources");
    state.sourcesCache = data.sources || [];
    updateDirectoryFilterLabels();
  } catch (e) {
    state.sourcesCache = [];
    toast(`Unable to load sources: ${e.message}`, "error");
  }
}

async function loadDirectoryOptions() {
  try {
    const data = await getJSON("/api/directory?page_size=100");
    state.directoryOptionRows = data.results || [];
  } catch (_) {
    state.directoryOptionRows = [];
  }
}

async function loadDirectory() {
  const params = new URLSearchParams({
    q: state.directoryFilters.q || "",
    org: state.directoryFilters.orgs.join(","),
    class_year: state.directoryFilters.class_years.join(","),
    source: state.directoryFilters.sources.join(","),
    page: String(state.directoryPage),
    page_size: "50",
  });
  const results = byId("results");
  try {
    const data = await getJSON(`/api/directory?${params.toString()}`);
    const total = data.total || 0;
    const sourceTotal = (state.sourcesCache || []).length;
    const totalPages = Math.max(1, Math.ceil(total / (data.page_size || 50)));
    state.directoryRows = sortDirectoryRows(data.results || []);
    setPageHeader({
      title: "Directory",
      subtitle: `${formatNumber(total || state.stats?.entities || 0)} people across ${formatNumber(sourceTotal)} source${sourceTotal === 1 ? "" : "s"}`,
      actions: directoryHeaderActions(),
    });
    updateDirectoryFilterLabels();
    bindDirectoryHeaderFilters();
    if (!sourceTotal) {
      results.innerHTML = `
        <div class="directory-empty-panel">
          <i class="ti ti-database-off" aria-hidden="true"></i>
          <h2>No sources yet</h2>
          <p>Add your first source to start ingesting people, projects, and connections.</p>
          <a class="btn-primary" href="#sources"><i class="ti ti-arrow-right" aria-hidden="true"></i> Go to Sources</a>
        </div>`;
      byId("pagination").innerHTML = "";
      return;
    }
    if (!data.results.length) {
      const entitiesTotal = state.stats?.entities || 0;
      if (!entitiesTotal) {
        results.innerHTML = `
          <div class="directory-empty-panel">
            <i class="ti ti-route-off" aria-hidden="true"></i>
            <h2>Pipeline hasn't run yet</h2>
            <p>Go to Sources to crawl your first source.</p>
            <a class="btn-primary" href="#sources"><i class="ti ti-arrow-right" aria-hidden="true"></i> Open Sources</a>
          </div>`;
        byId("pagination").innerHTML = "";
        return;
      }
      results.innerHTML = `
        <div class="directory-inline-empty">
          <span>No people matched those filters.</span>
          <button class="btn-ghost reset-filters inline" type="button" onclick="resetDirectoryFilters()">Reset filters</button>
        </div>
        ${directoryTable([])}
      `;
      byId("pagination").innerHTML = "";
      return;
    }
    results.innerHTML = directoryTable(state.directoryRows);
    results.querySelectorAll(".directory-table-row").forEach((row) => {
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

function bindDirectoryHeaderFilters() {
  byId("filter-source").onclick = (event) => openDirectoryFilter("source", event.currentTarget);
  byId("filter-class").onclick = (event) => openDirectoryFilter("class_year", event.currentTarget);
  byId("filter-org").onclick = (event) => openDirectoryFilter("org", event.currentTarget);
  byId("filter-sort").onclick = (event) => openDirectorySort(event.currentTarget);
  byId("reset-filters").onclick = resetDirectoryFilters;
}

function updateDirectoryFilterLabels() {
  const sourceLabel = byId("filter-source-label");
  const classLabel = byId("filter-class-label");
  const orgLabel = byId("filter-org-label");
  const sortLabel = byId("filter-sort-label");
  const reset = byId("reset-filters");
  if (sourceLabel) {
    const count = state.directoryFilters.sources.length;
    sourceLabel.textContent = count ? `${count} source${count === 1 ? "" : "s"}` : "All sources";
  }
  if (classLabel) {
    const count = state.directoryFilters.class_years.length;
    classLabel.textContent = count ? `${count} class${count === 1 ? "" : "es"}` : "All classes";
  }
  if (orgLabel) {
    const count = state.directoryFilters.orgs.length;
    orgLabel.textContent = count ? `${count} org${count === 1 ? "" : "s"}` : "All organizations";
  }
  if (sortLabel) sortLabel.textContent = directorySortLabel(state.directoryFilters.sort);
  if (reset) reset.hidden = !hasDirectoryFilters();
}

function openDirectoryFilter(type, anchor) {
  document.querySelectorAll(".filter-popover").forEach((popover) => popover.remove());
  const wrap = anchor.closest(".directory-filter");
  if (!wrap) return;
  const options = directoryFilterOptions(type);
  const popover = document.createElement("div");
  popover.className = "filter-popover";
  popover.innerHTML = `
    <input class="input filter-popover-search" placeholder="${escapeAttr(directoryFilterSearchPlaceholder(type))}" />
    <div class="filter-options">
      ${options
        .map(
          (option) => `
          <label class="filter-option">
            <input class="input checkbox-input" type="checkbox" data-value="${escapeAttr(option.value)}" ${option.active ? "checked" : ""} />
            <span class="filter-option-label">
              ${option.icon ? `<i class="ti ${escapeAttr(option.icon)}" aria-hidden="true"></i>` : ""}
              <span>${escapeHtml(option.label)}</span>
            </span>
            <span class="filter-count">${formatNumber(option.count || 0)}</span>
          </label>`,
        )
        .join("")}
    </div>
    <div class="filter-popover-footer">
      <button class="btn-ghost" type="button" data-filter-action="all">Select all</button>
      <button class="btn-ghost" type="button" data-filter-action="clear">Clear</button>
    </div>
  `;
  wrap.appendChild(popover);
  const search = popover.querySelector(".filter-popover-search");
  search.focus();
  search.oninput = () => {
    const q = search.value.trim().toLowerCase();
    popover.querySelectorAll(".filter-option").forEach((button) => {
      button.hidden = q && !button.textContent.toLowerCase().includes(q);
    });
  };
  popover.querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.onchange = () => {
      toggleDirectoryFilterValue(type, input.dataset.value || "", input.checked);
    };
  });
  popover.querySelector("[data-filter-action=all]").onclick = () => {
    popover.querySelectorAll("input[type=checkbox]").forEach((input) => {
      input.checked = true;
    });
    setDirectoryFilterValues(
      type,
      options.map((option) => option.value),
    );
  };
  popover.querySelector("[data-filter-action=clear]").onclick = () => {
    popover.querySelectorAll("input[type=checkbox]").forEach((input) => {
      input.checked = false;
    });
    setDirectoryFilterValues(type, []);
  };
  setTimeout(() => {
    document.addEventListener(
      "click",
      function onAway(event) {
        if (popover.contains(event.target) || event.target === anchor) return;
        popover.remove();
        document.removeEventListener("click", onAway);
      },
    );
  }, 0);
}

function directoryFilterOptions(type) {
  if (type === "source") {
    const sources = state.sourcesCache || [];
    const counts = sourceCountsForRows(state.directoryRows);
    return sources.map((source) => ({
        value: source.identifier,
        label: source.display_name || source.identifier,
        icon: source.icon_hint || "ti-database",
        count: counts[source.identifier] || 0,
        active: state.directoryFilters.sources.includes(source.identifier),
      }));
  }
  const attr = type === "class_year" ? "class_year" : "current_employer";
  const selected =
    type === "class_year" ? state.directoryFilters.class_years : state.directoryFilters.orgs;
  const counts = valueCountsForRows(state.directoryRows, attr);
  const values = Array.from(
    new Set(
      (state.directoryOptionRows.length ? state.directoryOptionRows : state.directoryRows)
        .map((row) => row.primary_attributes?.[attr])
        .filter(Boolean)
        .map(String),
    ),
  ).sort();
  return values.map((value) => ({
    value,
    label: value,
    count: counts[value] || 0,
    active: selected.includes(value),
  }));
}

function directoryFilterSearchPlaceholder(type) {
  if (type === "source") return "Search sources";
  if (type === "class_year") return "Search class years";
  return "Search organizations";
}

function selectedDirectoryFilterArray(type) {
  if (type === "source") return state.directoryFilters.sources;
  if (type === "class_year") return state.directoryFilters.class_years;
  return state.directoryFilters.orgs;
}

function toggleDirectoryFilterValue(type, value, checked) {
  const values = selectedDirectoryFilterArray(type);
  const next = checked
    ? Array.from(new Set([...values, value]))
    : values.filter((item) => item !== value);
  setDirectoryFilterValues(type, next);
}

function setDirectoryFilterValues(type, values) {
  if (type === "source") state.directoryFilters.sources = values;
  if (type === "class_year") state.directoryFilters.class_years = values;
  if (type === "org") state.directoryFilters.orgs = values;
  state.directoryPage = 1;
  updateDirectoryFilterLabels();
  loadDirectory();
}

function openDirectorySort(anchor) {
  document.querySelectorAll(".filter-popover").forEach((popover) => popover.remove());
  const wrap = anchor.closest(".directory-filter");
  const options = [
    ["name_asc", "Name A-Z"],
    ["name_desc", "Name Z-A"],
    ["connected_desc", "Most connected"],
    ["conflicts_desc", "Most conflicts"],
  ];
  const popover = document.createElement("div");
  popover.className = "filter-popover sort-popover";
  popover.innerHTML = options
    .map(
      ([value, label]) => `
      <button type="button" class="btn-ghost filter-sort-option" data-sort="${value}">
        <span>${label}</span>
        ${state.directoryFilters.sort === value ? `<i class="ti ti-check" aria-hidden="true"></i>` : ""}
      </button>`,
    )
    .join("");
  wrap.appendChild(popover);
  popover.querySelectorAll("[data-sort]").forEach((button) => {
    button.onclick = () => {
      state.directoryFilters.sort = button.dataset.sort;
      updateDirectoryFilterLabels();
      loadDirectory();
      popover.remove();
    };
  });
  setTimeout(() => {
    document.addEventListener(
      "click",
      function onAway(event) {
        if (popover.contains(event.target) || event.target === anchor) return;
        popover.remove();
        document.removeEventListener("click", onAway);
      },
    );
  }, 0);
}

function directorySortLabel(value) {
  return (
    {
      name_asc: "Name A-Z",
      name_desc: "Name Z-A",
      connected_desc: "Most connected",
      conflicts_desc: "Most conflicts",
    }[value] || "Name A-Z"
  );
}

function sortDirectoryRows(rows) {
  return [...rows].sort((left, right) => {
    if (state.directoryFilters.sort === "name_desc") {
      return String(right.canonical_name || "").localeCompare(String(left.canonical_name || ""));
    }
    if (state.directoryFilters.sort === "connected_desc") {
      return (right.connection_count || 0) - (left.connection_count || 0);
    }
    if (state.directoryFilters.sort === "conflicts_desc") {
      return (right.conflict_count || 0) - (left.conflict_count || 0);
    }
    return String(left.canonical_name || "").localeCompare(String(right.canonical_name || ""));
  });
}

function hasDirectoryFilters() {
  return Boolean(
    state.directoryFilters.q ||
      state.directoryFilters.sources.length ||
      state.directoryFilters.class_years.length ||
      state.directoryFilters.orgs.length ||
      state.directoryFilters.sort !== "name_asc",
  );
}

function resetDirectoryFilters() {
  state.directoryFilters = { q: "", sources: [], class_years: [], orgs: [], sort: "name_asc" };
  state.directoryPage = 1;
  const input = byId("dir-q");
  if (input) input.value = "";
  updateDirectoryFilterLabels();
  loadDirectory();
}

function sourceCountsForRows(rows) {
  const counts = {};
  (rows || []).forEach((row) => {
    Object.keys(row.source_mix || {}).forEach((identifier) => {
      counts[identifier] = (counts[identifier] || 0) + 1;
    });
  });
  return counts;
}

function valueCountsForRows(rows, attr) {
  const counts = {};
  (rows || []).forEach((row) => {
    const value = row.primary_attributes?.[attr];
    if (!value) return;
    const key = String(value);
    counts[key] = (counts[key] || 0) + 1;
  });
  return counts;
}

function directoryTable(rows) {
  return `
    <table class="directory-table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Title</th>
          <th>Org</th>
          <th>Class</th>
          <th>Sources</th>
          <th>Conflicts</th>
        </tr>
      </thead>
      <tbody>${rows.map(directoryTableRow).join("")}</tbody>
    </table>
  `;
}

function directoryTableRow(row) {
  const attrs = row.primary_attributes || {};
  const initials = (row.canonical_name || "")
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0] || "")
    .join("")
    .toUpperCase();
  const sourceMix = row.source_mix || {};
  const sourceKeys = Object.keys(sourceMix);
  const visibleSources = sourceKeys.slice(0, 3);
  const overflow = sourceKeys.length - visibleSources.length;
  const conflictCount = row.conflict_count || 0;
  return `
    <tr class="directory-table-row" data-entity-id="${escapeAttr(row.entity_id)}">
      <td>
        <div class="directory-name-cell">
          <div class="avatar-circle compact">${escapeHtml(initials || "??")}</div>
          <div>
            <div class="directory-person-name">${escapeHtml(row.canonical_name || "Unknown")}</div>
            <div class="directory-person-sub">${attrs.class_year ? `T'${escapeHtml(String(attrs.class_year).replace(/^T'?/, ""))}` : escapeHtml(capitalize(row.kind || "entity"))}</div>
          </div>
        </div>
      </td>
      <td>${escapeHtml(attrs.current_title || "")}</td>
      <td>${escapeHtml(attrs.current_employer || "")}</td>
      <td>${escapeHtml(attrs.class_year || "")}</td>
      <td>
        <div class="directory-source-cell">
          ${
            visibleSources.length
              ? visibleSources
                  .map((key) => `<span class="source-badge">${escapeHtml(sourceLabel(key))}</span>`)
                  .join("")
              : `<span class="muted small">—</span>`
          }
          ${overflow > 0 ? `<span class="source-badge muted-badge">+${overflow}</span>` : ""}
        </div>
      </td>
      <td>${conflictCount ? `<span class="conflict-pill">${conflictCount}</span>` : ""}</td>
    </tr>
  `;
}

function sourceLabel(identifier) {
  const source = (state.sourcesCache || []).find((item) => item.identifier === identifier);
  return source?.display_name || source?.identifier || identifier;
}

function rowBio(row) {
  const attrs = row.primary_attributes || {};
  const parts = [];
  if (attrs.current_title) parts.push(String(attrs.current_title));
  if (attrs.current_employer) parts.push(String(attrs.current_employer));
  if (!parts.length && row.kind) parts.push(capitalize(row.kind));
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
  const hasSession = state.askSession.length > 0;
  setPageHeader({
    title: "Ask",
    subtitle: "",
    actions: hasSession
      ? `<button class="btn-ghost" id="ask-new-question" type="button"><i class="ti ti-plus" aria-hidden="true"></i> New question</button>`
      : "",
  });
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="ask-page ${hasSession ? "has-session" : "is-empty"}">
      ${
        hasSession
          ? `<section class="ask-thread" id="ask-thread">${state.askSession.map(renderAskPair).join("")}</section>`
          : `<section class="ask-empty">
              <h2>What do you want to know?</h2>
            </section>`
      }
      ${renderAskComposer(hasSession)}
    </div>
    <aside class="side-drawer" id="ask-side-drawer" hidden></aside>
  `;
  const newQuestion = byId("ask-new-question");
  if (newQuestion) {
    newQuestion.onclick = () => {
      state.askSession = [];
      sessionStorage.removeItem(ASK_SESSION_KEY);
      renderAsk();
    };
  }
  setupAskComposer();
  attachAskCitationHandlers();
  scrollAskThreadToBottom();
}

function renderAskComposer(isPinned) {
  return `
    <form class="ask-composer ${isPinned ? "is-pinned" : ""}" id="ask-form">
      <textarea class="input ask-input" id="ask-input" rows="1" placeholder="Ask about people, projects, or organizations. For example, who worked on Gyrobike?"></textarea>
      <button class="btn-primary btn-icon-only ask-submit" id="ask-submit" type="submit" aria-label="Ask question" disabled>
        <i class="ti ti-send" aria-hidden="true"></i>
      </button>
    </form>
  `;
}

function setupAskComposer() {
  const input = byId("ask-input");
  if (!input) return;
  const submit = byId("ask-submit");
  const form = byId("ask-form");
  const syncComposer = () => {
    resizeAskInput(input);
    submit.disabled = !input.value.trim();
  };
  form.onsubmit = (e) => {
    e.preventDefault();
    ask();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask();
    }
  });
  input.addEventListener("input", syncComposer);
  syncComposer();
  input.focus();
}

function resizeAskInput(input) {
  const lines = input.value
    .split("\n")
    .reduce((count, line) => count + Math.max(1, Math.ceil(line.length / 72)), 0);
  input.rows = Math.min(4, Math.max(1, lines));
}

async function ask() {
  const input = byId("ask-input");
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  resizeAskInput(input);
  byId("ask-submit").disabled = true;

  const item = {
    id: createAskId(),
    question,
    answer: "",
    citations: [],
    isStreaming: true,
  };
  state.askSession.push(item);
  saveAskSession();
  renderAsk();
  const answerText = byId(`ask-answer-${item.id}`);
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
          item.answer += payload.text;
          if (answerText) {
            answerText.textContent = item.answer;
          }
        } else if (payload.kind === "citations") {
          item.citations = payload.citations || [];
          refreshAskSources(item.id);
        }
      });
    }
    if (!item.answer.trim()) {
      item.answer = "No answer could be generated.";
      if (answerText) {
        answerText.textContent = item.answer;
      }
    }
  } catch (e) {
    item.answer = `Unable to get an answer: ${e.message}`;
    if (answerText) {
      answerText.textContent = item.answer;
    }
  } finally {
    item.isStreaming = false;
    saveAskSession();
    refreshAskSources(item.id);
  }
}

function createAskId() {
  return `ask-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function saveAskSession() {
  const payload = state.askSession.map(({ id, question, answer, citations }) => ({
    id,
    question,
    answer,
    citations: citations || [],
  }));
  sessionStorage.setItem(ASK_SESSION_KEY, JSON.stringify(payload));
}

function renderAskPair(item) {
  return `
    <article class="ask-pair" data-ask-id="${escapeAttr(item.id)}">
      <div class="ask-question-block">${escapeHtml(item.question)}</div>
      <div class="ask-answer-block ${item.isStreaming ? "is-streaming" : ""}" id="ask-answer-${escapeAttr(item.id)}">${escapeHtml(item.answer || "")}</div>
      <div class="ask-sources" id="ask-sources-${escapeAttr(item.id)}">${renderAskSources(item)}</div>
    </article>
  `;
}

function renderAskSources(item) {
  const citations = item.citations || [];
  const count = citations.length;
  return `
    <details class="ask-source-details">
      <summary class="ask-source-pill">Sources (${count} ${count === 1 ? "source" : "sources"})</summary>
      <div class="ask-source-list">
        ${
          count
            ? citations
                .map(
                  (c, i) => `
                    <button class="btn-ghost ask-citation-card" type="button" data-ask-id="${escapeAttr(item.id)}" data-citation-index="${i}">
                      <span class="source-badge">${escapeHtml(c.source_id || c.claim_id || "source")}</span>
                      <strong>${escapeHtml(c.title || c.source_title || `Source ${i + 1}`)}</strong>
                      <span>${escapeHtml(c.quote || "No snippet returned for this citation.")}</span>
                    </button>`,
                )
                .join("")
            : `<div class="ask-citations-empty">Answered from corpus-level patterns, no specific citations.</div>`
        }
      </div>
    </details>
  `;
}

function refreshAskSources(itemId) {
  const item = state.askSession.find((candidate) => candidate.id === itemId);
  const root = byId(`ask-sources-${itemId}`);
  if (!item || !root) return;
  root.innerHTML = renderAskSources(item);
  attachAskCitationHandlers();
}

function attachAskCitationHandlers() {
  document.querySelectorAll(".ask-citation-card").forEach((button) => {
    button.onclick = () => {
      const item = state.askSession.find((candidate) => candidate.id === button.dataset.askId);
      const citation = item?.citations?.[Number(button.dataset.citationIndex)] || {};
      openCitationDrawer(citation);
    };
  });
}

function scrollAskThreadToBottom() {
  const thread = byId("ask-thread");
  if (thread) {
    thread.scrollTop = thread.scrollHeight;
  }
}

function openCitationDrawer(citation) {
  const drawer = byId("ask-side-drawer");
  drawer.hidden = false;
  drawer.innerHTML = `
    <div class="side-drawer-header">
      <div>
        <div class="side-drawer-title">${escapeHtml(citation.title || citation.source_title || "Source")}</div>
        <div class="side-drawer-subtitle">${escapeHtml(citation.source_id || citation.claim_id || "Citation")}</div>
      </div>
      <button class="btn-icon-only modal-close" type="button" onclick="closeSideDrawer()" aria-label="Close">×</button>
    </div>
    <div class="side-drawer-body">
      <div class="field-label">Snippet</div>
      <blockquote>${escapeHtml(citation.quote || "No snippet returned for this citation.")}</blockquote>
      <div class="field-label">Source reference</div>
      <div class="side-drawer-meta">${escapeHtml(citation.source_id || "No source id returned.")}</div>
      ${
        citation.document_id
          ? `<button class="btn-secondary" type="button" data-document-id="${escapeAttr(citation.document_id)}"><i class="ti ti-file-text" aria-hidden="true"></i> Open document</button>`
          : `<div class="muted small">The current API response does not include a document id for this citation.</div>`
      }
    </div>
  `;
  const documentButton = drawer.querySelector("[data-document-id]");
  if (documentButton) {
    documentButton.onclick = () => openDocumentModal(documentButton.dataset.documentId);
  }
}

function closeSideDrawer() {
  const drawer = byId("ask-side-drawer");
  if (drawer) drawer.hidden = true;
}

/* ───── Graph ───── */

async function renderGraph(entityId) {
  setPageHeader({
    title: "Graph",
    subtitle: entityId ? "Inspect one entity's relationships." : "Search for an entity to open its graph.",
    actions: entityId
      ? `<a class="btn-ghost" href="#graph"><i class="ti ti-arrow-left" aria-hidden="true"></i> Back to search</a>`
      : "",
  });
  const app = document.getElementById("app");
  if (!entityId) {
    app.innerHTML = `
      <div class="graph-empty">
        <div class="graph-search-card">
          <div class="search-row">
            <input class="input" id="graph-search" placeholder="Search for a person, organization, or project" value="${escapeAttr(state.graphSearch)}" autocomplete="off" />
            <button class="btn-primary" id="graph-search-go"><i class="ti ti-search" aria-hidden="true"></i> Find</button>
          </div>
          <div id="graph-results" class="graph-autocomplete"></div>
        </div>
        <svg id="placeholder-graph" class="placeholder-graph" aria-hidden="true"></svg>
        <div class="graph-placeholder-caption">Search above to see real connections.</div>
      </div>
    `;
    setupGraphSearch();
    drawPlaceholderGraph();
    return;
  }
  app.innerHTML = `
    <div id="entity-panel"></div>
  `;
  try {
    const data = await getJSON(`/api/entity/${entityId}`);
    setPageHeader({
      title: "Graph",
      subtitle: data.identity.canonical_name,
      actions: `<a class="btn-ghost" href="#graph"><i class="ti ti-arrow-left" aria-hidden="true"></i> Back to search</a>`,
    });
    renderEntityPanel(data);
  } catch (e) {
    byId("entity-panel").innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load: ${escapeHtml(e.message)}</div></div>`;
  }
}

function setupGraphSearch() {
  const input = byId("graph-search");
  const go = () => {
    const first = state.graphSearchResults[0];
    if (first) {
      location.hash = `#graph/${first.entity_id}`;
      return;
    }
    runGraphSearch();
  };
  let timer = null;
  input.addEventListener("input", () => {
    state.graphSearch = input.value.trim();
    clearTimeout(timer);
    timer = setTimeout(runGraphSearch, 180);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      go();
    }
  });
  byId("graph-search-go").onclick = go;
  if (state.graphSearch) runGraphSearch();
  input.focus();
}

async function runGraphSearch() {
  const q = byId("graph-search")?.value.trim() || state.graphSearch;
  const out = byId("graph-results");
  state.graphSearch = q;
  if (!q) {
    state.graphSearchResults = [];
    if (out) out.innerHTML = "";
    return;
  }
  try {
    const data = await getJSON(`/api/directory?q=${encodeURIComponent(q)}&page_size=8`);
    state.graphSearchResults = data.results || [];
    if (!out) return;
    if (!state.graphSearchResults.length) {
      out.innerHTML = `<div class="graph-result-empty">No matches.</div>`;
      return;
    }
    out.innerHTML = state.graphSearchResults
      .map(
        (row) => `
        <button class="btn-ghost graph-result" type="button" data-entity-id="${escapeAttr(row.entity_id)}">
          <span>${escapeHtml(row.canonical_name || "Unknown")}</span>
          <small>${escapeHtml(rowBio(row))}</small>
        </button>`,
      )
      .join("");
    out.querySelectorAll("[data-entity-id]").forEach((button) => {
      button.onclick = () => (location.hash = `#graph/${button.dataset.entityId}`);
    });
  } catch (e) {
    if (out) out.innerHTML = `<div class="graph-result-empty">Unable to search: ${escapeHtml(e.message)}</div>`;
  }
}

function drawPlaceholderGraph() {
  const svgElement = byId("placeholder-graph");
  if (!svgElement) return;
  const width = svgElement.clientWidth || 640;
  const height = svgElement.clientHeight || 260;
  if (typeof d3 === "undefined") {
    svgElement.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svgElement.innerHTML = Array.from({ length: 12 })
      .map((_, index) => {
        const x = 80 + (index % 4) * 150;
        const y = 55 + Math.floor(index / 4) * 72;
        return `<circle cx="${x}" cy="${y}" r="9" fill="${escapeAttr(cssVar("--line-strong"))}"></circle>`;
      })
      .join("");
    return;
  }
  const svg = d3.select(svgElement);
  svg.attr("viewBox", `0 0 ${width} ${height}`);
  svg.selectAll("*").remove();
  const nodes = Array.from({ length: 12 }, (_, id) => ({ id }));
  const links = Array.from({ length: 18 }, (_, index) => ({
    source: index % nodes.length,
    target: (index * 5 + 3) % nodes.length,
  }));
  const link = svg
    .append("g")
    .selectAll("line")
    .data(links)
    .enter()
    .append("line")
    .attr("stroke", cssVar("--line-strong"))
    .attr("stroke-width", 1);
  const node = svg
    .append("g")
    .selectAll("circle")
    .data(nodes)
    .enter()
    .append("circle")
    .attr("r", 8)
    .attr("fill", cssVar("--line"));
  const sim = d3
    .forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((item) => item.id).distance(64))
    .force("charge", d3.forceManyBody().strength(-55))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .alpha(0.7);
  sim.on("tick", () => {
    link
      .attr("x1", (item) => item.source.x)
      .attr("y1", (item) => item.source.y)
      .attr("x2", (item) => item.target.x)
      .attr("y2", (item) => item.target.y);
    node.attr("cx", (item) => item.x).attr("cy", (item) => item.y);
  });
  setTimeout(() => sim.stop(), 3000);
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
      <div class="entity-hero-main">
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
          <span class="swatch legend-verified"><svg width="20" height="3"><line x1="0" y1="1.5" x2="20" y2="1.5" stroke="currentColor" stroke-width="2"/></svg> verified edge</span>
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
      .attr("fill", cssVar("--text-faint"))
      .attr("font-size", cssVar("--fs-base"))
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
    .attr("stroke", (d) => (d.is_resolved === false ? cssVar("--text-faint") : cssVar("--green")))
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
        .attr("fill", isProject ? cssVar("--bg") : d.focus ? cssVar("--green") : cssVar("--bg"))
        .attr("stroke", cssVar("--green"))
        .attr("stroke-width", 2);
    } else {
      g.append("circle")
        .attr("r", d.focus ? 22 : 16)
        .attr("fill", d.focus ? cssVar("--green") : cssVar("--bg"))
        .attr("stroke", cssVar("--green"))
        .attr("stroke-width", 2);
    }
    g.append("text")
      .attr("y", d.focus ? 38 : 32)
      .attr("text-anchor", "middle")
      .attr("font-size", d.focus ? cssVar("--fs-sm") : cssVar("--fs-xs"))
      .attr("fill", cssVar("--text"))
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
  const adminActions = state.me?.is_admin
    ? `<div class="source-actions">
         <button class="btn-primary" id="add-source"><i class="ti ti-plus"></i> Add source</button>
         <button class="btn-primary" id="run-pipeline" title="Crawl all active sources"><i class="ti ti-player-play"></i> Run pipeline</button>
       </div>`
    : "";
  setPageHeader({ title: "Sources", subtitle: "Loading…", actions: adminActions });
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="stats-grid" id="sources-stats">${statCards(state.stats || {})}</div>
    <div id="archived-cleanup-banner"></div>
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
    renderShell();
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
          <button class="btn-icon-only stat-info" aria-label="${escapeAttr(card.ariaLabel)}" data-term="${escapeAttr(card.key)}"><i class="ti ti-info-circle" aria-hidden="true"></i></button>
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
    renderArchivedCleanupBanner(Number(data.archived_count || 0));
    state.sourcesCache = sources;
    const active = sources.filter((s) => s.status === "active");
    const paused = sources.filter((s) => s.status === "paused");
    const pageSubtitle = byId("page-subtitle");
    if (pageSubtitle) {
      pageSubtitle.textContent = `${active.length} active · ${paused.length} paused`;
    }
    const list = byId("sources-list");
    if (!sources.length) {
      list.innerHTML = state.me?.is_admin
        ? `<div class="empty-state sources-empty">
             <i class="ti ti-database-plus" aria-hidden="true"></i>
             <h2>No sources yet</h2>
             <p>Add your first source to start building your graph.</p>
             <button class="btn-primary" id="empty-add-source"><i class="ti ti-plus" aria-hidden="true"></i> Add source</button>
           </div>`
        : `<div class="empty-state sources-empty">
             <i class="ti ti-database-off" aria-hidden="true"></i>
             <h2>No sources yet</h2>
             <p>Add your first source to start building your graph.</p>
           </div>`;
      const emptyAddSource = byId("empty-add-source");
      if (emptyAddSource) emptyAddSource.onclick = openAddSourceModal;
      return;
    }
    list.innerHTML = sources.map((s) => sourceRow(s)).join("");
    list.querySelectorAll(".source-row").forEach((row) => {
      const id = row.dataset.sourceId;
      row.onclick = (e) => {
        if (e.target.closest("button")) return;
        location.hash = `#sources/${id}`;
      };
      const crawl = row.querySelector("[data-action=crawl]");
      const parse = row.querySelector("[data-action=parse]");
      const resume = row.querySelector("[data-action=resume]");
      const menuBtn = row.querySelector("[data-action=menu]");
      if (crawl) crawl.onclick = (e) => { e.stopPropagation(); runSourceAction(id, "crawl"); };
      if (parse) parse.onclick = (e) => { e.stopPropagation(); runSourceAction(id, "parse"); };
      if (resume) resume.onclick = (e) => {
        e.stopPropagation();
        updateSourceStatus(id, "active");
      };
      if (menuBtn) menuBtn.onclick = (e) => {
        e.stopPropagation();
        toggleMenu(row, id);
      };
    });
  } catch (e) {
    byId("sources-list").innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load sources: ${escapeHtml(e.message)}</div></div>`;
  }
}

function renderArchivedCleanupBanner(count) {
  const banner = byId("archived-cleanup-banner");
  if (!banner) return;
  if (!state.me?.is_admin || count <= 0) {
    banner.innerHTML = "";
    return;
  }
  banner.innerHTML = `
    <div class="panel panel-flush">
      <div class="panel-header">
        <div>
          <div class="panel-title">${formatNumber(count)} archived ${count === 1 ? "source" : "sources"} from a previous version are still taking up space.</div>
          <div class="muted small">Permanently remove archived sources and orphaned data.</div>
        </div>
        <button class="btn-secondary" id="nuke-archived" type="button">Clean up now</button>
      </div>
    </div>
  `;
  byId("nuke-archived").onclick = nukeArchivedSources;
}

async function nukeArchivedSources() {
  if (
    !confirm(
      "This permanently removes all archived sources and orphaned data (documents, claims, entities) that no longer belong to any active source. Cannot be undone.",
    )
  )
    return;
  try {
    const response = await fetch("/admin/nuke-archived", { method: "POST" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || response.statusText);
    }
    const summary = await response.json();
    toast(nukeSummaryText(summary), "success");
    await Promise.all([loadSourcesStats(), loadSourcesList(), loadAdminConflicts()]);
  } catch (e) {
    toast(`Cleanup failed: ${e.message}`, "error");
  }
}

function nukeSummaryText(summary) {
  const labels = [
    ["archived_sources_removed", "archived sources"],
    ["orphan_documents_removed", "documents"],
    ["orphan_claims_removed", "claims"],
    ["orphan_entities_removed", "entities"],
    ["orphan_files_removed", "files"],
  ];
  const parts = labels
    .map(([key, label]) => [Number(summary?.[key] || 0), label])
    .filter(([count]) => count > 0)
    .map(([count, label]) => `${formatNumber(count)} ${label}`);
  return parts.length ? `Cleanup removed ${parts.join(", ")}.` : "Cleanup found nothing to remove.";
}

function sourceRow(source) {
  const paused = source.status === "paused";
  const kindLabel = sourceKindLabel(source);
  const kindIcon = sourceKindIcon(source);
  const actions = paused
    ? `<button class="btn-source" data-action="resume"><i class="ti ti-player-play"></i> Resume</button>`
    : `<button class="btn-source" data-action="crawl" title="Fetch all documents from this source"><i class="ti ti-download"></i> Crawl</button>
       <button class="btn-source" data-action="parse" title="Re-run extraction on already-fetched documents"><i class="ti ti-cpu"></i> Parse</button>`;
  const menuButton = state.me?.is_admin
    ? `<button class="btn-icon-only" data-action="menu" aria-label="More"><i class="ti ti-dots"></i></button>`
    : "";
  return `
    <article class="source-row ${paused ? "paused" : ""}" data-source-id="${escapeAttr(source.id)}">
      <div class="source-row-main">
        <i class="ti ${kindIcon} source-row-icon" aria-hidden="true"></i>
        <div class="source-row-copy">
          <div class="source-row-name">${escapeHtml(source.display_name || source.identifier)}</div>
          <div class="source-row-meta">
            <span>${escapeHtml(kindLabel)}</span>
            <span class="source-row-identifier">${escapeHtml(source.identifier || "")}</span>
          </div>
        </div>
      </div>
      <div class="source-row-stats">
        <span><strong>${formatNumber(source.coverage.documents)}</strong> docs</span>
        <span><strong>${formatNumber(source.coverage.claims)}</strong> claims</span>
        <span class="muted">${source.last_run_at ? `last run ${timeAgo(source.last_run_at)}` : "never run"}</span>
        <span class="status-pill ${source.status}">${capitalize(source.status)}</span>
      </div>
      ${state.me?.is_admin ? `<div class="source-row-actions">${actions}${menuButton}</div>` : ""}
    </article>
  `;
}

function sourceMetaLine(source) {
  return `${sourceKindLabel(source)} · ${source.identifier}`;
}

function sourceFormat(source) {
  const line = String(source.notes || "")
    .split("\n")
    .map((part) => part.trim())
    .find((part) => part.startsWith("format:"));
  return line ? line.slice("format:".length).trim() : "";
}

function sourceKindLabel(source) {
  if (source.kind === "domain") return "Sitemap";
  if (source.kind === "file") {
    const format = sourceFormat(source);
    if (format === "dataset") return "Dataset";
    if (format === "text") return "General Text";
    return "File";
  }
  return source.kind || "Source";
}

function sourceKindIcon(source) {
  if (source.kind === "domain") return "ti-world";
  if (source.kind === "file") {
    const format = sourceFormat(source);
    if (format === "dataset") return "ti-table";
    if (format === "text") return "ti-file-text";
    return "ti-file";
  }
  return source.icon_hint || "ti-database";
}

function toggleMenu(container, sourceId) {
  const existing = container.querySelector(".menu");
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
    ${source && source.kind === "file" ? `<button class="menu-item" data-act="download"><i class="ti ti-download"></i> Download original</button>` : ""}
    ${isPaused ? "" : `<button class="menu-item" data-act="pause"><i class="ti ti-player-pause"></i> Pause</button>`}
    <button class="menu-item danger" data-act="remove"><i class="ti ti-trash"></i> Remove</button>
  `;
  container.querySelector(".source-row-actions, .source-actions").appendChild(menu);
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
  } else if (action === "pause") {
    await patchSource(sourceId, { status: "paused" });
    toast("Source paused.", "success");
    loadSourcesList();
  } else if (action === "remove") {
    if (!confirm(REMOVE_SOURCE_CONFIRM)) return;
    await fetch(`/admin/sources/${sourceId}`, { method: "DELETE" });
    toast("Source removed.", "success");
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
  setPageHeader({
    title: "Loading…",
    subtitle: "Sources",
    eyebrow: `<a href="#sources">Sources</a> / Source detail`,
  });
  const app = document.getElementById("app");
  app.innerHTML = `
    <div id="source-detail-head"></div>
    <div class="tabs-sub" id="source-detail-tabs"></div>
    <div class="tab-content" id="source-tab-content">
      <div class="empty-state"><i class="ti ti-loader"></i><div>Loading…</div></div>
    </div>
  `;
  try {
    const detail = await getJSON(`/api/sources/${sourceId}`);
    const normalizedTab = detail.kind === "file" || activeTab !== "files" ? activeTab : "documents";
    setPageHeader({
      title: detail.display_name || detail.identifier,
      subtitle: sourceMetaLine(detail),
      eyebrow: `<a href="#sources">Sources</a> / Source detail`,
    });
    renderSourceDetailHead(detail);
    renderSourceDetailTabs(detail, normalizedTab);
    if (normalizedTab === "documents") renderSourceDocuments(sourceId);
    else if (normalizedTab === "files") renderSourceFiles(detail);
    else if (normalizedTab === "runs") renderSourceRuns(detail);
    else renderSourceConfig(detail);
  } catch (e) {
    byId("source-tab-content").innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load: ${escapeHtml(e.message)}</div></div>`;
  }
}

function renderSourceDetailTabs(source, activeTab) {
  const tabs = [
    ["documents", "Documents"],
    ...(source.kind === "file" ? [["files", "Files"]] : []),
    ["runs", "Runs"],
    ["config", "Config"],
  ];
  const root = byId("source-detail-tabs");
  root.innerHTML = tabs
    .map(
      ([id, label]) =>
        `<button class="btn-ghost tab-sub ${activeTab === id ? "active" : ""}" data-tab="${id}">${label}</button>`,
    )
    .join("");
  root.querySelectorAll(".tab-sub").forEach((button) => {
    button.onclick = () => (location.hash = `#sources/${source.id}/${button.dataset.tab}`);
  });
}

function renderSourceDetailHead(source) {
  const head = byId("source-detail-head");
  const title = source.display_name || source.identifier;
  head.innerHTML = `
    <div class="entity-hero">
      <i class="ti ${sourceKindIcon(source)} source-detail-icon"></i>
      <div class="entity-hero-main">
        <h1 id="source-name-title">${
          state.me?.is_admin
            ? `<button class="btn-ghost source-title-edit" type="button" data-action="edit-source-name">${escapeHtml(title)}</button>`
            : escapeHtml(title)
        }</h1>
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
          ? `<div class="source-actions">
               <button class="btn-source" data-action="crawl" title="Fetch all documents from this source"><i class="ti ti-download"></i> Crawl</button>
               <button class="btn-source" data-action="parse" title="Re-run extraction on already-fetched documents"><i class="ti ti-cpu"></i> Parse</button>
             </div>`
          : ""
      }
    </div>
  `;
  if (state.me?.is_admin) {
    head.querySelector("[data-action=crawl]").onclick = () => runSourceAction(source.id, "crawl");
    head.querySelector("[data-action=parse]").onclick = () => runSourceAction(source.id, "parse");
    head.querySelector("[data-action=edit-source-name]").onclick = () => startSourceNameEdit(source);
  }
}

function startSourceNameEdit(source) {
  const title = byId("source-name-title");
  const current = source.display_name || source.identifier;
  title.innerHTML = `<input class="input source-title-input" aria-label="Source name" value="${escapeAttr(current)}" />`;
  const input = title.querySelector("input");
  let saving = false;
  input.focus();
  input.select();
  input.onblur = () => {
    if (!saving) renderSourceDetailHead(source);
  };
  input.onkeydown = async (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      renderSourceDetailHead(source);
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    const next = input.value.trim();
    if (!next || next === current) {
      renderSourceDetailHead(source);
      return;
    }
    saving = true;
    await patchSource(source.id, { display_name: next });
    toast("Renamed.", "success");
    renderSourceDetail(source.id, currentSourceDetailTab());
  };
}

function currentSourceDetailTab() {
  const [, , tab] = (location.hash.replace(/^#/, "") || "").split("/");
  return tab || "documents";
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
      <div class="muted small table-note">${data.total} total · showing ${data.results.length}</div>
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
        <button class="btn-icon-only modal-close" onclick="closeModal()" aria-label="Close">×</button>
      </div>
      <div class="doc-viewer">
        <div class="muted small">${data.word_count || 0} words · ${data.chunks?.length || 0} chunks · ${data.claims_raw?.length || 0} extracted claims</div>
        <pre>${escapeHtml((data.cleaned_text || "").slice(0, 5000))}${(data.cleaned_text || "").length > 5000 ? "\n\n…(truncated)" : ""}</pre>
        ${claimsHtml ? `<div class="doc-claims"><div class="muted small doc-claims-title">Extracted claims</div><ul class="doc-claims-list">${claimsHtml}</ul></div>` : ""}
      </div>
    `);
  } catch (e) {
    openModal(`<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load: ${escapeHtml(e.message)}</div></div>`);
  }
}

function renderSourceFiles(detail) {
  const wrap = byId("source-tab-content");
  const isAdmin = Boolean(state.me?.is_admin);
  const fileAvailable = detail.file_size_bytes != null;
  wrap.innerHTML = `
    <div class="panel panel-flush source-files-panel">
      ${
        isAdmin
          ? `<div class="file-upload-zone" id="file-upload-zone" role="button" tabindex="0">
               <i class="ti ti-upload" aria-hidden="true"></i>
               <div>Drop file to replace, or click to browse</div>
               <input
                 class="input file-replace-input"
                 id="file-replace-input"
                 type="file"
                 accept=".xlsx,.csv,.json,.tsv,.txt,.md,.pdf,.html"
               />
             </div>`
          : ""
      }
      ${
        fileAvailable
          ? `<table class="files-table">
               <thead>
                 <tr>
                   <th>Filename</th>
                   <th>Uploaded</th>
                   <th class="num">Size</th>
                   <th>Actions</th>
                 </tr>
               </thead>
               <tbody>
                 <tr>
                   <td><div class="cell-truncate">${escapeHtml(detail.identifier)}</div></td>
                   <td class="muted small">${escapeHtml(timeAgo(detail.created_at))}</td>
                   <td class="num">${escapeHtml(formatBytes(detail.file_size_bytes))}</td>
                   <td>
                     <div class="file-actions">
                       <a class="btn-secondary" href="/api/sources/${escapeAttr(detail.id)}/download"><i class="ti ti-download" aria-hidden="true"></i> Download</a>
                       ${
                         isAdmin
                           ? `<button class="btn-danger" id="file-remove" type="button" title="Delete the uploaded file and remove this source"><i class="ti ti-trash" aria-hidden="true"></i> Remove</button>`
                           : ""
                       }
                     </div>
                   </td>
                 </tr>
               </tbody>
             </table>`
          : `<div class="empty-state source-file-empty">
               <i class="ti ti-file-off" aria-hidden="true"></i>
               <div>Original file no longer available.</div>
               ${isAdmin ? `<div class="muted small">Use the replacement upload above to attach a new file.</div>` : ""}
             </div>`
      }
    </div>
  `;
  if (!isAdmin) return;
  setupSourceFileUpload(detail);
  const remove = byId("file-remove");
  if (remove) {
    remove.onclick = () => removeSourceFile(detail.id);
  }
}

function setupSourceFileUpload(detail) {
  const zone = byId("file-upload-zone");
  const input = byId("file-replace-input");
  if (!zone || !input) return;
  zone.onclick = () => input.click();
  zone.onkeydown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  };
  zone.ondragover = (event) => {
    event.preventDefault();
    zone.classList.add("dragging");
  };
  zone.ondragleave = () => zone.classList.remove("dragging");
  zone.ondrop = (event) => {
    event.preventDefault();
    zone.classList.remove("dragging");
    const [file] = Array.from(event.dataTransfer?.files || []);
    if (file) replaceSourceFile(detail.id, file);
  };
  input.onchange = () => {
    const [file] = Array.from(input.files || []);
    if (file) replaceSourceFile(detail.id, file);
  };
}

async function replaceSourceFile(sourceId, file) {
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await fetch(`/admin/sources/${sourceId}/upload`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || response.statusText);
    }
    toast("File replaced.", "success");
    renderSourceDetail(sourceId, "files");
  } catch (e) {
    toast(`Upload failed: ${e.message}`, "error");
  }
}

async function removeSourceFile(sourceId) {
  if (
    !confirm(
      REMOVE_SOURCE_CONFIRM,
    )
  )
    return;
  try {
    const response = await fetch(`/admin/sources/${sourceId}`, { method: "DELETE" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || response.statusText);
    }
    toast("Source removed.", "success");
    location.hash = "#sources";
  } catch (e) {
    toast(`Remove failed: ${e.message}`, "error");
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
  const formatLabel = sourceKindLabel(detail);
  wrap.innerHTML = `
    <div class="panel panel-flush">
      <div class="modal-body">
        <label class="field">
          <span class="field-label">Display name</span>
          <input class="input" id="cfg-name" value="${escapeAttr(detail.display_name || "")}" ${adminOnly ? "disabled" : ""} />
        </label>
        <div class="field-row">
          <label class="field">
            <span class="field-label">Identifier</span>
            <input class="input" value="${escapeAttr(detail.identifier)}" disabled />
            <span class="field-hint">Identifier cannot be changed after creation.</span>
          </label>
        </div>
        ${
          detail.kind === "file"
            ? `<label class="field">
                 <span class="field-label">Format</span>
                 <input class="input" value="${escapeAttr(formatLabel)}" disabled />
                 <span class="field-hint">This can't be changed after creation.</span>
               </label>`
            : ""
        }
        <label class="field">
          <span class="field-label">Notes</span>
          <textarea class="input" id="cfg-notes" rows="3" ${adminOnly ? "disabled" : ""}>${escapeHtml(stripSourceMetaLines(detail.notes || ""))}</textarea>
        </label>
        ${
          !adminOnly
            ? `<div class="form-actions"><button class="btn-primary" id="cfg-save"><i class="ti ti-device-floppy"></i> Save</button></div>`
            : ""
        }
      </div>
    </div>
  `;
  if (!adminOnly) {
    byId("cfg-save").onclick = async () => {
      const body = {
        display_name: byId("cfg-name").value.trim() || null,
        notes: buildSourceNotes(detail, byId("cfg-notes").value.trim()),
      };
      await patchSource(detail.id, body);
      toast("Saved.", "success");
      renderSourceDetail(detail.id, "config");
    };
  }
}

function stripSourceMetaLines(notes) {
  return String(notes || "")
    .split("\n")
    .filter((line) => !line.startsWith("status:") && !line.startsWith("format:"))
    .join("\n")
    .trim();
}

function buildSourceNotes(source, userNotes) {
  const lines = [];
  const format = sourceFormat(source);
  if (format) lines.push(`format:${format}`);
  if (userNotes) lines.push(userNotes);
  return lines.join("\n") || null;
}

/* ───── Add source modal ───── */

let modalKind = "domain";

function openAddSourceModal() {
  modalKind = "domain";
  renderAddSourceModal();
}

function renderAddSourceModal() {
  const selected = SOURCE_KINDS.find((k) => k.id === modalKind) || SOURCE_KINDS[0];
  openModal(`
    <div class="modal-header">
      <div>
        <div class="modal-title">Add source</div>
        <div class="modal-subtitle">Add a crawlable sitemap or upload a file.</div>
      </div>
      <button class="btn-icon-only modal-close" onclick="closeModal()" aria-label="Close">×</button>
    </div>
    <div class="modal-body">
      <div>
        <div class="field-label kind-label">Kind</div>
        <div class="kind-grid">
          ${SOURCE_KINDS.map(
            (k) => `
            <button type="button" class="btn-secondary kind-card ${k.id === modalKind ? "selected" : ""}" data-kind="${k.id}">
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
        <span class="field-label">Label</span>
        <input class="input" id="new-name" placeholder="e.g. Dartmouth News" />
        <span class="field-hint">Used everywhere this source appears.</span>
      </label>
      ${selected.fields
        .map((f) => {
          if (f.type === "file") {
            return `<label class="field">
                <span class="field-label">${escapeHtml(f.label)}</span>
                <input class="input" id="new-${f.name}" type="file" ${f.accept ? `accept="${escapeAttr(f.accept)}"` : ""} />
                ${selected.hint ? `<span class="field-hint">${escapeHtml(selected.hint)}</span>` : ""}
              </label>`;
          }
          return `<label class="field">
              <span class="field-label">${escapeHtml(f.label)}</span>
              <input class="input" id="new-${f.name}" placeholder="${escapeAttr(f.placeholder || "")}" />
            </label>`;
        })
        .join("")}
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
  if (!display_name) {
    toast("Label is required.", "error");
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
      if (selected.format) form.append("notes", `format:${selected.format}`);
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
        display_name,
        notes: selected.format ? `format:${selected.format}` : null,
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
        <div class="conflict-actions">
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
      "Crawl all active sources? This may take several minutes and incurs API costs.",
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
  event?.preventDefault();
  const tab = currentTab();
  await fetch("/admin/logout", { method: "POST" });
  await Promise.all([loadMe(), loadStats()]);
  renderShell();
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
  modalRestoreFocus = document.activeElement;
  if (modalKeydownHandler) {
    document.removeEventListener("keydown", modalKeydownHandler);
  }
  root.innerHTML = `<div class="modal-overlay" onclick="closeModalOnBackdrop(event)"><div class="modal" role="dialog" aria-modal="true" tabindex="-1" onclick="event.stopPropagation()">${html}</div></div>`;
  const modal = root.querySelector(".modal");
  modalKeydownHandler = (event) => trapModalFocus(event, modal);
  document.addEventListener("keydown", modalKeydownHandler);
  setTimeout(() => focusFirstModalControl(modal), 0);
}

function closeModal() {
  if (modalKeydownHandler) {
    document.removeEventListener("keydown", modalKeydownHandler);
    modalKeydownHandler = null;
  }
  document.getElementById("modal-root").innerHTML = "";
  const restoreTarget = modalRestoreFocus;
  modalRestoreFocus = null;
  if (restoreTarget && document.contains(restoreTarget) && typeof restoreTarget.focus === "function") {
    restoreTarget.focus();
  }
}

function closeModalOnBackdrop(event) {
  if (event.target.classList.contains("modal-overlay")) closeModal();
}

function trapModalFocus(event, modal) {
  if (!modal) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeModal();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = modalFocusableElements(modal);
  if (!focusable.length) {
    event.preventDefault();
    modal.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function focusFirstModalControl(modal) {
  if (!modal) return;
  const [first] = modalFocusableElements(modal);
  if (first) {
    first.focus();
  } else {
    modal.focus();
  }
}

function modalFocusableElements(modal) {
  return Array.from(
    modal.querySelectorAll(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => !element.hidden);
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

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || `var(${name})`;
}

function escapeHtml(value) {
  if (value == null) return "";
  return String(value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[c],
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

function formatBytes(bytes) {
  if (bytes == null) return "—";
  const value = Number(bytes);
  if (!Number.isFinite(value)) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
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
window.resetDirectoryFilters = resetDirectoryFilters;
window.closeSideDrawer = closeSideDrawer;
