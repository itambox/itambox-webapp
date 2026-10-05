"""Per-model data-transfer policy: one explicit declaration drives import and export.

Every installed model is declared here, in both directions, and nothing is
inferred. A model that is missing from ``DECLARATIONS`` is denied for import and
export alike (fail closed); ``core/tests/test_data_transfer_policy.py`` fails the
build for any such model, so adding a model forces an explicit decision.

``import_`` means "may be written through the generic CSV/YAML importer". It is
only honoured together with a curated, registered ``BulkImportForm`` (see
``core.importers.bulk_forms``); the contract test keeps the two in lockstep.
Lifecycle rows that a domain service owns (assignments, custody receipts and
signing sessions, disposals, seat assignments, reservations, inventory
assignments) are never importable: a raw-field write could put them into states
their service never produces.

``export`` drives the export menu on list headers and the Export Template
content type picker. The flags below deliberately reproduce the export
visibility that existed before this policy (every model except the former
``IMPORT_EXCLUDED_MODELS`` set); this change moves the declaration, it does not
review which models are safe to export. Tightening them per model, and wiring
the generic export route to this declaration, is issue #585.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DataTransferPolicy:
    import_: bool = False
    export: bool = False


DENIED = DataTransferPolicy()


def _decl(*, import_, export):
    return DataTransferPolicy(import_=import_, export=export)


DECLARATIONS = {
    # admin
    "admin.logentry": _decl(import_=False, export=True),
    # assets
    "assets.asset": _decl(import_=True, export=True),
    "assets.assetassignment": _decl(import_=False, export=True),
    "assets.assetdisposal": _decl(import_=False, export=True),
    "assets.assetmaintenance": _decl(import_=False, export=True),
    "assets.assetrequest": _decl(import_=False, export=True),
    "assets.assetreservation": _decl(import_=False, export=True),
    "assets.assetrole": _decl(import_=False, export=True),
    "assets.assettagsequence": _decl(import_=False, export=True),
    "assets.assettype": _decl(import_=True, export=True),
    "assets.assettypefieldset": _decl(import_=False, export=True),
    "assets.assettypeimagestage": _decl(import_=False, export=True),
    "assets.category": _decl(import_=False, export=True),
    "assets.categorydefaultfieldset": _decl(import_=False, export=True),
    "assets.depreciation": _decl(import_=False, export=True),
    "assets.manufacturer": _decl(import_=True, export=True),
    "assets.repairepisode": _decl(import_=False, export=True),
    "assets.statuslabel": _decl(import_=False, export=True),
    "assets.supplier": _decl(import_=False, export=True),
    "assets.warranty": _decl(import_=False, export=True),
    # auth
    "auth.group": _decl(import_=False, export=True),
    "auth.permission": _decl(import_=False, export=True),
    # compliance
    "compliance.assetaudit": _decl(import_=False, export=True),
    "compliance.auditsession": _decl(import_=False, export=True),
    "compliance.custodyhandoffdelivery": _decl(import_=False, export=True),
    "compliance.custodyreceipt": _decl(import_=False, export=True),
    "compliance.custodysigningsession": _decl(import_=False, export=True),
    "compliance.custodytemplate": _decl(import_=False, export=True),
    # contenttypes
    "contenttypes.contenttype": _decl(import_=False, export=True),
    # core
    "core.emailsettings": _decl(import_=False, export=True),
    "core.job": _decl(import_=False, export=False),
    "core.notification": _decl(import_=False, export=False),
    "core.objectchange": _decl(import_=False, export=False),
    "core.recyclebin": _decl(import_=False, export=True),
    # django_q
    "django_q.failure": _decl(import_=False, export=True),
    "django_q.ormq": _decl(import_=False, export=True),
    "django_q.schedule": _decl(import_=False, export=True),
    "django_q.success": _decl(import_=False, export=True),
    "django_q.task": _decl(import_=False, export=True),
    # extras
    "extras.alertlog": _decl(import_=False, export=False),
    "extras.alertrule": _decl(import_=False, export=False),
    "extras.bookmark": _decl(import_=False, export=True),
    "extras.customfield": _decl(import_=False, export=True),
    "extras.customfieldchoice": _decl(import_=False, export=True),
    "extras.customfieldchoiceset": _decl(import_=False, export=True),
    "extras.customfieldset": _decl(import_=False, export=True),
    "extras.customfieldsetfield": _decl(import_=False, export=True),
    "extras.dashboard": _decl(import_=False, export=False),
    "extras.event": _decl(import_=False, export=False),
    "extras.eventrule": _decl(import_=False, export=False),
    "extras.exporttemplate": _decl(import_=False, export=True),
    "extras.fileattachment": _decl(import_=False, export=True),
    "extras.imageattachment": _decl(import_=False, export=True),
    "extras.journalentry": _decl(import_=False, export=False),
    "extras.labeltemplate": _decl(import_=False, export=True),
    "extras.notificationchannel": _decl(import_=False, export=False),
    "extras.objectwatch": _decl(import_=False, export=True),
    "extras.reportgenerationarchive": _decl(import_=False, export=True),
    "extras.reporttemplate": _decl(import_=False, export=False),
    "extras.savedfilter": _decl(import_=False, export=True),
    "extras.scheduledreport": _decl(import_=False, export=False),
    "extras.scheduledreportfire": _decl(import_=False, export=True),
    "extras.scheduledreportscopeauthorization": _decl(import_=False, export=True),
    "extras.specificationlibrary": _decl(import_=False, export=True),
    "extras.specificationlibrarylegacyprovenance": _decl(import_=False, export=True),
    "extras.specificationlibraryrelease": _decl(import_=False, export=True),
    "extras.tag": _decl(import_=False, export=True),
    "extras.webhookdelivery": _decl(import_=False, export=True),
    "extras.webhookendpoint": _decl(import_=False, export=False),
    # inventory
    "inventory.accessory": _decl(import_=True, export=True),
    "inventory.accessoryassignment": _decl(import_=False, export=True),
    "inventory.accessorystock": _decl(import_=False, export=True),
    "inventory.component": _decl(import_=False, export=True),
    "inventory.componentallocation": _decl(import_=False, export=True),
    "inventory.componentstock": _decl(import_=False, export=True),
    "inventory.consumable": _decl(import_=True, export=True),
    "inventory.consumableassignment": _decl(import_=False, export=True),
    "inventory.consumablestock": _decl(import_=False, export=True),
    "inventory.kit": _decl(import_=False, export=True),
    "inventory.kititem": _decl(import_=False, export=True),
    # licenses
    "licenses.license": _decl(import_=True, export=True),
    "licenses.licenseseatassignment": _decl(import_=False, export=True),
    # organization
    "organization.assetholder": _decl(import_=True, export=True),
    "organization.contact": _decl(import_=False, export=True),
    "organization.contactassignment": _decl(import_=False, export=True),
    "organization.contactrole": _decl(import_=False, export=True),
    "organization.costcenter": _decl(import_=False, export=True),
    "organization.location": _decl(import_=True, export=True),
    "organization.membership": _decl(import_=False, export=False),
    "organization.region": _decl(import_=False, export=True),
    "organization.role": _decl(import_=False, export=False),
    "organization.rolegrant": _decl(import_=False, export=False),
    "organization.rolegrantscope": _decl(import_=False, export=False),
    "organization.site": _decl(import_=False, export=True),
    "organization.sitegroup": _decl(import_=False, export=True),
    "organization.tenant": _decl(import_=False, export=True),
    "organization.tenantgroup": _decl(import_=False, export=True),
    "organization.tenantresourcegrant": _decl(import_=False, export=False),
    "organization.tenantresourcegrantexpiryrevocation": _decl(import_=False, export=True),
    "organization.tenantresourcegrantexpiryrun": _decl(import_=False, export=True),
    # otp_static
    "otp_static.staticdevice": _decl(import_=False, export=True),
    "otp_static.statictoken": _decl(import_=False, export=True),
    # otp_totp
    "otp_totp.totpdevice": _decl(import_=False, export=True),
    # procurement
    "procurement.contract": _decl(import_=False, export=True),
    "procurement.fulfillmentlink": _decl(import_=False, export=True),
    "procurement.purchaseorder": _decl(import_=False, export=True),
    "procurement.purchaseorderline": _decl(import_=False, export=True),
    # sessions
    "sessions.session": _decl(import_=False, export=True),
    # software
    "software.installedsoftware": _decl(import_=False, export=True),
    "software.software": _decl(import_=False, export=True),
    # subscriptions
    "subscriptions.subscription": _decl(import_=True, export=True),
    "subscriptions.subscriptionassignment": _decl(import_=False, export=True),
    # users
    "users.groupmembership": _decl(import_=False, export=False),
    "users.oidcidentity": _decl(import_=False, export=True),
    "users.token": _decl(import_=False, export=False),
    "users.user": _decl(import_=False, export=False),
    "users.usergroup": _decl(import_=False, export=False),
    "users.userpreference": _decl(import_=False, export=True),
}


def policy_for_label(label):
    """Declared policy for ``app_label.model_name``; undeclared models are denied."""
    return DECLARATIONS.get(label, DENIED)


def policy_for(model):
    if model is None:
        return DENIED
    return policy_for_label(model._meta.label_lower)
