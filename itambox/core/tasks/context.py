import logging
import uuid
from django.contrib.auth import get_user_model
from core.managers import set_current_tenant, set_current_membership
from organization.models import Tenant, TenantMembership

logger = logging.getLogger(__name__)

class TaskContext:
    """
    Context manager for background/async tasks.

    Sets the tenant, membership, current user, and a synthetic request_id so
    that ChangeLoggingMixin records ObjectChange entries for all saves that
    happen inside the task — the same way middleware does for web requests.
    """
    def __init__(self, tenant_id=None, user_id=None):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.tenant = None
        self.user = None

    def __enter__(self):
        if self.tenant_id:
            try:
                self.tenant = Tenant.objects.get(pk=self.tenant_id)
            except Tenant.DoesNotExist:
                pass

        if self.user_id:
            try:
                User = get_user_model()
                self.user = User.objects.get(pk=self.user_id)
            except Exception:
                pass

        if self.tenant:
            set_current_tenant(self.tenant)
            if self.user:
                membership = TenantMembership.objects.filter(user=self.user, tenant=self.tenant).first()
                if membership:
                    set_current_membership(membership)

        # Wire up change-logging contextvars so ChangeLoggingMixin records
        # ObjectChange rows for all saves inside this task.
        from itambox.middleware import _request_id, _current_user
        _request_id.set(uuid.uuid4())
        _current_user.set(self.user)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        from itambox.middleware import _request_id, _current_user
        _request_id.set(None)
        _current_user.set(None)
        set_current_tenant(None)
        set_current_membership(None)
