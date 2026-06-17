import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


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

            # Auto-apply TomSelect attribute to all select fields (excluding CheckboxSelectMultiple/RadioSelect/TableConfigForm/listboxes)
            from django import forms
            class_name = self.__class__.__name__
            if 'TableConfig' not in class_name:
                for field in self.fields.values():
                    if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) and not isinstance(field.widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
                        # Do not apply to listboxes (select elements with a size attribute)
                        if 'size' in field.widget.attrs:
                            continue
                        widget_classes = field.widget.attrs.get('class', '')
                        if 'available-columns' not in widget_classes and 'selected-columns' not in widget_classes:
                            if 'data-tom-select' not in field.widget.attrs:
                                field.widget.attrs['data-tom-select'] = ''

        BaseForm.__init__ = scoped_baseform_init

        post_migrate.connect(self._register_alert_schedule, sender=self)

    def _register_alert_schedule(self, sender, **kwargs):
        """Ensure the daily alert evaluation schedule exists in django-q2."""
        try:
            # inline import: avoid AppRegistryNotReady at app-load time
            from django_q.models import Schedule
            Schedule.objects.get_or_create(
                func='core.tasks.evaluate_alert_rules_task',
                defaults={
                    'name': 'Daily Alert Rule Evaluation',
                    'schedule_type': Schedule.DAILY,
                    'repeats': -1,
                },
            )
        except Exception as exc:
            logger.warning("Failed to register alert rule evaluation schedule: %s", exc)

