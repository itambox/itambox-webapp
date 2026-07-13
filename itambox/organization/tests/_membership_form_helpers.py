"""Shared builders for MembershipForm POST data (RBAC stage 3 lossless editor).

The form's grant surface is ``own_roles`` (multi-select) plus a provider-only
managed-grants **formset** (prefix ``managed``), so a bound POST needs the
formset management form and one indexed block per managed row. These helpers keep
the many form/view tests readable and in one place.

List existing (id-carrying) managed rows FIRST — they map to the formset's
"initial" forms; new rows follow as "extra" forms.
"""
from organization.forms.membership_form import MANAGED_FORMSET_PREFIX


def managed_management_form(total, initial=0, prefix=MANAGED_FORMSET_PREFIX):
    return {
        f'{prefix}-TOTAL_FORMS': str(total),
        f'{prefix}-INITIAL_FORMS': str(initial),
        f'{prefix}-MIN_NUM_FORMS': '0',
        f'{prefix}-MAX_NUM_FORMS': '1000',
    }


def managed_row(index, *, role=None, managed_scope=None, scope_group=None,
                assigned_tenants=None, reason=None, valid_until=None,
                id=None, delete=False,
                prefix=MANAGED_FORMSET_PREFIX):
    """One indexed managed-grant formset block as POST keys."""
    p = f'{prefix}-{index}-'
    d = {}
    if id is not None:
        d[p + 'id'] = id
    if role is not None:
        d[p + 'role'] = role
    if managed_scope is not None:
        d[p + 'managed_scope'] = managed_scope
    if scope_group is not None:
        d[p + 'scope_group'] = scope_group
    if assigned_tenants:
        d[p + 'assigned_tenants'] = list(assigned_tenants)
    if reason is not None:
        d[p + 'reason'] = reason
    if valid_until is not None:
        d[p + 'valid_until'] = valid_until
    if delete:
        d[p + 'DELETE'] = 'on'
    return d


def membership_post_data(*, tenant, user=None, own_roles=None, is_active=True,
                         who=None, new_user_email=None, new_user_first_name=None,
                         new_user_last_name=None, reason=None, valid_until=None,
                         managed=None):
    """Assemble a full MembershipForm POST dict.

    ``managed`` is a list of dicts, each a kwargs bag for :func:`managed_row`
    (minus ``index``). Rows with ``id`` are counted into ``INITIAL_FORMS`` and
    must be listed first.
    """
    data = {'tenant': tenant}
    if is_active:
        data['is_active'] = 'on'
    if user is not None:
        data['user'] = user
    if own_roles is not None:
        data['own_roles'] = list(own_roles)
    if reason is not None:
        data['reason'] = reason
    if valid_until is not None:
        data['valid_until'] = valid_until
    if who is not None:
        data['who'] = who
    if new_user_email is not None:
        data['new_user_email'] = new_user_email
    if new_user_first_name is not None:
        data['new_user_first_name'] = new_user_first_name
    if new_user_last_name is not None:
        data['new_user_last_name'] = new_user_last_name

    managed = managed or []
    initial = sum(1 for m in managed if m.get('id') is not None)
    data.update(managed_management_form(len(managed), initial))
    for i, m in enumerate(managed):
        data.update(managed_row(i, **m))
    return data
