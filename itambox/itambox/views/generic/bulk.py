import inspect
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import ProtectedError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.generic import View
from django.views.generic.base import TemplateResponseMixin

from core.forms import BulkEditForm
from itambox.views.htmx import BaseHTMXView
from itambox.views.generic.mixins import BulkViewMixin
from itambox.views.generic.utils import safe_return_url

logger = logging.getLogger(__name__)


class ObjectBulkEditView(BulkViewMixin, PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, TemplateResponseMixin, View):
    queryset = None
    form_class = None
    template_name = 'generic/object_bulk_edit.html'

    def get_permission_required(self):
        model = self._get_model()
        if model:
            return (f'{model._meta.app_label}.change_{model._meta.model_name}',)
        return ('',)

    def _get_bulk_edit_form(self, data=None, model=None):
        form_class = getattr(self, 'form_class', None) or BulkEditForm
        sig = inspect.signature(form_class.__init__)
        if 'model' in sig.parameters:
            return form_class(data, model=model)
        return form_class(data)

    def post(self, request, *args, **kwargs):
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
                    f"Updated {updated_count} {model._meta.verbose_name_plural}.",
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


class ObjectBulkDeleteView(BulkViewMixin, PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, TemplateResponseMixin, View):
    queryset = None
    template_name = 'generic/object_confirm_bulk_delete.html'

    def get_permission_required(self):
        model = self._get_model()
        if model:
            return (f'{model._meta.app_label}.delete_{model._meta.model_name}',)
        return ('',)

    def post(self, request, *args, **kwargs):
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
                    f"Successfully deleted {deleted_count} {model._meta.verbose_name_plural}.",
                )
                return HttpResponseRedirect(return_url)
            except ProtectedError as e:
                messages.error(
                    request,
                    f"Could not delete objects due to protected relationships: {e}",
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
                'title': 'Confirm Bulk Deletion',
                'breadcrumbs': [
                    (reverse('dashboard'), 'Dashboard'),
                    (return_url, str(model._meta.verbose_name_plural).title()),
                    (None, f'Delete ({len(objects_to_delete)})'),
                ],
            }
            return self.render_to_response(context)
