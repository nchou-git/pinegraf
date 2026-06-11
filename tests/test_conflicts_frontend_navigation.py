from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "frontend" / "app.js"


def _app_js() -> str:
    return APP_JS.read_text()


def test_sidebar_uses_target_information_architecture() -> None:
    source = _app_js()

    assert '{ id: "ask", label: "Ask"' in source
    assert '{ id: "graph", label: "Graph"' in source
    assert '{ id: "sources", label: "Sources"' in source
    assert '{ id: "claims", label: "Claims"' in source
    assert '{ id: "faq", label: "FAQ"' in source
    assert '{ id: "raw-data", label: "Raw data"' in source
    assert 'label: "Conflicts"' not in source
    assert 'label: "Archive"' not in source
    assert 'label: "System"' not in source
    assert source.index('label: "Ask"') < source.index('label: "Graph"')
    assert source.index('label: "Graph"') < source.index('label: "Claims"')
    assert source.index('label: "Claims"') < source.index('label: "Sources"')
    assert source.index('label: "Sources"') < source.index('label: "Raw data"')
    assert source.index('label: "Raw data"') < source.index('label: "FAQ"')
    assert 'label: "Identity Review"' not in source


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
