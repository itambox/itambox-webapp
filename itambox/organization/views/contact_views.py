from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.utils.translation import gettext_lazy as _

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView, ObjectCloneView,
)
from itambox.utils import get_paginate_count
from itambox.panels import Panel

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
        ((Panel('info', _('Contact Details')),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contact = self.get_object()

        assignments_table = ContactAssignmentTable(contact.assignments.all(), request=self.request)
        assignments_table.configure(self.request)

        context['assignments_table'] = assignments_table
        return context


class ContactEditView(ObjectEditView):
    queryset = Contact.objects.all()
    model = Contact
    model_form = ContactForm
    template_name = 'generic/object_edit.html'


class ContactCloneView(ObjectCloneView):
    model = Contact
    model_form = ContactForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'organization:contact_list'


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
                _("Cannot delete contact '%(contact)s': It has %(count)d assignment%(plural)s.") % {
                    'contact': contact,
                    'count': assignment_count,
                    'plural': 's' if assignment_count != 1 else '',
                }
            )
            return redirect(contact.get_absolute_url())

        return super().post(request, *args, **kwargs)


class ContactBulkEditView(ObjectBulkEditView):
    queryset = Contact.objects.all()


class ContactBulkDeleteView(ObjectBulkDeleteView):
    queryset = Contact.objects.all()
