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
from django.utils.module_loading import import_string
from django.views.decorators.http import require_POST
from django.db.models import ProtectedError
from django.template.loader import get_template
from django.template import TemplateDoesNotExist

from core.registry import registry
from core.utils import get_model_viewname, get_table_for_model
from core.models import ObjectChange, JournalEntry, ImageAttachment, FileAttachment
from core.tables import ObjectChangeTable
from core.forms import ConfirmationForm, JournalEntryForm, BulkEditForm
from users.forms import TableConfigForm
from users.models import UserPreference

logger = logging.getLogger(__name__)

class BaseHTMXView:
    page_body_partial_name = "htmx/page_body_content_wrapper.html"
    content_partial_name = None

    def get_template_names(self):
        if not hasattr(self, 'template_name') or not self.template_name:
            if hasattr(super(), 'get_template_names'):
                return super().get_template_names()
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} needs a template_name attribute defined."
            )
        return [self.template_name]

    def render_to_response(self, context, **response_kwargs):
        request = self.request

        if getattr(request, 'htmx', False):
            context['request'] = request

            target = getattr(request.htmx, 'target', '') or ''
            is_boosted_main_swap = getattr(request.htmx, 'boosted', False) or \
                                   target in ('page-content-wrapper', '#page-content-wrapper', 'page-body-main', '#page-body-main')

            if is_boosted_main_swap:
                context['base_template'] = 'base_htmx.html'
                context.setdefault('title', 'AssetBox')
                context.setdefault('breadcrumbs', [(reverse('dashboard'), 'Dashboard'), (None, context['title'])])
                context.setdefault('page_actions', None)
            elif self.content_partial_name:
                return render(request, self.content_partial_name, context)

        if hasattr(super(), 'render_to_response'):
            return super().render_to_response(context, **response_kwargs)
        else:
            if hasattr(self, 'response_class') and hasattr(self, 'get_template_names'):
                 return self.response_class(
                    request=request,
                    template=self.get_template_names(),
                    context=context,
                    using=self.template_engine,
                    **response_kwargs
                )
            else:
                raise ImproperlyConfigured(f"{self.__class__.__name__} or its superclasses must provide a render_to_response method or be mixed with TemplateResponseMixin.")

class ObjectListView(PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, ListView):
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
            plural_name = model._meta.verbose_name_plural.lower().replace(' ', '')

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
        queryset = super().get_queryset()
        if self.filterset:
            self.filter = self.filterset(self.request.GET, queryset)
            if not self.filter.is_valid():
                logger.warning('Invalid filter params for %s: %s', self.__class__.__name__, self.filter.errors)
                self.filter = None
            else:
                return self.filter.qs
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

        context.setdefault('title', _model._meta.verbose_name_plural.title())

        try:
            create_url_name = get_model_viewname(_model, 'create')
            reverse(create_url_name)
            context['create_url_name'] = create_url_name
        except NoReverseMatch:
            context['create_url_name'] = None

        try:
            import_url_name = get_model_viewname(_model, 'import')
            reverse(import_url_name)
            context['import_url_name'] = import_url_name
        except NoReverseMatch:
            context['import_url_name'] = None

        context['action_buttons'] = self.action_buttons
        if 'add' in self.action_buttons and not context['create_url_name']:
            logger.debug("'add' action button enabled but create URL not resolvable for %s", self.model)

        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
            (None, context['title'])
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        return context

class ObjectDetailView(LoginRequiredMixin, BaseHTMXView, DetailView):
    template_name = 'generic/object_detail.html'
    detail_page_body_partial_name = "htmx/detail_page_wrapper.html"
    layout = None

    def get_template_names(self):
        if self.template_name and self.template_name != 'generic/object_detail.html':
            return [self.template_name]

        obj = self.get_object()
        if obj:
            app_label = obj._meta.app_label
            model_name = obj._meta.model_name
            plural_name = obj._meta.verbose_name_plural.lower().replace(" ", "")

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
        verbose_name = obj._meta.verbose_name.title()
        verbose_name_plural = obj._meta.verbose_name_plural.title()

        context['model'] = obj.__class__
        context['layout'] = self.layout

        can_change = self.request.user.has_perm(f'{app_label}.change_{model_name}')
        can_delete = self.request.user.has_perm(f'{app_label}.delete_{model_name}')
        context['can_change'] = can_change
        context['can_delete'] = can_delete
        context['edit_url'] = None
        if can_change:
            try:
                context['edit_url'] = reverse(f'{app_label}:{model_name}_update', kwargs={'pk': obj.pk})
            except NoReverseMatch:
                if hasattr(obj, 'slug') and obj.slug:
                    try:
                        context['edit_url'] = reverse(f'{app_label}:{model_name}_update', kwargs={'slug': obj.slug})
                    except NoReverseMatch:
                        logger.debug("Edit URL not resolvable for %s obj=%s slug=%s", model_name, obj.pk, obj.slug)
        
        context['delete_url'] = None
        if can_delete:
            try:
                context['delete_url'] = reverse(f'{app_label}:{model_name}_delete', kwargs={'pk': obj.pk})
            except NoReverseMatch:
                if hasattr(obj, 'slug') and obj.slug:
                    try:
                        context['delete_url'] = reverse(f'{app_label}:{model_name}_delete', kwargs={'slug': obj.slug})
                    except NoReverseMatch:
                        logger.debug("Delete URL not resolvable for %s obj=%s slug=%s", model_name, obj.pk, obj.slug)
        
        context['title'] = str(obj)
        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
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
                            label = related_model._meta.verbose_name_plural.title()
                            
                            related_objects_list.append({
                                'label': label,
                                'count': count,
                                'url': url
                            })
                        except NoReverseMatch:
                            continue
            
            related_objects_list.sort(key=lambda x: x['label'])
            context['related_objects_list'] = related_objects_list

        return context

class ObjectEditView(PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, UpdateView):
    model_form = None
    template_name = 'generic/object_edit.html'

    def get_permission_required(self):
        model = self._get_model()
        if model:
            app_label = model._meta.app_label
            model_name = model._meta.model_name
            if self.get_object():
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
        if self.request.POST.get('return_url'):
            return self.request.POST.get('return_url')
        if hasattr(self, 'default_return_url') and self.default_return_url:
            return reverse(self.default_return_url)
        if self.object and hasattr(self.object, 'get_absolute_url'):
            return self.object.get_absolute_url()
        _model = self._get_model()
        if _model:
            try:
                list_view_name = get_model_viewname(_model, 'list')
                return reverse(list_view_name)
            except NoReverseMatch:
                logger.debug("List view URL fallback failed for model %s", _model)
        return reverse('dashboard')

    def form_valid(self, form):
        is_creating = self.object is None
        _model = self._get_model()
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

        is_editing = self.object is not None
        context['model'] = _model
        context['verbose_name'] = _model._meta.verbose_name
        context['is_editing'] = is_editing
        context['title'] = f"{'Edit' if is_editing else 'Create'} {context['verbose_name']}"

        if self.object and hasattr(self.object, 'get_absolute_url'):
            context['cancel_url'] = self.object.get_absolute_url()
        else:
            try:
                 list_view_name = get_model_viewname(_model, 'list')
                 context['cancel_url'] = reverse(list_view_name)
            except NoReverseMatch:
                 context['cancel_url'] = reverse('dashboard')
        
        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
            (context['cancel_url'], _model._meta.verbose_name_plural.title()),
            (None, context['title'])
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        return context

class ObjectCloneView(ObjectEditView):
    def get_object(self, queryset=None):
        original = get_object_or_404(self.model, pk=self.kwargs['pk'])
        cloned = original.clone()
        
        if hasattr(cloned, 'name'):
            cloned.name = f"{original.name} (Copy)"
        elif hasattr(cloned, 'model'):
            cloned.model = f"{original.model} (Copy)"
            
        if hasattr(cloned, 'slug'):
            cloned.slug = ''
            
        self.pre_save_clone(original, cloned)
        cloned.save()
        
        if hasattr(original, 'tags') and hasattr(cloned, 'tags'):
            cloned.tags.set(original.tags.all())
            
        return cloned

    def pre_save_clone(self, original, cloned):
        pass

class ObjectDeleteView(PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, DeleteView):
    template_name = 'generic/object_confirm_delete.html'
    form_class = ConfirmationForm

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
        if self.request.POST.get('return_url'):
            return self.request.POST.get('return_url')
        if hasattr(self, 'default_return_url') and self.default_return_url:
            return reverse(self.default_return_url)
        try:
            list_view_name = get_model_viewname(self.model, 'list')
            return reverse(list_view_name)
        except NoReverseMatch:
            return reverse('dashboard')

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
        context['title'] = f"Delete {context['verbose_name']}: {self.object}"
        
        if hasattr(self.object, 'get_absolute_url'):
            context['cancel_url'] = self.object.get_absolute_url()
        else:
            try:
                list_view_name = get_model_viewname(model, 'list')
                context['cancel_url'] = reverse(list_view_name)
            except NoReverseMatch:
                context['cancel_url'] = reverse('dashboard')
        context['return_url'] = self.request.GET.get('return_url', context['cancel_url'])
        
        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
            (context['cancel_url'], model._meta.verbose_name_plural.title()),
            (None, f"Delete {self.object}")
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
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
        if self.model_form and hasattr(self.model_form, 'model'):
            return self.model_form.model
        if hasattr(self, 'model') and self.model:
            return self.model
        return None

    def get(self, request, *args, **kwargs):
        form = self.model_form()
        return self._render_response(request, form)

    def post(self, request, *args, **kwargs):
        form = self.model_form(request.POST, request.FILES)

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
                from django.db import transaction
                form = self.model_form()
                form._rows_data = rows
                with transaction.atomic():
                    imported, errors = form.import_data(request=request)
                context = {
                    'form': form,
                    'import_summary': {
                        'imported_count': imported,
                        'row_count': len(rows),
                        'errors_list': errors,
                    },
                    'title': self._get_title(),
                    'cancel_url': self._get_cancel_url(),
                }
                request.session.pop('import_rows', None)
                request.session.pop('import_delimiter', None)
                return self.render_to_response(context)
            return self._render_response(request, form, errors=['No import data found. Please upload a file first.'])

        return self._render_response(request, form)

    def _render_response(self, request, form, **extra_context):
        context = {
            'form': form,
            'title': self._get_title(),
            'cancel_url': self._get_cancel_url(),
            **extra_context,
        }
        return self.render_to_response(context)

    def _get_title(self):
        model = self._get_model()
        if model:
            return f'Import {model._meta.verbose_name_plural.title()}'
        return 'Import Objects'

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
            (reverse('dashboard'), 'Dashboard'),
            (context['cancel_url'], self._get_model()._meta.verbose_name_plural.title() if self._get_model() else 'List'),
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
        return None

    def _get_queryset(self, pks):
        qs = self.queryset if self.queryset is not None else self._get_model().objects.all()
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
        return_url = request.POST.get('return_url') or request.META.get('HTTP_REFERER', reverse('dashboard'))

        if not pks:
            messages.warning(request, f"No {model._meta.verbose_name_plural} were selected.")
            return HttpResponseRedirect(return_url)

        queryset = self._get_queryset(pks)

        if '_apply' in request.POST:
            form = self._get_bulk_edit_form(request.POST, model)
            selected_fields = request.POST.getlist('_selected_fields')

            if form.is_valid() and (selected_fields or form.cleaned_data.get('add_tags') or form.cleaned_data.get('remove_tags')):
                updated_count = 0

                with transaction.atomic():
                    for obj in queryset:
                        if hasattr(obj, 'snapshot') and callable(obj.snapshot):
                            obj.snapshot()

                        for field_name in selected_fields:
                            if field_name in form.cleaned_data:
                                setattr(obj, field_name, form.cleaned_data[field_name])

                        obj.full_clean()
                        obj.save()

                        if hasattr(obj, 'tags'):
                            if form.cleaned_data.get('add_tags'):
                                obj.tags.add(*form.cleaned_data['add_tags'])
                            if form.cleaned_data.get('remove_tags'):
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
                form = self._get_bulk_edit_form(request.POST if '_apply' in request.POST else None, model)
        else:
            form = self._get_bulk_edit_form(model=model)

        context = {
            'form': form,
            'model': model,
            'objects': queryset,
            'object_pks': pks,
            'return_url': return_url,
            'verbose_name': model._meta.verbose_name,
            'verbose_name_plural': model._meta.verbose_name_plural,
            'title': f'Bulk Edit {model._meta.verbose_name_plural.title()}',
            'breadcrumbs': [
                (reverse('dashboard'), 'Dashboard'),
                (return_url, model._meta.verbose_name_plural.title()),
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
        return None

    def _get_queryset(self, pks):
        qs = self.queryset if self.queryset is not None else self._get_model().objects.all()
        return qs.filter(pk__in=pks)

    def post(self, request, *args, **kwargs):
        from django.db import transaction

        pks = request.POST.getlist('pk')
        model = self._get_model()
        return_url = request.POST.get('return_url') or request.META.get('HTTP_REFERER', reverse('dashboard'))

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
                    (return_url, model._meta.verbose_name_plural.title()),
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
    table_verbose_name = TableClass.Meta.model._meta.verbose_name_plural.title()
    
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
