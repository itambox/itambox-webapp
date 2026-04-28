from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseBadRequest
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import View
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from itambox.utils import get_paginate_count
from itambox.panels import Panel

from ..models import ContactRole, ContactAssignment
from ..forms import ContactRoleForm, ContactRoleFilterForm, ContactAssignmentForm
from ..tables import ContactRoleTable, ContactAssignmentTable
from ..filters import ContactRoleFilterSet
from django_tables2 import RequestConfig


class ContactRoleListView(ObjectListView):
    queryset = ContactRole.objects.all()
    filterset = ContactRoleFilterSet
    filterset_form = ContactRoleFilterForm
    table = ContactRoleTable
    action_buttons = ('add',)


class ContactRoleDetailView(ObjectDetailView):
    queryset = ContactRole.objects.prefetch_related('assignments')

    layout = (
        ((Panel('info', 'Contact Role Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = self.get_object()

        assignments_table = ContactAssignmentTable(role.assignments.all(), request=self.request)
        assignments_table.configure(self.request)

        context['assignments_table'] = assignments_table
        return context


class ContactRoleEditView(ObjectEditView):
    queryset = ContactRole.objects.all()
    model = ContactRole
    model_form = ContactRoleForm
    template_name = 'generic/object_edit.html'


class ContactRoleDeleteView(ObjectDeleteView):
    queryset = ContactRole.objects.all()
    model = ContactRole
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:contactrole_list')

    def post(self, request, *args, **kwargs):
        role = self.get_object()
        assignment_count = role.assignments.count()

        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete role '{role}': It is associated with {assignment_count} contact assignment{'s' if assignment_count != 1 else ''}."
            )
            return redirect(role.get_absolute_url())

        return super().post(request, *args, **kwargs)


class ContactAssignmentCreateView(LoginRequiredMixin, View):
    template_name = 'organization/contactassignments/contactassignment_form.html'

    def get(self, request, *args, **kwargs):
        content_type_id = request.GET.get('content_type')
        object_id = request.GET.get('object_id')

        if not content_type_id or not object_id:
            return HttpResponseBadRequest("Missing content_type or object_id")

        content_type = get_object_or_404(ContentType, id=content_type_id)
        target_obj = get_object_or_404(content_type.model_class(), id=object_id)

        form = ContactAssignmentForm(content_type=content_type, object_id=object_id)
        context = {
            'form': form,
            'target_obj': target_obj,
            'content_type': content_type,
            'object_id': object_id,
        }
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        content_type_id = request.POST.get('content_type') or request.GET.get('content_type')
        object_id = request.POST.get('object_id') or request.GET.get('object_id')

        if not content_type_id or not object_id:
            return HttpResponseBadRequest("Missing content_type or object_id")

        content_type = get_object_or_404(ContentType, id=content_type_id)
        target_obj = get_object_or_404(content_type.model_class(), id=object_id)

        form = ContactAssignmentForm(request.POST, content_type=content_type, object_id=object_id)
        if form.is_valid():
            form.save()
            messages.success(request, f"Assigned contact successfully to {target_obj}.")
            return redirect(target_obj.get_absolute_url())

        context = {
            'form': form,
            'target_obj': target_obj,
            'content_type': content_type,
            'object_id': object_id,
        }
        return render(request, self.template_name, context)


class ContactAssignmentDeleteView(ObjectDeleteView):
    queryset = ContactAssignment.objects.all()
    model = ContactAssignment
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        return_url = self.request.GET.get('return_url') or self.request.POST.get('return_url')
        if return_url:
            return return_url
        obj = self.object
        if obj and obj.assigned_object and hasattr(obj.assigned_object, 'get_absolute_url'):
            return obj.assigned_object.get_absolute_url()
        return reverse('dashboard')


class ContactRoleBulkEditView(ObjectBulkEditView):
    queryset = ContactRole.objects.all()


class ContactRoleBulkDeleteView(ObjectBulkDeleteView):
    queryset = ContactRole.objects.all()
