import logging

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, NoReverseMatch
from django.utils.translation import gettext as _
from django.views.generic import UpdateView

from itambox.utils import get_model_viewname, get_help_url
from itambox.views.htmx import BaseHTMXView
from itambox.views.generic.mixins import TenantScopingViewMixin, CachedObjectMixin
from itambox.views.generic.utils import safe_return_url

logger = logging.getLogger(__name__)


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
                HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
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
                return redirect(reverse(edit_view_name, kwargs={'pk': self.object.pk}))
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
            (None, context['title']),
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
