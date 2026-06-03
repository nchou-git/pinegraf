from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "frontend" / "app.js"


def _app_js() -> str:
    return APP_JS.read_text()


def test_sidebar_omits_removed_admin_pages() -> None:
    source = _app_js()

    assert 'label: "Conflicts"' not in source
    assert 'label: "Logs"' not in source
    assert '{ id: "claims", label: "Claims"' not in source
    assert "CONFLICTS_TAB" not in source
    assert "LOGS_TAB" not in source
    assert 'tab.id === "ask"' in source
    assert 'tab.id === "claims"' not in source


def test_stale_removed_routes_redirect_to_directory() -> None:
    source = _app_js()

    assert '["conflicts", "logs", "claims"].includes(tab)' in source
    assert 'history.replaceState(null, "", "#directory")' in source
    assert 'if (tab === "claims")' not in source
    assert 'if (tab === "conflicts")' not in source
    assert 'if (tab === "logs")' not in source


def test_sources_inline_conflicts_summary_stays() -> None:
    source = _app_js()

    assert 'id="conflict-summary-text">Conflicts (0 unresolved)' in source
    assert 'id="conflicts-body"' in source
    assert 'loadAdminPanelData("/admin/conflicts"' in source
    assert "renderFactConflictsList(byId(targetId), data" in source
