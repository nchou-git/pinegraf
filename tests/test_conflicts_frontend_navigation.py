from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "frontend" / "app.js"


def _app_js() -> str:
    return APP_JS.read_text()


def test_sidebar_uses_conflicts_parent_not_identity_review() -> None:
    source = _app_js()

    assert 'label: "Conflicts"' in source
    assert 'label: "Identity Review"' not in source
    assert "IDENTITY_REVIEW_TAB" not in source
    assert "CONFLICTS_TAB" in source


def test_conflicts_route_defaults_to_facts_tab() -> None:
    source = _app_js()

    assert 'history.replaceState(null, "", "#conflicts/facts")' in source
    assert 'href="#conflicts/facts"' in source
    assert 'href="#conflicts/identity"' in source


def test_conflicts_tab_counts_use_endpoint_totals() -> None:
    source = _app_js()

    assert 'loadAdminPanelData("/admin/conflicts"' in source
    assert 'loadAdminPanelData("/admin/identity-review"' in source
    assert "conflictTabStrip(active, facts.total || 0, identity.total || 0)" in source
    assert "Contradicting Facts (${formatNumber(factsTotal)})" in source
    assert "Ambiguous Identity (${formatNumber(identityTotal)})" in source
