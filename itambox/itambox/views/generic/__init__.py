# This file is adapted from NetBox (https://github.com/netbox-community/netbox).
# Copyright (c) DigitalOcean, LLC.
# Licensed under the Apache License, Version 2.0.

import logging
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.apps import apps
from django.urls import reverse, NoReverseMatch
from django_tables2 import RequestConfig
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib import messages
from django.core.exceptions import ImproperlyConfigured
from django.views.generic import View, ListView, DetailView, UpdateView, DeleteView, TemplateView
from django.views.generic.base import TemplateResponseMixin
from django.contrib.contenttypes.models import ContentType
from django.utils.http import urlencode
from django.utils.translation import gettext as _, override
from django.utils.module_loading import import_string
from django.views.decorators.http import require_POST
from django.db.models import ProtectedError
from django.template.loader import get_template
from django.template import TemplateDoesNotExist

from itambox.registry import registry
from itambox.utils import get_model_viewname, get_table_for_model, get_help_url
from core.models import ObjectChange
from extras.models import JournalEntry, ImageAttachment, FileAttachment
from core.tables import ObjectChangeTable, BaseTable
from core.forms import ConfirmationForm, JournalEntryForm, BulkEditForm
from users.forms import TableConfigForm
from users.models import UserPreference

from itambox.views.htmx import BaseHTMXView
from itambox.views.generic.mixins import CachedObjectMixin
from itambox.views.generic.utils import safe_return_url

logger = logging.getLogger(__name__)

class TenantScopingViewMixin:
    def get_queryset(self):
        queryset = super().get_queryset()
        if hasattr(queryset, 'filter_by_tenant'):
            queryset = queryset.filter_by_tenant()
        return queryset

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
        queryset = self.get_queryset()
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

        try:
            import_url_name = get_model_viewname(_model, 'import')
            context['import_url'] = reverse(import_url_name)
        except NoReverseMatch:
            try:
                context['import_url'] = reverse('generic_import', kwargs={
                    'app_label': _model._meta.app_label,
                    'model_name': _model._meta.model_name
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
                verbose_name_plural=_model._meta.verbose_name_plural
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
                (None, _("Recycle Bin"))
            ]
        else:
            base_breadcrumbs = [
                (reverse('dashboard'), _('Dashboard')),
                (None, context['title'])
            ]

        context['has_soft_delete'] = has_soft_delete
        context['is_deleted_view'] = show_deleted
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        context['help_url'] = get_help_url(self, _model._meta.app_label, _model._meta.model_name)
        return context

class ObjectDetailView(TenantScopingViewMixin, PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, CachedObjectMixin, DetailView):
    template_name = 'generic/object_detail.html'
    layout = None

    def render_to_response(self, context, **response_kwargs):
        # Tables shown in detail-view tabs opt into the shared batch-action bar
        # (rendered by global_includes/htmx_table.html). django_tables2's
        # {% render_table %} only passes {table, request} to the table template, so
        # the flag has to ride on the table instance rather than the page context.
        for value in context.values():
            if isinstance(value, BaseTable):
                value.embed_bulk_bar = True
        return super().render_to_response(context, **response_kwargs)

    def get_permission_required(self):
        model = getattr(self, 'model', None)
        if model is None and hasattr(self, 'queryset') and self.queryset is not None:
            model = self.queryset.model
        if model:
            app_label = model._meta.app_label
            model_name = model._meta.model_name
            return (f'{app_label}.view_{model_name}',)
        return ('',)

    def has_permission(self):
        perms = self.get_permission_required()
        try:
            obj = self.get_object()
        except Http404:
            # 404 (not 403) for objects outside the tenant scope: don't reveal
            # whether the pk exists in another tenant. Anonymous users fall
            # through to the permission check (and the login redirect).
            if self.request.user.is_authenticated:
                raise
            obj = None
        return self.request.user.has_perms(perms, obj=obj)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        
        tab = request.GET.get('tab')
        if tab and request.headers.get('HX-Request'):
            # Try replacing hyphens with underscores
            tab_clean = tab.replace('-', '_')
            tab_method_name = f"get_tab_{tab_clean}"
            if hasattr(self, tab_method_name):
                return getattr(self, tab_method_name)(request)
            
            # Try removing hyphens entirely (e.g., asset-holders -> assetholders)
            tab_flat = tab.replace('-', '')
            tab_method_name_flat = f"get_tab_{tab_flat}"
            if hasattr(self, tab_method_name_flat):
                return getattr(self, tab_method_name_flat)(request)
                
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        if self.template_name and self.template_name != 'generic/object_detail.html':
            return [self.template_name]

        obj = self.get_object()
        if obj:
            app_label = obj._meta.app_label
            model_name = obj._meta.model_name
            with override('en'):
                plural_name = str(obj._meta.verbose_name_plural).lower().replace(" ", "")

            templates_to_try = [
                f"{app_label}/{plural_name}/{model_name}_detail.html",
                f"{app_label}/{model_name}_detail.html",
                'generic/object_detail.html'
            ]

            for template_name in templates_to_try:
                try:
                    get_template(template_name)
                    return [template_name]
                except TemplateDoesNotExist:
                    continue

        return ['generic/object_detail.html']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()
        app_label = obj._meta.app_label
        model_name = obj._meta.model_name
        verbose_name = obj._meta.verbose_name
        verbose_name_plural = obj._meta.verbose_name_plural

        context['model'] = obj.__class__
        context['layout'] = self.layout

        can_change = self.request.user.has_perm(f'{app_label}.change_{model_name}', obj=obj)
        can_delete = self.request.user.has_perm(f'{app_label}.delete_{model_name}', obj=obj)
        context['can_change'] = can_change
        context['can_delete'] = can_delete
        context['edit_url'] = None
        if can_change:
            try:
                context['edit_url'] = reverse(get_model_viewname(obj, 'update'), kwargs={'pk': obj.pk})
            except NoReverseMatch:
                if hasattr(obj, 'slug') and obj.slug:
                    try:
                        context['edit_url'] = reverse(get_model_viewname(obj, 'update'), kwargs={'slug': obj.slug})
                    except NoReverseMatch:
                        logger.debug("Edit URL not resolvable for %s obj=%s slug=%s", model_name, obj.pk, obj.slug)
        
        context['delete_url'] = None
        if can_delete:
            try:
                context['delete_url'] = reverse(get_model_viewname(obj, 'delete'), kwargs={'pk': obj.pk})
            except NoReverseMatch:
                if hasattr(obj, 'slug') and obj.slug:
                    try:
                        context['delete_url'] = reverse(get_model_viewname(obj, 'delete'), kwargs={'slug': obj.slug})
                    except NoReverseMatch:
                        logger.debug("Delete URL not resolvable for %s obj=%s slug=%s", model_name, obj.pk, obj.slug)

        # Clone is offered generically for any model flagged cloneable (via
        # CloneableMixin) that has a clone view wired and that the user may add.
        context['clone_url'] = None
        if registry.model_has_feature(obj.__class__, 'cloneable') and \
                self.request.user.has_perm(f'{app_label}.add_{model_name}', obj=obj):
            try:
                context['clone_url'] = reverse(get_model_viewname(obj, 'clone'), kwargs={'pk': obj.pk})
            except NoReverseMatch:
                if hasattr(obj, 'slug') and obj.slug:
                    try:
                        context['clone_url'] = reverse(get_model_viewname(obj, 'clone'), kwargs={'slug': obj.slug})
                    except NoReverseMatch:
                        logger.debug("Clone URL not resolvable for %s obj=%s slug=%s", model_name, obj.pk, obj.slug)

        context['title'] = str(obj)
        base_breadcrumbs = [
            (reverse('dashboard'), _('Dashboard')),
            (reverse(get_model_viewname(obj, 'list')), verbose_name_plural),
            (None, context['title'])
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()

        if hasattr(obj, 'get_changelog_url'):
            context['changelog_url'] = obj.get_changelog_url()
        elif ContentType.objects.filter(app_label='core', model='objectchange').exists():
            obj_type = ContentType.objects.get_for_model(obj)
            changelog_url = reverse('objectchange_list') + '?' + urlencode({'changed_object_type': obj_type.pk, 'changed_object_id': obj.pk})
            context['changelog_url'] = changelog_url

        if ContentType.objects.filter(app_label='core', model='objectchange').exists():
            obj_type = ContentType.objects.get_for_model(obj)
            changelog_qs = ObjectChange.objects.filter(
                changed_object_type=obj_type,
                changed_object_id=obj.pk
            ).order_by('-time')[:50]
            changelog_table = ObjectChangeTable(list(changelog_qs))
            RequestConfig(self.request, paginate={'per_page': 10}).configure(changelog_table)
            context['changelog_table'] = changelog_table

        context['page_actions'] = {
            'edit_url': context.get('edit_url'),
            'delete_url': context.get('delete_url'),
        }
        context['action_urls'] = {
            'edit': context.get('edit_url'),
            'delete': context.get('delete_url'),
            'clone': context.get('clone_url'),
        }
        context['content_template_name'] = self.get_template_names()[0]

        if registry.model_has_feature(obj.__class__, 'journaling'):
            obj_type = ContentType.objects.get_for_model(obj)
            journal_qs = JournalEntry.objects.filter(
                model=obj_type,
                object_id=obj.pk
            )
            context['has_journaling'] = True
            context['journal_app_label'] = app_label
            context['journal_model_name'] = model_name
            context['journal_entries'] = journal_qs.select_related('user').order_by('-created')[:50]
            context['journal_entries_count'] = journal_qs.count()
            context['journal_form'] = JournalEntryForm()

        context['attachment_app_label'] = app_label
        context['attachment_model_name'] = model_name

        if registry.model_has_feature(obj.__class__, 'custom_field_data'):
            from extras.customfields import get_custom_fields_display
            context['custom_fields_display'] = get_custom_fields_display(obj)

        if registry.model_has_feature(obj.__class__, 'image_attachments'):
            obj_type = ContentType.objects.get_for_model(obj)
            context['image_attachments'] = ImageAttachment.objects.filter(
                model=obj_type, object_id=obj.pk
            ).order_by('-created')[:20]
            context['has_image_attachments'] = True

        if registry.model_has_feature(obj.__class__, 'file_attachments'):
            obj_type = ContentType.objects.get_for_model(obj)
            context['file_attachments'] = FileAttachment.objects.filter(
                model=obj_type, object_id=obj.pk
            ).order_by('-created')[:20]
            context['has_file_attachments'] = True

        if registry.model_has_feature(obj.__class__, 'subscribable'):
            obj_type = ContentType.objects.get_for_model(obj)
            context['has_subscriptions'] = True
            context['subscribable_content_type_id'] = obj_type.pk
            
            from subscriptions.models import SubscriptionAssignment
            from subscriptions.tables import SubscriptionAssignmentTable
            
            assignments_qs = SubscriptionAssignment.objects.filter(
                content_type=obj_type,
                object_id=obj.pk
            ).select_related('subscription', 'subscription__provider', 'assigned_by')
            
            subs_table = SubscriptionAssignmentTable(assignments_qs, request=self.request)
            subs_table.exclude = ('content_type', 'object_id', 'assigned_object')
            RequestConfig(self.request, paginate=False).configure(subs_table)
            context['subscription_assignments_table'] = subs_table
            context['subscription_assignments_count'] = assignments_qs.count()

        if registry.model_has_feature(obj.__class__, 'bookmarkable'):
            obj_type = ContentType.objects.get_for_model(obj)
            context['is_bookmarkable'] = True
            context['bookmark_content_type_id'] = obj_type.pk
            if self.request.user.is_authenticated:
                from extras.models import Bookmark
                context['is_bookmarked'] = Bookmark.objects.filter(
                    user=self.request.user,
                    model=obj_type,
                    object_id=obj.pk
                ).exists()
            else:
                context['is_bookmarked'] = False


        if 'related_objects_list' not in context:
            related_objects_list = []
            for relation in obj._meta.get_fields(include_parents=True):
                if not relation.is_relation or relation.concrete:
                    continue
                if relation.auto_created and not relation.concrete:
                    related_model = relation.related_model
                    if not related_model:
                        continue
                    
                    accessor_name = relation.get_accessor_name()
                    if not accessor_name or not hasattr(obj, accessor_name):
                        continue
                    
                    try:
                        manager = getattr(obj, accessor_name)
                        count = manager.count()
                    except Exception:
                        continue
                        
                    if count > 0:
                        related_app = related_model._meta.app_label
                        related_model_name = related_model._meta.model_name
                        view_name = f"{related_app}:{related_model_name}_list"
                        
                        try:
                            base_url = reverse(view_name)
                            filter_field_name = relation.remote_field.name if relation.remote_field else obj._meta.model_name
                            filter_val = getattr(obj, 'slug', obj.pk)
                            url = f"{base_url}?{filter_field_name}={filter_val}"
                            label = str(related_model._meta.verbose_name_plural).title()
                            
                            related_objects_list.append({
                                'label': label,
                                'count': count,
                                'url': url
                            })
                        except NoReverseMatch:
                            continue
            
            related_objects_list.sort(key=lambda x: x['label'])
            context['related_objects_list'] = related_objects_list

        context['help_url'] = get_help_url(self, app_label, model_name)
        return context

class ObjectEditView(TenantScopingViewMixin, PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, CachedObjectMixin, UpdateView):
    model_form = None
    template_name = 'generic/object_edit.html'

    def has_permission(self):
        perms = self.get_permission_required()
        try:
            obj = self.get_object()
        except Http404:
            # 404 (not 403) for objects outside the tenant scope: don't reveal
            # whether the pk exists in another tenant. Anonymous users fall
            # through to the permission check (and the login redirect).
            if self.request.user.is_authenticated:
                raise
            obj = None
        return self.request.user.has_perms(perms, obj=obj)

    def get_permission_required(self):
        model = self._get_model()
        if model:
            app_label = model._meta.app_label
            model_name = model._meta.model_name
            try:
                obj = self.get_object()
            except Http404:
                obj = None
            if obj:
                return (f'{app_label}.change_{model_name}',)
            return (f'{app_label}.add_{model_name}',)
        return ('',)

    def _get_model(self):
        if hasattr(self, 'model') and self.model:
            return self.model
        if hasattr(self, 'queryset') and self.queryset is not None:
            return self.queryset.model
        if hasattr(self, 'model_form') and self.model_form:
             return self.model_form._meta.model
        if hasattr(self, 'form_class') and self.form_class and hasattr(self.form_class, '_meta'):
            return self.form_class._meta.model
        return None

    def get_form_class(self):
        if self.model_form:
            return self.model_form
        return super().get_form_class()
    
    def get_object(self, queryset=None):
        if 'pk' not in self.kwargs and 'slug' not in self.kwargs:
            return None
        return super().get_object(queryset)

    def get_form(self, form_class=None):
        kwargs = self.get_form_kwargs()
        kwargs['instance'] = self.object
        if form_class is None:
            form_class = self.get_form_class()
        form = form_class(**kwargs)
        
        if not hasattr(form, 'helper') or form.helper is None:
            from crispy_forms.helper import FormHelper
            from crispy_forms.layout import Layout, Submit, HTML
            
            helper = FormHelper(form)
            helper.form_method = 'post'
            helper.form_tag = True
            
            is_editing = self.object is not None and self.object.pk is not None
            button_text = 'Update' if is_editing else 'Create'
            
            cancel_url = '#'
            if self.object and hasattr(self.object, 'get_absolute_url'):
                try:
                    cancel_url = self.object.get_absolute_url()
                except Exception:
                    pass
            if cancel_url == '#':
                _model = self._get_model()
                if _model:
                    try:
                        list_view_name = get_model_viewname(_model, 'list')
                        cancel_url = reverse(list_view_name)
                    except Exception:
                        cancel_url = reverse('dashboard')
            
            layout_elements = list(form.fields.keys())
            layout_elements.extend([
                HTML('<div class="mt-4"></div>'),
                Submit('submit', button_text, css_class='btn btn-primary'),
                HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>')
            ])
            helper.layout = Layout(*layout_elements)
            form.helper = helper
            
        return form

    def get_success_url(self):
        fallback = None
        if hasattr(self, 'default_return_url') and self.default_return_url:
            fallback = reverse(self.default_return_url)
        elif self.object and hasattr(self.object, 'get_absolute_url'):
            fallback = self.object.get_absolute_url()
        if fallback is None:
            _model = self._get_model()
            if _model:
                try:
                    list_view_name = get_model_viewname(_model, 'list')
                    fallback = reverse(list_view_name)
                except NoReverseMatch:
                    logger.debug("List view URL fallback failed for model %s", _model)
        if fallback is None:
            fallback = reverse('dashboard')
        return safe_return_url(self.request, self.request.POST.get('return_url'), fallback)

    def form_valid(self, form):
        # Unsaved instances (new objects and clones) are creations.
        is_creating = self.object is None or self.object.pk is None
        _model = self._get_model()
        
        # Enforce scoping check on the selected tenant of the object
        if _model:
            app_label = _model._meta.app_label
            model_name = _model._meta.model_name
            selected_tenant = form.cleaned_data.get('tenant')
            if not selected_tenant and hasattr(form.instance, 'tenant'):
                selected_tenant = getattr(form.instance, 'tenant', None)
                
            if selected_tenant:
                is_creating_instance = self.object is None or self.object.pk is None
                perm_codename = f'{app_label}.add_{model_name}' if is_creating_instance else f'{app_label}.change_{model_name}'
                if not self.request.user.has_perm(perm_codename, obj=selected_tenant):
                    form.add_error('tenant', f"You do not have permission to assign objects to tenant '{selected_tenant}'.")
                    return self.form_invalid(form)
                    
        self.object = form.save()
        msg_verb = 'Created' if is_creating else 'Modified'
        msg_link = f"<a href='{self.object.get_absolute_url()}'>{self.object}</a>" if hasattr(self.object, 'get_absolute_url') else str(self.object)
        messages.success(self.request, f"{msg_verb} {_model._meta.verbose_name} {msg_link}")
        
        if self.request.POST.get('_addanother') and _model:
            try:
                add_view_name = get_model_viewname(_model, 'add') 
                return redirect(reverse(add_view_name))
            except NoReverseMatch:
                pass
        elif self.request.POST.get('_continue') and _model:
            try:
                edit_view_name = get_model_viewname(_model, 'edit') 
                try:
                    return redirect(reverse(edit_view_name, kwargs={'pk': self.object.pk}))
                except NoReverseMatch:
                    if hasattr(self.object, 'slug') and self.object.slug:
                        return redirect(reverse(edit_view_name, kwargs={'slug': self.object.slug}))
                    raise
            except NoReverseMatch:
                pass
            
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _model = self._get_model()
        if not _model:
             raise ImproperlyConfigured(f"{self.__class__.__name__} needs a model attribute, or related form/queryset.")

        # A clone is an unsaved instance (pk is None): treat it as creation, not
        # an edit, so we don't reverse get_absolute_url() with a null pk.
        is_editing = self.object is not None and self.object.pk is not None
        context['model'] = _model
        context['verbose_name'] = _model._meta.verbose_name
        context['is_editing'] = is_editing
        action_verb = _('Edit') if is_editing else _('Create')
        context['title'] = f"{action_verb} {context['verbose_name']}"

        if is_editing and hasattr(self.object, 'get_absolute_url'):
            context['cancel_url'] = self.object.get_absolute_url()
        else:
            try:
                 list_view_name = get_model_viewname(_model, 'list')
                 context['cancel_url'] = reverse(list_view_name)
            except NoReverseMatch:
                 context['cancel_url'] = reverse('dashboard')
        
        base_breadcrumbs = [
            (reverse('dashboard'), _('Dashboard')),
            (context['cancel_url'], _model._meta.verbose_name_plural),
            (None, context['title'])
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        context['help_url'] = get_help_url(self, _model._meta.app_label, _model._meta.model_name)
        return context

class ObjectCloneView(ObjectEditView):
    """Render a create form pre-filled from an existing object.

    The clone is NOT persisted on GET — ``get_object`` returns an *unsaved*
    instance used only to pre-fill the form's fields. The new record is created
    only when the user submits the form (handled by ``ObjectEditView.form_valid``),
    so the user can review and adjust the copied values first.
    """
    def get_object(self, queryset=None):
        self.original_object = get_object_or_404(self.model, pk=self.kwargs['pk'])
        cloned = self.original_object.clone()

        if hasattr(cloned, 'name'):
            cloned.name = f"{self.original_object.name} (Copy)"
        elif hasattr(cloned, 'model'):
            cloned.model = f"{self.original_object.model} (Copy)"

        if hasattr(cloned, 'slug'):
            cloned.slug = ''

        self.pre_save_clone(self.original_object, cloned)
        # Intentionally NOT saved here — the form's POST creates the record.
        return cloned

    def get_initial(self):
        # An unsaved instance can't supply its many-to-many values to the form,
        # so seed them (e.g. tags) from the source object as form initial. Only
        # fields actually present on the form are rendered/saved.
        initial = super().get_initial()
        original = getattr(self, 'original_object', None)
        if original is not None and original.pk:
            for field in original._meta.many_to_many:
                initial.setdefault(
                    field.name,
                    list(getattr(original, field.name).values_list('pk', flat=True)),
                )
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_clone'] = True
        context['title'] = _('Clone %(name)s') % {'name': context['verbose_name']}
        return context

    def pre_save_clone(self, original, cloned):
        pass

class ObjectDeleteView(TenantScopingViewMixin, PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, CachedObjectMixin, DeleteView):
    template_name = 'generic/object_confirm_delete.html'
    form_class = ConfirmationForm

    def has_permission(self):
        perms = self.get_permission_required()
        try:
            obj = self.get_object()
        except Http404:
            # 404 (not 403) for objects outside the tenant scope: don't reveal
            # whether the pk exists in another tenant. Anonymous users fall
            # through to the permission check (and the login redirect).
            if self.request.user.is_authenticated:
                raise
            obj = None
        return self.request.user.has_perms(perms, obj=obj)

    def get_permission_required(self):
        if self.model:
            app_label = self.model._meta.app_label
            model_name = self.model._meta.model_name
            return (f'{app_label}.delete_{model_name}',)
        if hasattr(self, 'queryset') and self.queryset is not None:
            model = self.queryset.model
            app_label = model._meta.app_label
            model_name = model._meta.model_name
            return (f'{app_label}.delete_{model_name}',)
        return ('',)

    def get_success_url(self):
        if hasattr(self, 'default_return_url') and self.default_return_url:
            fallback = reverse(self.default_return_url)
        elif hasattr(self, 'success_url') and self.success_url:
            fallback = str(self.success_url)
        else:
            try:
                list_view_name = get_model_viewname(self.object.__class__, 'list')
                fallback = reverse(list_view_name)
            except NoReverseMatch:
                fallback = reverse('dashboard')
        return safe_return_url(self.request, self.request.POST.get('return_url'), fallback)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.object
        return kwargs

    def form_valid(self, form):
        obj_repr = str(self.object)
        model = self.object.__class__
        try:
            self.object.delete()
            messages.success(self.request, f"Deleted {model._meta.verbose_name} {obj_repr}.")
            return HttpResponseRedirect(self.get_success_url())
        except ProtectedError as e:
            messages.error(self.request, f"Unable to delete {obj_repr}. Objects are protected: {e}")
            if hasattr(self.object, 'get_absolute_url'):
                return redirect(self.object.get_absolute_url())
            return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        model = self.object.__class__ if self.object else (self.model or None)
        if model is None:
            raise ValueError("Cannot determine model for delete view.")
        context['model'] = self.model or model
        context['verbose_name'] = model._meta.verbose_name
        context['title'] = _("Delete {verbose_name}: {object}").format(verbose_name=model._meta.verbose_name, object=self.object)
        
        if hasattr(self.object, 'get_absolute_url'):
            context['cancel_url'] = self.object.get_absolute_url()
        else:
            try:
                list_view_name = get_model_viewname(model, 'list')
                context['cancel_url'] = reverse(list_view_name)
            except NoReverseMatch:
                context['cancel_url'] = reverse('dashboard')
        context['return_url'] = safe_return_url(self.request, self.request.GET.get('return_url'), context['cancel_url'])
        
        base_breadcrumbs = [
            (reverse('dashboard'), _('Dashboard')),
            (context['cancel_url'], model._meta.verbose_name_plural),
            (None, _("Delete {object}").format(object=self.object))
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        context['help_url'] = get_help_url(self, model._meta.app_label, model._meta.model_name)
        return context

class ObjectImportView(PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, TemplateView):
    model_form = None
    template_name = 'generic/object_import.html'

    def get_permission_required(self):
        model = self._get_model()
        if model:
            return (f'{model._meta.app_label}.add_{model._meta.model_name}',)
        return ('',)

    def _get_model(self):
        form_cls = getattr(self, 'model_form', None)
        if form_cls and hasattr(form_cls, 'model') and form_cls.model:
            return form_cls.model
        if hasattr(self, 'model') and self.model:
            return self.model
        return None

    def get_form_class(self):
        if self.model_form:
            return self.model_form
        
        model = self._get_model()
        if not model:
            raise ImproperlyConfigured(f"{self.__class__.__name__} needs a model attribute or model_form.")
            
        from core.forms.import_forms import BulkImportForm
        from django.db import models
        
        required_fields = []
        optional_fields = []
        
        for field in model._meta.fields:
            if field.primary_key or field.auto_created or not field.editable:
                continue
            if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                if not field.blank and not field.null and field.default is models.NOT_PROVIDED:
                    required_fields.append(field.name)
                else:
                    optional_fields.append(field.name)
            elif not field.blank and not field.null and field.default is models.NOT_PROVIDED:
                required_fields.append(field.name)
            else:
                optional_fields.append(field.name)
                
        target_model = model
        target_required = list(required_fields)
        target_optional = list(optional_fields)
        
        class DynamicBulkImportForm(BulkImportForm):
            model = target_model
            required_fields = target_required
            optional_fields = target_optional
            
            def map_row(self, row):
                mapped = {}
                
                # Check for primary key in the CSV/YAML row to support UPSERT (NetBox Gold Standard)
                pk_name = self.model._meta.pk.name
                pk_val = row.get('id') or row.get(pk_name)
                if pk_val and pk_val.strip():
                    mapped[pk_name] = pk_val.strip()

                for k in self.field_names:
                    if k not in row:
                        continue
                    val = row.get(k, '').strip()
                    if not val:
                        continue
                    field = self.model._meta.get_field(k)
                    if field.is_relation and field.many_to_one:
                        related_model = field.related_model
                        obj = None
                        
                        # 1. Primary Key Lookup
                        if val.isdigit():
                            obj = related_model.objects.filter(pk=int(val)).first()
                            
                        # 2. Case-Sensitive Attribute Lookup
                        if not obj:
                            lookup_fields = ['slug', 'name', 'model', 'username', 'upn']
                            for lookup in lookup_fields:
                                if hasattr(related_model, lookup):
                                    obj = related_model.objects.filter(**{lookup: val}).first()
                                    if obj:
                                        break
                                        
                        # 3. Case-Insensitive Attribute Lookup (Bilingual / Human-Friendly)
                        if not obj:
                            for lookup in lookup_fields:
                                if hasattr(related_model, lookup):
                                    obj = related_model.objects.filter(**{f"{lookup}__iexact": val}).first()
                                    if obj:
                                        break
                                        
                        if obj:
                            mapped[field.attname] = obj.pk
                        else:
                            mapped[field.attname] = val  # Fallback to trigger safe model clean validation error
                    else:
                        mapped[k] = val
                return mapped

                
        return DynamicBulkImportForm

    def get(self, request, *args, **kwargs):
        form = self.get_form_class()()
        return self._render_response(request, form)

    def post(self, request, *args, **kwargs):
        form = self.get_form_class()(request.POST, request.FILES)

        if '_preview' in request.POST:
            if form.is_valid():
                rows = form._rows_data
                request.session['import_rows'] = rows
                request.session['import_delimiter'] = form.cleaned_data.get('delimiter', ',')
                context = {
                    'form': form,
                    'preview_rows': rows,
                    'preview_headers': form.field_names if rows else [],
                    'title': self._get_title(),
                    'cancel_url': self._get_cancel_url(),
                }
                return self.render_to_response(context)
            return self._render_response(request, form, errors=[str(e) for e in form.errors.values()])

        if '_confirm' in request.POST:
            rows = request.session.get('import_rows', [])
            if rows:
                from django.contrib.contenttypes.models import ContentType
                from core.models import Job
                from django_q.tasks import async_task

                model = self._get_model()
                ct = ContentType.objects.get_for_model(model)
                
                # Create background Job tracker instance
                job = Job.objects.create(
                    name=f"Bulk Import: {str(model._meta.verbose_name_plural).title()}",
                    model=ct,
                    status=Job.STATUS_PENDING
                )
                
                # Dispatch async task to worker queue safely after transaction commits (with sync bypass for tests)
                from django.db import transaction
                from django.conf import settings
                from core.managers import get_current_tenant
                current_tenant = get_current_tenant()
                tenant_id = current_tenant.pk if current_tenant else None

                if getattr(settings, 'Q_CLUSTER', {}).get('sync', False):
                    async_task(
                        'core.tasks.import_csv_task',
                        job.pk,
                        rows,
                        model._meta.app_label,
                        model._meta.model_name,
                        request.user.pk,
                        tenant_id=tenant_id
                    )
                else:
                    transaction.on_commit(
                        lambda job_pk=job.pk, r=rows, app=model._meta.app_label, name=model._meta.model_name, u_pk=request.user.pk, t_id=tenant_id: async_task(
                            'core.tasks.import_csv_task',
                            job_pk,
                            r,
                            app,
                            name,
                            u_pk,
                            tenant_id=t_id
                        )
                    )

                
                messages.success(
                    request,
                    f"Asynchronous import job '{job.name}' enqueued successfully! Tracking progress in real-time."
                )
                
                request.session.pop('import_rows', None)
                request.session.pop('import_delimiter', None)
                
                try:
                    return redirect('job_detail', pk=job.pk)
                except NoReverseMatch:
                    return redirect(f"/jobs/{job.pk}/")
            return self._render_response(request, form, errors=['No import data found. Please upload a file first.'])

        return self._render_response(request, form)

    def _get_fields_info(self):
        model = self._get_model()
        fields_info = []
        if model:
            from django.db import models
            for field in model._meta.fields:
                if field.primary_key or field.auto_created or not field.editable:
                    continue
                
                description = field.help_text or field.verbose_name or ''
                if description:
                    description = str(description)
                
                is_relation = field.is_relation and field.many_to_one
                accessor = ''
                choices = []
                if is_relation:
                    related_model = field.related_model
                    accessor = 'slug' if hasattr(related_model, 'slug') else 'name'
                    description = _("Relation to {model_name} (resolves by ID, Slug or Name).").format(
                        model_name=str(related_model._meta.verbose_name)
                    )
                
                if field.choices:
                    choices = [(val, label) for val, label in field.choices]
                
                if isinstance(field, models.DateField):
                    description = f"{description} Format: YYYY-MM-DD"
                elif isinstance(field, models.BooleanField):
                    description = f"{description} Specify true or false"
                
                required = False
                if is_relation:
                    if not field.blank and not field.null and field.default is models.NOT_PROVIDED:
                        required = True
                elif not field.blank and not field.null and field.default is models.NOT_PROVIDED:
                    required = True
                
                fields_info.append({
                    'name': field.name,
                    'required': required,
                    'accessor': accessor,
                    'description': description,
                    'choices': choices,
                })
        return fields_info

    def _render_response(self, request, form, **extra_context):
        context = {
            'form': form,
            'title': self._get_title(),
            'cancel_url': self._get_cancel_url(),
            'fields': self._get_fields_info(),
            **extra_context,
        }
        return self.render_to_response(context)


    def _get_title(self):
        model = self._get_model()
        if model:
            return _('Import {verbose_name_plural}').format(verbose_name_plural=model._meta.verbose_name_plural)
        return _('Import Objects')

    def _get_cancel_url(self):
        model = self._get_model()
        if model:
            try:
                list_view_name = get_model_viewname(model, 'list')
                return reverse(list_view_name)
            except NoReverseMatch:
                pass
        return reverse('dashboard')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs) if hasattr(super(), 'get_context_data') else {}
        context['title'] = kwargs.get('title', self._get_title())
        context['cancel_url'] = kwargs.get('cancel_url', self._get_cancel_url())
        context['breadcrumbs'] = [
            (reverse('dashboard'), _('Dashboard')),
            (context['cancel_url'], self._get_model()._meta.verbose_name_plural if self._get_model() else _('List')),
            (None, context['title']),
        ]
        return context

    def render_to_response(self, context, **response_kwargs):
        context['breadcrumbs'] = context.get('breadcrumbs', [])
        return BaseHTMXView.render_to_response(self, context, **response_kwargs)

class ObjectBulkEditView(PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, TemplateResponseMixin, View):
    queryset = None
    form_class = None
    template_name = 'generic/object_bulk_edit.html'

    def get_permission_required(self):
        model = self._get_model()
        if model:
            return (f'{model._meta.app_label}.change_{model._meta.model_name}',)
        return ('',)

    def _get_model(self):
        if self.queryset is not None:
            return self.queryset.model
        if hasattr(self, 'model') and self.model:
            return self.model
        if self.form_class and hasattr(self.form_class, '_meta'):
            return self.form_class._meta.model
        if hasattr(self, 'request') and self.request:
            model_name = self.request.POST.get('model_name') or self.request.GET.get('model_name')
            if model_name:
                try:
                    app_label, model_name = model_name.split('.')
                    return apps.get_model(app_label, model_name)
                except (ValueError, LookupError):
                    pass
        return None

    def _get_queryset(self, pks):
        qs = self.queryset if self.queryset is not None else self._get_model().objects.all()
        if hasattr(qs, 'filter_by_tenant'):
            qs = qs.filter_by_tenant()
        return qs.filter(pk__in=pks)

    def _get_bulk_edit_form(self, data=None, model=None):
        form_class = getattr(self, 'form_class', None) or BulkEditForm
        import inspect
        sig = inspect.signature(form_class.__init__)
        if 'model' in sig.parameters:
            return form_class(data, model=model)
        return form_class(data)

    def post(self, request, *args, **kwargs):
        from django.db import transaction

        pks = request.POST.getlist('pk')
        model = self._get_model()
        return_url = safe_return_url(
            request,
            request.POST.get('return_url') or request.META.get('HTTP_REFERER'),
            reverse('dashboard'),
        )
        raw_selected_fields = request.POST.getlist('_selected_fields')
        selected_fields = [f for f in raw_selected_fields if f not in ('add_tags', 'remove_tags')]

        if not pks:
            messages.warning(request, f"No {model._meta.verbose_name_plural} were selected.")
            return HttpResponseRedirect(return_url)

        queryset = self._get_queryset(pks)

        if '_apply' in request.POST:
            form = self._get_bulk_edit_form(request.POST, model)

            if form.is_valid() and (selected_fields or ('add_tags' in raw_selected_fields and form.cleaned_data.get('add_tags')) or ('remove_tags' in raw_selected_fields and form.cleaned_data.get('remove_tags'))):
                
                # Validate tenant assignment permissions for bulk edits
                if 'tenant' in selected_fields and 'tenant' in form.cleaned_data:
                    selected_tenant = form.cleaned_data['tenant']
                    if selected_tenant:
                        app_label = model._meta.app_label
                        model_name = model._meta.model_name
                        perm_codename = f'{app_label}.change_{model_name}'
                        if not request.user.has_perm(perm_codename, obj=selected_tenant):
                            messages.error(request, f"You do not have permission to assign objects to tenant '{selected_tenant}'.")
                            form.add_error('tenant', f"You do not have permission to assign objects to tenant '{selected_tenant}'.")
                            context = {
                                'form': form,
                                'model': model,
                                'model_name': f'{model._meta.app_label}.{model._meta.model_name}',
                                'objects': queryset,
                                'object_pks': pks,
                                'return_url': return_url,
                                'selected_fields': selected_fields,
                                'verbose_name': model._meta.verbose_name,
                                'verbose_name_plural': model._meta.verbose_name_plural,
                                'title': f'Bulk Edit {str(model._meta.verbose_name_plural).title()}',
                                'breadcrumbs': [
                                    (reverse('dashboard'), 'Dashboard'),
                                    (return_url, str(model._meta.verbose_name_plural).title()),
                                    (None, f'Bulk Edit ({len(pks)})'),
                                ],
                            }
                            return self.render_to_response(context)

                updated_count = 0

                with transaction.atomic():
                    for obj in queryset:
                        if hasattr(obj, 'snapshot') and callable(obj.snapshot):
                            obj.snapshot()

                        for field_name in selected_fields:
                            if field_name in form.cleaned_data:
                                val = form.cleaned_data[field_name]
                                field = model._meta.get_field(field_name)
                                if field.is_relation and val == '':
                                    val = None
                                setattr(obj, field.attname, val)

                        obj.full_clean()
                        obj.save()

                        if hasattr(obj, 'tags'):
                            if 'add_tags' in raw_selected_fields and form.cleaned_data.get('add_tags'):
                                obj.tags.add(*form.cleaned_data['add_tags'])
                            if 'remove_tags' in raw_selected_fields and form.cleaned_data.get('remove_tags'):
                                obj.tags.remove(*form.cleaned_data['remove_tags'])

                        updated_count += 1

                messages.success(
                    request,
                    f"Updated {updated_count} {model._meta.verbose_name_plural}."
                )
                return HttpResponseRedirect(return_url)
            else:
                if not (selected_fields or form.cleaned_data.get('add_tags') or form.cleaned_data.get('remove_tags')):
                    messages.warning(request, "No fields or tags were selected for editing.")
                else:
                    for field_name, errors in form.errors.items():
                        field_label = form[field_name].label if field_name in form else field_name
                        messages.error(request, f"Error in field '{field_label}': {', '.join(errors)}")
                form = self._get_bulk_edit_form(request.POST if '_apply' in request.POST else None, model)
        else:
            form = self._get_bulk_edit_form(model=model)

        context = {
            'form': form,
            'model': model,
            'model_name': f'{model._meta.app_label}.{model._meta.model_name}',
            'objects': queryset,
            'object_pks': pks,
            'return_url': return_url,
            'selected_fields': selected_fields,
            'verbose_name': model._meta.verbose_name,
            'verbose_name_plural': model._meta.verbose_name_plural,
            'title': f'Bulk Edit {str(model._meta.verbose_name_plural).title()}',
            'breadcrumbs': [
                (reverse('dashboard'), 'Dashboard'),
                (return_url, str(model._meta.verbose_name_plural).title()),
                (None, f'Bulk Edit ({len(pks)})'),
            ],
        }
        return self.render_to_response(context)

class ObjectBulkDeleteView(PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, TemplateResponseMixin, View):
    queryset = None
    template_name = 'generic/object_confirm_bulk_delete.html'

    def get_permission_required(self):
        model = self._get_model()
        if model:
            return (f'{model._meta.app_label}.delete_{model._meta.model_name}',)
        return ('',)

    def _get_model(self):
        if self.queryset is not None:
            return self.queryset.model
        if hasattr(self, 'model') and self.model:
            return self.model
        if hasattr(self, 'request') and self.request:
            model_name = self.request.POST.get('model_name') or self.request.GET.get('model_name')
            if model_name:
                try:
                    app_label, model_name = model_name.split('.')
                    return apps.get_model(app_label, model_name)
                except (ValueError, LookupError):
                    pass
        return None

    def _get_queryset(self, pks):
        qs = self.queryset if self.queryset is not None else self._get_model().objects.all()
        if hasattr(qs, 'filter_by_tenant'):
            qs = qs.filter_by_tenant()
        return qs.filter(pk__in=pks)

    def post(self, request, *args, **kwargs):
        from django.db import transaction

        pks = request.POST.getlist('pk')
        model = self._get_model()
        return_url = safe_return_url(
            request,
            request.POST.get('return_url') or request.META.get('HTTP_REFERER'),
            reverse('dashboard'),
        )

        if not pks:
            messages.warning(request, f"No {model._meta.verbose_name_plural} were selected.")
            return HttpResponseRedirect(return_url)

        queryset = self._get_queryset(pks)
        objects_to_delete = list(queryset)

        if not objects_to_delete:
            messages.warning(request, f"No valid {model._meta.verbose_name_plural} selected for deletion.")
            return HttpResponseRedirect(return_url)

        if '_confirm' in request.POST:
            try:
                deleted_count = 0
                with transaction.atomic():
                    for obj in objects_to_delete:
                        if hasattr(obj, 'snapshot') and callable(obj.snapshot):
                            obj.snapshot()
                        obj.delete()
                        deleted_count += 1

                messages.success(
                    request,
                    f"Successfully deleted {deleted_count} {model._meta.verbose_name_plural}."
                )
                return HttpResponseRedirect(return_url)
            except ProtectedError as e:
                messages.error(
                    request,
                    f"Could not delete objects due to protected relationships: {e}"
                )
                return HttpResponseRedirect(return_url)
        else:
            context = {
                'model': model,
                'model_name': f'{model._meta.app_label}.{model._meta.model_name}',
                'model_verbose_name': model._meta.verbose_name,
                'model_verbose_name_plural': model._meta.verbose_name_plural,
                'objects': objects_to_delete,
                'object_pks': pks,
                'return_url': return_url,
                'title': f'Confirm Bulk Deletion',
                'breadcrumbs': [
                    (reverse('dashboard'), 'Dashboard'),
                    (return_url, str(model._meta.verbose_name_plural).title()),
                    (None, f'Delete ({len(objects_to_delete)})'),
                ],
            }
            return self.render_to_response(context)

@login_required
def table_config(request, model_name):
    app_label, table_part = model_name.split('.')
    app_config = apps.get_app_config(app_label)
    table_module = import_string(f'{app_config.name}.tables')
    TableClass = getattr(table_module, table_part)
    
    table = TableClass([])
    table_verbose_name = str(TableClass.Meta.model._meta.verbose_name_plural).title()
    
    prefs, _ = UserPreference.objects.get_or_create(user=request.user)
    table_key_for_form = f'{app_label}.{table_part}'
    user_config = prefs.data.get('tables', {}).get(app_label, {}).get(table_part, {})
    logger.debug("Fetched user_config for %s.%s: %s", app_label, table_part, user_config)

    form = TableConfigForm(table=table, user_config=user_config) 

    template = get_template('core/includes/table_config_modal.html')
    context = {
        'form': form,
        'table_name': table_key_for_form,
        'table_verbose_name': table_verbose_name,
    }
    return HttpResponse(template.render(context, request))


from itambox.views.features import ObjectExportView  # noqa: E402


class GenericObjectImportView(ObjectImportView):
    def _get_model(self):
        app_label = self.kwargs.get('app_label')
        model_name = self.kwargs.get('model_name')
        return apps.get_model(app_label, model_name)


class ObjectRestoreView(PermissionRequiredMixin, LoginRequiredMixin, View):
    def has_permission(self):
        self.content_type = get_object_or_404(ContentType, pk=self.kwargs['content_type_id'])
        self.model = self.content_type.model_class()
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        
        manager = getattr(self.model, 'all_objects', self.model._base_manager)
        self.object = get_object_or_404(manager, pk=self.kwargs['object_id'])
        
        if not self.request.user.is_superuser and not self.request.user.has_perm('core.change_recyclebin'):
            return False
            
        return self.request.user.has_perm(f'{app_label}.change_{model_name}', self.object)

    def post(self, request, *args, **kwargs):
        import json
        from django.http import HttpResponse
        self.object.restore()
        
        success_msg = _("Restored {model} {object}").format(
            model=self.model._meta.verbose_name,
            object=self.object
        )
        
        if request.headers.get('HX-Request') or getattr(request, 'htmx', False):
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                "tableRefreshRequired": None,
                "showMessage": {
                    "message": success_msg,
                    "level": "success"
                }
            })
            return response

        messages.success(request, success_msg)
        try:
            list_url = reverse(get_model_viewname(self.model, 'list')) + "?deleted=true"
        except Exception:
            list_url = '/'
        return HttpResponseRedirect(
            safe_return_url(request, request.META.get('HTTP_REFERER'), list_url)
        )


class ObjectPurgeView(PermissionRequiredMixin, LoginRequiredMixin, View):
    def has_permission(self):
        self.content_type = get_object_or_404(ContentType, pk=self.kwargs['content_type_id'])
        self.model = self.content_type.model_class()
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        
        manager = getattr(self.model, 'all_objects', self.model._base_manager)
        self.object = get_object_or_404(manager, pk=self.kwargs['object_id'])
        
        if not self.request.user.is_superuser and not self.request.user.has_perm('core.delete_recyclebin'):
            return False
            
        return self.request.user.has_perm(f'{app_label}.delete_{model_name}', self.object)

    def post(self, request, *args, **kwargs):
        import json
        from django.http import HttpResponse
        obj_repr = str(self.object)
        self.object.delete(force_hard_delete=True)
        
        success_msg = _("Permanently purged {model} {object}").format(
            model=self.model._meta.verbose_name,
            object=obj_repr
        )
        
        if request.headers.get('HX-Request') or getattr(request, 'htmx', False):
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                "tableRefreshRequired": None,
                "showMessage": {
                    "message": success_msg,
                    "level": "success"
                }
            })
            return response

        messages.success(request, success_msg)
        try:
            list_url = reverse(get_model_viewname(self.model, 'list')) + "?deleted=true"
        except Exception:
            list_url = '/'
        return HttpResponseRedirect(
            safe_return_url(request, request.META.get('HTTP_REFERER'), list_url)
        )


class ObjectBulkRestoreView(PermissionRequiredMixin, LoginRequiredMixin, View):
    def has_permission(self):
        self.content_type = get_object_or_404(ContentType, pk=self.kwargs['content_type_id'])
        self.model = self.content_type.model_class()
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        
        if not self.request.user.is_superuser and not self.request.user.has_perm('core.change_recyclebin'):
            return False
            
        return self.request.user.has_perm(f'{app_label}.change_{model_name}')

    def post(self, request, *args, **kwargs):
        import json
        from django.http import HttpResponse
        pks = request.POST.getlist('pk')
        if not pks:
            messages.warning(request, _("No items selected."))
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))
            
        manager = getattr(self.model, 'all_objects', self.model._base_manager)
        queryset = manager.filter(pk__in=pks, deleted_at__isnull=False)
        
        count = 0
        from django.db import transaction
        with transaction.atomic():
            for obj in queryset:
                obj.restore()
                count += 1
                
        success_msg = _("Successfully restored {count} {model_plural}.").format(
            count=count,
            model_plural=self.model._meta.verbose_name_plural
        )
        
        if request.headers.get('HX-Request') or getattr(request, 'htmx', False):
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                "tableRefreshRequired": None,
                "showMessage": {
                    "message": success_msg,
                    "level": "success"
                }
            })
            return response

        messages.success(request, success_msg)
        try:
            list_url = reverse(get_model_viewname(self.model, 'list')) + "?deleted=true"
        except Exception:
            list_url = '/'
        return HttpResponseRedirect(
            safe_return_url(request, request.META.get('HTTP_REFERER'), list_url)
        )


class ObjectBulkPurgeView(PermissionRequiredMixin, LoginRequiredMixin, View):
    def has_permission(self):
        self.content_type = get_object_or_404(ContentType, pk=self.kwargs['content_type_id'])
        self.model = self.content_type.model_class()
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        
        if not self.request.user.is_superuser and not self.request.user.has_perm('core.delete_recyclebin'):
            return False
            
        return self.request.user.has_perm(f'{app_label}.delete_{model_name}')

    def post(self, request, *args, **kwargs):
        import json
        from django.http import HttpResponse
        pks = request.POST.getlist('pk')
        if not pks:
            messages.warning(request, _("No items selected."))
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))
            
        manager = getattr(self.model, 'all_objects', self.model._base_manager)
        queryset = manager.filter(pk__in=pks, deleted_at__isnull=False)
        
        count = 0
        from django.db import transaction
        with transaction.atomic():
            for obj in queryset:
                obj.delete(force_hard_delete=True)
                count += 1
                
        success_msg = _("Successfully permanently purged {count} {model_plural}.").format(
            count=count,
            model_plural=self.model._meta.verbose_name_plural
        )
        
        if request.headers.get('HX-Request') or getattr(request, 'htmx', False):
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                "tableRefreshRequired": None,
                "showMessage": {
                    "message": success_msg,
                    "level": "success"
                }
            })
            return response

        messages.success(request, success_msg)
        try:
            list_url = reverse(get_model_viewname(self.model, 'list')) + "?deleted=true"
        except Exception:
            list_url = '/'
        return HttpResponseRedirect(
            safe_return_url(request, request.META.get('HTTP_REFERER'), list_url)
        )
