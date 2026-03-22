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
                    link='components:component_list',
                    link_text=_('Components'),
                    permissions=['components.view_component'],
                    buttons=(
                        MenuItemButton(
                            link='components:component_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['components.add_component'],
                        ),
                        MenuItemButton(
                            link='/import/components/component/',
                            title='Import',
                            icon_class='mdi mdi-upload',
                            permissions=['components.add_component'],
                            color='outline text-success',
                        ),
                    ),
                ),
                MenuItem(
                    link='components:componentstock_list',
                    link_text=_('Component Stocks'),
                    permissions=['components.view_componentstock'],
                    buttons=(
                        MenuItemButton(
                            link='components:componentstock_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['components.add_componentstock'],
                        ),
                        MenuItemButton(
                            link='/import/components/componentstock/',
                            title='Import',
                            icon_class='mdi mdi-upload',
                            permissions=['components.add_componentstock'],
                            color='outline text-success',
                        ),
                    ),
                ),
                MenuItem(
                    link='components:componentallocation_list',
                    link_text=_('Component Allocations'),
                    permissions=['components.view_componentallocation'],
                    buttons=(
                        MenuItemButton(
                            link='components:componentallocation_create',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['components.add_componentallocation'],
                        ),
                        MenuItemButton(
                            link='/import/components/componentallocation/',
                            title='Import',
                            icon_class='mdi mdi-upload',
                            permissions=['components.add_componentallocation'],
                            color='outline text-success',
                        ),
                    ),
                ),
            ),
        ),
        MenuGroup(
            label=_('Accessory Inventory'),
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
                        MenuItemButton(
                            link='/import/inventory/accessorystock/',
                            title='Import',
                            icon_class='mdi mdi-upload',
                            permissions=['inventory.add_accessorystock'],
                            color='outline text-success',
                        ),
                    ),
                ),
                MenuItem(
                    link='inventory:accessoryassignment_list',
                    link_text=_('Accessory Assignments'),
                    permissions=['inventory.view_accessoryassignment'],
                    buttons=(),
                ),
            ),
        ),
        MenuGroup(
            label=_('Consumable Inventory'),
            items=(
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
                        MenuItemButton(
                            link='/import/inventory/consumablestock/',
                            title='Import',
                            icon_class='mdi mdi-upload',
                            permissions=['inventory.add_consumablestock'],
                            color='outline text-success',
                        ),
                    ),
                ),
                MenuItem(
                    link='inventory:consumableassignment_list',
                    link_text=_('Consumable Consumptions'),
                    permissions=['inventory.view_consumableassignment'],
                    buttons=(),
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
                        MenuItemButton(
                            link='/import/assets/auditsession/',
                            title='Import',
                            icon_class='mdi mdi-upload',
                            permissions=['assets.add_auditsession'],
                            color='outline text-success',
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
                        MenuItemButton(
                            link='/import/assets/assetrequest/',
                            title='Import',
                            icon_class='mdi mdi-upload',
                            permissions=['assets.add_assetrequest'],
                            color='outline text-success',
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
                    link='alertlog_list',
                    link_text=_('Alerts Center'),
                    permissions=['core.view_alertlog'],
                    buttons=(),
                ),
                MenuItem(
                    link='alertrule_list',
                    link_text=_('Alert Rules'),
                    permissions=['core.view_alertrule'],
                    buttons=(
                        MenuItemButton(
                            link='alertrule_add',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['core.add_alertrule'],
                        ),
                    ),
                ),
                MenuItem(
                    link='notificationchannel_list',
                    link_text=_('Notification Channels'),
                    permissions=['core.view_notificationchannel'],
                    buttons=(
                        MenuItemButton(
                            link='notificationchannel_add',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['core.add_notificationchannel'],
                        ),
                    ),
                ),
                MenuItem(
                    link='scheduledreport_list',
                    link_text=_('Scheduled Reports'),
                    permissions=['core.view_scheduledreport'],
                    buttons=(),
                ),
                MenuItem(
                    link='objectchange_list',
                    link_text=_('Changelog'),
                    permissions=['core.view_objectchange'],
                    buttons=(),
                ),
                MenuItem(
                    link='job_list',
                    link_text=_('Background Jobs'),
                    permissions=['core.view_job'],
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
                        MenuItemButton(
                            link='/import/assets/assettagsequence/',
                            title='Import',
                            icon_class='mdi mdi-upload',
                            permissions=['assets.add_assettagsequence'],
                            color='outline text-success',
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
        MenuGroup(
            label=_('Access Control'),
            items=(
                MenuItem(
                    link='permissiongroup_list',
                    link_text=_('Permission Groups'),
                    permissions=['core.view_permissiongroup'],
                    buttons=(
                        MenuItemButton(
                            link='permissiongroup_add',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['core.add_permissiongroup'],
                        ),
                    ),
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
