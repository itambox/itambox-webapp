import logging
from django.contrib.auth import get_user_model
from core.managers import set_current_tenant, set_current_membership
from organization.models import Tenant, TenantMembership

logger = logging.getLogger(__name__)

class TaskContext:
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
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        set_current_tenant(None)
        set_current_membership(None)
