import logging
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from core.managers import (
    set_current_tenant, set_current_membership,
    get_current_tenant, get_current_membership,
    set_current_tenant_group, get_current_tenant_group,
    set_current_all_accessible, get_current_all_accessible,
)
from organization.models import Tenant, Membership

logger = logging.getLogger(__name__)

class TaskContext:
    """
    Context manager for background/async tasks.

    Sets the tenant, membership, current user, and a synthetic request_id so
    that ChangeLoggingMixin records ObjectChange entries for all saves that
    happen inside the task — the same way middleware does for web requests.

    On exit it restores whatever context was active on entry rather than
    clearing to ``None``. This keeps nested ``TaskContext`` blocks and inline
    (``Q_CLUSTER['sync'] = True``) execution inside a web request from tearing
    down the surrounding request's user/tenant scoping — which would silently
    drop change-log entries (ChangeLoggingMixin skips logging when
    ``_request_id`` is ``None``) and disable tenant filtering for the rest of
    the request.
    """
    def __init__(self, tenant_id=None, user_id=None):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.tenant = None
        self.user = None

    def __enter__(self):
        from itambox.middleware import _request_id, _current_user

        # Capture the context active on entry so __exit__ can restore it.
        self._prev_request_id = _request_id.get()
        self._prev_user = _current_user.get()
        self._prev_tenant = get_current_tenant()
        self._prev_membership = get_current_membership()
        self._prev_tenant_group = get_current_tenant_group()
        self._prev_all_accessible = get_current_all_accessible()

        # Every task starts from an explicit, isolated scope. In particular,
        # TaskContext(None, None) is a system/global task and must not inherit a
        # caller's tenant, group, all-accessible flag, or membership. A scoped
        # task binds only an ACTIVE membership for exactly its user and tenant.
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        set_current_all_accessible(False)
        _current_user.set(None)

        try:
            self._resolve_principal_and_tenant()
            _current_user.set(self.user)
            if self.tenant:
                set_current_tenant(self.tenant)
                if self.user:
                    membership = Membership._base_manager.filter(
                        user=self.user,
                        tenant=self.tenant,
                        is_active=True,
                    ).first()
                    if membership:
                        set_current_membership(membership)

            # Wire up change-logging contextvars so ChangeLoggingMixin records
            # ObjectChange rows for all saves inside this task.
            _request_id.set(uuid.uuid4())
        except Exception:
            self._restore_context()
            raise

        return self

    def _resolve_principal_and_tenant(self):
        """Load and authorize the task's explicit scope via unscoped managers."""
        # Base managers are intentional bootstrap paths: an inline task may
        # target a tenant outside the wrapping request's scope. Explicit bad
        # identifiers are fatal; silently continuing would turn a scoped job
        # into a tenantless/global one.
        if self.tenant_id is not None:
            self.tenant = Tenant._base_manager.get(
                pk=self.tenant_id,
                deleted_at__isnull=True,
            )
        if self.user_id is not None:
            User = get_user_model()
            self.user = User._base_manager.get(pk=self.user_id)
            if not self.user.is_active:
                raise PermissionDenied('Inactive task principal')

        # A user-bound tenant task must prove canonical access to the target.
        # System tasks (no user) and superusers retain their explicit paths.
        if (
            self.tenant is not None
            and self.user is not None
            and not self.user.is_superuser
        ):
            from organization.access import accessible_tenant_ids
            if self.tenant.pk not in accessible_tenant_ids(self.user):
                raise PermissionDenied('Task principal cannot access target tenant')

    def _restore_context(self):
        from itambox.middleware import _request_id, _current_user
        _request_id.set(self._prev_request_id)
        _current_user.set(self._prev_user)
        set_current_tenant(self._prev_tenant)
        set_current_tenant_group(self._prev_tenant_group)
        set_current_membership(self._prev_membership)
        set_current_all_accessible(self._prev_all_accessible)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._restore_context()
