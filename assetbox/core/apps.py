from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Core'

    def ready(self):
        import core.signals  # noqa: F401

        # Monkey-patch ModelChoiceField.queryset to dynamically apply tenant scoping at request time
        from django.forms.models import ModelChoiceField
        original_queryset_getter = ModelChoiceField.queryset.fget

        def scoped_queryset_getter(self):
            qs = original_queryset_getter(self)
            if qs is not None and hasattr(qs, 'filter_by_tenant'):
                qs = qs.filter_by_tenant()
            return qs

        ModelChoiceField.queryset = property(scoped_queryset_getter, ModelChoiceField.queryset.fset)

        # Monkey-patch BaseForm.__init__ to make 'tenant' field required globally (excluding filters/bulk edit)
        from django.forms.forms import BaseForm
        original_baseform_init = BaseForm.__init__

        def scoped_baseform_init(self, *args, **kwargs):
            original_baseform_init(self, *args, **kwargs)
            if 'tenant' in self.fields:
                class_name = self.__class__.__name__
                if 'Filter' not in class_name and 'BulkEdit' not in class_name:
                    from django.db import connection
                    # Safely check if the tenant table exists to avoid poisoning transaction during migrations
                    try:
                        if 'organization_tenant' in connection.introspection.table_names():
                            from organization.models import Tenant
                            if Tenant.objects.exists():
                                self.fields['tenant'].required = True
                    except Exception:
                        pass

        BaseForm.__init__ = scoped_baseform_init

