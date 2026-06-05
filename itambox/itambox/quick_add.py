"""
Quick-Add mixin for FK/M2M field inline creation.

Usage on a CreateView:
    class ManufacturerQuickAddView(QuickAddMixin, ObjectEditView):
        model = Manufacturer
        model_form = ManufacturerForm
        quick_add_target = 'id_manufacturer'  # ID of the parent select to refresh
"""

from django.http import HttpResponse
from django.template.loader import render_to_string


class QuickAddMixin:
    """
    Mixin that modifies the form response for quick-add scenarios.

    When the form is submitted in a quick-add modal, instead of redirecting,
    returns a script that closes the modal and triggers a select refresh.
    """

    quick_add_target = None

    def is_quick_add(self):
        return self.request.GET.get('_quickadd') == '1'

    def get_template_names(self):
        if self.is_quick_add():
            return ['generic/includes/quick_add_modal.html']
        return super().get_template_names()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.is_quick_add():
            context['model_label'] = (
                self.model._meta.verbose_name.title()
                if hasattr(self, 'model') and self.model
                else ''
            )
            form = context.get('form')
            if form:
                if not hasattr(form, 'helper') or form.helper is None:
                    from crispy_forms.helper import FormHelper
                    form.helper = FormHelper(form)
                form.helper.form_tag = False
            context['quick_add_form'] = form
        return context

    def form_valid(self, form):
        if self.is_quick_add():
            self.object = form.save()
            target = getattr(self, 'quick_add_target', None) or ''
            value = str(self.object)
            pk = self.object.pk

            script = render_to_string('generic/includes/quick_add_success.html', {
                'target_id': target,
                'value': value,
                'pk': pk,
            })
            return HttpResponse(script)

        return super().form_valid(form)
