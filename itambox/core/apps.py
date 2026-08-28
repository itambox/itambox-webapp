from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


class SuperuserAdminConfig(AdminConfig):
    default_site = "core.admin.SuperuserAdminSite"


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = _("Core")

    def ready(self):
        # Monkey-patch ModelChoiceField.queryset to dynamically apply tenant scoping at request time
        # Issue #445: replace the vendor Success/Failure resubmission action
        # with the all-or-nothing guarded variant. Importing the guard module
        # here runs no ORM queries.
        # inline import: app-registry: avoid AppRegistryNotReady at app-load time
        from django.contrib import admin
        from django.forms.models import ModelChoiceField
        from django_q.models import Failure, Success

        import core.signals  # noqa: F401

        # inline import: app-registry: register the production configuration checks after app loading
        from core import checks  # noqa: F401

        # inline import: app-registry: avoid AppRegistryNotReady at app-load time
        from core.django_q_task_resubmission import GuardedFailAdmin, GuardedTaskAdmin

        admin.site.unregister(Success)
        admin.site.unregister(Failure)
        admin.site.register(Success, GuardedTaskAdmin)
        admin.site.register(Failure, GuardedFailAdmin)

        original_queryset_getter = ModelChoiceField.queryset.fget

        def scoped_queryset_getter(self):
            qs = original_queryset_getter(self)
            if qs is not None and hasattr(qs, "filter_by_tenant"):
                qs = qs.filter_by_tenant()
            return qs

        ModelChoiceField.queryset = property(scoped_queryset_getter, ModelChoiceField.queryset.fset)

        # Monkey-patch BaseForm.__init__ to make 'tenant' field required globally (excluding filters/bulk edit)
        from django.forms.forms import BaseForm

        original_baseform_init = BaseForm.__init__

        def scoped_baseform_init(self, *args, **kwargs):
            original_baseform_init(self, *args, **kwargs)
            # Skip tenant-XOR-group forms (a `tenant_group` field present alongside
            # `tenant` means the object scopes to a tenant OR a group OR is global):
            # forcing `tenant` required would make their tenant-XOR-group clean()
            # unsatisfiable. Such forms manage `tenant.required` themselves.
            if "tenant" in self.fields and "tenant_group" not in self.fields:
                class_name = self.__class__.__name__
                if "Filter" not in class_name and "BulkEdit" not in class_name:
                    from django.db import connection

                    # Safely check if the tenant table exists to avoid poisoning transaction during migrations
                    try:
                        if "organization_tenant" in connection.introspection.table_names():
                            from organization.models import Tenant

                            if Tenant.objects.exists():
                                self.fields["tenant"].required = True
                    except Exception:
                        pass

            # Auto-apply TomSelect attribute to all select fields (excluding CheckboxSelectMultiple/RadioSelect/TableConfigForm/listboxes)
            from django import forms

            class_name = self.__class__.__name__
            if "TableConfig" not in class_name:
                for field in self.fields.values():
                    if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) and not isinstance(
                        field.widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)
                    ):
                        # Do not apply to listboxes (select elements with a size attribute)
                        if "size" in field.widget.attrs:
                            continue
                        widget_classes = field.widget.attrs.get("class", "")
                        if "available-columns" not in widget_classes and "selected-columns" not in widget_classes:
                            if "data-tom-select" not in field.widget.attrs:
                                field.widget.attrs["data-tom-select"] = ""

        BaseForm.__init__ = scoped_baseform_init

        post_migrate.connect(self._register_prune_schedule, sender=self)

    def _register_prune_schedule(self, sender, **kwargs):
        """Ensure the daily changelog/operational-data retention prune schedule exists."""
        # inline import: app-registry: avoid AppRegistryNotReady at app-load time
        from django_q.models import Schedule

        from core.schedules import register_schedule

        register_schedule(
            "core.tasks.prune_changelog_task",
            defaults={
                "name": "Daily Changelog & Operational-Data Retention Prune",
                "schedule_type": Schedule.DAILY,
                "repeats": -1,
            },
        )
