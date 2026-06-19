from functools import cache

from django.utils.translation import gettext_lazy as _

from . import Menu, MenuGroup, MenuItem, MenuItemButton, get_model_item

ORG_MENU = Menu(
    label=_('Organization'),
    icon_class='mdi mdi-domain',
    groups=(
        MenuGroup(
            label=_('Sites & Locations'),
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
            label=_('Hardware'),
            items=(
                get_model_item('assets', 'asset', _('Assets')),
            ),
        ),
        MenuGroup(
            label=_('Bulk Actions'),
            items=(
                MenuItem(
                    link='assets:asset_bulk_checkin_scan',
                    link_text=_('Bulk Check-in'),
                    permissions=['assets.change_asset'],
                    buttons=(),
                ),
                MenuItem(
                    link='assets:asset_bulk_dispose_scan',
                    link_text=_('Bulk Disposal'),
                    permissions=['assets.add_assetdisposal'],
                    buttons=(),
                ),
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
        MenuGroup(
            label=_('Classification'),
            items=(
                get_model_item('assets', 'assetrole', _('Asset Roles')),
                get_model_item('assets', 'statuslabel', _('Status Labels')),
            ),
        ),
        MenuGroup(
            label=_('Lifecycle'),
            items=(
                get_model_item('assets', 'warranty', _('Warranties')),
                get_model_item('assets', 'assetmaintenance', _('Maintenances')),
                get_model_item('assets', 'assetreservation', _('Reservations')),
                get_model_item('assets', 'assetdisposal', _('Disposals')),
            ),
        ),
    ),
)

INVENTORY_MENU = Menu(
    label=_('Inventory & Stock'),
    icon_class='mdi mdi-package-variant-closed',
    groups=(
        MenuGroup(
            label=_('Stock'),
            items=(
                MenuItem(
                    link='inventory:component_list',
                    link_text=_('Components'),
                    permissions=['inventory.view_component'],
                    buttons=(
                        MenuItemButton(
                            link='inventory:component_create',
                            title=_('Add Component'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['inventory.add_component'],
                        ),
                        MenuItemButton(
                            link='/import/inventory/component/',
                            title=_('Import Components'),
                            icon_class='mdi mdi-upload',
                            permissions=['inventory.add_component'],
                            color='outline text-success',
                        ),
                    ),
                ),
                MenuItem(
                    link='inventory:accessory_list',
                    link_text=_('Accessories'),
                    permissions=['inventory.view_accessory'],
                    buttons=(
                        MenuItemButton(
                            link='inventory:accessory_create',
                            title=_('Add Accessory'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['inventory.add_accessory'],
                        ),
                        MenuItemButton(
                            link='/import/inventory/accessory/',
                            title=_('Import Accessories'),
                            icon_class='mdi mdi-upload',
                            permissions=['inventory.add_accessory'],
                            color='outline text-success',
                        ),
                    ),
                ),
                MenuItem(
                    link='inventory:consumable_list',
                    link_text=_('Consumables'),
                    permissions=['inventory.view_consumable'],
                    buttons=(
                        MenuItemButton(
                            link='inventory:consumable_create',
                            title=_('Add Consumable'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['inventory.add_consumable'],
                        ),
                        MenuItemButton(
                            link='/import/inventory/consumable/',
                            title=_('Import Consumables'),
                            icon_class='mdi mdi-upload',
                            permissions=['inventory.add_consumable'],
                            color='outline text-success',
                        ),
                    ),
                ),
            ),
        ),
        MenuGroup(
            label=_('Bundles'),
            items=(
                get_model_item('inventory', 'kit', _('Kits')),
            ),
        ),
    ),
)

SOFTWARE_MENU = Menu(
    label=_('Software & Licensing'),
    icon_class='mdi mdi-file-certificate',
    groups=(
        MenuGroup(
            label=_('Licensing'),
            items=(
                get_model_item('software', 'software', _('Software')),
                get_model_item('licenses', 'license', _('Licenses')),
            ),
        ),
        MenuGroup(
            label=_('SaaS'),
            items=(
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
            label=_('Procurement'),
            beta=True,
            items=(
                MenuItem(
                    link='procurement:purchaseorder_list',
                    link_text=_('Purchase Orders'),
                    permissions=['procurement.view_purchaseorder'],
                    buttons=(
                        MenuItemButton(
                            link='procurement:purchaseorder_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['procurement.add_purchaseorder'],
                        ),
                        MenuItemButton(
                            link='/import/procurement/purchaseorder/',
                            title=_('Import'),
                            icon_class='mdi mdi-upload',
                            permissions=['procurement.add_purchaseorder'],
                            color='outline text-success',
                        ),
                    ),
                ),
                MenuItem(
                    link='assets:request_list',
                    link_text=_('Requests'),
                    permissions=['assets.view_assetrequest'],
                    buttons=(
                        MenuItemButton(
                            link='assets:request_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['assets.add_assetrequest'],
                        ),
                        MenuItemButton(
                            link='/import/assets/assetrequest/',
                            title=_('Import'),
                            icon_class='mdi mdi-upload',
                            permissions=['assets.add_assetrequest'],
                            color='outline text-success',
                        ),
                    ),
                ),
                get_model_item('assets', 'supplier', _('Suppliers')),
                get_model_item('procurement', 'contract', _('Contracts')),
            ),
        ),
        MenuGroup(
            label=_('Finance'),
            items=(
                get_model_item('assets', 'depreciation', _('Depreciation')),
                get_model_item('organization', 'costcenter', _('Cost Centers')),
            ),
        ),
        MenuGroup(
            label=_('Compliance'),
            items=(
                MenuItem(
                    link='compliance:auditsession_list',
                    link_text=_('Audit Sessions'),
                    permissions=['compliance.view_auditsession'],
                    buttons=(
                        MenuItemButton(
                            link='compliance:auditsession_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['compliance.add_auditsession'],
                        ),
                        MenuItemButton(
                            link='/import/compliance/auditsession/',
                            title=_('Import'),
                            icon_class='mdi mdi-upload',
                            permissions=['compliance.add_auditsession'],
                            color='outline text-success',
                        ),
                    ),
                ),
                get_model_item('compliance', 'custodytemplate', _('Custody Templates')),
            ),
        ),
    ),
)

MONITORING_MENU = Menu(
    label=_('Monitoring & Reporting'),
    icon_class='mdi mdi-bell-alert-outline',
    groups=(
        MenuGroup(
            label=_('Alerting'),
            items=(
                MenuItem(
                    link='extras:alertlog_list',
                    link_text=_('Alerts Center'),
                    permissions=['extras.view_alertlog'],
                    buttons=(),
                ),
                MenuItem(
                    link='extras:alertrule_list',
                    link_text=_('Alert Rules'),
                    permissions=['extras.view_alertrule'],
                    buttons=(
                        MenuItemButton(
                            link='extras:alertrule_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['extras.add_alertrule'],
                        ),
                    ),
                ),
                MenuItem(
                    link='extras:notificationchannel_list',
                    link_text=_('Notification Channels'),
                    permissions=['extras.view_notificationchannel'],
                    buttons=(
                        MenuItemButton(
                            link='extras:notificationchannel_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['extras.add_notificationchannel'],
                        ),
                    ),
                ),
            ),
        ),
        MenuGroup(
            label=_('Reporting'),
            beta=True,
            items=(
                MenuItem(
                    link='extras:scheduledreport_list',
                    link_text=_('Scheduled Reports'),
                    permissions=['extras.view_scheduledreport'],
                    buttons=(
                        MenuItemButton(
                            link='extras:scheduledreport_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['extras.add_scheduledreport'],
                        ),
                    ),
                ),
                MenuItem(
                    link='extras:reporttemplate_list',
                    link_text=_('Report Templates'),
                    permissions=['extras.view_reporttemplate'],
                    buttons=(
                        MenuItemButton(
                            link='extras:reporttemplate_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['extras.add_reporttemplate'],
                        ),
                    ),
                ),
            ),
        ),
        MenuGroup(
            label=_('Activity'),
            items=(
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
            label=_('Data Model'),
            items=(
                get_model_item('extras', 'customfield', _('Custom Fields')),
                get_model_item('extras', 'customfieldset', _('Custom Fieldsets')),
                MenuItem(
                    link='extras:configcontext_list',
                    link_text=_('Config Contexts'),
                    permissions=['extras.view_configcontext'],
                    buttons=(),
                ),
                get_model_item('extras', 'savedfilter', _('Saved Filters')),
            ),
        ),
        MenuGroup(
            label=_('Tagging'),
            items=(
                get_model_item('extras', 'tag', _('Tags')),
                MenuItem(
                    link='assets:assettagsequence_list',
                    link_text=_('Asset Tag Sequences'),
                    permissions=['assets.view_assettagsequence'],
                    buttons=(
                        MenuItemButton(
                            link='assets:assettagsequence_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['assets.add_assettagsequence'],
                        ),
                        MenuItemButton(
                            link='/import/assets/assettagsequence/',
                            title=_('Import'),
                            icon_class='mdi mdi-upload',
                            permissions=['assets.add_assettagsequence'],
                            color='outline text-success',
                        ),
                    ),
                ),
            ),
        ),
        MenuGroup(
            label=_('Templates'),
            items=(
                MenuItem(
                    link='extras:exporttemplate_list',
                    link_text=_('Export Templates'),
                    permissions=['extras.view_exporttemplate'],
                    buttons=(
                        MenuItemButton(
                            link='extras:exporttemplate_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['extras.add_exporttemplate'],
                        ),
                    ),
                ),
                MenuItem(
                    link='extras:labeltemplate_list',
                    link_text=_('Label Templates'),
                    permissions=['extras.view_labeltemplate'],
                    buttons=(
                        MenuItemButton(
                            link='extras:labeltemplate_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['extras.add_labeltemplate'],
                        ),
                    ),
                ),
            ),
        ),
        MenuGroup(
            label=_('Automation'),
            beta=True,
            items=(
                MenuItem(
                    link='extras:webhookendpoint_list',
                    link_text=_('Webhook Endpoints'),
                    permissions=['extras.view_webhookendpoint'],
                    buttons=(
                        MenuItemButton(
                            link='extras:webhookendpoint_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['extras.add_webhookendpoint'],
                        ),
                    ),
                ),
                MenuItem(
                    link='extras:eventrule_list',
                    link_text=_('Event Rules'),
                    permissions=['extras.view_eventrule'],
                    buttons=(
                        MenuItemButton(
                            link='extras:eventrule_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['extras.add_eventrule'],
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
            label=_('Access Control'),
            items=(
                MenuItem(
                    link='users:user_list',
                    link_text=_('Users'),
                    permissions=['auth.view_user'],
                    buttons=(
                        MenuItemButton(
                            link='users:user_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['auth.add_user'],
                        ),
                    ),
                ),
                MenuItem(
                    link='organization:tenantrole_list',
                    link_text=_('Roles'),
                    permissions=['organization.view_tenantrole'],
                    buttons=(
                        MenuItemButton(
                            link='organization:tenantrole_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['organization.add_tenantrole'],
                        ),
                    ),
                ),
                MenuItem(
                    link='organization:tenantmembership_list',
                    link_text=_('Tenant Assignments'),
                    permissions=['organization.view_tenantmembership'],
                    buttons=(
                        MenuItemButton(
                            link='organization:tenantmembership_create',
                            title=_('Add'),
                            icon_class='mdi mdi-plus-thick',
                            permissions=['organization.add_tenantmembership'],
                        ),
                    ),
                ),
            ),
        ),
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
    from itambox.registry import registry
    from . import Menu, MenuGroup, MenuItem, MenuItemButton

    menus = [
        ORG_MENU,
        ASSETS_MENU,
        INVENTORY_MENU,
        SOFTWARE_MENU,
        OPERATIONS_MENU,
        MONITORING_MENU,
        EXTRAS_MENU,
        ADMIN_MENU,
    ]

    # Dynamically append registered PluginNavigationMenu classes
    for menu_cls in registry.get_plugin_menus():
        if isinstance(menu_cls, type):
            menu_instance = menu_cls()
        else:
            menu_instance = menu_cls

        groups = []
        for group in getattr(menu_instance, 'groups', []):
            items = []
            for item in getattr(group, 'items', []):
                if isinstance(item, type):
                    item_inst = item()
                else:
                    item_inst = item

                buttons = []
                for btn in getattr(item_inst, 'buttons', []):
                    if isinstance(btn, type):
                        btn_inst = btn()
                    else:
                        btn_inst = btn
                    buttons.append(MenuItemButton(
                        link=getattr(btn_inst, 'link', None),
                        title=getattr(btn_inst, 'title', None),
                        icon_class=getattr(btn_inst, 'icon_class', None),
                        permissions=getattr(btn_inst, 'permissions', ()),
                        color=getattr(btn_inst, 'color', None),
                    ))

                items.append(MenuItem(
                    link=getattr(item_inst, 'link', None),
                    link_text=getattr(item_inst, 'link_text', None),
                    permissions=getattr(item_inst, 'permissions', ()),
                    auth_required=getattr(item_inst, 'auth_required', False),
                    staff_only=getattr(item_inst, 'staff_only', False),
                    buttons=buttons,
                ))
            groups.append(MenuGroup(
                label=getattr(group, 'label', ''),
                items=items
            ))

        menus.append(Menu(
            label=getattr(menu_instance, 'label', ''),
            icon_class=getattr(menu_instance, 'icon_class', 'mdi mdi-puzzle'),
            groups=groups
        ))

    # Dynamically append registered PluginNavigationItem classes
    standalone_items = []
    for item_cls in registry.get_plugin_menu_items():
        if isinstance(item_cls, type):
            item_inst = item_cls()
        else:
            item_inst = item_cls

        buttons = []
        for btn in getattr(item_inst, 'buttons', []):
            if isinstance(btn, type):
                btn_inst = btn()
            else:
                btn_inst = btn
            buttons.append(MenuItemButton(
                link=getattr(btn_inst, 'link', None),
                title=getattr(btn_inst, 'title', None),
                icon_class=getattr(btn_inst, 'icon_class', None),
                permissions=getattr(btn_inst, 'permissions', ()),
                color=getattr(btn_inst, 'color', None),
            ))

        standalone_items.append(MenuItem(
            link=getattr(item_inst, 'link', None),
            link_text=getattr(item_inst, 'link_text', None),
            permissions=getattr(item_inst, 'permissions', ()),
            auth_required=getattr(item_inst, 'auth_required', False),
            staff_only=getattr(item_inst, 'staff_only', False),
            buttons=buttons,
        ))

    if standalone_items:
        plugins_menu = None
        for m in menus:
            if m.label == 'Plugins':
                plugins_menu = m
                break
        if not plugins_menu:
            plugins_menu = Menu(
                label=_('Plugins'),
                icon_class='mdi mdi-puzzle',
                groups=[MenuGroup(label=_('Plugin List'), items=[])]
            )
            menus.append(plugins_menu)

        plugins_menu.groups[0].items = list(plugins_menu.groups[0].items) + standalone_items

    return menus
