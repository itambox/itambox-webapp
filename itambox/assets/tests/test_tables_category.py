"""Colour-chip columns (ColorChipColumn) on the asset / asset-type / inventory
tables.

ColorChipColumn (core.tables) renders a coloured dot + the object's name linking
to its detail page. It is used for Category (asset list via asset_type.category;
asset-type, accessory, consumable and component lists via category) and AssetRole
(asset list) — all of which carry a user-pickable colour. Pure render logic — no
DB needed.
"""
from types import SimpleNamespace

from core.tables import ColorChipColumn
from assets.tables import AssetTable, AssetTypeTable
from inventory.tables import AccessoryTable, ConsumableTable, ComponentTable


def _chip(color='', name='Laptops', url='/assets/categories/1/'):
    return SimpleNamespace(color=color, name=name, get_absolute_url=lambda: url)


class TestColorChipColumnRender:
    def _render(self, value):
        return str(ColorChipColumn().render(value))

    def test_renders_color_name_and_link(self):
        html = self._render(_chip(color='4263eb', name='Servers'))
        assert '#4263eb' in html
        assert 'Servers' in html
        assert '/assets/categories/1/' in html
        assert 'rounded-circle' in html

    def test_blank_color_falls_back_to_grey(self):
        html = self._render(_chip(color='', name='Other'))
        assert '#6c757d' in html

    def test_none_is_muted_dash(self):
        html = self._render(None)
        assert 'text-muted' in html
        assert 'rounded-circle' not in html


class TestTablesExposeChipColumns:
    def test_asset_table_category_and_role_are_chips(self):
        assert isinstance(AssetTable.base_columns['category'], ColorChipColumn)
        assert isinstance(AssetTable.base_columns['asset_role'], ColorChipColumn)
        assert 'category' in AssetTable.Meta.default_columns
        assert 'asset_role' in AssetTable.Meta.default_columns

    def test_assettype_table_category_is_chip(self):
        assert isinstance(AssetTypeTable.base_columns['category'], ColorChipColumn)
        assert 'category' in AssetTypeTable.Meta.default_columns

    def test_inventory_tables_category_is_chip(self):
        for table in (AccessoryTable, ConsumableTable, ComponentTable):
            col = table.base_columns['category']
            assert isinstance(col, ColorChipColumn), table.__name__
            # accessor must resolve to the Category object (not .name) for the chip
            assert str(col.accessor) == 'category', table.__name__
