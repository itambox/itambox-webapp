from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.contrib import messages

from core.views import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from core.utils import get_paginate_count
from core.panels import Panel

from ..models import Contact
from ..forms import ContactForm, ContactFilterForm
from ..tables import ContactTable, ContactAssignmentTable
from ..filters import ContactFilterSet
from django_tables2 import RequestConfig


class ContactListView(ObjectListView):
    queryset = Contact.objects.prefetch_related('tags')
    filterset = ContactFilterSet
    filterset_form = ContactFilterForm
    table = ContactTable
    action_buttons = ('add',)


class ContactDetailView(ObjectDetailView):
    queryset = Contact.objects.prefetch_related('tags', 'assignments')

    layout = (
        ((Panel('info', 'Contact Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contact = self.get_object()

        assignments_table = ContactAssignmentTable(contact.assignments.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)

        context['assignments_table'] = assignments_table
        return context


class ContactEditView(ObjectEditView):
    queryset = Contact.objects.all()
    model = Contact
    model_form = ContactForm
    template_name = 'generic/object_edit.html'


class ContactDeleteView(ObjectDeleteView):
    queryset = Contact.objects.all()
    model = Contact
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:contact_list')

    def post(self, request, *args, **kwargs):
        contact = self.get_object()
        assignment_count = contact.assignments.count()

        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete contact '{contact}': It has {assignment_count} assignment{'s' if assignment_count != 1 else ''}."
            )
            return redirect(contact.get_absolute_url())

        return super().post(request, *args, **kwargs)


class ContactBulkEditView(ObjectBulkEditView):
    queryset = Contact.objects.all()


class ContactBulkDeleteView(ObjectBulkDeleteView):
    queryset = Contact.objects.all()
