from functools import cache

from django.utils.translation import gettext_lazy as _

from . import Menu, MenuGroup, MenuItem, MenuItemButton, get_model_item

def _build_org_menu():
    from django.conf import settings
    extended = getattr(settings, 'ITAMBOX_ENABLE_EXTENDED_ORG_HIERARCHY', False)
    sites_items = [get_model_item('organization', 'site', _('Sites'))]
    if extended:
        sites_items += [
            get_model_item('organization', 'region', _('Regions')),
            get_model_item('organization', 'sitegroup', _('Site Groups')),
        ]
    sites_items.append(get_model_item('organization', 'location', _('Locations')))
    return Menu(
        label=_('Organization'),
        icon_class='mdi mdi-domain',
        groups=(
            MenuGroup(
                label=_('Sites & Locations'),
                items=tuple(sites_items),
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
                            title='Add Component',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['inventory.add_component'],
                        ),
                        MenuItemButton(
                            link='/import/inventory/component/',
                            title='Import Components',
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
                            title='Add Accessory',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['inventory.add_accessory'],
                        ),
                        MenuItemButton(
                            link='inventory:accessory_import',
                            title='Import Accessories',
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
                            title='Add Consumable',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['inventory.add_consumable'],
                        ),
                        MenuItemButton(
                            link='inventory:consumable_import',
                            title='Import Consumables',
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
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['procurement.add_purchaseorder'],
                        ),
                        MenuItemButton(
                            link='/import/procurement/purchaseorder/',
                            title='Import',
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
            label=_('Finance'),
            items=(
                get_model_item('assets', 'depreciation', _('Depreciation')),
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
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['compliance.add_auditsession'],
                        ),
                        MenuItemButton(
                            link='/import/compliance/auditsession/',
                            title='Import',
                            icon_class='mdi mdi-upload',
                            permissions=['compliance.add_auditsession'],
                            color='outline text-success',
                        ),
                    ),
                ),
                get_model_item('assets', 'assetmaintenance', _('Maintenances')),
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
                    link='alertlog_list',
                    link_text=_('Alerts Center'),
                    permissions=['extras.view_alertlog'],
                    buttons=(),
                ),
                MenuItem(
                    link='alertrule_list',
                    link_text=_('Alert Rules'),
                    permissions=['extras.view_alertrule'],
                    buttons=(
                        MenuItemButton(
                            link='alertrule_add',
                            title='Add',
                            icon_class='mdi mdi-plus-thick',
                            permissions=['extras.add_alertrule'],
                        ),
                    ),
                ),
                MenuItem(
                    link='notificationchannel_list',
                    link_text=_('Notification Channels'),
                    permissions=['extras.view_notificationchannel'],
                    buttons=(
                        MenuItemButton(
                            link='notificationchannel_add',
                            title='Add',
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
                    link='scheduledreport_list',
                    link_text=_('Scheduled Reports'),
                    permissions=['extras.view_scheduledreport'],
                    buttons=(),
                ),
                MenuItem(
                    link='reporttemplate_list',
                    link_text=_('Report Templates'),
                    permissions=['extras.view_reporttemplate'],
                    buttons=(),
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
        MenuGroup(
            label=_('Templates'),
            items=(
                MenuItem(
                    link='export_template_list',
                    link_text=_('Export Templates'),
                    permissions=['extras.view_exporttemplate'],
                    buttons=(),
                ),
                MenuItem(
                    link='labeltemplate_list',
                    link_text=_('Label Templates'),
                    permissions=['extras.view_labeltemplate'],
                    buttons=(),
                ),
            ),
        ),
        MenuGroup(
            label=_('Automation'),
            beta=True,
            items=(
                MenuItem(
                    link='webhookendpoint_list',
                    link_text=_('Webhook Endpoints'),
                    permissions=['extras.view_webhookendpoint'],
                    buttons=(),
                ),
                MenuItem(
                    link='eventrule_list',
                    link_text=_('Event Rules'),
                    permissions=['extras.view_eventrule'],
                    buttons=(),
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
                            title='Add',
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
                            title='Add',
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
                            title='Add',
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
        _build_org_menu(),
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
                label='Plugins',
                icon_class='mdi mdi-puzzle',
                groups=[MenuGroup(label='Plugin List', items=[])]
            )
            menus.append(plugins_menu)

        plugins_menu.groups[0].items = list(plugins_menu.groups[0].items) + standalone_items

    return menus
