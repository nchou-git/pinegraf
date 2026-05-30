"use strict";

const ASK_SESSION_KEY = "pinegraf_ask_session";
const RUN_PROGRESS_KEY = "pinegraf_run_progress";
const ASK_EXAMPLES = ["Tuck alums in tech", "Who worked on Gyrobike?"];
const ZERO_STATS = { documents: 0, claims: 0, entities: 0, sources: 0 };
const ARCHIVE_SOURCE_CONFIRM = "Derived data is preserved and the source can be restored later.";
const MAX_TOASTS = 3;

const state = {
  me: null,
  stats: null,
  directoryPage: 1,
  directoryFilters: { q: "", sources: [], class_years: [], orgs: [], sort: "name_asc" },
  directoryRows: [],
  directoryOptionRows: [],
  claimsPage: 1,
  claimsFilters: { q: "", predicate: "", source_id: "", status: "current" },
  claimPredicates: [],
  rawDataPage: 1,
  rawDataFilters: { q: "", predicate: "" },
  rawDataExpanded: {},
  rawDataChunks: {},
  sourcesCache: null,
  sourcesError: null,
  runProgress: readSessionJSON(RUN_PROGRESS_KEY, {}),
  runStreams: {},
  askSession: JSON.parse(sessionStorage.getItem(ASK_SESSION_KEY) || "[]"),
  graphSearch: "",
  graphSearchResults: [],
  sidebarCollapsed: sessionStorage.getItem("pinegraf.sidebarCollapsed") === "true",
};

let modalRestoreFocus = null;
let modalKeydownHandler = null;
let logsViewStream = null;

const TAB_DEFS = [
  { id: "directory", label: "Directory", icon: "ti-list-search" },
  { id: "ask", label: "Ask", icon: "ti-message-question" },
  { id: "claims", label: "Claims", icon: "ti-file-search" },
  { id: "graph", label: "Graph", icon: "ti-vector-triangle" },
  { id: "sources", label: "Sources", icon: "ti-database" },
];
const RAW_DATA_TAB = { id: "raw", label: "Raw data", icon: "ti-database-search" };
const CONFLICTS_TAB = { id: "conflicts", label: "Conflicts", icon: "ti-alert-triangle" };
const LOGS_TAB = { id: "logs", label: "Logs", icon: "ti-terminal-2" };

const SOURCE_KINDS = [
  {
    id: "domain",
    kind: "domain",
    label: "Website",
    description: "Crawl a website by domain, sitemap URL, or any page URL.",
    icon: "ti-world",
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
    id: "file",
    kind: "file",
    label: "Upload a file",
    icon: "ti-file",
    fields: [
      {
        name: "file",
        label: "File",
        type: "file",
        required: true,
      },
    ],
  },
  {
    id: "enrichment",
    kind: "enrichment",
    label: "Alumni roster (PDL)",
    description: "Upload an XLSX/CSV with First Name, Last Name, and Class columns. Each row is enriched via People Data Labs.",
    icon: "ti-users",
    fields: [
      {
        name: "file",
        label: "Alumni file (XLSX or CSV)",
        type: "file",
        accept: ".xlsx,.csv,.xlsm",
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
    definition: "The number of discrete records fed into Pinegraf.",
    example: "Example: one row of alumni data from the database. One webpage from tuck.dartmouth.edu.",
  },
  {
    key: "claims",
    label: "Claims",
    ariaLabel: "What is a claim?",
    definition: "The number of inferred associations made by Pinegraf.",
    example: "Example: Errik Anderson founded Adimab in 2007.",
  },
  {
    key: "entities",
    label: "Entities",
    ariaLabel: "What is an entity?",
    definition: "The number of people after resolving conflicts.",
    example: "Example: 'Daniella Reichstetter' and 'Daniella Reichstetter T'07' are one entity.",
  },
  {
    key: "sources",
    label: "Sources",
    ariaLabel: "What is a source?",
    definition: "The number of data feeds.",
    example: "Example: tuck.dartmouth.edu, SerpAPI, and alum_data.xlsx are three unique sources.",
  },
];

document.addEventListener("DOMContentLoaded", init);

async function init() {
  if (window.__PINEGRAF_FORCE_LOGIN__ === true) {
    renderLoginGate();
    return;
  }
  ensureToastContainer();
  setupShell();
  renderShell();
  renderInitialRouteSkeleton();
  window.addEventListener("hashchange", renderRoute);
  await Promise.all([loadMe(), loadStats()]);
  renderShell();
  renderRoute();
}

function renderLoginGate() {
  document.body.className = "login-body";
  document.body.innerHTML = `
    <main class="login-shell">
      <div class="login-card">
        <div class="login-brand">
          <svg
            class="login-mark"
            viewBox="0 0 40 40"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
          >
            <polygon
              points="20,4 12,14 16,14 9,22 14,22 6,32 34,32 26,22 31,22 24,14 28,14"
              fill="currentColor"
            />
            <rect x="18" y="32" width="4" height="4" fill="currentColor"/>
          </svg>
          <div>
            <div class="wordmark">Pinegraf</div>
            <div class="login-note">Demo environment</div>
          </div>
        </div>
        <div class="login-error" id="login-gate-error" hidden></div>
        <form class="login-form" id="login-gate-form">
          <label class="field">
            <span class="field-label">Username</span>
            <input
              class="input"
              id="login-gate-username"
              name="username"
              autocomplete="username"
              value="pinegraf"
              autofocus
              required
            />
          </label>
          <label class="field">
            <span class="field-label">Password</span>
            <input
              class="input"
              id="login-gate-password"
              name="password"
              type="password"
              autocomplete="current-password"
              required
            />
          </label>
          <button type="submit" class="btn-primary" id="login-gate-submit">Submit</button>
        </form>
      </div>
    </main>
  `;

  const form = byId("login-gate-form");
  const error = byId("login-gate-error");
  const submit = byId("login-gate-submit");
  byId("login-gate-username").focus();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    try {
      const response = await fetch("/admin/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          username: byId("login-gate-username").value,
          password: byId("login-gate-password").value,
          next: "/",
        }),
      });
      if (!response.ok) throw new Error("Wrong username or password.");
      location.reload();
    } catch (err) {
      error.textContent = err.message || "Unable to sign in.";
      error.hidden = false;
      submit.disabled = false;
      byId("login-gate-password").focus();
    }
  });
}

function renderInitialRouteSkeleton() {
  const rawRoute = location.hash.replace(/^#/, "") || "directory";
  const [route] = rawRoute.split("?");
  const [tab, ...rest] = route.split("/");
  if (tab === "sources" && !rest.length) {
    renderSourcesFrame("");
  }
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
  nav.innerHTML = navTabs()
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

function navTabs() {
  if (!isAdmin()) return TAB_DEFS;
  const tabs = [];
  TAB_DEFS.forEach((tab) => {
    tabs.push(tab);
    if (tab.id === "claims") tabs.push(RAW_DATA_TAB);
  });
  return [...tabs, CONFLICTS_TAB, LOGS_TAB];
}

function isAdmin() {
  return Boolean(state.me?.is_admin);
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
        ? `<button class="menu-item" data-admin-action="logout"><i class="ti ti-logout" aria-hidden="true"></i> Sign out</button>`
        : `<button class="menu-item" data-admin-action="login"><i class="ti ti-login" aria-hidden="true"></i> Sign in</button>`
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
      location.href = loginUrl();
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
  const route = (location.hash.replace(/^#/, "") || "directory").split("?")[0];
  return route.split("/")[0];
}

function renderRoute() {
  const rawRoute = location.hash.replace(/^#/, "") || "directory";
  const [route, queryString = ""] = rawRoute.split("?");
  const [tab, ...rest] = route.split("/");
  document.body.classList.toggle("route-logs", tab === "logs" && Boolean(state.me?.is_admin));
  if (tab !== "logs") stopLogsViewStream();
  if (tab === "admin") {
    history.replaceState(null, "", "#sources");
    renderShell();
    return renderSources([]);
  }
  if (tab === "logs") {
    if (!state.me?.is_admin) {
      history.replaceState(null, "", "#directory");
      renderShell();
      return renderDirectory();
    }
    closeMobileSidebar();
    renderShell();
    return renderLogs();
  }
  if (tab === "conflicts") {
    if (!state.me?.is_admin) {
      history.replaceState(null, "", "#directory");
      renderShell();
      return renderDirectory();
    }
    const section = rest[0] === "identity" ? "identity" : "facts";
    if (!rest[0]) history.replaceState(null, "", "#conflicts/facts");
    closeMobileSidebar();
    renderShell();
    return renderConflictsPage(section);
  }
  if (tab === "raw") {
    if (!state.me?.is_admin) {
      history.replaceState(null, "", "#directory");
      renderShell();
      return renderDirectory();
    }
    closeMobileSidebar();
    renderShell();
    return renderRawData();
  }
  closeMobileSidebar();
  renderShell();
  if (tab === "ask") return renderAsk();
  if (tab === "claims") return renderClaims(rest, new URLSearchParams(queryString));
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
    state.sourcesError = null;
    updateDirectoryFilterLabels();
  } catch (e) {
    state.sourcesCache = null;
    state.sourcesError = e;
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
    const sourceLoadFailed = state.sourcesCache === null && state.sourcesError;
    const sourceTotal = sourceLoadFailed ? null : (state.sourcesCache || []).length;
    const totalPages = Math.max(1, Math.ceil(total / (data.page_size || 50)));
    state.directoryRows = sortDirectoryRows(data.results || []);
    setPageHeader({
      title: "Directory",
      subtitle: sourceLoadFailed
        ? `${formatNumber(total || state.stats?.entities || 0)} people · sources unavailable`
        : `${formatNumber(total || state.stats?.entities || 0)} people across ${formatNumber(sourceTotal)} source${sourceTotal === 1 ? "" : "s"}`,
      actions: directoryHeaderActions(),
    });
    updateDirectoryFilterLabels();
    bindDirectoryHeaderFilters();
    if (sourceLoadFailed) {
      results.innerHTML = directorySourcesErrorState(state.sourcesError);
      bindDirectorySourcesErrorActions();
      byId("pagination").innerHTML = "";
      return;
    }
    if (sourceTotal === 0) {
      results.innerHTML = `
        <div class="directory-empty-panel">
          <i class="ti ti-database-off" aria-hidden="true"></i>
          <h2>No sources yet</h2>
          <p>Add your first source to start ingesting people, projects, and connections.</p>
          <a class="btn-primary" href="#sources">Go to Sources <i class="ti ti-arrow-right" aria-hidden="true"></i></a>
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
            <h2>Parse hasn't run yet</h2>
            <p>Go to Sources to crawl your first source.</p>
            <a class="btn-primary" href="#sources">Open Sources <i class="ti ti-arrow-right" aria-hidden="true"></i></a>
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

function directorySourcesErrorState(error) {
  const message = normalizeErrorMessage(error);
  const signIn = isAuthError(error)
    ? `<a class="btn-secondary" href="${escapeAttr(loginUrl())}">Sign in</a>`
    : "";
  return `
    <div class="directory-empty-panel">
      <i class="ti ti-alert-circle" aria-hidden="true"></i>
      <h2>Couldn't load sources.</h2>
      <p>${escapeHtml(message)}</p>
      <div class="empty-actions">
        <button class="btn-primary" id="directory-sources-retry" type="button">Retry</button>
        ${signIn}
      </div>
    </div>
  `;
}

function bindDirectorySourcesErrorActions() {
  const retry = byId("directory-sources-retry");
  if (!retry) return;
  retry.onclick = async () => {
    retry.disabled = true;
    await loadDirectorySources();
    await loadDirectory();
  };
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
  const attrClaims = row.primary_attribute_claims || {};
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
          <div class="avatar-circle">${escapeHtml(initials || "??")}</div>
          <div>
            <div class="directory-person-name">${escapeHtml(row.canonical_name || "Unknown")}</div>
            <div class="directory-person-sub">${attrs.class_year ? `T'${escapeHtml(String(attrs.class_year).replace(/^T'?/, ""))}` : escapeHtml(capitalize(row.kind || "entity"))}</div>
          </div>
        </div>
      </td>
      <td>${claimValueWithAttribution(attrs.current_title, attrClaims.current_title)}</td>
      <td>${claimValueWithAttribution(attrs.current_employer, attrClaims.current_employer)}</td>
      <td>${claimValueWithAttribution(attrs.class_year, attrClaims.class_year)}</td>
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

function claimValueWithAttribution(value, claim) {
  if (!value) return "";
  return `<span class="claim-value">${escapeHtml(value)}</span>${claimAttribution(claim)}`;
}

function claimAttribution(claimOrEvidence) {
  const evidence = claimEvidenceList(claimOrEvidence);
  if (!evidence.length) return "";
  const uniqueSourceIds = new Set(evidence.map((item) => item.source_id || item.source_identifier || item.source_name));
  const first = evidence[0];
  const label = uniqueSourceIds.size > 1
    ? `${uniqueSourceIds.size} sources`
    : `${first.source_name || first.source_identifier || "Source"}${first.fetched_at ? ` · ${formatDate(first.fetched_at)}` : ""}`;
  return `
    <details class="claim-attribution">
      <summary><i class="ti ti-chevron-right details-chevron" aria-hidden="true"></i><i class="ti ti-link" aria-hidden="true"></i><span>${escapeHtml(label)}</span></summary>
      <div class="claim-attribution-panel">
        ${evidence.map(claimEvidenceRow).join("")}
      </div>
    </details>
  `;
}

function claimDisplayLine(claim) {
  return `${claim.subject?.name || "Unknown"} ${claim.predicate || ""} ${claim.object?.name || claim.object_value || ""}`;
}

function claimEvidenceList(claimOrEvidence) {
  if (Array.isArray(claimOrEvidence)) return claimOrEvidence;
  if (Array.isArray(claimOrEvidence?.evidence)) return claimOrEvidence.evidence;
  return [];
}

function claimEvidenceRow(evidence) {
  const sourceName = evidence.source_name || evidence.source_identifier || "Source";
  const url = evidence.document_url || evidence.live_url || evidence.url || "";
  const fetched = evidence.fetched_at ? formatDate(evidence.fetched_at) : "";
  return `
    <div class="claim-evidence-row">
      <div class="claim-evidence-head">
        <strong>${escapeHtml(sourceName)}</strong>
        ${fetched ? `<span>${escapeHtml(fetched)}</span>` : ""}
      </div>
      ${url ? `<a href="${escapeAttr(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>` : ""}
      ${evidence.raw_quote ? `<blockquote>${escapeHtml(evidence.raw_quote)}</blockquote>` : ""}
    </div>
  `;
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

/* ───── Claims ───── */

async function renderClaims(parts = [], query = new URLSearchParams()) {
  const claimId = parts[0];
  if (claimId) return renderClaimDetail(claimId);
  if (query.get("source")) {
    state.claimsFilters.source_id = query.get("source");
  }
  setPageHeader({
    title: "Claims",
    subtitle: "Browse extracted claims across all sources.",
  });
  const app = byId("app");
  app.innerHTML = `
    <div class="directory-page claims-page">
      <section class="directory-filter-bar claims-filter-bar">
        <label class="directory-search input-with-icon">
          <i class="ti ti-search icon" aria-hidden="true"></i>
          <input class="input" id="claims-q" placeholder="Search subject or object" value="${escapeAttr(state.claimsFilters.q)}" />
        </label>
        <select class="input claims-filter-select" id="claims-predicate">
          <option value="">All predicates</option>
        </select>
        <select class="input claims-filter-select" id="claims-status">
          <option value="current">Current</option>
          <option value="superseded">Superseded</option>
          <option value="all">All</option>
        </select>
        <select class="input claims-filter-select" id="claims-source">
          <option value="">All sources</option>
        </select>
      </section>
      <section class="directory-results" id="claims-results">
        <div class="empty-state"><i class="ti ti-loader" aria-hidden="true"></i><div>Loading...</div></div>
      </section>
      <div class="pagination directory-pagination" id="claims-pagination"></div>
    </div>
  `;
  await Promise.all([loadClaimsSources(), loadClaimPredicates()]);
  setupClaimsFilters();
  await loadClaims();
}

async function loadClaimsSources() {
  if (state.sourcesCache) return;
  const data = await getJSON("/api/sources");
  state.sourcesCache = data.sources || [];
}

async function loadClaimPredicates() {
  const data = await getJSON("/api/claims/predicates");
  state.claimPredicates = data.predicates || [];
}

function setupClaimsFilters() {
  const predicate = byId("claims-predicate");
  predicate.innerHTML = `<option value="">All predicates</option>${(state.claimPredicates || [])
    .map((item) => `<option value="${escapeAttr(item)}">${escapeHtml(item)}</option>`)
    .join("")}`;
  predicate.value = state.claimsFilters.predicate || "";
  const source = byId("claims-source");
  source.innerHTML = `<option value="">All sources</option>${(state.sourcesCache || [])
    .filter((item) => item.status !== "archived")
    .map((item) => `<option value="${escapeAttr(item.id)}">${escapeHtml(item.display_name || item.identifier)}</option>`)
    .join("")}`;
  source.value = state.claimsFilters.source_id || "";
  byId("claims-status").value = state.claimsFilters.status || "current";
  const reload = () => {
    state.claimsFilters.q = byId("claims-q").value.trim();
    state.claimsFilters.predicate = byId("claims-predicate").value;
    state.claimsFilters.status = byId("claims-status").value;
    state.claimsFilters.source_id = byId("claims-source").value;
    state.claimsPage = 1;
    loadClaims();
  };
  byId("claims-q").addEventListener("keydown", (event) => {
    if (event.key === "Enter") reload();
  });
  predicate.onchange = reload;
  source.onchange = reload;
  byId("claims-status").onchange = reload;
}

async function loadClaims(extraParams = {}) {
  const params = new URLSearchParams({
    q: state.claimsFilters.q || "",
    predicate: state.claimsFilters.predicate || "",
    source_id: state.claimsFilters.source_id || "",
    status: state.claimsFilters.status || "current",
    page: String(state.claimsPage),
    page_size: "50",
    ...extraParams,
  });
  const data = await getJSON(`/api/claims?${params.toString()}`);
  const results = byId("claims-results");
  results.innerHTML = claimsList(data.claims || {});
  attachClaimsRows(results);
  renderClaimsPagination(data.page || 1, Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 50))));
  setPageHeader({
    title: "Claims",
    subtitle: `${formatNumber(data.total || 0)} claims`,
  });
}

function claimsList(claims, options = {}) {
  const rows = Array.isArray(claims) ? claims : [];
  if (!rows.length) {
    return `<div class="empty-state"><i class="ti ti-file-search" aria-hidden="true"></i><div>No claims matched.</div></div>`;
  }
  return `
    <table class="directory-table claims-table">
      <thead>
        <tr>
          <th>Subject</th>
          <th>Predicate</th>
          <th>Object</th>
          <th>Evidence</th>
          <th>Valid from</th>
          ${options.role ? `<th>Role</th>` : ""}
        </tr>
      </thead>
      <tbody>
        ${rows.map((claim) => claimsListRow(claim, options)).join("")}
      </tbody>
    </table>
  `;
}

function claimsListRow(claim, options = {}) {
  const role = options.entityId
    ? claim.subject?.id === options.entityId ? "as subject" : "as object"
    : "";
  return `
    <tr class="directory-table-row claim-row" data-claim-id="${escapeAttr(claim.id || claim.claim_id)}">
      <td>${escapeHtml(claim.subject?.name || "Unknown")}</td>
      <td><span class="claim-predicate">${escapeHtml(claim.predicate || "")}</span></td>
      <td>${escapeHtml(claim.object?.name || claim.object_value || "")}</td>
      <td>${formatNumber(claim.evidence_count || 0)}</td>
      <td>${claim.valid_from ? escapeHtml(formatDate(claim.valid_from)) : ""}</td>
      ${options.role ? `<td><span class="source-badge">${escapeHtml(role)}</span></td>` : ""}
    </tr>
  `;
}

function attachClaimsRows(root) {
  root.querySelectorAll("[data-claim-id]").forEach((row) => {
    row.onclick = () => {
      location.hash = `#claims/${row.dataset.claimId}`;
    };
  });
}

function renderClaimsPagination(page, totalPages) {
  const pag = byId("claims-pagination");
  if (!pag || totalPages <= 1) {
    if (pag) pag.innerHTML = "";
    return;
  }
  pag.innerHTML = `
    <button class="btn-secondary" ${page <= 1 ? "disabled" : ""}>Previous</button>
    <button class="btn-secondary accent" ${page >= totalPages ? "disabled" : ""}>Next</button>
  `;
  const [prev, next] = pag.querySelectorAll("button");
  prev.onclick = () => {
    state.claimsPage = Math.max(1, page - 1);
    loadClaims();
  };
  next.onclick = () => {
    state.claimsPage = page + 1;
    loadClaims();
  };
}

async function renderClaimDetail(claimId) {
  const data = await getJSON(`/api/claims/${claimId}`);
  setPageHeader({
    title: "Claim",
    subtitle: data.statement || "",
    eyebrow: `<a href="#claims">Claims</a> / Claim detail`,
  });
  byId("app").innerHTML = `
    <section class="panel">
      <div class="panel-header">
        <div>
          <div class="panel-title">${escapeHtml(data.statement || "")}</div>
          <div class="muted small">${escapeHtml(data.predicate || "")}</div>
        </div>
      </div>
      <div class="claim-detail-grid">
        ${claimEntityCard("Subject", data.subject_entity || data.subject)}
        ${claimEntityCard("Object", data.object_entity || data.object)}
      </div>
      <div class="panel-header">
        <div class="panel-title">Evidence</div>
        <div class="muted small">${formatNumber(data.evidence_count || 0)} rows</div>
      </div>
      <div class="claim-attribution-panel">
        ${(data.evidence || []).map(claimEvidenceRow).join("") || `<div class="muted small">No evidence rows.</div>`}
      </div>
    </section>
  `;
}

function claimEntityCard(label, entity) {
  return `
    <div class="claim-entity-card">
      <div class="field-label">${escapeHtml(label)}</div>
      <strong>${escapeHtml(entity?.display_name || entity?.canonical_name || entity?.name || "Unknown")}</strong>
      ${entity?.id ? `<a href="#graph/${escapeAttr(entity.id)}">Open entity</a>` : ""}
    </div>
  `;
}

/* ───── Raw Data ───── */

async function renderRawData() {
  setPageHeader({
    title: "Raw data",
    subtitle: "Inspect raw extraction rows, chunks, and cleaned documents.",
  });
  byId("app").innerHTML = `
    <div class="directory-page raw-data-page">
      <section class="directory-filter-bar raw-filter-bar">
        <label class="directory-search input-with-icon">
          <i class="ti ti-search icon" aria-hidden="true"></i>
          <input class="input" id="raw-q" placeholder="Search subject or object" value="${escapeAttr(state.rawDataFilters.q)}" />
        </label>
        <input class="input raw-predicate-input" id="raw-predicate" placeholder="Predicate" value="${escapeAttr(state.rawDataFilters.predicate)}" />
        <button class="btn-secondary" id="raw-search" type="button">Search</button>
      </section>
      <section class="directory-results" id="raw-results">
        <div class="empty-state"><i class="ti ti-loader" aria-hidden="true"></i><div>Loading...</div></div>
      </section>
      <div class="pagination directory-pagination" id="raw-pagination"></div>
    </div>
  `;
  const reload = () => {
    state.rawDataFilters.q = byId("raw-q").value.trim();
    state.rawDataFilters.predicate = byId("raw-predicate").value.trim();
    state.rawDataPage = 1;
    loadRawData();
  };
  byId("raw-search").onclick = reload;
  byId("raw-q").addEventListener("keydown", (event) => {
    if (event.key === "Enter") reload();
  });
  byId("raw-predicate").addEventListener("keydown", (event) => {
    if (event.key === "Enter") reload();
  });
  await loadRawData();
}

async function loadRawData() {
  const target = byId("raw-results");
  const params = new URLSearchParams({
    q: state.rawDataFilters.q || "",
    predicate: state.rawDataFilters.predicate || "",
    page: String(state.rawDataPage),
    page_size: "50",
  });
  try {
    const data = await loadAdminPanelData(`/admin/raw/claim-raw?${params.toString()}`, {
      targetId: "raw-results",
      title: "Sign in to inspect raw data",
    });
    if (!data) return;
    const rows = data.claim_raw || data.results || [];
    if (!rows.length) {
      target.innerHTML = `<div class="empty-state"><i class="ti ti-database-search" aria-hidden="true"></i><div>No raw claims matched.</div></div>`;
    } else {
      target.innerHTML = rawDataTable(rows);
      attachRawDataRows(target);
    }
    renderRawPagination(data.page || 1, Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 50))));
    setPageHeader({
      title: "Raw data",
      subtitle: `${formatNumber(data.total || 0)} raw extraction rows`,
    });
  } catch (error) {
    target.innerHTML = `<div class="muted small">Unable to load raw data: ${escapeHtml(error.message)}</div>`;
  }
}

function rawDataTable(rows) {
  return `
    <table class="directory-table raw-data-table">
      <thead>
        <tr>
          <th>Subject</th>
          <th>Predicate</th>
          <th>Object</th>
          <th>Quote</th>
          <th>Source URL</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((row) => `${rawDataRow(row)}${rawExpandedRow(row)}`).join("")}
      </tbody>
    </table>
  `;
}

function rawDataRow(row) {
  const expanded = Boolean(state.rawDataExpanded[row.id]);
  return `
    <tr class="directory-table-row raw-data-row ${expanded ? "active" : ""}" data-raw-id="${escapeAttr(row.id)}" data-chunk-id="${escapeAttr(row.chunk_id)}">
      <td>${escapeHtml(row.subject_text || "")}</td>
      <td><span class="claim-predicate">${escapeHtml(row.predicate || "")}</span></td>
      <td>${escapeHtml(row.object_text || "")}</td>
      <td>${escapeHtml(row.raw_quote || "")}</td>
      <td>${escapeHtml(row.canonical_url || row.document?.canonical_url || "")}</td>
    </tr>
  `;
}

function rawExpandedRow(row) {
  if (!state.rawDataExpanded[row.id]) return "";
  const chunk = state.rawDataChunks[row.chunk_id];
  const quote = row.raw_quote || "";
  const body = chunk
    ? `
        <div class="raw-expanded-meta">
          <a href="${escapeAttr(chunk.document?.canonical_url || row.canonical_url || "")}" target="_blank" rel="noreferrer">${escapeHtml(chunk.document?.canonical_url || row.canonical_url || "Source URL")}</a>
          <button class="btn-ghost raw-document-button" type="button" data-document-id="${escapeAttr(chunk.document_id || row.document_id)}">View full document</button>
        </div>
        <div class="raw-chunk-text">${highlightRawQuote(chunk.text || "", quote)}</div>
        ${rawChunkClaims(chunk.claim_raw || [])}
      `
    : `<div class="empty-state compact"><i class="ti ti-loader" aria-hidden="true"></i><div>Loading chunk...</div></div>`;
  return `
    <tr class="raw-expanded-row" data-raw-expanded="${escapeAttr(row.id)}">
      <td colspan="5">${body}</td>
    </tr>
  `;
}

function rawChunkClaims(rows) {
  if (!rows.length) return "";
  return `
    <div class="raw-claim-list">
      <div class="muted small">Raw claims from this chunk</div>
      ${rows
        .map(
          (row) =>
            `<div class="raw-claim-item"><strong>${escapeHtml(row.subject_text)}</strong> ${escapeHtml(row.predicate)} ${escapeHtml(row.object_text || "")}</div>`,
        )
        .join("")}
    </div>
  `;
}

function attachRawDataRows(root) {
  root.querySelectorAll("[data-raw-id]").forEach((row) => {
    row.onclick = async () => {
      const rawId = row.dataset.rawId;
      const chunkId = row.dataset.chunkId;
      state.rawDataExpanded[rawId] = !state.rawDataExpanded[rawId];
      const tableRows = Array.from(root.querySelectorAll("[data-raw-id]")).map((item) => ({
        id: item.dataset.rawId,
        chunk_id: item.dataset.chunkId,
        subject_text: item.cells[0]?.textContent || "",
        predicate: item.cells[1]?.textContent || "",
        object_text: item.cells[2]?.textContent || "",
        raw_quote: item.cells[3]?.textContent || "",
        canonical_url: item.cells[4]?.textContent || "",
      }));
      root.innerHTML = rawDataTable(tableRows);
      attachRawDataRows(root);
      if (state.rawDataExpanded[rawId] && !state.rawDataChunks[chunkId]) {
        try {
          state.rawDataChunks[chunkId] = await getJSON(`/admin/raw/chunks/${chunkId}`, {
            redirectOnAuth: false,
          });
          root.innerHTML = rawDataTable(tableRows);
          attachRawDataRows(root);
        } catch (error) {
          toast(normalizeErrorMessage(error), { level: "error" });
        }
      }
    };
  });
  root.querySelectorAll("[data-document-id]").forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation();
      openRawDocumentModal(button.dataset.documentId);
    };
  });
}

function renderRawPagination(page, totalPages) {
  const pag = byId("raw-pagination");
  if (!pag || totalPages <= 1) {
    if (pag) pag.innerHTML = "";
    return;
  }
  pag.innerHTML = `
    <button class="btn-secondary" ${page <= 1 ? "disabled" : ""}>Previous</button>
    <button class="btn-secondary accent" ${page >= totalPages ? "disabled" : ""}>Next</button>
  `;
  const [prev, next] = pag.querySelectorAll("button");
  prev.onclick = () => {
    state.rawDataPage = Math.max(1, page - 1);
    loadRawData();
  };
  next.onclick = () => {
    state.rawDataPage = page + 1;
    loadRawData();
  };
}

async function openRawDocumentModal(documentId) {
  if (!documentId) return;
  openModal(`<div class="empty-state"><i class="ti ti-loader"></i><div>Loading...</div></div>`);
  try {
    const data = await getJSON(`/admin/raw/documents/${documentId}`, { redirectOnAuth: false });
    openModal(`
      <div class="modal-header">
        <div>
          <div class="modal-title">${escapeHtml(data.title || data.canonical_url || "Document")}</div>
          <div class="modal-subtitle">${escapeHtml(data.canonical_url || "")}</div>
        </div>
        <button class="btn-icon-only modal-close" onclick="closeModal()" aria-label="Close">×</button>
      </div>
      <div class="doc-viewer raw-document-viewer">
        <div class="muted small">${formatNumber(data.word_count || 0)} words · ${formatNumber(data.chunk_count || 0)} chunks</div>
        <pre>${rawDocumentWithBoundaries(data)}</pre>
      </div>
    `);
  } catch (error) {
    openModal(`<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load: ${escapeHtml(error.message)}</div></div>`);
  }
}

function rawDocumentWithBoundaries(data) {
  const text = data.cleaned_text || "";
  const chunks = data.chunks || [];
  if (!chunks.length) return escapeHtml(text);
  let offset = 0;
  const parts = [];
  chunks.forEach((chunk) => {
    const length = Number(chunk.char_count || 0);
    const segment = text.slice(offset, offset + length);
    parts.push(`\n\n--- chunk ${chunk.ordinal} · ${chunk.chunk_id} ---\n`);
    parts.push(segment || "");
    offset += length;
  });
  if (offset < text.length) {
    parts.push("\n\n--- remaining text ---\n");
    parts.push(text.slice(offset));
  }
  return escapeHtml(parts.join("").trim());
}

function highlightRawQuote(text, quote) {
  if (!quote) return escapeHtml(text);
  const lowerText = text.toLowerCase();
  const lowerQuote = quote.toLowerCase();
  const index = lowerText.indexOf(lowerQuote);
  if (index < 0) return escapeHtml(text);
  return `${escapeHtml(text.slice(0, index))}<mark>${escapeHtml(text.slice(index, index + quote.length))}</mark>${escapeHtml(text.slice(index + quote.length))}`;
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
          : `<div class="ask-empty-shell">
              <section class="ask-empty">
                <h2>What do you want to know?</h2>
                <div class="ask-examples">
                  ${ASK_EXAMPLES.map((example) => `<button class="chip ask-example" type="button">${escapeHtml(example)}</button>`).join("")}
                </div>
              </section>
              ${renderAskComposer(false)}
            </div>`
      }
      ${hasSession ? renderAskComposer(true) : ""}
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
      <textarea class="input ask-input" id="ask-input" rows="1" placeholder="Ask about people, projects, or organizations"></textarea>
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
  document.querySelectorAll(".ask-example").forEach((button) => {
    button.onclick = () => {
      input.value = button.textContent.trim();
      syncComposer();
      input.focus();
    };
  });
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
      <summary class="ask-source-pill"><i class="ti ti-chevron-right details-chevron" aria-hidden="true"></i>Sources (${count} ${count === 1 ? "source" : "sources"})</summary>
      <div class="ask-source-list">
        ${
          count
            ? citations
                .map(
                  (c, i) => `
                    <article class="ask-citation-card" role="button" tabindex="0" data-ask-id="${escapeAttr(item.id)}" data-citation-index="${i}">
                      <span class="source-badge">${escapeHtml(c.source_id || c.claim_id || "source")}</span>
                      <strong>${escapeHtml(c.source_name || c.title || c.source_title || `Source ${i + 1}`)}</strong>
                      ${c.claim ? `<div class="muted small">${escapeHtml(claimDisplayLine(c.claim))}</div>` : ""}
                      ${claimAttribution(c)}
                      <span>${escapeHtml(c.quote || "No snippet returned for this citation.")}</span>
                    </article>`,
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
  document.querySelectorAll(".ask-citation-card").forEach((card) => {
    const open = (event) => {
      if (event?.target?.closest?.(".claim-attribution, a")) return;
      const item = state.askSession.find((candidate) => candidate.id === card.dataset.askId);
      const citation = item?.citations?.[Number(card.dataset.citationIndex)] || {};
      openCitationDrawer(citation);
    };
    card.onclick = open;
    card.onkeydown = (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      open(event);
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
        <div class="side-drawer-title">${escapeHtml(citation.source_name || citation.title || citation.source_title || "Source")}</div>
        <div class="side-drawer-subtitle">${escapeHtml(citation.source_id || citation.claim_id || "Citation")}</div>
      </div>
      <button class="btn-icon-only modal-close" type="button" onclick="closeSideDrawer()" aria-label="Close">×</button>
    </div>
    <div class="side-drawer-body">
      <div class="field-label">Snippet</div>
      <blockquote>${escapeHtml(citation.quote || "No snippet returned for this citation.")}</blockquote>
      <div class="field-label">Source reference</div>
      ${claimAttribution(citation) || `<div class="side-drawer-meta">${escapeHtml(citation.source_id || "No source id returned.")}</div>`}
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
            <input class="input" id="graph-search" placeholder="Search for a person, project, or organization" value="${escapeAttr(state.graphSearch)}" autocomplete="off" />
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
  // TODO: add the temporal claim/document timeline view here once supersession lands.
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
    ${renderEntityAttributeClaims(data.attributes || {})}
    <section class="panel entity-claims-panel">
      <div class="panel-header">
        <div class="panel-title">Claims</div>
      </div>
      <div id="entity-claims-list">
        <div class="muted small">Loading claims...</div>
      </div>
    </section>
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
  loadEntityClaims(data.identity.entity_id);
}

async function loadEntityClaims(entityId) {
  const root = byId("entity-claims-list");
  if (!root) return;
  try {
    const [subject, object] = await Promise.all([
      getJSON(`/api/claims?subject_entity_id=${encodeURIComponent(entityId)}&page_size=100`),
      getJSON(`/api/claims?object_entity_id=${encodeURIComponent(entityId)}&page_size=100`),
    ]);
    const seen = new Set();
    const claims = [...(subject.claims || []), ...(object.claims || [])].filter((claim) => {
      const id = claim.id || claim.claim_id;
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    });
    root.innerHTML = claimsList(claims, { entityId, role: true });
    attachClaimsRows(root);
  } catch (error) {
    root.innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load claims: ${escapeHtml(error.message)}</div></div>`;
  }
}

function renderEntityAttributeClaims(attributes) {
  const rows = Object.entries(attributes || {}).flatMap(([predicate, claims]) =>
    (claims || []).map((claim) => ({ predicate, claim })),
  );
  if (!rows.length) return "";
  return `
    <section class="panel entity-claims-panel">
      <div class="panel-header">
        <div class="panel-title">Attributes</div>
      </div>
      <div class="entity-claim-list">
        ${rows
          .map(
            ({ predicate, claim }) => `
              <div class="entity-claim-row">
                <div>
                  <span class="claim-predicate">${escapeHtml(predicate.replace(/_/g, " "))}</span>
                  <strong>${escapeHtml(claim.object_value || "")}</strong>
                </div>
                ${claimAttribution(claim)}
              </div>`,
          )
          .join("")}
      </div>
    </section>
  `;
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
      evidence_count: conn.evidence_count,
      is_resolved: conn.is_resolved,
      claims: conn.claims || [],
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
    .attr("stroke-width", 2)
    .attr("opacity", 0.7)
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
  const claims = edge.claims || [];
  panel.innerHTML = `
    <div class="claim-statement">
      <span class="entity">${escapeHtml(subject)}</span>
      <span class="predicate">${escapeHtml((edge.predicates || []).join(", "))}</span>
      <span class="entity">${escapeHtml(object)}</span>
    </div>
    <div class="claim-meta">
      <span><strong>${edge.evidence_count || 0}</strong> evidence rows</span>
    </div>
    ${
      claims.length
        ? `<div class="edge-claim-list">
            ${claims
              .map(
                (claim) => `
                  <div class="edge-claim-row">
                    <div>${escapeHtml(claim.statement || "")}</div>
                    ${claimAttribution(claim)}
                  </div>`,
              )
              .join("")}
          </div>`
        : ""
    }
    <div class="muted small">Click the names in this graph to drill into each entity.</div>
  `;
}

/* ───── Sources ───── */

async function renderSources(parts) {
  if (parts[0] === "archive") {
    if (!isAdmin()) {
      location.hash = "#sources";
      return;
    }
    return renderSourcesArchive();
  }
  if (parts[0]) {
    return renderSourceDetail(parts[0], parts[1]);
  }
  const adminActions = isAdmin()
    ? `<div class="source-actions">
         <button class="btn-primary" id="add-source"><i class="ti ti-plus"></i> Add source</button>
       </div>`
    : "";
  renderSourcesFrame(adminActions);
  if (isAdmin()) {
    byId("add-source").onclick = openAddSourceModal;
  }
  await Promise.all([loadSourcesStats(), loadSourcesList()]);
  if (isAdmin()) await loadAdminConflicts();
}

function renderSourcesFrame(adminActions) {
  setPageHeader({ title: "Sources", subtitle: "Loading…", actions: adminActions });
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="stats-grid" id="sources-stats">${statCards(ZERO_STATS)}</div>
    <div class="sources-list" id="sources-list" data-source-list="active">
      ${sourceSkeletonRow()}
    </div>
  `;
  setupStatInfoButtons();
}

async function loadSourcesStats() {
  const statsGrid = byId("sources-stats");
  try {
    const stats = await getJSON("/api/stats");
    state.stats = stats;
    if (byId("sources-list")?.querySelector(".sources-error")) return;
    statsGrid.innerHTML = statCards(stats);
    setupStatInfoButtons();
    renderShell();
  } catch (e) {
    statsGrid.innerHTML = statCards(ZERO_STATS);
    setupStatInfoButtons();
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
      const stat = STAT_CARDS.find((item) => item.key === button.dataset.term);
      if (!stat) return;
      const tooltip = document.createElement("div");
      tooltip.className = "menu stat-tooltip";
      tooltip.innerHTML = `
        <div class="stat-tooltip-definition">${escapeHtml(stat.definition)}</div>
        <div class="stat-tooltip-example">${escapeHtml(stat.example)}</div>
      `;
      card.appendChild(tooltip);
      setTimeout(() => document.addEventListener("click", closeStatTooltips, { once: true }), 0);
    };
  });
}

function closeStatTooltips() {
  document.querySelectorAll(".stat-tooltip").forEach((tooltip) => tooltip.remove());
}

async function loadSourcesList() {
  const list = byId("sources-list");
  if (list) {
    list.innerHTML = sourceSkeletonRow();
  }
  try {
    const [data, archivedData] = await Promise.all([
      getJSON("/api/sources"),
      isAdmin()
        ? loadAdminPanelData("/api/sources/archived", {
            targetId: "sources-list",
            title: "Sign in to view archived sources",
          })
        : Promise.resolve({ sources: [] }),
    ]);
    if (!archivedData) return;
    const sources = data.sources || [];
    const archivedSources = archivedData.sources || [];
    state.sourcesCache = sources;
    state.sourcesError = null;
    const active = sources.filter((s) => s.status === "active");
    const archivedCount = archivedSources.length;
    const pageSubtitle = byId("page-subtitle");
    if (pageSubtitle) {
      pageSubtitle.textContent = isAdmin()
        ? `${formatNumber(active.length)} active · ${formatNumber(archivedCount)} archived`
        : `${formatNumber(active.length)} active`;
    }
    if (!sources.length && (!isAdmin() || !archivedSources.length)) {
      list.innerHTML = sourceEmptyState();
      const emptyAddSource = byId("empty-add-source");
      if (emptyAddSource) emptyAddSource.onclick = openAddSourceModal;
      return;
    }
    list.innerHTML = `
      ${
        sources.length
          ? sources.map((source) => sourceRow(source)).join("")
          : `<div class="empty-state sources-empty compact"><i class="ti ti-database-off" aria-hidden="true"></i><div>No active sources.</div></div>`
      }
      ${
        isAdmin()
          ? `<details class="archived-sources" open>
              <summary><i class="ti ti-chevron-right details-chevron" aria-hidden="true"></i><span id="conflict-summary-text">Conflicts (0 unresolved)</span></summary>
              <div class="archived-sources-list" id="conflicts-body">
                <div class="muted small">Loading...</div>
              </div>
            </details>`
          : ""
      }
      ${
        isAdmin()
          ? `<details class="archived-sources">
              <summary><i class="ti ti-chevron-right details-chevron" aria-hidden="true"></i><span>Archived (${formatNumber(archivedCount)})</span></summary>
              <div class="archived-sources-list" data-source-list="archived">
                ${
                  archivedSources.length
                    ? archivedSources.map((source) => sourceRow(source, { archived: true })).join("")
                    : `<div class="empty-state sources-empty compact"><i class="ti ti-archive-off" aria-hidden="true"></i><div>No archived sources.</div></div>`
                }
              </div>
            </details>`
          : ""
      }
    `;
    setupSourceRows(list, sources, { archived: false });
    if (isAdmin()) setupSourceRows(list, archivedSources, { archived: true });
    ensureActiveRunStreams(sources);
  } catch (e) {
    state.sourcesCache = null;
    state.sourcesError = e;
    const pageSubtitle = byId("page-subtitle");
    if (pageSubtitle) pageSubtitle.textContent = "Unable to load";
    const statsGrid = byId("sources-stats");
    if (statsGrid) {
      statsGrid.innerHTML = statCards(ZERO_STATS);
      setupStatInfoButtons();
    }
    if (list) {
      list.innerHTML = sourceErrorState(e);
      const retry = byId("sources-retry");
      if (retry) retry.onclick = loadSourcesList;
    }
  }
}

function setupSourceRows(root, sources, { archived }) {
  sources.forEach((source) => {
    const row = root.querySelector(`[data-source-id="${CSS.escape(source.id)}"]`);
    if (!row) return;
    if (!archived) {
      row.onclick = (e) => {
        if (e.target.closest("button")) return;
        location.hash = `#sources/${source.id}`;
      };
    }
    if (!isAdmin()) {
      if (state.runProgress[source.id]) updateRunProgressDom(source.id);
      return;
    }
    const crawl = row.querySelector("[data-action=crawl]");
    const parse = row.querySelector("[data-action=parse]");
    const unarchive = row.querySelector("[data-action=unarchive]");
    const destroy = row.querySelector("[data-action=delete]");
    const pauseButtons = row.querySelectorAll("[data-action=pause]");
    const resumeButtons = row.querySelectorAll("[data-action=resume]");
    const pendingParse = row.querySelector("[data-action=pending-parse]");
    const dueRecrawl = row.querySelector("[data-action=due-recrawl]");
    const menuBtn = row.querySelector("[data-action=menu]");
    if (crawl) crawl.onclick = (e) => { e.stopPropagation(); runSourceAction(source.id, "crawl"); };
    if (parse) parse.onclick = (e) => { e.stopPropagation(); runSourceAction(source.id, "parse"); };
    if (pendingParse) pendingParse.onclick = (e) => { e.stopPropagation(); runSourceAction(source.id, "parse"); };
    if (dueRecrawl) dueRecrawl.onclick = (e) => { e.stopPropagation(); runSourceAction(source.id, "crawl"); };
    if (unarchive) unarchive.onclick = (e) => {
      e.stopPropagation();
      updateSourceStatus(source.id, "active");
    };
    if (destroy) destroy.onclick = (e) => {
      e.stopPropagation();
      const listKind = row.closest("[data-source-list]")?.dataset.sourceList || (archived ? "archived" : "active");
      confirmDeleteSource(source, listKind);
    };
    pauseButtons.forEach((pause) => {
      pause.onclick = async (e) => {
        e.stopPropagation();
        await pauseSourceRun(source, pause.dataset.runKind);
      };
    });
    resumeButtons.forEach((resume) => {
      resume.onclick = async (e) => {
        e.stopPropagation();
        await runSourceAction(source.id, resume.dataset.runKind);
      };
    });
    if (menuBtn) menuBtn.onclick = (e) => {
      e.stopPropagation();
      toggleMenu(row, source.id);
    };
    if (isAdmin()) {
      Object.values(activeRuns(source)).forEach((run) => {
        trackRun(source.id, sourceDisplayName(source), run.action || "crawl", run.id, {
          percent: Number(run.stats?.percent ?? 0),
          fetched: Number(run.stats?.fetched ?? source.pages_fetched_total),
          known: Number(run.stats?.known ?? source.urls_known_total),
          done: Number(run.stats?.item_done),
          total: Number(run.stats?.item_total),
        });
      });
    }
    if (state.runProgress[source.id]) updateRunProgressDom(source.id);
  });
}

function sourceSkeletonRow() {
  return `
    <div class="source-row source-skeleton" aria-label="Loading sources">
      <div class="source-row-top">
        <div class="source-row-main">
          <span class="skeleton-box source-row-icon" aria-hidden="true"></span>
          <div class="source-row-copy">
            <span class="skeleton-line skeleton-title" aria-hidden="true"></span>
            <span class="skeleton-line skeleton-subtitle" aria-hidden="true"></span>
          </div>
        </div>
        <div class="source-row-stats">
          <span class="skeleton-line skeleton-stat" aria-hidden="true"></span>
          <span class="skeleton-line skeleton-stat" aria-hidden="true"></span>
        </div>
        <div class="source-row-actions" aria-hidden="true"></div>
      </div>
    </div>
  `;
}

function sourceEmptyState() {
  const body = isAdmin()
    ? "Add a source to start ingesting documents and building your graph."
    : "No active sources are available yet.";
  return `
    <div class="empty-state sources-empty">
      <i class="ti ti-database-off" aria-hidden="true"></i>
      <h2>No sources yet</h2>
      <p>${escapeHtml(body)}</p>
      ${
        isAdmin()
          ? `<button class="btn-primary" id="empty-add-source" type="button"><i class="ti ti-plus" aria-hidden="true"></i> Add source</button>`
          : ""
      }
    </div>
  `;
}

function sourceErrorState(error) {
  const message = normalizeErrorMessage(error);
  const isStack = message.includes("\n") || message.includes("Traceback");
  return `
    <div class="empty-state sources-error">
      <i class="ti ti-alert-circle" aria-hidden="true"></i>
      <h2>Couldn't load sources</h2>
      <p class="sources-error-message ${isStack ? "is-stack" : ""}">${escapeHtml(message)}</p>
      <button class="btn-secondary" id="sources-retry" type="button"><i class="ti ti-refresh" aria-hidden="true"></i> Retry</button>
      ${
        isAdmin()
          ? `<div class="sources-log-note">
               <div class="muted small">Check Cloud Run logs:</div>
               <code>gcloud run services logs read pinegraf --region=us-east4 --limit=50</code>
             </div>`
          : ""
      }
    </div>
  `;
}

function normalizeErrorMessage(error) {
  const raw = String(error?.message || error || "Unknown error");
  try {
    const parsed = JSON.parse(raw);
    return parsed.detail || parsed.message || raw;
  } catch (_) {
    return raw;
  }
}

function isAuthError(error) {
  const status = Number(error?.status || 0);
  if (status === 401 || status === 403) return true;
  return /\b(401|403)\b/.test(String(error?.message || error || ""));
}

async function renderSourcesArchive() {
  if (!isAdmin()) {
    location.hash = "#sources";
    return;
  }
  setPageHeader({
    title: "Archived sources",
    subtitle: "Hidden from the main Sources list",
    actions: `<a class="btn-ghost" href="#sources">&larr; Back to Sources</a>`,
  });
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="sources-list" id="archived-sources-list" data-source-list="archived">
      <div class="empty-state"><i class="ti ti-loader" aria-hidden="true"></i><div>Loading…</div></div>
    </div>
  `;
  await loadArchivedSourcesList();
}

async function loadArchivedSourcesList() {
  const list = byId("archived-sources-list");
  try {
    const data = await loadAdminPanelData("/api/sources/archived", {
      targetId: "archived-sources-list",
      title: "Sign in to view archived sources",
    });
    if (!data) return;
    const sources = data.sources || [];
    if (!sources.length) {
      list.innerHTML = `<div class="empty-state sources-empty">
        <i class="ti ti-archive-off" aria-hidden="true"></i>
        <h2>No archived sources</h2>
        <p>Archived sources will appear here.</p>
      </div>`;
      return;
    }
    list.innerHTML = sources.map((source) => sourceArchiveRow(source)).join("");
    list.querySelectorAll(".source-row").forEach((row) => {
      const id = row.dataset.sourceId;
      const restore = row.querySelector("[data-action=restore]");
      const destroy = row.querySelector("[data-action=delete]");
      if (restore) {
        restore.onclick = () => restoreArchivedSource(id);
      }
      if (destroy) {
        const source = sources.find((item) => item.id === id);
        destroy.onclick = () => deleteArchivedSource(source);
      }
    });
  } catch (e) {
    list.innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load archived sources: ${escapeHtml(e.message)}</div></div>`;
  }
}

function sourceArchiveRow(source) {
  const kindLabel = sourceKindLabel(source);
  const kindIcon = sourceKindIcon(source);
  return `
    <article class="source-row archived" data-source-id="${escapeAttr(source.id)}">
      <div class="source-row-top">
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
          <span><strong>${formatNumber(sourcePagesFetched(source))}</strong> pages fetched</span>
          <span><strong>${formatNumber(source.coverage.documents_parsed)}</strong> docs parsed</span>
          <span><strong>${formatNumber(source.coverage.claims)}</strong> claims</span>
          <span class="muted">${source.last_run_at ? `last run ${timeAgo(source.last_run_at)}` : "never run"}</span>
          <span class="status-pill archived">Archived</span>
        </div>
        <div class="source-row-actions">
          <button class="btn-secondary" data-action="restore" type="button">Restore</button>
          <button class="btn-danger" data-action="delete" type="button">Delete permanently</button>
        </div>
      </div>
    </article>
  `;
}

async function restoreArchivedSource(sourceId) {
  await patchSource(sourceId, { status: "active" });
  loadArchivedSourcesList();
}

async function deleteArchivedSource(source) {
  confirmDeleteSource(source, "archived");
}

function sourceRow(source, options = {}) {
  const archived = Boolean(options.archived);
  const kindLabel = sourceKindLabel(source);
  const kindIcon = sourceKindIcon(source);
  const progress = state.runProgress[source.id];
  const actions = sourceRowActions(source, { archived });
  return `
    <article class="source-row ${archived ? "archived" : ""}" data-source-id="${escapeAttr(source.id)}">
      <div class="source-row-top">
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
          <span><strong>${formatNumber(sourcePagesFetched(source))}</strong> pages fetched</span>
          <span><strong>${formatNumber(source.coverage.documents_parsed)}</strong> docs parsed</span>
          <span><strong>${formatNumber(source.coverage.claims)}</strong> claims</span>
          ${sourceStateMarkup(source, progress)}
          <span class="status-pill ${source.status}">${capitalize(source.status)}</span>
        </div>
        ${
          isAdmin()
            ? `<div class="source-row-actions">${actions}</div>`
            : ""
        }
      </div>
    </article>
  `;
}

function sourceRowActions(source, { archived }) {
  if (archived) {
    return `<button class="btn-secondary" data-action="unarchive" type="button">Unarchive</button>
      <button class="btn-danger" data-action="delete" type="button">Delete permanently</button>`;
  }
  const runs = activeRuns(source);
  const paused = pausedRuns(source);
  const menuButton = isAdmin()
    ? `<button class="btn-icon-only" data-action="menu" aria-label="More"><i class="ti ti-dots"></i></button>`
    : "";
  return `${sourceActionButton("crawl", runs.crawl ? "pause" : paused.crawl ? "resume" : "start")}
    ${sourceActionButton("parse", runs.parse ? "pause" : paused.parse ? "resume" : "start")}
    ${menuButton}`;
}

function sourceActionButton(kind, state = "start") {
  if (state === "pause") {
    return `<button class="btn-danger-outline" data-action="pause" data-run-kind="${escapeAttr(kind)}" type="button"><i class="ti ti-player-pause"></i> Pause</button>`;
  }
  if (state === "resume") {
    return `<button class="btn-source" data-action="resume" data-run-kind="${escapeAttr(kind)}" type="button"><i class="ti ti-player-play"></i> Resume</button>`;
  }
  if (kind === "parse") {
    return `<button class="btn-source" data-action="parse" title="Parse fetched documents that have not been parsed yet"><i class="ti ti-cpu"></i> Parse</button>`;
  }
  return `<button class="btn-source" data-action="crawl" title="Fetch all documents from this source"><i class="ti ti-download"></i> Crawl</button>`;
}

function sourcePagesFetched(source) {
  return Math.max(
    Number(source.pages_fetched_total || 0),
    Number(source.coverage?.pages_fetched || 0),
  );
}

function activeRuns(source) {
  if (source?.active_runs && typeof source.active_runs === "object") return source.active_runs;
  if (!source?.active_run_id) return {};
  const action = source.active_run_kind === "parse" ? "parse" : "crawl";
  return {
    [action]: {
      id: source.active_run_id,
      action,
      status: "running",
    },
  };
}

function pausedRuns(source) {
  if (source?.paused_runs && typeof source.paused_runs === "object") return source.paused_runs;
  return {};
}

function sourceRunStatusText(source, progress) {
  const runs = activeRuns(source);
  const paused = pausedRuns(source);
  const parts = [];
  if (runs.crawl) {
    parts.push(runProgressText(progressForRun(source, "crawl", runs.crawl)));
  }
  if (runs.parse) {
    parts.push(runProgressText(progressForRun(source, "parse", runs.parse)));
  }
  if (parts.length) return parts.join(" · ");
  if (paused.crawl || paused.parse) {
    return ["crawl", "parse"]
      .filter((action) => paused[action])
      .map((action) => `${capitalize(action)} paused`)
      .join(" · ");
  }
  if (!source.active_run_id) {
    return source.last_run_at ? `last run ${timeAgo(source.last_run_at)}` : "never run";
  }
  return runProgressText(progress || { action: source.active_run_kind || "crawl" });
}

function sourceStateMarkup(source, progress) {
  const runs = activeRuns(source);
  if (runs.crawl || runs.parse || source.active_run_id) {
    return `<span class="muted source-last-run">${escapeHtml(sourceRunStatusText(source, progress))}</span>`;
  }
  const pending = Number(source.pending_parse_count ?? source.coverage?.pending_parse_count ?? 0);
  if (pending > 0) {
    return `<button class="source-state-pill info" data-action="pending-parse" type="button"><i class="ti ti-info-circle" aria-hidden="true"></i>${formatNumber(pending)} documents haven't been parsed</button>`;
  }
  if (sourceDueForRecrawl(source)) {
    return `<button class="source-state-pill info" data-action="due-recrawl" type="button"><i class="ti ti-refresh-alert" aria-hidden="true"></i>Due for re-crawl</button>`;
  }
  if (sourceUpToDate(source)) {
    return `<span class="source-state-indicator"><i class="ti ti-circle-check" aria-hidden="true"></i>Up to date</span>`;
  }
  return `<span class="muted source-last-run">${source.last_run_at ? `last run ${timeAgo(source.last_run_at)}` : "never run"}</span>`;
}

function sourceDueForRecrawl(source) {
  const last = source.last_full_recrawl_at ? new Date(source.last_full_recrawl_at) : null;
  if (!last || Number.isNaN(last.getTime())) return true;
  const intervalDays = Math.max(1, Number(source.recrawl_interval_days || 7));
  return Date.now() - last.getTime() >= intervalDays * 24 * 60 * 60 * 1000;
}

function sourceUpToDate(source) {
  const runs = activeRuns(source);
  return !runs.crawl && !runs.parse && Number(source.pending_parse_count || 0) === 0 && !sourceDueForRecrawl(source);
}

function progressForRun(source, action, run) {
  const sourceId = source.id;
  const stats = run?.stats || {};
  const fetched = Number(stats.fetched ?? sourcePagesFetched(source));
  const known = Number(stats.known ?? Math.max(Number(source.urls_known_total || 0), fetched));
  return state.runProgress[sourceId]?.[action] || {
    action,
    runId: run?.id,
    percent: Number(stats.percent ?? (known ? (100 * fetched) / known : 0)),
    fetched,
    known,
    done: Number(stats.item_done),
    total: Number(stats.item_total),
    itemsParsed: Number(stats.items_parsed),
    totalToParse: Number(stats.total_to_parse),
  };
}

function runProgressText(progress) {
  const action = progress?.action === "parse" ? "parse" : "crawl";
  const label = action === "parse" ? "Parsing" : "Crawling";
  const count = runProgressCount(progress);
  const percent =
    action === "parse" && Number.isFinite(progress?.itemsParsed) && Number(progress?.totalToParse) > 0
      ? ((100 * Number(progress.itemsParsed)) / Number(progress.totalToParse)).toFixed(1)
      : Number(progress?.percent || 0).toFixed(1);
  return count ? `${label} ${count} (${percent}%)` : `${label} (${percent}%)`;
}

function runProgressCount(progress) {
  const throttled = throttledDelay(progress);
  if (throttled > 2000) return `Throttled (${formatNumber(throttled)}ms)`;
  if (progress?.action === "parse") {
    const done = Number.isFinite(progress.itemsParsed) ? progress.itemsParsed : progress.done;
    const total = Number.isFinite(progress.totalToParse) ? progress.totalToParse : progress.total;
    if (Number.isFinite(done) && Number.isFinite(total)) {
      return `${formatNumber(done)} / ${formatNumber(total)} docs`;
    }
  }
  if (!Number.isFinite(progress.fetched) || !Number.isFinite(progress.known)) return "";
  return `${formatNumber(progress.fetched)} / ${formatNumber(progress.known)}`;
}

function throttledDelay(progress) {
  const pacing = progress?.host_pacing || {};
  return Math.max(
    0,
    ...Object.values(pacing).map((entry) => Number(entry?.delay_ms || 0)),
  );
}

function sourceMetaLine(source) {
  return `${sourceKindLabel(source)} · ${source.identifier}`;
}

function sourceDisplayName(source) {
  return source?.display_name || source?.identifier || "This source";
}

function sourceKindLabel(source) {
  if (source.kind === "domain") return "Website";
  if (source.kind === "file") return "File";
  return source.kind || "Source";
}

function sourceRunKindLabel(kind) {
  if (kind === "sitemap") return "Website";
  if (kind === "seed") return "File upload";
  if (kind === "adhoc") return "Manual URLs";
  if (kind === "parse") return "Parse";
  return capitalize(kind || "");
}

function sourceRunStatusLabel(status) {
  if (status === "stopped") return "paused";
  return status || "";
}

function sourceKindIcon(source) {
  if (source.kind === "domain") return "ti-world";
  if (source.kind === "file") return "ti-file";
  return source.icon_hint || "ti-database";
}

function toggleMenu(container, sourceId) {
  if (!isAdmin()) return;
  const existing = container.querySelector(".menu");
  if (existing) {
    existing.remove();
    return;
  }
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  const source = (state.sourcesCache || []).find((s) => s.id === sourceId);
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.innerHTML = `
    <button class="menu-item" data-act="rename"><i class="ti ti-pencil"></i> Rename</button>
    ${source && source.kind === "file" ? `<button class="menu-item" data-act="download"><i class="ti ti-download"></i> Download original</button>` : ""}
    <button class="menu-item" data-act="archive"><i class="ti ti-archive"></i> Archive</button>
    <button class="menu-item danger" data-act="delete"><i class="ti ti-trash"></i> Delete permanently</button>
  `;
  container.querySelector(".source-row-actions").appendChild(menu);
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
  if (!isAdmin()) return;
  const source = (state.sourcesCache || []).find((s) => s.id === sourceId);
  if (!source) return;
  if (action === "rename") {
    const name = prompt("New display name", source.display_name || source.identifier);
    if (!name) return;
    await patchSource(sourceId, { display_name: name });
    loadSourcesList();
  } else if (action === "archive") {
    try {
      await patchSource(sourceId, { status: "archived" });
      loadSourcesList();
    } catch (error) {
      toast(normalizeErrorMessage(error), { level: "error" });
    }
  } else if (action === "download") {
    window.location.href = `/api/sources/${sourceId}/download`;
  } else if (action === "delete") {
    confirmDeleteSource(source, "active");
  }
}

function confirmDeleteSource(source, listKind = "active") {
  if (!source || !isAdmin()) return;
  openConfirmModal({
    title: "Delete permanently",
    body: `Delete ${sourceDisplayName(source)} and all its data? This cannot be undone.`,
    danger: true,
    confirmLabel: "Delete permanently",
    onConfirm: async () => {
      await expectOk(fetch(`/admin/sources/${source.id}`, { method: "DELETE" }));
      await loadSourcesStats();
      if (listKind === "archived" && byId("archived-sources-list")) {
        await loadArchivedSourcesList();
      } else if (byId("sources-list")) {
        await loadSourcesList();
      } else {
        await renderRoute();
      }
    },
  });
}

async function patchSource(id, body) {
  if (!isAdmin()) return;
  await expectOk(fetch(`/admin/sources/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }));
}

async function updateSourceStatus(id, status) {
  try {
    await patchSource(id, { status });
    await loadSourcesList();
  } catch (error) {
    toast(normalizeErrorMessage(error), { level: "error" });
  }
}

async function runSourceAction(sourceId, action) {
  if (!isAdmin()) return;
  const source = (state.sourcesCache || []).find((item) => item.id === sourceId);
  let keepDisabled = false;
  setSourceActionButtonsDisabled(sourceId, true);
  try {
    const res = await fetch(`/admin/sources/${sourceId}/${action}`, {
      method: "POST",
      ...(action === "parse"
        ? { headers: { "content-type": "application/json" }, body: JSON.stringify({ scope: "unparsed" }) }
        : {}),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 409 && data.error === "already_running") {
      keepDisabled = true;
      toast(`${sourceRunKindLabel(action)} is already running`, { level: "warning" });
      return;
    }
    if (!res.ok) {
      throw new Error(data.detail || res.statusText);
    }
    trackRun(sourceId, sourceDisplayName(source), action, data.run_id, {
      fetched: action === "crawl" ? sourcePagesFetched(source || {}) : undefined,
      known:
        action === "crawl"
          ? Math.max(Number(source?.urls_known_total || 0), sourcePagesFetched(source || {}))
          : undefined,
    });
    await loadSourcesList();
    toast(`${sourceRunKindLabel(action)} queued`, { level: "success" });
  } catch (error) {
    toast(normalizeErrorMessage(error), { level: "error" });
  }
  finally {
    if (!keepDisabled) setSourceActionButtonsDisabled(sourceId, false);
  }
}

async function pauseSourceRun(source, kind) {
  const run = activeRuns(source)[kind] || (source.active_run_id ? { id: source.active_run_id } : null);
  if (!run?.id || !isAdmin()) return;
  setSourceActionButtonsDisabled(source.id, true);
  try {
    await expectOk(fetch(`/admin/runs/${run.id}/stop`, { method: "POST" }));
    const stream = state.runStreams[run.id];
    if (stream) {
      stream.close();
      delete state.runStreams[run.id];
    }
    if (state.runProgress[source.id]) {
      delete state.runProgress[source.id][kind];
      if (!Object.keys(state.runProgress[source.id]).length) delete state.runProgress[source.id];
    }
    await Promise.all([loadStats(), loadSourcesStats(), loadSourcesList()]);
    toast(`${sourceRunKindLabel(kind || source.active_run_kind || "run")} paused`, {
      level: "success",
    });
  } catch (error) {
    toast(normalizeErrorMessage(error), { level: "error" });
    setSourceActionButtonsDisabled(source.id, false);
  }
}

function setSourceActionButtonsDisabled(sourceId, disabled) {
  sourceActionButtons(sourceId).forEach((button) => {
    button.disabled = disabled;
  });
}

function sourceActionButtons(sourceId) {
  return document.querySelectorAll(
      `[data-source-id="${CSS.escape(sourceId)}"] [data-action=crawl], ` +
      `[data-source-id="${CSS.escape(sourceId)}"] [data-action=parse], ` +
      `[data-source-id="${CSS.escape(sourceId)}"] [data-action=pause], ` +
      `[data-source-id="${CSS.escape(sourceId)}"] [data-action=resume]`,
  );
}

function trackRun(sourceId, sourceName, action, runId, initial = {}) {
  if (!runId) return;
  const sourceProgress = normalizeSourceProgress(sourceId);
  sourceProgress[action] = {
    ...sourceProgress[action],
    runId,
    sourceId,
    sourceName,
    action,
    percent: sourceProgress[action]?.percent ?? initial.percent ?? 0,
    fetched: sourceProgress[action]?.fetched ?? initial.fetched,
    known: sourceProgress[action]?.known ?? initial.known,
    done: sourceProgress[action]?.done ?? initial.done,
    total: sourceProgress[action]?.total ?? initial.total,
    itemsParsed: sourceProgress[action]?.itemsParsed ?? initial.itemsParsed,
    totalToParse: sourceProgress[action]?.totalToParse ?? initial.totalToParse,
  };
  state.runProgress[sourceId] = sourceProgress;
  persistRunProgress();
  updateRunProgressDom(sourceId);
  if (state.runStreams[runId]) return;
  const stream = new EventSource(`/admin/runs/${runId}/stream`);
  state.runStreams[runId] = stream;
  stream.onmessage = (event) => {
    const data = JSON.parse(event.data);
    const current = normalizeSourceProgress(sourceId);
    const progress = current[action] || { runId, sourceId, sourceName, action };
    const progressData = data.data || {};
    progress.percent = Number(data.percent || 0);
    progress.fetched = Number(progressData.fetched);
    progress.known = Number(progressData.known);
    progress.done = Number(progressData.item_done);
    progress.total = Number(progressData.item_total);
    progress.itemsParsed = Number(progressData.items_parsed);
    progress.totalToParse = Number(progressData.total_to_parse);
    progress.host_pacing = progressData.host_pacing || progress.host_pacing || {};
    progress.status = data.status;
    current[action] = progress;
    state.runProgress[sourceId] = current;
    persistRunProgress();
    updateRunProgressDom(sourceId);
    if (
      data.status === "complete" ||
      data.status === "failed" ||
      data.status === "stopped" ||
      data.status === "superseded"
    ) {
      stream.close();
      delete state.runStreams[runId];
      delete current[action];
      if (Object.keys(current).length) state.runProgress[sourceId] = current;
      else delete state.runProgress[sourceId];
      persistRunProgress();
      loadStats();
      loadSourcesList();
    }
  };
  stream.onerror = () => {
    updateRunProgressDom(sourceId);
  };
}

function ensureActiveRunStreams(sources) {
  if (!isAdmin()) return;
  sources.forEach((source) => {
    Object.values(activeRuns(source)).forEach((run) => {
      trackRun(source.id, sourceDisplayName(source), run.action || "crawl", run.id, {
        percent: Number(run.stats?.percent ?? 0),
        fetched: Number(run.stats?.fetched ?? source.pages_fetched_total),
        known: Number(run.stats?.known ?? source.urls_known_total),
        done: Number(run.stats?.item_done),
        total: Number(run.stats?.item_total),
        itemsParsed: Number(run.stats?.items_parsed),
        totalToParse: Number(run.stats?.total_to_parse),
      });
    });
  });
}

function normalizeSourceProgress(sourceId) {
  const progress = state.runProgress[sourceId] || {};
  if (progress.runId) {
    const action = progress.action === "parse" ? "parse" : "crawl";
    state.runProgress[sourceId] = { [action]: progress };
  }
  return state.runProgress[sourceId] || {};
}

function persistRunProgress() {
  sessionStorage.setItem(RUN_PROGRESS_KEY, JSON.stringify(state.runProgress || {}));
}

function updateRunProgressDom(sourceId) {
  const progress = normalizeSourceProgress(sourceId);
  const row = document.querySelector(`[data-source-id="${CSS.escape(sourceId)}"]`);
  if (!row) return;
  if (!progress) return;
  const lastRun = row.querySelector(".source-last-run");
  if (lastRun) {
    lastRun.textContent = ["crawl", "parse"]
      .filter((action) => progress[action])
      .map((action) => runProgressText(progress[action]))
      .join(" · ");
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
    const normalizedTab = activeTab === "files" && (!isAdmin() || detail.kind !== "file")
      ? "documents"
      : activeTab;
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
    ...(source.kind === "file" && isAdmin() ? [["files", "Files"]] : []),
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
  const kindIcon = sourceKindIcon(source);
  const kindLabel = sourceKindLabel(source);
  head.innerHTML = `
    <div class="source-detail-head">
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
      <div class="source-row-stats source-detail-stats">
        <span><strong>${formatNumber(sourcePagesFetched(source))}</strong> pages fetched</span>
        <span><strong>${formatNumber(source.coverage.documents_parsed)}</strong> docs parsed</span>
        <span><strong>${formatNumber(source.coverage.claims)}</strong> claims</span>
        <span class="muted">${source.last_run_at ? `last run ${timeAgo(source.last_run_at)}` : "never run"}</span>
        <span class="status-pill ${source.status}">${capitalize(source.status)}</span>
      </div>
    </div>
  `;
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
      ${isAdmin() ? `<div class="docs-selection-toolbar" id="docs-selection-toolbar" hidden></div>` : ""}
      <table class="docs-table">
        <thead>
          <tr>
            ${isAdmin() ? `<th class="select-col"></th>` : ""}
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
              ${
                isAdmin()
                  ? `<td class="select-col"><input type="checkbox" data-fetch-select="${escapeAttr(d.fetch_id || "")}" aria-label="Select document" ${d.fetch_id ? "" : "disabled"} /></td>`
                  : ""
              }
              <td>
                <div class="cell-truncate">${escapeHtml(d.title || d.url || "")}</div>
                <div class="muted small cell-truncate">${escapeHtml(d.url || "")}</div>
              </td>
              <td class="muted small">${escapeHtml(timeAgo(d.fetched_at))}</td>
              <td class="num">${d.word_count}</td>
              <td class="num">${d.chunks}</td>
              <td class="num">${d.claims_extracted}</td>
              <td class="document-actions">
                <button class="btn-ghost" data-doc="${escapeAttr(d.document_id)}"><i class="ti ti-eye"></i> View</button>
                ${
                  isAdmin()
                    ? `<button class="btn-icon-only" data-delete-doc="${escapeAttr(d.document_id)}" type="button" aria-label="Delete document" title="Delete document"><i class="ti ti-trash"></i></button>`
                    : ""
                }
              </td>
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
    wrap.querySelectorAll("[data-delete-doc]").forEach((button) => {
      button.onclick = () => confirmDeleteDocument(sourceId, button.dataset.deleteDoc);
    });
    setupDocumentSelectionToolbar(sourceId, wrap);
  } catch (e) {
    wrap.innerHTML = `<div class="empty-state"><i class="ti ti-alert-circle"></i><div>Unable to load: ${escapeHtml(e.message)}</div></div>`;
  }
}

function setupDocumentSelectionToolbar(sourceId, wrap) {
  const toolbar = byId("docs-selection-toolbar");
  if (!toolbar) return;
  const checkboxes = Array.from(wrap.querySelectorAll("[data-fetch-select]"));
  const refresh = () => {
    const selected = checkboxes.filter((input) => input.checked).map((input) => input.dataset.fetchSelect).filter(Boolean);
    toolbar.hidden = selected.length === 0;
    toolbar.innerHTML = selected.length
      ? `<span class="muted small">${formatNumber(selected.length)} selected</span>
         <button class="btn-primary" id="parse-selected-docs" type="button"><i class="ti ti-cpu"></i> Parse selected</button>`
      : "";
    const button = byId("parse-selected-docs");
    if (button) {
      button.onclick = () => parseSelectedFetches(sourceId, selected);
    }
  };
  checkboxes.forEach((input) => {
    input.onchange = refresh;
  });
  refresh();
}

async function parseSelectedFetches(sourceId, fetchIds) {
  if (!isAdmin() || !fetchIds.length) return;
  try {
    const response = await fetch(`/admin/sources/${sourceId}/parse`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ scope: "fetch_ids", fetch_ids: fetchIds }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 409 && data.error === "already_running") {
      toast("Parse is already running", { level: "warning" });
      return;
    }
    if (!response.ok) {
      throw new Error(data.detail || response.statusText);
    }
    toast("Parse queued", { level: "success" });
    renderSourceDocuments(sourceId);
  } catch (error) {
    toast(normalizeErrorMessage(error), { level: "error" });
  }
}

function confirmDeleteDocument(sourceId, documentId) {
  if (!isAdmin()) return;
  openConfirmModal({
    title: "Delete document",
    body: "Delete this document? Its claims and chunks will also be removed.",
    danger: true,
    confirmLabel: "Delete document",
    onConfirm: async () => {
      await expectOk(fetch(`/admin/documents/${documentId}`, { method: "DELETE" }));
      await Promise.all([loadStats(), renderSourceDocuments(sourceId)]);
    },
  });
}

async function openDocumentModal(documentId) {
  openModal(`<div class="empty-state"><i class="ti ti-loader"></i><div>Loading…</div></div>`);
  try {
    const data = await getJSON(`/api/document/${documentId}`);
    const claimsHtml = (data.claims_raw || [])
      .map(
        (c) => `<li><strong>${escapeHtml(c.subject_text)}</strong> ${escapeHtml(c.predicate)} ${escapeHtml(c.object_text || "")} ${claimAttribution(c)}</li>`,
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
  const canAdmin = isAdmin();
  const fileAvailable = detail.file_size_bytes != null;
  wrap.innerHTML = `
    <div class="panel panel-flush source-files-panel">
      ${
        canAdmin
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
                         canAdmin
                           ? `<button class="btn-secondary" id="file-archive" type="button" title="Archive this source; derived data is preserved"><i class="ti ti-archive" aria-hidden="true"></i> Archive</button>`
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
               ${canAdmin ? `<div class="muted small">Use the replacement upload above to attach a new file.</div>` : ""}
             </div>`
      }
    </div>
  `;
  if (!canAdmin) return;
  setupSourceFileUpload(detail);
  const archive = byId("file-archive");
  if (archive) {
    archive.onclick = () => archiveSourceFromDetail(detail);
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
    renderSourceDetail(sourceId, "files");
  } catch (_) {}
}

function archiveSourceFromDetail(source) {
  if (!isAdmin()) return;
  patchSource(source.id, { status: "archived" })
    .then(() => {
      location.hash = "#sources";
    })
    .catch(() => {});
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
            <td>${escapeHtml(sourceRunKindLabel(r.kind))}</td>
            <td><span class="status-pill ${r.status === "complete" ? "active" : escapeAttr(r.status || "")}">${escapeHtml(sourceRunStatusLabel(r.status))}</span></td>
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
  const adminOnly = !isAdmin();
  wrap.innerHTML = `
    <div class="panel panel-flush">
      <div class="modal-body">
        <div class="source-config-status">
          ${sourceConfigStatusMarkup(detail)}
        </div>
        <a class="btn-ghost" href="#claims?source=${escapeAttr(detail.id)}"><i class="ti ti-file-search" aria-hidden="true"></i> View ${formatNumber(detail.coverage?.claims || 0)} claims from this source</a>
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
        <label class="field">
          <span class="field-label">Notes</span>
          <textarea class="input" id="cfg-notes" rows="3" ${adminOnly ? "disabled" : ""}>${escapeHtml(detail.notes || "")}</textarea>
        </label>
        <label class="field checkbox-field">
          <span class="checkbox-row">
            <input id="cfg-respect-robots" type="checkbox" ${detail.respect_robots !== false ? "checked" : ""} ${adminOnly ? "disabled" : ""} />
            <span>Respect robots.txt</span>
          </span>
          <span class="field-hint warning">Disable only with explicit permission from the site owner.</span>
        </label>
        <label class="field">
          <span class="field-label">Re-crawl interval (days)</span>
          <input class="input" id="cfg-recrawl-days" type="number" min="1" max="3650" value="${escapeAttr(detail.recrawl_interval_days || 7)}" ${adminOnly ? "disabled" : ""} />
        </label>
        ${
          !adminOnly
            ? `<div class="form-actions">
                <button class="btn-secondary" id="cfg-verify" type="button"><i class="ti ti-shield-check"></i> Verify integrity</button>
                <button class="btn-primary" id="cfg-save"><i class="ti ti-device-floppy"></i> Save</button>
              </div>`
            : ""
        }
      </div>
    </div>
  `;
  if (!adminOnly) {
    byId("cfg-save").onclick = async () => {
      const body = {
        display_name: byId("cfg-name").value.trim() || null,
        notes: byId("cfg-notes").value.trim() || null,
        respect_robots: byId("cfg-respect-robots").checked,
        recrawl_interval_days: Math.max(1, Number(byId("cfg-recrawl-days").value || 7)),
      };
      await patchSource(detail.id, body);
      renderSourceDetail(detail.id, "config");
    };
    byId("cfg-verify").onclick = () => verifySourceIntegrity(detail.id);
  }
}

function sourceConfigStatusMarkup(detail) {
  if (sourceUpToDate(detail)) {
    return `<div class="source-config-indicator ok"><i class="ti ti-circle-check" aria-hidden="true"></i><div><strong>Up to date</strong><span>Up to date as of ${escapeHtml(detail.last_full_recrawl_at ? timeAgo(detail.last_full_recrawl_at) : "the last crawl")}; last parsed ${escapeHtml(detail.latest_parse_finished_at ? timeAgo(detail.latest_parse_finished_at) : "never")}.</span></div></div>`;
  }
  const pending = Number(detail.pending_parse_count || 0);
  if (pending > 0) {
    return `<div class="source-config-indicator info"><i class="ti ti-info-circle" aria-hidden="true"></i><div><strong>${formatNumber(pending)} documents haven't been parsed</strong><span>Click Parse from the sources list to process pending work.</span></div></div>`;
  }
  if (sourceDueForRecrawl(detail)) {
    return `<div class="source-config-indicator info"><i class="ti ti-refresh-alert" aria-hidden="true"></i><div><strong>Due for re-crawl</strong><span>Last crawled ${escapeHtml(detail.last_full_recrawl_at ? timeAgo(detail.last_full_recrawl_at) : "never")}.</span></div></div>`;
  }
  return `<div class="source-config-indicator"><i class="ti ti-clock" aria-hidden="true"></i><div><strong>Status</strong><span>Last crawled ${escapeHtml(detail.last_full_recrawl_at ? timeAgo(detail.last_full_recrawl_at) : "never")}; last parsed ${escapeHtml(detail.latest_parse_finished_at ? timeAgo(detail.latest_parse_finished_at) : "never")}.</span></div></div>`;
}

async function verifySourceIntegrity(sourceId) {
  try {
    const result = await postJSON(`/admin/sources/${sourceId}/verify-integrity`, {});
    openModal(`
      <div class="modal-header">
        <div>
          <div class="modal-title">Integrity check</div>
          <div class="modal-subtitle">${result.ok ? "No violations found" : "Violations found"}</div>
        </div>
        <button class="btn-icon-only modal-close" onclick="closeModal()" aria-label="Close">×</button>
      </div>
      <div class="modal-body">
        <pre class="integrity-result">${escapeHtml(JSON.stringify(result, null, 2))}</pre>
      </div>
    `);
  } catch (error) {
    toast(normalizeErrorMessage(error), { level: "error" });
  }
}

/* ───── Add source modal ───── */

let modalKind = "domain";

function openAddSourceModal() {
  if (!isAdmin()) return;
  modalKind = "domain";
  renderAddSourceModal();
}

function renderAddSourceModal() {
  const selected = SOURCE_KINDS.find((k) => k.id === modalKind) || SOURCE_KINDS[0];
  openModal(`
    <div class="modal-header">
      <div>
        <div class="modal-title">Add source</div>
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
              <span class="kind-card-copy">
                <span class="label">${escapeHtml(k.label)}</span>
                ${k.description ? `<span class="description">${escapeHtml(k.description)}</span>` : ""}
              </span>
            </button>`,
          ).join("")}
        </div>
      </div>
      <label class="field">
        <span class="field-label">Label</span>
        <input class="input" id="new-name" placeholder="e.g. Tuck Website" />
      </label>
      ${selected.fields
        .map((f) => {
          if (f.type === "file") {
            return `<label class="field">
                <span class="field-label">${escapeHtml(f.label)}</span>
                <input class="input" id="new-${f.name}" type="file" ${f.accept ? `accept="${escapeAttr(f.accept)}"` : ""} />
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
      <button class="btn-primary" id="new-submit">Add source</button>
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
  if (!isAdmin()) return;
  const selected = SOURCE_KINDS.find((k) => k.id === modalKind) || SOURCE_KINDS[0];
  const kind = selected.kind;
  const display_name = byId("new-name").value.trim();
  if (!display_name) {
    return;
  }
  try {
    if (kind === "file" || kind === "enrichment") {
      const input = byId("new-file");
      if (!input.files || !input.files[0]) {
        return;
      }
      const form = new FormData();
      form.append("display_name", display_name);
      form.append("file", input.files[0]);
      const url =
        kind === "enrichment" ? "/admin/sources/upload-enrichment" : "/admin/sources/upload";
      const res = await fetch(url, { method: "POST", body: form });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || res.statusText);
      }
    } else {
      const identifier = byId("new-identifier").value.trim();
      if (!identifier) {
        return;
      }
      const body = {
        kind,
        identifier,
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
    await Promise.all([loadSourcesStats(), loadSourcesList()]);
  } catch (error) {
    toast(normalizeErrorMessage(error), { level: "error" });
  }
}

/* ───── Source conflicts ───── */

async function loadAdminConflicts(targetId = "conflicts-body", summaryId = "conflict-summary-text") {
  try {
    const data = await loadAdminPanelData("/admin/conflicts", {
      targetId,
      title: "Sign in to view conflicts",
    });
    if (!data) {
      const target = byId(targetId);
      if (target) {
        target.innerHTML = `
        <div class="empty-state sources-empty compact">
          <i class="ti ti-alert-triangle-off" aria-hidden="true"></i>
          <div>No conflicts. Sources agree.</div>
        </div>`;
      }
      const summary = byId(summaryId);
      if (summary) {
        summary.textContent = "Conflicts (0 unresolved)";
      }
      return;
    }
    const summary = byId(summaryId);
    if (summary) {
      summary.textContent = `Conflicts (${formatNumber(data.total || 0)} unresolved)`;
    }
    renderFactConflictsList(byId(targetId), data, () => loadAdminConflicts(targetId, summaryId));
  } catch (e) {
    const target = byId(targetId);
    if (target) target.innerHTML = `<div class="muted small">Unable to load conflicts: ${escapeHtml(e.message)}</div>`;
  }
}

async function renderConflictsPage(section = "facts") {
  const active = section === "identity" ? "identity" : "facts";
  setPageHeader({
    title: "Conflicts",
    subtitle:
      active === "identity"
        ? "Two records may refer to the same entity. Merge or split."
        : "Sources disagree about a fact. Pick the correct claim.",
  });
  byId("app").innerHTML = `
    <section class="conflicts-page">
      <nav class="conflicts-tabs" id="conflicts-tabs" aria-label="Conflict type">
        ${conflictTabStrip(active)}
      </nav>
      <div class="sources-list" id="conflicts-panel-body">
        <div class="empty-state compact"><i class="ti ti-loader" aria-hidden="true"></i><div>Loading...</div></div>
      </div>
    </section>
  `;
  await loadConflictsPanel(active);
}

async function loadConflictsPanel(active) {
  const body = byId("conflicts-panel-body");
  try {
    const [facts, identity] = await Promise.all([
      loadAdminPanelData("/admin/conflicts", {
        targetId: "conflicts-panel-body",
        title: "Sign in to view conflicts",
      }),
      loadAdminPanelData("/admin/identity-review", {
        targetId: "conflicts-panel-body",
        title: "Sign in to view conflicts",
      }),
    ]);
    if (!facts || !identity) return;
    byId("conflicts-tabs").innerHTML = conflictTabStrip(active, facts.total || 0, identity.total || 0);
    setPageHeader({
      title: "Conflicts",
      subtitle:
        active === "identity"
          ? "Two records may refer to the same entity. Merge or split."
          : "Sources disagree about a fact. Pick the correct claim.",
    });
    if (active === "identity") {
      renderIdentityReviewList(body, identity, () => loadConflictsPanel("identity"));
    } else {
      renderFactConflictsList(body, facts, () => loadConflictsPanel("facts"));
    }
  } catch (error) {
    if (body) body.innerHTML = `<div class="muted small">Unable to load conflicts: ${escapeHtml(error.message)}</div>`;
  }
}

function conflictTabStrip(active, factsTotal = 0, identityTotal = 0) {
  return `
    <a class="conflicts-tab ${active === "facts" ? "active" : ""}" href="#conflicts/facts">
      Contradicting Facts (${formatNumber(factsTotal)})
    </a>
    <a class="conflicts-tab ${active === "identity" ? "active" : ""}" href="#conflicts/identity">
      Ambiguous Identity (${formatNumber(identityTotal)})
    </a>
  `;
}

function renderFactConflictsList(target, data, reload) {
  if (!target) return;
  const rows = data.results || [];
  if (!rows.length) {
    target.innerHTML = `
      <div class="empty-state sources-empty compact">
        <i class="ti ti-alert-triangle-off" aria-hidden="true"></i>
        <div>No conflicts. Sources agree.</div>
      </div>`;
    return;
  }
  target.innerHTML = rows
    .map(
      (c) => `
      <div class="conflict-row">
        <div class="stmt"><strong>Conflict ${escapeHtml(c.id.slice(0, 8))}</strong></div>
        <div class="versus">
          ${renderConflictClaim("Claim A", c.claim_a, c.claim_a_id)}
          <span>vs</span>
          ${renderConflictClaim("Claim B", c.claim_b, c.claim_b_id)}
        </div>
        <div class="conflict-actions">
          <button class="btn-source" data-resolve="${escapeAttr(c.id)}" data-side="claim_a_wins">Pick A</button>
          <button class="btn-source" data-resolve="${escapeAttr(c.id)}" data-side="claim_b_wins">Pick B</button>
          <button class="btn-ghost" data-resolve="${escapeAttr(c.id)}" data-side="both_valid_distinct">Both valid</button>
        </div>
      </div>`,
    )
    .join("");
  target.querySelectorAll("[data-resolve]").forEach((b) => {
    b.onclick = async () => {
      const id = b.dataset.resolve;
      const resolution = b.dataset.side;
      try {
        await expectOk(fetch(`/admin/conflicts/${id}/resolve`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ resolution }),
        }));
        toast("Conflict resolved", { level: "success" });
        reload();
      } catch (error) {
        toast(normalizeErrorMessage(error), { level: "error" });
      }
    };
  });
}

function renderConflictClaim(label, claim, claimId) {
  return `
    <div class="conflict-claim">
      <span class="muted small">${escapeHtml(label)}: ${escapeHtml(String(claimId || "").slice(0, 8))}</span>
      <strong>${escapeHtml(claim?.statement || "Claim unavailable")}</strong>
      ${claimAttribution(claim)}
    </div>
  `;
}

async function loadIdentityReview() {
  const list = byId("identity-review-list");
  try {
    const data = await loadAdminPanelData("/admin/identity-review", {
      targetId: "identity-review-list",
      title: "Sign in to review identities",
    });
    if (!data) return;
    renderIdentityReviewList(list, data, loadIdentityReview);
  } catch (error) {
    if (list) {
      list.innerHTML = `<div class="muted small">Unable to load ambiguous identities: ${escapeHtml(error.message)}</div>`;
    }
  }
}

function renderIdentityReviewList(target, data, reload) {
  if (!target) return;
  const rows = data.results || [];
  if (!rows.length) {
    target.innerHTML = `
      <div class="empty-state sources-empty compact">
        <i class="ti ti-user-check" aria-hidden="true"></i>
        <div>No ambiguous identities.</div>
      </div>`;
    return;
  }
  target.innerHTML = rows.map(identityReviewRow).join("");
  target.querySelectorAll("[data-review-decision]").forEach((button) => {
    button.onclick = async () => {
      const id = button.dataset.reviewId;
      const decision = button.dataset.reviewDecision;
      const entityId = button.dataset.entityId;
      try {
        if (decision === "alias-add") {
          const input = document.querySelector(`input[data-alias-input-id="${id}"]`);
          const aliasValue = (input && input.value.trim()) || "";
          if (!aliasValue) {
            toast("Alias text required", { level: "error" });
            return;
          }
          await postJSON(`/admin/entities/${entityId}/alias`, {
            alias: aliasValue,
            reviewer: state.me?.username || "admin",
          });
          toast("Alias added", { level: "success" });
        } else if (decision === "verify") {
          await postJSON(`/admin/entities/${entityId}/verify`, {
            reviewer: state.me?.username || "admin",
          });
          toast("Entity verified", { level: "success" });
        } else {
          await postJSON(`/admin/identity-review/${id}`, {
            decision,
            reviewer: state.me?.username || "admin",
          });
          const messages = {
            merge: "Entities merged",
            split: "Marked as separate entities",
            confirm: "Identity review updated",
            defer: "Deferred for later",
          };
          toast(messages[decision] || "Identity review updated", { level: "success" });
        }
        await reload();
      } catch (error) {
        toast(normalizeErrorMessage(error), { level: "error" });
      }
    };
  });
}

function identityReviewRow(row) {
  const source = row.source_entity || {};
  const candidate = row.candidate_entity || {};
  const score = Number(row.name_similarity_score || 0);
  const scoreLabel = score ? `${Math.round(score * 100)}% similar` : "similarity unknown";
  const canMerge = Boolean(row.mention && source.id && candidate.id);
  return `
    <article class="conflict-row">
      <div class="stmt"><strong>${escapeHtml(source.display_name || source.canonical_name || "Unknown entity")}</strong> <span class="muted">vs</span> <strong>${escapeHtml(candidate.display_name || candidate.canonical_name || "Candidate missing")}</strong></div>
      <div class="muted small">${escapeHtml(scoreLabel)} · ${escapeHtml(row.mention?.text || "No mention text")}</div>
      <div class="versus">
        ${identityReviewEntityBlock("Mention entity", source, row.source_qualifiers)}
        <span>vs</span>
        ${identityReviewEntityBlock("Candidate", candidate, row.candidate_qualifiers)}
      </div>
      ${row.llm_reasoning ? `<div class="muted small">${escapeHtml(row.llm_reasoning)}</div>` : ""}
      <div class="conflict-actions">
        ${canMerge ? `<button class="btn-source" data-review-id="${escapeAttr(row.id)}" data-review-decision="merge">Merge</button>` : ""}
        <button class="btn-ghost" data-review-id="${escapeAttr(row.id)}" data-review-decision="split">Different entity</button>
        <button class="btn-ghost" data-review-id="${escapeAttr(row.id)}" data-review-decision="defer">Defer</button>
        ${candidate.id ? `<button class="btn-ghost" data-review-id="${escapeAttr(row.id)}" data-review-decision="verify" data-entity-id="${escapeAttr(candidate.id)}">Verify candidate</button>` : ""}
      </div>
      ${candidate.id ? `<div class="conflict-actions">
        <input class="input" type="text" placeholder="Add alias to candidate..." data-alias-input-id="${escapeAttr(row.id)}" />
        <button class="btn-ghost" data-review-id="${escapeAttr(row.id)}" data-review-decision="alias-add" data-entity-id="${escapeAttr(candidate.id)}">Add alias</button>
      </div>` : ""}
    </article>
  `;
}

function identityReviewEntityBlock(label, entity, qualifiers = {}) {
  const aliasText = (entity.aliases || []).slice(0, 4).join(", ");
  return `
    <div class="conflict-claim">
      <span class="muted small">${escapeHtml(label)}: ${escapeHtml(String(entity.id || "").slice(0, 8))}</span>
      <strong>${escapeHtml(entity.display_name || entity.canonical_name || "Unknown entity")}</strong>
      <div class="muted small">${escapeHtml(entity.type || "")}${aliasText ? ` · aliases: ${escapeHtml(aliasText)}` : ""}</div>
      ${identityReviewQualifierText(qualifiers)}
    </div>
  `;
}

function identityReviewQualifierText(qualifiers = {}) {
  const parts = Object.entries(qualifiers)
    .filter(([, values]) => Array.isArray(values) && values.length)
    .slice(0, 5)
    .map(([key, values]) => `${key}: ${values.slice(0, 4).join(", ")}`);
  return parts.length ? `<div class="muted small">${escapeHtml(parts.join(" · "))}</div>` : "";
}

async function loadAdminPanelData(url, { targetId, title }) {
  try {
    return await getJSON(url, { redirectOnAuth: false });
  } catch (error) {
    if (isAuthError(error)) {
      const target = byId(targetId);
      if (target) target.innerHTML = adminSignInPrompt(title);
      return null;
    }
    throw error;
  }
}

function adminSignInPrompt(title = "Sign in to view") {
  return `
    <div class="empty-state compact admin-auth-prompt">
      <i class="ti ti-lock" aria-hidden="true"></i>
      <div>${escapeHtml(title)}</div>
      <a class="btn-secondary" href="${escapeAttr(loginUrl())}">Sign in</a>
    </div>
  `;
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
  } else if (tab === "logs" || tab === "conflicts" || tab === "raw") {
    stopLogsViewStream();
    location.hash = "#directory";
  } else if (tab === "sources") {
    await renderRoute();
  }
  return false;
}

function loginUrl() {
  const next = `${location.pathname}${location.search}${location.hash || ""}`;
  return `${state.me?.admin_login_url || "/admin/login"}?next=${encodeURIComponent(next)}`;
}

function handleAdminUnauthorized() {
  location.href = loginUrl();
}

/* ───── Logs ───── */

function renderLogs() {
  setPageHeader({
    title: "Logs",
    subtitle: "Live backend stream",
  });
  byId("app").innerHTML = `
    <section class="logs-view" aria-label="Application logs">
      <div class="logs-terminal" id="logs-terminal"></div>
    </section>
  `;
  startLogsViewStream();
}

function startLogsViewStream() {
  stopLogsViewStream();
  appendLog({
    timestamp: new Date().toISOString(),
    level: "INFO",
    message: "Connecting to backend log stream.",
  });
  logsViewStream = new EventSource("/api/logs/stream");
  logsViewStream.onmessage = (event) => appendLog(JSON.parse(event.data));
  logsViewStream.onerror = () => {
    appendLog({
      timestamp: new Date().toISOString(),
      level: "ERROR",
      message: "Log stream disconnected.",
    });
    stopLogsViewStream();
  };
}

function stopLogsViewStream() {
  if (!logsViewStream) return;
  logsViewStream.close();
  logsViewStream = null;
}

/* ───── Modal ───── */

function openConfirmModal({ title, body, danger = false, confirmLabel = "Confirm", onConfirm }) {
  openModal(`
    <div class="modal-header">
      <div>
        <div class="modal-title">${escapeHtml(title)}</div>
      </div>
      <button class="btn-icon-only modal-close" onclick="closeModal()" aria-label="Close">×</button>
    </div>
    <div class="modal-body">
      <p>${escapeHtml(body)}</p>
      <div class="modal-error" id="confirm-error" hidden></div>
    </div>
    <div class="modal-footer">
      <button class="btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="${danger ? "btn-danger" : "btn-primary"}" id="confirm-submit">${escapeHtml(confirmLabel)}</button>
    </div>
  `);
  byId("confirm-submit").onclick = async () => {
    const button = byId("confirm-submit");
    const errorBox = byId("confirm-error");
    if (errorBox) {
      errorBox.hidden = true;
      errorBox.textContent = "";
    }
    button.disabled = true;
    try {
      await onConfirm();
      closeModal();
    } catch (error) {
      toast(normalizeErrorMessage(error), { level: "error" });
      if (errorBox) {
        errorBox.textContent = normalizeErrorMessage(error);
        errorBox.hidden = false;
      }
      button.disabled = false;
    }
  };
}

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

function appendLog(line) {
  const root = byId("logs-terminal");
  if (!root) return;
  const entry = document.createElement("div");
  entry.className = `log-line ${String(line.level || "").toLowerCase()}`;
  const timestamp = new Date(line.timestamp || Date.now()).toLocaleTimeString();
  entry.innerHTML = `<span class="log-time">[${escapeHtml(timestamp)}]</span> <span class="log-level">${escapeHtml(line.level || "INFO")}</span> ${escapeHtml(line.message || "")}`;
  root.appendChild(entry);
  while (root.children.length > 1000) root.firstElementChild?.remove();
  root.scrollTop = root.scrollHeight;
}

function ensureToastContainer() {
  if (byId("toast-container")) return byId("toast-container");
  const container = document.createElement("div");
  container.id = "toast-container";
  container.className = "toast-container";
  container.setAttribute("aria-live", "polite");
  container.setAttribute("aria-atomic", "false");
  document.body.appendChild(container);
  return container;
}

function toast(message, options = {}) {
  const text = String(message || "").trim();
  if (!text) return;
  const level = ["info", "success", "warning", "error"].includes(options.level)
    ? options.level
    : "info";
  const duration = Number.isFinite(options.duration) ? options.duration : 4000;
  const container = ensureToastContainer();
  while (container.children.length >= MAX_TOASTS) {
    container.firstElementChild?.remove();
  }
  const item = document.createElement("button");
  item.type = "button";
  item.className = `toast toast-${level}`;
  item.innerHTML = `<span>${escapeHtml(text)}</span>`;
  item.onclick = () => dismissToast(item);
  container.appendChild(item);
  window.setTimeout(() => dismissToast(item), duration);
}

function dismissToast(item) {
  if (!item || item.dataset.closing) return;
  item.dataset.closing = "true";
  item.classList.add("toast-dismissing");
  window.setTimeout(() => item.remove(), 180);
}

/* ───── Utilities ───── */

async function getJSON(url, options = {}) {
  const { redirectOnAuth = true } = options;
  const res = await fetch(url);
  if (!res.ok) {
    throw await errorFromResponse(res, url, { redirectOnAuth });
  }
  return res.json();
}

async function postJSON(url, body = {}, options = {}) {
  const { redirectOnAuth = true } = options;
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await errorFromResponse(res, url, { redirectOnAuth });
  return res.json();
}

async function expectOk(responsePromise, options = {}) {
  const { redirectOnAuth = true } = options;
  const res = await responsePromise;
  if (!res.ok) throw await errorFromResponse(res, res.url, { redirectOnAuth });
  return res;
}

async function errorFromResponse(res, url, { redirectOnAuth = true } = {}) {
  const contentType = res.headers.get("content-type") || "";
  let message = `${res.status} ${res.statusText}`;
  if (contentType.includes("application/json")) {
    const data = await res.json().catch(() => ({}));
    message = data.detail || data.error || message;
  } else {
    message = (await res.text().catch(() => "")).trim() || message;
  }
  const error = new Error(message);
  error.status = res.status;
  error.statusText = res.statusText;
  error.adminAuthRequired = res.status === 401 && isAdminEndpoint(url);
  if (error.adminAuthRequired && redirectOnAuth) {
    setTimeout(handleAdminUnauthorized, 0);
  }
  return error;
}

function isAdminEndpoint(url) {
  const pathname = new URL(url, location.origin).pathname;
  return pathname.startsWith("/admin/") || pathname === "/api/sources/archived";
}

function byId(id) {
  return document.getElementById(id);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || `var(${name})`;
}

function readSessionJSON(key, fallback) {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "") || fallback;
  } catch (_) {
    return fallback;
  }
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
