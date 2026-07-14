from django.contrib.admin import AdminSite


class SuperuserAdminSite(AdminSite):
    """Keep Django's unrestricted maintenance console superuser-only."""

    def has_permission(self, request):
        return bool(
            request.user.is_active
            and request.user.is_authenticated
            and request.user.is_superuser
        )
