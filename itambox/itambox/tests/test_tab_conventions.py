"""Guard tests for the detail-view related-tab conventions.

See scratch/TAB_CONVENTIONS.md. These are static template-source checks (no DB),
so they run fast and catch regressions of the tab-load race / boost fixes:

* Every tab anchor must either opt out of hx-boost (``hx-boost="false"``) or load
  its own pane (``hx-get``). A plain ``<a href="?tab=x" data-bs-toggle="tab">``
  under the app-wide ``hx-boost="true"`` body fires a boosted full-content reload
  of ``#page-content-wrapper`` on every click -- wasteful and the source of the
  active-tab "flip-back" race.
* The shared tab list keeps its ``id="detail-tabs"`` so lazy tabs can target it as
  their ``hx-sync`` abort group.
"""
import re
from pathlib import Path

import pytest

# itambox/itambox/tests/test_tab_conventions.py -> parents[2] == project root (manage.py dir)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DETAIL_TEMPLATES = sorted(PROJECT_ROOT.glob("**/*_detail.html"))

# Capture each opening <a ...> tag (anchors never nest; [^>] also spans newlines).
_ANCHOR_RE = re.compile(r"<a\b[^>]*>")


def _tab_anchors(src: str) -> list[str]:
    return [a for a in _ANCHOR_RE.findall(src) if 'data-bs-toggle="tab"' in a]


def test_detail_templates_discovered():
    assert DETAIL_TEMPLATES, "No *_detail.html templates found under the project root"


@pytest.mark.parametrize("tpl", DETAIL_TEMPLATES, ids=lambda p: p.name)
def test_no_plain_boosted_tab_anchors(tpl: Path):
    src = tpl.read_text(encoding="utf-8")
    offenders = [
        a for a in _tab_anchors(src)
        if 'hx-boost="false"' not in a and "hx-get=" not in a
    ]
    assert not offenders, (
        f'{tpl.relative_to(PROJECT_ROOT)} has plain-boosted tab anchor(s); add '
        f'hx-boost="false" (or use a lazy hx-get tab):\n  ' + "\n  ".join(offenders)
    )


def test_generic_detail_has_sync_group_id():
    """The shared <ul> must keep id="detail-tabs" -- lazy tabs reference it as the
    hx-sync abort group (#detail-tabs:replace)."""
    base = PROJECT_ROOT / "templates" / "generic" / "object_detail.html"
    src = base.read_text(encoding="utf-8")
    assert 'id="detail-tabs"' in src, "generic object_detail.html lost id=\"detail-tabs\""
