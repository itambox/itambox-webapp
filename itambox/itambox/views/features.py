import logging
import json
import difflib
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
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
from django_tables2 import RequestConfig

from core.models import (
    ObjectChange, ExportTemplate, JournalEntry, 
    WebhookEndpoint, EventRule, LabelTemplate, ImageAttachment, FileAttachment
)
from core.tables import (
    ObjectChangeTable, ExportTemplateTable, WebhookEndpointTable, 
    EventRuleTable, LabelTemplateTable
)
from core.forms import (
    JournalEntryForm, WebhookEndpointForm, EventRuleForm, LabelTemplateForm,
    ObjectChangeFilterForm
)
from core.filters import ObjectChangeFilterSet
from itambox.registry import registry
from itambox.panels import Panel

from .generic import BaseHTMXView, ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView

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
            (reverse('dashboard'), 'Dashboard'),
            (None, 'Changelog')
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Changelog'
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

        context['title'] = f"Change #{obj.pk}"
        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
            (reverse('objectchange_list'), 'Changelog'),
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
        
        export_format = request.GET.get('format', 'csv').lower()
        export_scope = request.GET.get('export_scope', 'all').lower()

        pks = request.GET.get('pk', '')
        if pks:
            pks = [int(p) for p in pks.split(',') if p.strip()]
            queryset = model.objects.filter(pk__in=pks)
        elif export_scope == 'filtered':
            queryset = model.objects.all()
            filterset_class = get_filterset_for_model(model)
            if filterset_class:
                filterset = filterset_class(request.GET, queryset=queryset)
                if filterset.is_valid():
                    queryset = filterset.qs
        else:
            queryset = model.objects.all()

        if template_id == 0:
            if export_format == 'yaml':
                import yaml
                fields = [f for f in model._meta.fields if not f.many_to_many]
                export_data = []
                for obj in queryset:
                    row_dict = {}
                    for field in fields:
                        val = getattr(obj, field.name)
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
                        if val is None:
                            val = ''
                        row.append(str(val))
                    writer.writerow(row)
                return response

        content_type = ContentType.objects.get_for_model(model)
        template = get_object_or_404(ExportTemplate, pk=template_id, content_type=content_type)
        content = template.render_queryset(queryset)

        response = HttpResponse(content, content_type=template.mime_type)
        response['Content-Disposition'] = f'attachment; filename="{model_name}_export.{template.file_extension}"'
        return response


@method_decorator(login_required, name='dispatch')
class ExportTemplateListView(ObjectListView):
    queryset = ExportTemplate.objects.select_related('content_type')
    table = ExportTemplateTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Export Templates'
        return context


@method_decorator(login_required, name='dispatch')
class ExportTemplateDetailView(ObjectDetailView):
    queryset = ExportTemplate.objects.select_related('content_type')
    layout = (
        ((Panel('info', 'Export Template Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = str(self.get_object())
        return context


@method_decorator(login_required, name='dispatch')
class ExportTemplateEditView(ObjectEditView):
    queryset = ExportTemplate.objects.all()
    fields = ['name', 'description', 'content_type', 'template_code', 'mime_type', 'file_extension']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Export Template' if self.object else 'Create Export Template'
        return context


@method_decorator(login_required, name='dispatch')
class ExportTemplateDeleteView(ObjectDeleteView):
    queryset = ExportTemplate.objects.all()


class JournalEntryCreateView(LoginRequiredMixin, View):
    def post(self, request, app_label, model_name, object_id):
        model = apps.get_model(app_label, model_name)
        obj = get_object_or_404(model, pk=object_id)
        form = JournalEntryForm(request.POST)
        if form.is_valid():
            obj_type = ContentType.objects.get_for_model(model)
            JournalEntry.objects.create(
                model=obj_type,
                object_id=obj.pk,
                user=request.user,
                comment=form.cleaned_data['comment'],
            )
            messages.success(request, 'Journal entry added.')
        else:
            messages.error(request, 'Could not add journal entry.')
        redirect_url = request.POST.get('return_url') or request.META.get('HTTP_REFERER')
        if redirect_url:
            return HttpResponseRedirect(redirect_url)
        return redirect(obj)


class ImageAttachmentUploadView(LoginRequiredMixin, View):
    def post(self, request, app_label, model_name, object_id):
        model = apps.get_model(app_label, model_name)
        obj = get_object_or_404(model, pk=object_id)
        obj_type = ContentType.objects.get_for_model(obj)
        uploaded_file = request.FILES.get('image')
        if uploaded_file:
            ImageAttachment.objects.create(
                model=obj_type,
                object_id=obj.pk,
                image=uploaded_file,
                name=uploaded_file.name,
            )
            messages.success(request, f"Image '{uploaded_file.name}' uploaded.")
        return redirect(request.POST.get('return_url', obj.get_absolute_url()))


class FileAttachmentUploadView(LoginRequiredMixin, View):
    def post(self, request, app_label, model_name, object_id):
        model = apps.get_model(app_label, model_name)
        obj = get_object_or_404(model, pk=object_id)
        obj_type = ContentType.objects.get_for_model(obj)
        uploaded_file = request.FILES.get('file')
        if uploaded_file:
            import mimetypes
            mime_type, _ = mimetypes.guess_type(uploaded_file.name)
            FileAttachment.objects.create(
                model=obj_type,
                object_id=obj.pk,
                file=uploaded_file,
                name=uploaded_file.name,
                mime_type=mime_type or '',
            )
            messages.success(request, f"File '{uploaded_file.name}' uploaded.")
        return redirect(request.POST.get('return_url', obj.get_absolute_url()))


class ImageAttachmentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        attachment = get_object_or_404(ImageAttachment, pk=pk)
        obj_url = request.POST.get('return_url', '/')
        attachment.delete()
        messages.success(request, f"Image '{attachment.name}' deleted.")
        return redirect(obj_url)


class FileAttachmentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        attachment = get_object_or_404(FileAttachment, pk=pk)
        obj_url = request.POST.get('return_url', '/')
        attachment.delete()
        messages.success(request, f"File '{attachment.name}' deleted.")
        return redirect(obj_url)


class LabelSelectView(LoginRequiredMixin, View):
    def get(self, request, app_label, model_name, object_id):
        templates = LabelTemplate.objects.all()
        context = {
            'label_templates': templates,
            'object_id': object_id,
            'app_label': app_label,
            'model_name': model_name,
            'title': 'Select Label Template',
        }
        return render(request, 'generic/label_select.html', context)


class LabelPrintView(LoginRequiredMixin, View):
    def get(self, request, template_id, object_id):
        label_template = get_object_or_404(LabelTemplate, pk=template_id)
        content_type = label_template.content_type if hasattr(label_template, 'content_type') else None

        if content_type:
            model = content_type.model_class()
            obj = get_object_or_404(model, pk=object_id)
        else:
            model = None
            try:
                # Dynamic model lookup to break circular dependency
                Asset = apps.get_model('assets', 'Asset')
                obj = get_object_or_404(Asset, pk=object_id)
            except Exception:
                obj = None

        if label_template.template_code:
            from django.template import Template, Context
            template = Template(label_template.template_code)
            context = Context({'obj': obj, 'barcode_format': label_template.barcode_format})
            html = template.render(context)
        else:
            html = self._render_default_label(obj, label_template)

        response = HttpResponse(html)
        response['Content-Type'] = 'text/html'
        return response

    def _render_default_label(self, obj, label_template):
        barcode_fmt = label_template.barcode_format
        obj_name = str(obj) if obj else 'Unknown'
        asset_tag = getattr(obj, 'asset_tag', '') if obj else ''
        barcode_img = ''
        if barcode_fmt:
            barcode_img = self._generate_barcode(asset_tag or obj_name, barcode_fmt)
        return f'<html><body style="width:{label_template.page_width}in;height:{label_template.page_height}in;margin:0;padding:5pt;font-family:sans-serif;font-size:8pt;"><div style="text-align:center"><h3 style="margin:0">{obj_name}</h3>{barcode_img}<p style="margin:2pt 0">{asset_tag}</p></div></body></html>'

    def _generate_barcode(self, data, fmt):
        try:
            import segno
            qr = segno.make(data)
            return f'<div style="max-width:100%">{qr.svg_inline(scale=4, border=0)}</div>'
        except Exception:
            try:
                import barcode
                from barcode.writer import SVGWriter
                from io import BytesIO
                buf = BytesIO()
                if fmt.lower() in ('code128', 'code39'):
                    bc_class = barcode.get(fmt.lower(), lambda x: x)
                    bc = bc_class(data, writer=SVGWriter())
                    bc.write(buf)
                    return buf.getvalue().decode('utf-8')
            except Exception:
                pass
        return ''


@method_decorator(login_required, name='dispatch')
class WebhookEndpointListView(ObjectListView):
    queryset = WebhookEndpoint.objects.all()
    table = WebhookEndpointTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Webhook Endpoints'
        return context


@method_decorator(login_required, name='dispatch')
class WebhookEndpointDetailView(ObjectDetailView):
    queryset = WebhookEndpoint.objects.all()
    layout = (
        ((Panel('info', 'Webhook Endpoint Details'),),),
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

        try:
            response = http_requests.request(
                method=endpoint.http_method,
                url=endpoint.url,
                headers=headers,
                data=body,
                timeout=10,
            )
            messages.success(request, f"Test webhook sent — HTTP {response.status_code}")
        except http_requests.RequestException as e:
            messages.error(request, f"Test webhook failed: {e}")

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
        url = request.POST.get('url', '')
        if not url:
            messages.error(request, "No URL configured.")
            return redirect(request.get_full_path())
        success = False
        try:
            test_payload = "Test notification from ITAMbox"
            if 'hooks.slack.com' in url:
                from core.events import _send_slack_notification
                success = _send_slack_notification(url, test_payload, "ITAMbox Test")
            elif 'webhook.office.com' in url or 'outlook.office.com/webhook' in url:
                from core.events import _send_teams_notification
                success = _send_teams_notification(url, test_payload, "ITAMbox Test")
            else:
                import requests
                resp = requests.post(url, json={'test': True, 'message': test_payload}, timeout=10)
                success = resp.status_code < 400
        except Exception as e:
            messages.error(request, f"Test failed: {e}")
        if success:
            messages.success(request, "Webhook test succeeded!")
        else:
            messages.error(request, "Webhook test failed.")
        return redirect(request.get_full_path())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Webhook Endpoint' if self.object else 'Create Webhook Endpoint'
        return context


@method_decorator(login_required, name='dispatch')
class WebhookEndpointDeleteView(ObjectDeleteView):
    queryset = WebhookEndpoint.objects.all()


@method_decorator(login_required, name='dispatch')
class EventRuleListView(ObjectListView):
    queryset = EventRule.objects.select_related('model')
    table = EventRuleTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Event Rules'
        return context


@method_decorator(login_required, name='dispatch')
class EventRuleDetailView(ObjectDetailView):
    queryset = EventRule.objects.select_related('model')
    layout = (
        ((Panel('info', 'Event Rule Details'),),),
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
        context['title'] = 'Edit Event Rule' if self.object else 'Create Event Rule'
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
        context['title'] = 'Label Templates'
        return context


@method_decorator(login_required, name='dispatch')
class LabelTemplateDetailView(ObjectDetailView):
    queryset = LabelTemplate.objects.all()
    layout = (
        ((Panel('info', 'Label Template Details'),),),
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
        context['title'] = 'Edit Label Template' if self.object else 'Create Label Template'
        return context


@method_decorator(login_required, name='dispatch')
class LabelTemplateDeleteView(ObjectDeleteView):
    queryset = LabelTemplate.objects.all()
