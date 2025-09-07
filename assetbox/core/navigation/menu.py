from functools import cache

from django.utils.translation import gettext_lazy as _

from . import Menu, MenuGroup, MenuItem, MenuItemButton, get_model_item

ORGANIZATION_MENU = Menu(
    label=_('Organization'),
    icon_class='mdi mdi-domain',
    groups=(
        MenuGroup(
            label=_('Sites & Regions'),
            items=(
                get_model_item('organization', 'site', _('Sites')),
                get_model_item('organization', 'region', _('Regions')),
                get_model_item('organization', 'sitegroup', _('Site Groups')),
                get_model_item('organization', 'location', _('Locations')),
            ),
        ),
        MenuGroup(
            label=_('Tenancy'),
            items=(
                get_model_item('organization', 'tenant', _('Tenants')),
                get_model_item('organization', 'tenantgroup', _('Tenant Groups')),
                get_model_item('organization', 'assetholder', _('Asset Holders')),
                MenuItem(
                    link='organization:assetholderassignment_list',
                    link_text=_('Assignments'),
                    permissions=['organization.view_assetholderassignment'],
                    buttons=(),
                ),
            ),
        ),
    ),
)

ASSETS_MENU = Menu(
    label=_('Physical Assets'),
    icon_class='mdi mdi-server',
    groups=(
        MenuGroup(
            label=_('Hardware'),
            items=(
                get_model_item('assets', 'asset', _('Assets')),
                get_model_item('assets', 'assetrole', _('Asset Roles')),
                get_model_item('assets', 'statuslabel', _('Status Labels')),
            ),
        ),
        MenuGroup(
            label=_('Catalog'),
            items=(
                get_model_item('assets', 'assettype', _('Asset Types')),
                get_model_item('assets', 'manufacturer', _('Manufacturers')),
                get_model_item('assets', 'category', _('Categories')),
            ),
        ),
    ),
)

INVENTORY_MENU = Menu(
    label=_('Inventory & Stock'),
    icon_class='mdi mdi-package-variant-closed',
    groups=(
        MenuGroup(
            label=_('Component Inventory'),
            items=(
                MenuItem(
                    link='assets:component_list',
                    link_text=_('Components'),
                    permissions=['components.view_component'],
                    buttons=(
                        MenuItemButton(
                            link='assets:component_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['components.add_component'],
                        ),
                    ),
                ),
                MenuItem(
                    link='assets:componentstock_list',
                    link_text=_('Component Stocks'),
                    permissions=['components.view_componentstock'],
                    buttons=(
                        MenuItemButton(
                            link='assets:componentstock_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['components.add_componentstock'],
                        ),
                    ),
                ),
                MenuItem(
                    link='assets:componentallocation_list',
                    link_text=_('Component Allocations'),
                    permissions=['components.view_componentallocation'],
                    buttons=(
                        MenuItemButton(
                            link='assets:componentallocation_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['components.add_componentallocation'],
                        ),
                    ),
                ),
            ),
        ),
        MenuGroup(
            label=_('Peripherals & Accessories'),
            items=(
                get_model_item('inventory', 'accessory', _('Accessories')),
                MenuItem(
                    link='inventory:accessorystock_list',
                    link_text=_('Accessory Stocks'),
                    permissions=['inventory.view_accessorystock'],
                    buttons=(
                        MenuItemButton(
                            link='inventory:accessorystock_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['inventory.add_accessorystock'],
                        ),
                    ),
                ),
                get_model_item('inventory', 'consumable', _('Consumables')),
                MenuItem(
                    link='inventory:consumablestock_list',
                    link_text=_('Consumable Stocks'),
                    permissions=['inventory.view_consumablestock'],
                    buttons=(
                        MenuItemButton(
                            link='inventory:consumablestock_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['inventory.add_consumablestock'],
                        ),
                    ),
                ),
            ),
        ),
        MenuGroup(
            label=_('Kits & Lifecycle'),
            items=(
                get_model_item('inventory', 'kit', _('Kits')),
                get_model_item('assets', 'depreciation', _('Depreciation')),
            ),
        ),
    ),
)

SOFTWARE_MENU = Menu(
    label=_('Software & SaaS'),
    icon_class='mdi mdi-file-certificate',
    groups=(
        MenuGroup(
            label=_('Contracts & SaaS'),
            items=(
                get_model_item('software', 'software', _('Software')),
                get_model_item('licenses', 'license', _('Licenses')),
                get_model_item('subscriptions', 'subscription', _('Subscriptions')),
                get_model_item('subscriptions', 'provider', _('Providers')),
            ),
        ),
    ),
)

OPERATIONS_MENU = Menu(
    label=_('Operations'),
    icon_class='mdi mdi-clipboard-text-clock',
    groups=(
        MenuGroup(
            label=_('Compliance & Audits'),
            items=(
                MenuItem(
                    link='assets:auditsession_list',
                    link_text=_('Audit Sessions'),
                    permissions=['assets.view_auditsession'],
                    buttons=(
                        MenuItemButton(
                            link='assets:auditsession_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['assets.add_auditsession'],
                        ),
                    ),
                ),
                get_model_item('compliance', 'assetmaintenance', _('Maintenances')),
            ),
        ),
        MenuGroup(
            label=_('Procurement'),
            items=(
                MenuItem(
                    link='assets:request_list',
                    link_text=_('Requests'),
                    permissions=['assets.view_assetrequest'],
                    buttons=(
                        MenuItemButton(
                            link='assets:request_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['assets.add_assetrequest'],
                        ),
                    ),
                ),
                get_model_item('assets', 'supplier', _('Suppliers')),
            ),
        ),
        MenuGroup(
            label=_('Contacts'),
            items=(
                get_model_item('organization', 'contact', _('Contacts')),
                get_model_item('organization', 'contactrole', _('Contact Roles')),
            ),
        ),
        MenuGroup(
            label=_('System Activity'),
            items=(
                MenuItem(
                    link='objectchange_list',
                    link_text=_('Changelog'),
                    permissions=['core.view_objectchange'],
                    buttons=(),
                ),
            ),
        ),
    ),
)

EXTRAS_MENU = Menu(
    label=_('Customization'),
    icon_class='mdi mdi-tune',
    groups=(
        MenuGroup(
            label=_('Custom Schemas'),
            items=(
                get_model_item('assets', 'customfield', _('Custom Fields')),
                get_model_item('assets', 'customfieldset', _('Custom Fieldsets')),
            ),
        ),
        MenuGroup(
            label=_('Metadata'),
            items=(
                get_model_item('extras', 'tag', _('Tags')),
                MenuItem(
                    link='assets:assettagsequence_list',
                    link_text=_('Asset Tag Sequences'),
                    permissions=['assets.view_assettagsequence'],
                    buttons=(
                        MenuItemButton(
                            link='assets:assettagsequence_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['assets.add_assettagsequence'],
                        ),
                    ),
                ),
            ),
        ),
    ),
)

ADMIN_MENU = Menu(
    label=_('Admin'),
    icon_class='mdi mdi-shield-account',
    groups=(
        MenuGroup(
            label=_('System'),
            items=(
                MenuItem(
                    link='admin:index',
                    link_text=_('Admin Panel'),
                    permissions=(),
                    staff_only=True,
                    buttons=(),
                ),
            ),
        ),
    ),
)


@cache
def get_menus():
    return [
        ORGANIZATION_MENU,
        ASSETS_MENU,
        INVENTORY_MENU,
        SOFTWARE_MENU,
        OPERATIONS_MENU,
        EXTRAS_MENU,
        ADMIN_MENU,
    ]
