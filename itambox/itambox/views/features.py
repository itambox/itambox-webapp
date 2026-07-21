import logging
import json
import difflib
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.core.exceptions import PermissionDenied
from django.apps import apps
from django.urls import reverse, NoReverseMatch
from django.utils.decorators import method_decorator
from django.core.serializers.json import DjangoJSONEncoder
from django.views.generic import View, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from django_tables2 import RequestConfig

from core.models import ObjectChange
from extras.models import WebhookEndpoint, EventRule, ExportTemplate, LabelTemplate, JournalEntry, ImageAttachment, FileAttachment
from organization.services import visible_to_containers, is_container_scoped_unfiltered
from core.tables import (
    ObjectChangeTable, ExportTemplateTable, WebhookEndpointTable,
    EventRuleTable, LabelTemplateTable
)
from extras.tables import JournalEntryTable
from core.forms import JournalEntryForm
from extras.forms import WebhookEndpointForm, EventRuleForm, ExportTemplateForm, LabelTemplateForm, ObjectChangeFilterForm, JournalEntryFilterForm
from core.filters import ObjectChangeFilterSet
from extras.filters import JournalEntryFilterSet
from itambox.registry import registry
from itambox.panels import Panel

from .generic import BaseHTMXView, ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
from itambox.views.generic.utils import safe_return_url
from core.csv_utils import csv_safe

logger = logging.getLogger(__name__)


@method_decorator(login_required, name='dispatch')
class ObjectChangeListView(ObjectListView):
    queryset = ObjectChange.objects.prefetch_related(
        'user', 'changed_object_type', 'related_object_type'
    )
    filterset = ObjectChangeFilterSet
    filterset_form = ObjectChangeFilterForm
    table = ObjectChangeTable
    template_name = 'core/objectchange/objectchange_list.html'
    action_buttons = ()

    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), _('Dashboard')),
            (None, _('Changelog'))
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Changelog')
        return context


@method_decorator(login_required, name='dispatch')
class JournalEntryListView(ObjectListView):
    # Global activity list of journal entries across all objects (NetBox-style
    # "Journal Entries" under Monitoring › Activity). Tenant scoping is re-applied
    # per request by TenantScopingViewMixin; allow_global_tenant keeps entries on
    # shared/global objects visible. content_object is prefetched for the linked
    # Object column (a GFK cannot be select_related).
    queryset = JournalEntry.objects.select_related('model', 'user', 'tenant').prefetch_related('content_object')
    filterset = JournalEntryFilterSet
    filterset_form = JournalEntryFilterForm
    table = JournalEntryTable
    template_name = 'extras/journalentry/journalentry_list.html'
    action_buttons = ()

    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), _('Dashboard')),
            (None, _('Journal Entries')),
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Journal Entries')
        return context


def resolve_serialized_data(model_class, data):
    if not model_class or not data:
        return data

    resolved_data = {}
    for k, v in data.items():
        if v is None:
            resolved_data[k] = v
            continue

        try:
            field = model_class._meta.get_field(k)
        except Exception:
            resolved_data[k] = v
            continue

        if field.is_relation and field.related_model:
            related_model = field.related_model
            if isinstance(v, list):
                resolved_list = []
                for item_id in v:
                    try:
                        related_obj = related_model.objects.get(pk=item_id)
                        resolved_list.append(str(related_obj))
                    except Exception:
                        resolved_list.append(f"{related_model._meta.model_name} #{item_id} (deleted)")
                resolved_data[k] = resolved_list
            else:
                try:
                    related_obj = related_model.objects.get(pk=v)
                    resolved_data[k] = str(related_obj)
                except Exception:
                    resolved_data[k] = f"{related_model._meta.model_name} #{v} (deleted)"
        else:
            resolved_data[k] = v

    # Resolve generic foreign keys if present
    try:
        from django.contrib.contenttypes.fields import GenericForeignKey
        for gfk in [f for f in model_class._meta.private_fields if isinstance(f, GenericForeignKey)]:
            ct_field = gfk.ct_field
            fk_field = gfk.fk_field
            if ct_field in resolved_data and fk_field in resolved_data:
                ct_val = data.get(ct_field)
                fk_val = data.get(fk_field)
                if ct_val and fk_val:
                    try:
                        ct = ContentType.objects.get(pk=ct_val)
                        related_model = ct.model_class()
                        related_obj = related_model.objects.get(pk=fk_val)
                        resolved_data[fk_field] = str(related_obj)
                    except Exception:
                        pass
    except Exception:
        pass

    return resolved_data


@method_decorator(login_required, name='dispatch')
class ObjectChangeView(BaseHTMXView, DetailView):
    model = ObjectChange
    template_name = 'core/objectchange/objectchange.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()

        model_class = obj.changed_object_type.model_class()
        prechange_data = resolve_serialized_data(model_class, obj.prechange_data or {})
        postchange_data = resolve_serialized_data(model_class, obj.postchange_data or {})

        prechange_string = json.dumps(prechange_data, cls=DjangoJSONEncoder, indent=2, sort_keys=True)
        postchange_string = json.dumps(postchange_data, cls=DjangoJSONEncoder, indent=2, sort_keys=True)

        prechange_lines = prechange_string.splitlines(keepends=True)
        postchange_lines = postchange_string.splitlines(keepends=True)

        differ = difflib.Differ()
        diff_lines = list(differ.compare(prechange_lines, postchange_lines))
        context['diff_lines'] = diff_lines

        context['prechange_data_json'] = prechange_string
        context['postchange_data_json'] = postchange_string

        diff_added_keys = {k for k, v in postchange_data.items() if k not in prechange_data or prechange_data[k] != v}
        diff_removed_keys = {k for k, v in prechange_data.items() if k not in postchange_data or postchange_data[k] != v}
        diff_added = {k: v for k, v in postchange_data.items() if k in diff_added_keys}
        diff_removed = {k: v for k, v in prechange_data.items() if k in diff_removed_keys}
        context['diff_added_json'] = json.dumps(diff_added, cls=DjangoJSONEncoder, indent=2)
        context['diff_removed_json'] = json.dumps(diff_removed, cls=DjangoJSONEncoder, indent=2)

        context['title'] = _("Change #%(pk)s") % {'pk': obj.pk}
        base_breadcrumbs = [
            (reverse('dashboard'), _('Dashboard')),
            (reverse('objectchange_list'), _('Changelog')),
            (None, context['title'])
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        context['content_template_name'] = self.template_name
        return context


def get_filterset_for_model(model):
    from itambox.registry import registry
    fs = registry.get_filter_set(model)
    if fs:
        return fs

    app_label = model._meta.app_label
    model_name = model._meta.model_name
    import importlib
    try:
        filters_module = importlib.import_module(f"{app_label}.filters")
        for attr_name in dir(filters_module):
            if attr_name.lower() == f"{model_name}filterset":
                return getattr(filters_module, attr_name)
    except ImportError:
        pass
    return None


class ObjectExportView(LoginRequiredMixin, View):
    def get(self, request, app_label, model_name, template_id):
        model = apps.get_model(app_label, model_name)

        if not request.user.has_perm(f'{app_label}.view_{model_name}'):
            raise Http404

        export_format = request.GET.get('format', 'csv').lower()
        export_scope = request.GET.get('export_scope', 'all').lower()

        pks = request.GET.get('pk', '')
        if pks:
            valid_pks = [int(p) for p in pks.split(',') if p.strip().isdigit()]
            if not valid_pks:
                return HttpResponseBadRequest(_("Invalid pk value(s)."))
            queryset = model.objects.filter(pk__in=valid_pks)
        elif export_scope == 'filtered':
            queryset = model.objects.all()
            filterset_class = get_filterset_for_model(model)
            if filterset_class:
                filterset = filterset_class(request.GET, queryset=queryset)
                if filterset.is_valid():
                    queryset = filterset.qs
        else:
            queryset = model.objects.all()

        if is_container_scoped_unfiltered(model):
            # Membership/Token-shaped models: the default manager has no
            # filter_by_tenant, so the queryset built above is not tenant-scoped
            # at all — an ambient view_<model> permission (granted by every
            # seeded role, including Read-Only) would otherwise dump every
            # tenant's rows. Mirrors the restriction
            # MembershipListView/MembershipDetailView apply on top of the same
            # unscoped manager.
            queryset = visible_to_containers(request.user, queryset, f'{app_label}.view_{model_name}')

        if template_id == 0:
            _REDACTED_FIELD_SUBSTRINGS = ('secret', 'password', 'token')

            def _is_redacted(field_name, val):
                # Redact by name, AND auto-redact any encrypted-field ciphertext regardless
                # of the field's name (enc$ is the Fernet sentinel — covers product_key,
                # smtp_password, webhook secret, and any future encrypted field).
                name = field_name.lower()
                if any(sub in name for sub in _REDACTED_FIELD_SUBSTRINGS):
                    return True
                return isinstance(val, str) and val.startswith('enc$')

            if export_format == 'yaml':
                import yaml
                fields = [f for f in model._meta.fields if not f.many_to_many]
                export_data = []
                for obj in queryset:
                    row_dict = {}
                    for field in fields:
                        val = getattr(obj, field.name)
                        if _is_redacted(field.name, val):
                            row_dict[field.name] = '***'
                            continue
                        if val is None:
                            val = ''
                        elif isinstance(val, (int, float, bool)):
                            row_dict[field.name] = val
                        else:
                            row_dict[field.name] = str(val)
                    export_data.append(row_dict)

                yaml_content = yaml.safe_dump(export_data, default_flow_style=False, sort_keys=False)
                response = HttpResponse(yaml_content, content_type='text/yaml')
                response['Content-Disposition'] = f'attachment; filename="{model_name}_export.yaml"'
                return response
            else:
                import csv
                response = HttpResponse(content_type='text/csv')
                response['Content-Disposition'] = f'attachment; filename="{model_name}_export.csv"'

                writer = csv.writer(response)
                fields = [f for f in model._meta.fields if not f.many_to_many]
                writer.writerow([f.name for f in fields])

                for obj in queryset:
                    row = []
                    for field in fields:
                        val = getattr(obj, field.name)
                        if _is_redacted(field.name, val):
                            row.append('***')
                            continue
                        row.append(csv_safe(val))
                    writer.writerow(row)
                return response

        content_type = ContentType.objects.get_for_model(model)
        template = get_object_or_404(ExportTemplate, pk=template_id, content_type=content_type)
        try:
            content = template.render(queryset)
        except Exception as exc:
            # Template code is author-controlled and can fail at render time (undefined
            # variable, type error, sandbox violation). Never surface a 500 — flash the
            # error and send the user back where they came from, NetBox-style.
            logger.warning("Export template %s render failed: %s", template.pk, exc)
            messages.error(request, _(
                'There was an error rendering the export template "%(name)s": %(error)s'
            ) % {'name': template.name, 'error': exc})
            return HttpResponseRedirect(safe_return_url(
                request, request.META.get('HTTP_REFERER'), template.get_absolute_url(),
            ))

        response = HttpResponse(content, content_type=template.mime_type or ExportTemplate.DEFAULT_MIME_TYPE)
        # mime_type AND the rendered body are author-controlled (could be HTML/SVG);
        # stop the browser content-sniffing its way into executing them.
        response['X-Content-Type-Options'] = 'nosniff'
        if template.as_attachment:
            response['Content-Disposition'] = f'attachment; filename="{template.get_export_filename(model)}"'
        return response


@method_decorator(login_required, name='dispatch')
class ExportTemplateListView(ObjectListView):
    queryset = ExportTemplate.objects.select_related('content_type')
    table = ExportTemplateTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Export Templates')
        return context


@method_decorator(login_required, name='dispatch')
class ExportTemplateDetailView(ObjectDetailView):
    queryset = ExportTemplate.objects.select_related('content_type')
    layout = (
        ((Panel('info', _('Export Template Details')),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()
        context['title'] = str(obj)
        # Expose the target model so the panel can link "Run this export" to the
        # live export endpoint — but only when the viewer actually holds
        # view_<target_model>. ObjectExportView 404s without it, so showing the
        # button to a user who lacks the perm would just dead-end on a 404.
        target_model = obj.content_type.model_class()
        if target_model is not None:
            app_label = target_model._meta.app_label
            model_name = target_model._meta.model_name
            if self.request.user.has_perm(f'{app_label}.view_{model_name}'):
                context['target_app_label'] = app_label
                context['target_model_name'] = model_name
                context['target_model_verbose'] = target_model._meta.verbose_name_plural
        return context


@method_decorator(login_required, name='dispatch')
class ExportTemplateEditView(ObjectEditView):
    queryset = ExportTemplate.objects.all()
    model_form = ExportTemplateForm

    def has_permission(self):
        # ExportTemplate is a global, admin-managed resource with NO tenant field — its
        # template_code is server-rendered (Jinja) for EVERY tenant's exports. Gating only on
        # the model perm let any tenant member create/edit/delete the shared templates,
        # i.e. tamper with another tenant's export output (a cross-tenant integrity /
        # stored-template-injection vector). Authoring is restricted to superusers; members
        # keep read/render access.
        return self.request.user.is_superuser and super().has_permission()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Edit Export Template') if self.object else _('Create Export Template')
        return context


@method_decorator(login_required, name='dispatch')
class ExportTemplateDeleteView(ObjectDeleteView):
    queryset = ExportTemplate.objects.all()

    def has_permission(self):
        # See ExportTemplateEditView: deleting a shared global template affects every tenant.
        return self.request.user.is_superuser and super().has_permission()


class JournalEntryCreateView(LoginRequiredMixin, View):
    def post(self, request, app_label, model_name, object_id):
        try:
            model_class = apps.get_model(app_label, model_name)
        except LookupError:
            raise Http404
        obj_type = ContentType.objects.get_for_model(model_class)
        obj = _check_attachment_parent_access(request, obj_type, object_id)
        form = JournalEntryForm(request.POST)
        if form.is_valid():
            JournalEntry.objects.create(
                model=obj_type,
                object_id=obj.pk,
                user=request.user,
                comment=form.cleaned_data['comment'],
            )
            messages.success(request, _('Journal entry added.'))
        else:
            messages.error(request, _('Could not add journal entry.'))
        return HttpResponseRedirect(safe_return_url(
            request,
            request.POST.get('return_url') or request.META.get('HTTP_REFERER'),
            obj.get_absolute_url(),
        ))


class ImageAttachmentUploadView(LoginRequiredMixin, View):
    def post(self, request, app_label, model_name, object_id):
        try:
            model_class = apps.get_model(app_label, model_name)
        except LookupError:
            raise Http404
        obj_type = ContentType.objects.get_for_model(model_class)
        obj = _check_attachment_parent_access(request, obj_type, object_id)
        uploaded_file = request.FILES.get('image')
        if uploaded_file:
            ImageAttachment.objects.create(
                model=obj_type,
                object_id=obj.pk,
                image=uploaded_file,
                name=uploaded_file.name,
            )
            messages.success(request, _("Image '%(name)s' uploaded.") % {'name': uploaded_file.name})
        return redirect(safe_return_url(request, request.POST.get('return_url'), obj.get_absolute_url()))


class FileAttachmentUploadView(LoginRequiredMixin, View):
    def post(self, request, app_label, model_name, object_id):
        try:
            model_class = apps.get_model(app_label, model_name)
        except LookupError:
            raise Http404
        obj_type = ContentType.objects.get_for_model(model_class)
        obj = _check_attachment_parent_access(request, obj_type, object_id)
        uploaded_file = request.FILES.get('file')
        if uploaded_file:
            import mimetypes
            mime_type, _encoding = mimetypes.guess_type(uploaded_file.name)
            FileAttachment.objects.create(
                model=obj_type,
                object_id=obj.pk,
                file=uploaded_file,
                name=uploaded_file.name,
                mime_type=mime_type or '',
            )
            messages.success(request, _("File '%(name)s' uploaded.") % {'name': uploaded_file.name})
        return redirect(safe_return_url(request, request.POST.get('return_url'), obj.get_absolute_url()))


def _check_attachment_parent_access(request, content_type, object_id):
    """Return the parent object if the user can change it, else raise Http404."""
    model_class = content_type.model_class()
    if model_class is None:
        raise Http404
    parent = model_class.objects.filter(pk=object_id).first()
    if parent is None:
        raise Http404
    app_label = model_class._meta.app_label
    model_name = model_class._meta.model_name
    if not request.user.has_perm(f'{app_label}.change_{model_name}', obj=parent):
        raise Http404
    return parent


class ImageAttachmentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        attachment = get_object_or_404(ImageAttachment, pk=pk)
        _check_attachment_parent_access(request, attachment.model, attachment.object_id)
        obj_url = safe_return_url(request, request.POST.get('return_url'), '/')
        attachment.delete()
        messages.success(request, _("Image '%(name)s' deleted.") % {'name': attachment.name})
        return redirect(obj_url)


class FileAttachmentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        attachment = get_object_or_404(FileAttachment, pk=pk)
        _check_attachment_parent_access(request, attachment.model, attachment.object_id)
        obj_url = safe_return_url(request, request.POST.get('return_url'), '/')
        attachment.delete()
        messages.success(request, _("File '%(name)s' deleted.") % {'name': attachment.name})
        return redirect(obj_url)


def _attachment_within_tenant(content_type, object_id):
    """True if an attachment's parent object is inside the current tenant boundary.

    Attachments have no tenant field of their own — they inherit it from the
    object they are attached to. Without this check, files are reachable purely
    by their /media/ path (served directly by the web server), so any user could
    download another tenant's attachments (cross-tenant file IDOR). Parents that
    are genuinely global (no tenant) are allowed; tenant-owned parents must match
    the active tenant, and a missing tenant context fails closed.
    """
    model_class = content_type.model_class()
    if model_class is None:
        return False
    parent = model_class._default_manager.filter(pk=object_id).first()
    if parent is None:
        return False
    from core.managers import get_current_tenant
    tenant = get_current_tenant()
    parent_tenant = getattr(parent, 'tenant', None)
    if parent_tenant is not None and parent_tenant != tenant:
        return False
    return True


class FileAttachmentDownloadView(LoginRequiredMixin, View):
    """Authenticated, tenant-scoped download proxy for file attachments.

    Replaces linking files via their raw MEDIA_URL (which the web server would
    serve with no auth/tenant check). Forces an attachment disposition and
    nosniff so stored HTML/SVG cannot execute in the user's origin.
    """
    def get(self, request, pk):
        from django.http import FileResponse
        attachment = get_object_or_404(FileAttachment, pk=pk)
        if not _attachment_within_tenant(attachment.model, attachment.object_id):
            raise Http404
        filename = attachment.name or attachment.file.name.rsplit('/', 1)[-1]
        response = FileResponse(attachment.file.open('rb'), as_attachment=True, filename=filename)
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class ImageAttachmentServeView(LoginRequiredMixin, View):
    """Authenticated, tenant-scoped serving of image attachments (inline)."""
    def get(self, request, pk):
        from django.http import FileResponse
        attachment = get_object_or_404(ImageAttachment, pk=pk)
        if not _attachment_within_tenant(attachment.model, attachment.object_id):
            raise Http404
        response = FileResponse(attachment.image.open('rb'))
        # Force an image/* content-type (defence-in-depth alongside nosniff) so a mislabeled
        # upload can't be served as HTML/SVG inline. The validator restricts uploads to image
        # extensions, so a valid image always resolves to image/*; anything else -> download.
        import mimetypes
        guessed, _enc = mimetypes.guess_type(attachment.image.name)
        response['Content-Type'] = guessed if (guessed or '').startswith('image/') else 'application/octet-stream'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class LabelSelectView(LoginRequiredMixin, View):
    def get(self, request, app_label, model_name, object_id):
        # Require view access to the object's model — printing a label exposes its
        # name/tag/serial, so a member lacking view_<model> must not reach it.
        if not request.user.has_perm(f'{app_label}.view_{model_name}'):
            raise PermissionDenied
        templates = LabelTemplate.objects.all()
        context = {
            'label_templates': templates,
            'object_id': object_id,
            'app_label': app_label,
            'model_name': model_name,
            'title': _('Select Label Template'),
        }
        return render(request, 'generic/label_select.html', context)


class LabelPrintView(LoginRequiredMixin, View):
    def get(self, request, template_id, object_id):
        from core.tasks.labels import render_labels_pdf

        label_template = get_object_or_404(LabelTemplate, pk=template_id)
        content_type = label_template.content_type if hasattr(label_template, 'content_type') else None

        if content_type:
            model = content_type.model_class()
            obj = get_object_or_404(model, pk=object_id)
        else:
            # Dynamic model lookup to break circular dependency
            model = apps.get_model('assets', 'Asset')
            obj = get_object_or_404(model, pk=object_id)

        # Require object-level view access — a printed label exposes name/tag/serial.
        if not request.user.has_perm(f'{model._meta.app_label}.view_{model._meta.model_name}', obj=obj):
            raise PermissionDenied

        # Same engine as the bulk print job — synchronous, no background Job.
        pdf_bytes = render_labels_pdf([obj], label_template)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        filename = getattr(obj, 'asset_tag', None) or object_id
        response['Content-Disposition'] = f'inline; filename="label_{filename}.pdf"'
        return response


class WorkerStatusContextMixin:
    """Adds ``worker_status`` to the context so templates can warn when the django-q
    worker isn't running (webhook/notification deliveries queue but never send)."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from core.worker_status import get_worker_status
        context['worker_status'] = get_worker_status()
        return context


@method_decorator(login_required, name='dispatch')
class WebhookEndpointListView(WorkerStatusContextMixin, ObjectListView):
    queryset = WebhookEndpoint.objects.all()
    table = WebhookEndpointTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Webhook Endpoints')
        context['is_beta_module'] = True
        return context


@method_decorator(login_required, name='dispatch')
class WebhookEndpointDetailView(WorkerStatusContextMixin, ObjectDetailView):
    queryset = WebhookEndpoint.objects.all()
    layout = (
        ((Panel('info', _('Webhook Endpoint Details')),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = str(self.get_object())
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if 'test_webhook' in request.POST:
            return self._send_test_webhook(request)
        return self.get(request, *args, **kwargs)

    def _send_test_webhook(self, request):
        import json
        import requests as http_requests
        from django.http import JsonResponse

        endpoint = self.object
        payload = {
            'event': 'test',
            'model': 'core.webhookendpoint',
            'object_id': endpoint.pk,
            'timestamp': timezone.now().isoformat(),
            'data': {'test': True, 'endpoint': endpoint.name},
        }
        body = json.dumps(payload, default=str)
        headers = endpoint.headers or {}
        headers.setdefault('Content-Type', 'application/json')

        from django.core.exceptions import ValidationError
        from core.validators import validate_external_url
        try:
            validate_external_url(endpoint.url)
        except ValidationError as e:
            messages.error(request, _("Test webhook blocked: %(reason)s") % {'reason': '; '.join(e.messages)})
            return redirect(self.object.get_absolute_url())

        try:
            response = http_requests.request(
                method=endpoint.http_method,
                url=endpoint.url,
                headers=headers,
                data=body,
                timeout=10,
            )
            messages.success(request, _("Test webhook sent — HTTP %(status)s") % {'status': response.status_code})
        except http_requests.RequestException as e:
            messages.error(request, _("Test webhook failed: %(error)s") % {'error': e})

        return redirect(self.object.get_absolute_url())


@method_decorator(login_required, name='dispatch')
class WebhookEndpointEditView(ObjectEditView):
    queryset = WebhookEndpoint.objects.all()
    model_form = WebhookEndpointForm

    def post(self, request, *args, **kwargs):
        if '_test' in request.POST:
            self.object = self.get_object() if 'pk' in self.kwargs else None
            return self._test_webhook(request)
        return super().post(request, *args, **kwargs)

    def _test_webhook(self, request):
        # Same-origin post-redirect-get back to the current form. The fallback must
        # be an untainted constant (a reversed route): using request.path here would
        # let user-controlled input reach redirect() on the fallback branch.
        self_url = safe_return_url(
            request, request.get_full_path(), reverse('extras:webhookendpoint_list')
        )
        url = request.POST.get('url', '')
        if not url:
            messages.error(request, _("No URL configured."))
            return redirect(self_url)

        from django.core.exceptions import ValidationError
        from core.validators import validate_external_url
        from core.http import webhook_target_kind
        try:
            validate_external_url(url)
        except ValidationError as e:
            messages.error(request, _("Webhook test blocked: %(reason)s") % {'reason': '; '.join(e.messages)})
            return redirect(self_url)

        success = False
        try:
            test_payload = str(_("Test notification from ITAMbox"))
            test_title = str(_("ITAMbox Test"))
            target_kind = webhook_target_kind(url)
            if target_kind == 'slack':
                from core.events import _send_slack_notification
                success = _send_slack_notification(url, test_payload, test_title)
            elif target_kind == 'teams':
                from core.events import _send_teams_notification
                success = _send_teams_notification(url, test_payload, test_title)
            else:
                # SSRF-hardened send: request_pinned re-validates at send time,
                # pins the connection to the already-validated IP (closing the
                # DNS-rebinding TOCTOU gap) and never follows redirects — unlike a
                # raw requests.post, which re-resolves DNS and follows 3xx.
                from core.http import request_pinned
                resp = request_pinned('POST', url, json={'test': True, 'message': test_payload}, timeout=10)
                success = resp.status_code < 400
        except Exception as e:
            messages.error(request, _("Test failed: %(error)s") % {'error': e})
        if success:
            messages.success(request, _("Webhook test succeeded!"))
        else:
            messages.error(request, _("Webhook test failed."))
        return redirect(self_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Edit Webhook Endpoint') if self.object else _('Create Webhook Endpoint')
        return context


@method_decorator(login_required, name='dispatch')
class WebhookEndpointDeleteView(ObjectDeleteView):
    queryset = WebhookEndpoint.objects.all()


@method_decorator(login_required, name='dispatch')
class EventRuleListView(WorkerStatusContextMixin, ObjectListView):
    queryset = EventRule.objects.select_related('model')
    table = EventRuleTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Event Rules')
        context['is_beta_module'] = True
        return context


@method_decorator(login_required, name='dispatch')
class EventRuleDetailView(WorkerStatusContextMixin, ObjectDetailView):
    queryset = EventRule.objects.select_related('model', 'webhook')
    layout = (
        ((Panel('info', _('Event Rule Details')),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = str(self.get_object())
        return context


@method_decorator(login_required, name='dispatch')
class EventRuleEditView(ObjectEditView):
    queryset = EventRule.objects.all()
    model_form = EventRuleForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Edit Event Rule') if self.object else _('Create Event Rule')
        return context


@method_decorator(login_required, name='dispatch')
class EventRuleDeleteView(ObjectDeleteView):
    queryset = EventRule.objects.all()


@method_decorator(login_required, name='dispatch')
class LabelTemplateListView(ObjectListView):
    queryset = LabelTemplate.objects.all()
    table = LabelTemplateTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Label Templates')
        return context


@method_decorator(login_required, name='dispatch')
class LabelTemplateDetailView(ObjectDetailView):
    queryset = LabelTemplate.objects.all()
    layout = (
        ((Panel('info', _('Label Template Details')),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()
        context['title'] = str(obj)
        context['barcode_formats'] = dict(LabelTemplate._meta.get_field('barcode_format').choices)
        return context


@method_decorator(login_required, name='dispatch')
class LabelTemplateEditView(ObjectEditView):
    queryset = LabelTemplate.objects.all()
    model_form = LabelTemplateForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Edit Label Template') if self.object else _('Create Label Template')
        return context


@method_decorator(login_required, name='dispatch')
class LabelTemplateDeleteView(ObjectDeleteView):
    queryset = LabelTemplate.objects.all()
