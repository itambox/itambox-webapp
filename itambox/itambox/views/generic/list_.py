import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.template.loader import get_template
from django.template import TemplateDoesNotExist
from django.urls import reverse, NoReverseMatch
from django.utils.translation import gettext as _, override
from django.views.generic import ListView

from itambox.registry import registry
from itambox.utils import get_model_viewname, get_table_for_model, get_help_url
from itambox.views.htmx import BaseHTMXView
from itambox.views.generic.mixins import TenantScopingViewMixin

logger = logging.getLogger(__name__)


class ObjectListView(TenantScopingViewMixin, PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, ListView):
    filterset = None
    filterset_form = None
    table = None
    template_name = 'generic/object_list.html'
    content_partial_name = "htmx/list_page_wrapper.html"
    action_buttons = ()

    def get_permission_required(self):
        model = getattr(self, 'model', None)
        if model is None and hasattr(self, 'queryset') and self.queryset is not None:
            model = self.queryset.model
        if model:
            app_label = model._meta.app_label
            model_name = model._meta.model_name
            return (f'{app_label}.view_{model_name}',)
        return ('',)

    def get_template_names(self):
        if self.template_name and self.template_name != 'generic/object_list.html':
            return [self.template_name]

        model = getattr(self, 'model', None)
        if model is None and hasattr(self, 'queryset') and self.queryset is not None:
            model = self.queryset.model

        if model:
            app_label = model._meta.app_label
            model_name = model._meta.model_name
            with override('en'):
                plural_name = str(model._meta.verbose_name_plural).lower().replace(' ', '')

            templates_to_try = [
                f'{app_label}/{plural_name}/{model_name}_list.html',
                f'{app_label}/{model_name}_list.html',
                'generic/object_list.html',
            ]

            for template_name in templates_to_try:
                try:
                    get_template(template_name)
                    return [template_name]
                except TemplateDoesNotExist:
                    continue

        return ['generic/object_list.html']

    def get_queryset(self):
        model = getattr(self, 'model', None)
        if model is None and hasattr(self, 'queryset') and self.queryset is not None:
            model = self.queryset.model

        show_deleted = self.request.GET.get('deleted') == 'true'
        if show_deleted and model and registry.model_has_feature(model, 'soft_delete'):
            from django.core.exceptions import PermissionDenied
            if not self.request.user.is_superuser and not self.request.user.has_perm('core.view_recyclebin'):
                raise PermissionDenied("You do not have permission to view the Recycle Bin.")
            manager = getattr(model, 'all_objects', model._base_manager)
            queryset = manager.all()
            if hasattr(queryset, 'filter_by_tenant'):
                queryset = queryset.filter_by_tenant()
            elif any(f.name == 'tenant' for f in model._meta.fields):
                # Fail loud: a tenant-bearing model whose all_objects manager cannot
                # scope by tenant would expose other tenants' deleted objects here.
                raise ImproperlyConfigured(
                    f"{model.__name__}.all_objects is not tenant-scoped but the model "
                    f"has a tenant field. Use TenantScopingAllObjectsManager."
                )
            queryset = queryset.filter(deleted_at__isnull=False)
        else:
            queryset = super().get_queryset()

        if self.filterset:
            self.filter = self.filterset(self.request.GET, queryset)
            if not self.filter.is_valid():
                logger.warning('Invalid filter params for %s: %s', self.__class__.__name__, self.filter.errors)
                self.filter = None
            else:
                queryset = self.filter.qs

        # cf_<name>=<value> params filter on custom field data (NetBox-style).
        if model and registry.model_has_feature(model, 'custom_field_data'):
            from extras.customfields import apply_custom_field_filters
            queryset = apply_custom_field_filters(queryset, model, self.request.GET)

        return queryset

    def get_paginate_by(self, queryset):
        return None

    def get_table(self):
        # A5: reuse self.object_list (already filtered + resolved by get_queryset
        # via ListView.get()) instead of calling get_queryset() a second time,
        # which would re-run the full filterset and custom-field filters.
        queryset = self.object_list
        table_class = self.table or get_table_for_model(self.model)
        if not table_class:
            raise Http404(f"No table defined for model {self.model._meta.model_name}")

        table = table_class(queryset, request=self.request)
        return table

    def get_context_data(self, **kwargs):
        _model = getattr(self, 'model', None)
        if not _model and hasattr(self, 'queryset') and self.queryset is not None:
            _model = self.queryset.model
        elif not _model and hasattr(self, 'object_list') and self.object_list is not None:
            _model = self.object_list.model

        if not _model:
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} is missing a QuerySet. Define "
                f"{self.__class__.__name__}.model, {self.__class__.__name__}.queryset, or override "
                f"{self.__class__.__name__}.get_queryset()."
            )

        context = super().get_context_data(**kwargs)
        self.model = _model

        table = self.get_table()
        table.configure(self.request)
        filter_form = self.filterset_form(self.request.GET) if self.filterset_form else None
        context['table'] = table
        context['filter_form'] = filter_form
        context['model'] = _model
        context['verbose_name_plural'] = _model._meta.verbose_name_plural
        context['model_name_str'] = f"{_model._meta.app_label}.{_model._meta.model_name}"
        context['table_config_key'] = f"{_model._meta.app_label}.{table.__class__.__name__}"
        context['app_label'] = _model._meta.app_label
        context['model_name'] = _model._meta.model_name
        context['object_type'] = _model._meta.verbose_name

        from core.features import module_maturity, BETA
        context.setdefault('is_beta_module', module_maturity(_model._meta.app_label) == BETA)

        context.setdefault('title', _model._meta.verbose_name_plural)

        # Export/label template catalogs feed dropdowns that only exist on the
        # full page — partial (table refresh/filter/pagination) renders never
        # use them, so don't pay for the queries there.
        if self.is_htmx_partial() and self.content_partial_name:
            context['export_templates'] = []
            context['label_templates'] = []
        else:
            from extras.models import ExportTemplate, LabelTemplate
            try:
                content_type = ContentType.objects.get_for_model(_model)
                context['export_templates'] = list(ExportTemplate.objects.filter(content_type=content_type))
            except Exception:
                context['export_templates'] = []

            try:
                context['label_templates'] = list(LabelTemplate.objects.all())
            except Exception:
                context['label_templates'] = []

        try:
            create_url_name = get_model_viewname(_model, 'create')
            reverse(create_url_name)
            context['create_url_name'] = create_url_name
        except NoReverseMatch:
            context['create_url_name'] = None

        # Import/export are offered only for importable models (not generated
        # logs or UI-only config). Importable models import via the single
        # centralized route /import/<app>/<model>/.
        from core.forms.import_forms import is_model_importable
        _importable = is_model_importable(_model)
        context['can_export'] = _importable
        context['import_url'] = None
        if _importable:
            try:
                context['import_url'] = reverse('generic_import', kwargs={
                    'app_label': _model._meta.app_label,
                    'model_name': _model._meta.model_name,
                })
            except NoReverseMatch:
                context['import_url'] = None

        try:
            bulk_delete_url_name = get_model_viewname(_model, 'bulk_delete')
            context['bulk_delete_url'] = reverse(bulk_delete_url_name)
        except NoReverseMatch:
            try:
                context['bulk_delete_url'] = reverse('bulk_delete')
            except NoReverseMatch:
                context['bulk_delete_url'] = None

        try:
            bulk_edit_url_name = get_model_viewname(_model, 'bulk_edit')
            context['bulk_edit_url'] = reverse(bulk_edit_url_name)
        except NoReverseMatch:
            try:
                context['bulk_edit_url'] = reverse('bulk_edit')
            except NoReverseMatch:
                context['bulk_edit_url'] = None

        # Check permissions in Python
        can_add = self.request.user.has_perm(f"{_model._meta.app_label}.add_{_model._meta.model_name}")
        context['can_add'] = can_add

        context['action_buttons'] = self.action_buttons
        if 'add' in self.action_buttons and not context['create_url_name']:
            logger.debug("'add' action button enabled but create URL not resolvable for %s", self.model)

        has_soft_delete = registry.model_has_feature(_model, 'soft_delete')
        show_deleted = self.request.GET.get('deleted') == 'true'

        if show_deleted and has_soft_delete:
            context['title'] = _("Recycle Bin — {verbose_name_plural}").format(
                verbose_name_plural=_model._meta.verbose_name_plural,
            )
            context['pretitle'] = _("Trash")
            context['is_deleted_view'] = True

            try:
                ct = ContentType.objects.get_for_model(_model)
                context['bulk_restore_url'] = reverse('object_bulk_restore', kwargs={'content_type_id': ct.pk})
                context['bulk_purge_url'] = reverse('object_bulk_purge', kwargs={'content_type_id': ct.pk})
            except Exception:
                context['bulk_restore_url'] = None
                context['bulk_purge_url'] = None

            base_breadcrumbs = [
                (reverse('dashboard'), _('Dashboard')),
                (reverse(get_model_viewname(_model, 'list')), _model._meta.verbose_name_plural),
                (None, _("Recycle Bin")),
            ]
        else:
            base_breadcrumbs = [
                (reverse('dashboard'), _('Dashboard')),
                (None, context['title']),
            ]

        context['has_soft_delete'] = has_soft_delete
        context['is_deleted_view'] = show_deleted
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        context['help_url'] = get_help_url(self, _model._meta.app_label, _model._meta.model_name)
        return context
