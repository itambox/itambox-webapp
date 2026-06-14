"""SAM license reconciliation helpers.

Reconciliation answers: "For a given software product, how many seats are we
entitled to versus how many installs exist within the active tenant?"

Public API
----------
reconcile_software(software) -> dict
    Compute the compliance posture for one ``Software`` catalogue entry.

reconcile_tenant_licensing() -> list[dict]
    Iterate the tenant's visible software catalogue and return a compliance
    dict for each entry that has at least one install or at least one license.

Dict shape (both functions)
---------------------------
{
    "software_id": int,
    "software_name": str,
    "installed_count": int,       # InstalledSoftware rows (any version)
    "entitled_seats": int,        # sum of License.seats (active, non-deleted)
    "delta": int,                 # entitled_seats - installed_count
                                  #   positive  → spare capacity
                                  #   negative  → over-deployed
    "compliant": bool,            # installed_count <= entitled_seats
    "status": str,                # 'compliant' | 'over_deployed' | 'unlicensed'
}

Tenant scoping
--------------
Both functions rely entirely on the model managers, which apply
``filter_by_tenant()`` automatically based on the active tenant stored in the
``_current_tenant`` ContextVar.  No explicit tenant filtering is needed here.

* ``Software.objects`` uses ``TenantScopingSoftDeleteManager`` with
  ``allow_global_tenant = True``, so global (null-tenant) entries that are
  visible to all tenants are included.

* ``InstalledSoftware.objects`` uses ``TenantScopingManager`` with
  ``tenant_lookup = 'asset__tenant'``, scoping installs through the asset.

* ``License.objects`` uses ``SoftDeleteLicenseManager`` (a
  ``TenantScopingSoftDeleteManager`` subclass), scoping by the license's own
  ``tenant`` field (with ``allow_global_tenant`` false on License, so only the
  active tenant's licenses are counted).

Result: a tenant A call cannot see tenant B's installs or entitlements.
"""

from django.db.models import Sum, Count, Q

from software.models import Software, InstalledSoftware
from licenses.models import License

# ─────────────────────────────────────────────────────────────────────────────
# Status constants
# ─────────────────────────────────────────────────────────────────────────────

STATUS_COMPLIANT = 'compliant'
STATUS_OVER_DEPLOYED = 'over_deployed'
STATUS_UNLICENSED = 'unlicensed'


def _compute_status(installed_count: int, entitled_seats: int) -> str:
    """Return a status string given raw install and seat counts."""
    if entitled_seats == 0 and installed_count > 0:
        return STATUS_UNLICENSED
    if installed_count > entitled_seats:
        return STATUS_OVER_DEPLOYED
    return STATUS_COMPLIANT


def _build_result(software: Software, installed_count: int, entitled_seats: int) -> dict:
    """Assemble the reconciliation dict for one software entry."""
    delta = entitled_seats - installed_count
    compliant = installed_count <= entitled_seats
    return {
        'software_id': software.pk,
        'software_name': str(software),
        'installed_count': installed_count,
        'entitled_seats': entitled_seats,
        'delta': delta,
        'compliant': compliant,
        'status': _compute_status(installed_count, entitled_seats),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def reconcile_software(software: Software) -> dict:
    """Return a compliance dict for a single ``Software`` entry.

    Both queries are scoped by the model managers (active tenant + soft-delete
    exclusion), so callers never need to pass a tenant explicitly.

    Args:
        software: A ``Software`` instance (must be visible to the current tenant).

    Returns:
        A reconciliation dict as described in the module docstring.
    """
    # Installs for this software visible to the current tenant (scoped via
    # tenant_lookup='asset__tenant' on InstalledSoftware.objects).
    installed_count = InstalledSoftware.objects.filter(software=software).count()

    # Active (non-soft-deleted) licenses for this software owned by the current
    # tenant.  License.objects is already tenant-scoped + soft-delete-filtered.
    entitled_seats = (
        License.objects.filter(software=software)
        .aggregate(total=Sum('seats', default=0))['total']
    )

    return _build_result(software, installed_count, entitled_seats)


def reconcile_tenant_licensing() -> list:
    """Return reconciliation dicts for every relevant software in the active tenant.

    "Relevant" means the software has at least one install OR at least one
    active license in the current tenant scope — bare catalogue entries with
    neither are excluded to keep the result focused.

    The function issues two bulk queries (one for install counts, one for seat
    sums) rather than N per-software queries, making it safe to call for tenants
    with large catalogues.

    Returns:
        A list of reconciliation dicts (one per relevant ``Software`` entry),
        sorted by software name.
    """
    # ── bulk install counts (scoped to active tenant via manager) ────────────
    install_counts: dict[int, int] = {
        row['software_id']: row['count']
        for row in InstalledSoftware.objects.values('software_id').annotate(count=Count('id'))
    }

    # ── bulk seat sums (scoped to active tenant via manager) ─────────────────
    seat_sums: dict[int, int] = {
        row['software_id']: row['total']
        for row in License.objects.values('software_id').annotate(total=Sum('seats', default=0))
    }

    # Union of software PKs that have at least one install or one license
    relevant_pks = set(install_counts.keys()) | set(seat_sums.keys())

    if not relevant_pks:
        return []

    # Fetch Software rows (manager applies tenant + soft-delete scoping).
    # We intersect with relevant_pks so we only pull rows that actually matter.
    software_qs = Software.objects.filter(pk__in=relevant_pks).select_related('manufacturer')

    results = []
    for sw in software_qs:
        installed_count = install_counts.get(sw.pk, 0)
        entitled_seats = seat_sums.get(sw.pk, 0)
        results.append(_build_result(sw, installed_count, entitled_seats))

    # Sort deterministically by software name for consistent output
    results.sort(key=lambda r: r['software_name'])
    return results
