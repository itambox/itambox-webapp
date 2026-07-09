"""Rendering of the 'Requestable' column on the asset / asset-type tables.

AssetType.requestable is a plain bool (the source); Asset.requestable is
nullable and inherits from its type when unset (Asset.is_requestable resolves
the effective value). The columns render check/cross icons (matching the core
BooleanColumn used elsewhere), and the asset table additionally distinguishes a
value set on the asset from one inherited from the type. Pure render logic — no
DB needed.
"""
from types import SimpleNamespace

from assets.tables import AssetTable, AssetTypeTable
from core.tables import BooleanColumn


def _render_asset(requestable, is_requestable):
    record = SimpleNamespace(requestable=requestable, is_requestable=is_requestable)
    # render_requestable only uses `record`, so self is irrelevant here.
    return str(AssetTable.render_requestable(None, record))


class TestAssetRequestableColumn:
    def test_set_true_shows_solid_check(self):
        html = _render_asset(True, True)
        assert 'mdi-check-circle-outline' in html
        assert 'text-success' in html
        assert 'Set on this asset' in html
        assert 'opacity-50' not in html

    def test_set_false_shows_solid_cross(self):
        html = _render_asset(False, False)
        assert 'mdi-close-circle-outline' in html
        assert 'text-danger' in html
        assert 'Set on this asset' in html
        assert 'opacity-50' not in html

    def test_inherited_true_is_muted_with_marker(self):
        html = _render_asset(None, True)
        assert 'mdi-check-circle-outline' in html
        assert 'opacity-50' in html
        assert 'Inherited from asset type' in html

    def test_inherited_false_is_muted_with_marker(self):
        html = _render_asset(None, False)
        assert 'mdi-close-circle-outline' in html
        assert 'opacity-50' in html
        assert 'Inherited from asset type' in html


class TestAssetTypeRequestableColumn:
    def test_column_is_icon_boolean_column(self):
        col = AssetTypeTable.base_columns['requestable']
        assert isinstance(col, BooleanColumn)

    def test_icons(self):
        col = BooleanColumn()
        assert 'mdi-check-circle-outline' in str(col.render(True))
        assert 'mdi-close-circle-outline' in str(col.render(False))
