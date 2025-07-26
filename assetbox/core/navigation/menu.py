from functools import cache

from django.utils.translation import gettext_lazy as _

from . import Menu, MenuGroup, MenuItem, get_model_item

ORGANIZATION_MENU = Menu(
    label=_('Organization'),
    icon_class='mdi mdi-domain',
    groups=(
        MenuGroup(
            label=_('Sites'),
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
                get_model_item('organization', 'assetholder', _('Asset Holders')),
                MenuItem(
                    link='organization:assetholderassignment_list',
                    link_text=_('Assignments'),
                    permissions=['organization.view_assetholderassignment'],
                    buttons=(),
                ),
            ),
        ),
        MenuGroup(
            label=_('Contacts'),
            items=(
                get_model_item('organization', 'contact', _('Contacts')),
                get_model_item('organization', 'contactrole', _('Contact Roles')),
            ),
        ),
    ),
)

ASSETS_MENU = Menu(
    label=_('Assets'),
    icon_class='mdi mdi-server',
    groups=(
        MenuGroup(
            label=_('Asset Inventory'),
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
                get_model_item('assets', 'componenttype', _('Component Types')),
                get_model_item('assets', 'componentinstance', _('Component Instances')),
            ),
        ),
        MenuGroup(
            label=_('Peripherals & Consumables'),
            items=(
                get_model_item('assets', 'accessory', _('Accessories')),
                get_model_item('assets', 'consumable', _('Consumables')),
            ),
        ),
        MenuGroup(
            label=_('Lifecycle'),
            items=(
                get_model_item('assets', 'kit', _('Kits')),
                get_model_item('assets', 'depreciation', _('Depreciation')),
            ),
        ),
        MenuGroup(
            label=_('Procurement'),
            items=(
                get_model_item('assets', 'supplier', _('Suppliers')),
                get_model_item('assets', 'category', _('Categories')),
                get_model_item('assets', 'assetrequest', _('Asset Requests')),
            ),
        ),
    ),
)

SOFTWARE_MENU = Menu(
    label=_('Software & Licenses'),
    icon_class='mdi mdi-license',
    groups=(
        MenuGroup(
            label=_('Software'),
            items=(
                get_model_item('software', 'software', _('Software')),
            ),
        ),
        MenuGroup(
            label=_('Licenses'),
            items=(
                get_model_item('licenses', 'license', _('Licenses')),
            ),
        ),
        MenuGroup(
            label=_('Subscriptions'),
            items=(
                get_model_item('subscriptions', 'subscription', _('Subscriptions')),
                get_model_item('subscriptions', 'provider', _('Providers')),
            ),
        ),
    ),
)

EXTRAS_MENU = Menu(
    label=_('Extras'),
    icon_class='mdi mdi-puzzle',
    groups=(
        MenuGroup(
            label=_('Tags'),
            items=(
                get_model_item('extras', 'tag', _('Tags')),
            ),
        ),
        MenuGroup(
            label=_('Custom Schemas'),
            items=(
                get_model_item('assets', 'customfield', _('Custom Fields')),
                get_model_item('assets', 'customfieldset', _('Custom Fieldsets')),
            ),
        ),
    ),
)

OPERATIONS_MENU = Menu(
    label=_('Operations'),
    icon_class='mdi mdi-cogs',
    groups=(
        MenuGroup(
            label=_('Logging'),
            items=(
                MenuItem(
                    link='objectchange_list',
                    link_text=_('Changelog'),
                    permissions=['core.view_objectchange'],
                    buttons=(),
                ),
            ),
        ),
        MenuGroup(
            label=_('Maintenance'),
            items=(
                get_model_item('assets', 'assetmaintenance', _('Maintenances')),
            ),
        ),
    ),
)

ADMIN_MENU = Menu(
    label=_('Admin'),
    icon_class='mdi mdi-account-multiple',
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
        SOFTWARE_MENU,
        EXTRAS_MENU,
        OPERATIONS_MENU,
        ADMIN_MENU,
    ]
