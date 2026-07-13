import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404
from django.template.loader import get_template
from django.template import TemplateDoesNotExist
from django.urls import reverse, NoReverseMatch
from django.utils.http import urlencode
from django.utils.translation import gettext as _, override
from django.views.generic import DetailView
from django_tables2 import RequestConfig

from core.models import ObjectChange
from core.tables import ObjectChangeTable, BaseTable
from core.forms import JournalEntryForm
from extras.customfields import get_custom_fields_display
from extras.models import (
    JournalEntry, ImageAttachment, FileAttachment, Bookmark, ObjectWatch,
)
from itambox.registry import registry
from itambox.utils import get_model_viewname, get_help_url
from itambox.views.htmx import BaseHTMXView
from itambox.views.generic.mixins import (
    CachedObjectMixin,
    TenantScopingViewMixin,
    user_can_mutate_model,
)
from subscriptions.models import SubscriptionAssignment
from subscriptions.tables import SubscriptionAssignmentTable

logger = logging.getLogger(__name__)


class ObjectDetailView(TenantScopingViewMixin, PermissionRequiredMixin, LoginRequiredMixin, BaseHTMXView, CachedObjectMixin, DetailView):
    template_name = 'generic/object_detail.html'
    layout = None
    # Opt-in escape hatch: when True, skip the per-reverse-relation .count()
    # loop entirely (10-15 COUNTs/page) and supply an empty list. Default False
    # preserves identical behavior for every existing detail view.
    disable_related_objects_list = False

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
                'generic/object_detail.html',
            ]

            for template_name in templates_to_try:
                try:
                    get_template(template_name)
                    return [template_name]
                except TemplateDoesNotExist:
                    continue

        return ['generic/object_detail.html']

    @staticmethod
    def _related_count_uses_distinct(related_model):
        """Return True when the related model's default-manager queryset applies
        ``.distinct()`` — i.e. it has a ``filter_tenants`` M2M, so tenant scoping
        joins that table and de-duplicates rows (see
        ``TenantScopingQuerySet.filter_by_tenant``). For such models a
        ``.values().annotate(Count('pk'))`` subquery would count the M2M-join
        fan-out instead of distinct rows, miscounting. We keep the legacy
        ``.count()`` (which counts distinct rows) for these relations.
        """
        from django.core.exceptions import FieldDoesNotExist
        try:
            related_model._meta.get_field('filter_tenants')
            return True
        except FieldDoesNotExist:
            return False

    def _build_related_objects_list(self, obj):
        """Build the "Related Objects" sidebar list (label/count/url per reverse
        relation) for ``obj``.

        H4 batching: the legacy implementation issued one ``.count()`` query per
        auto-created reverse relation (~10-15 separate COUNTs per detail GET).
        Each of those counts went through the related model's *default* manager
        (``_default_manager.get_queryset()``) — i.e. tenant scoping + soft-delete
        filtering — because Django's reverse related manager subclasses the
        related model's default manager and calls ``super().get_queryset()``
        before applying the FK filter.

        We reproduce the *identical* counts with far fewer queries by annotating
        the single object's row with one correlated ``Subquery`` COUNT per
        reverse FK / one-to-one relation. Each subquery is built from the related
        model's ``_default_manager`` and filtered ``<fk>=OuterRef(<target>)``, so
        it carries exactly the same WHERE clauses (tenant + soft-delete) the old
        ``.count()`` applied. Independent per-relation subqueries (not one
        multi-join aggregate) avoid the cartesian fan-out that would inflate
        counts. The outer query uses ``_base_manager`` purely to fetch the single
        pk row — the subqueries do their own default-manager scoping, so the
        outer manager's filtering does not affect any displayed count.

        Reverse many-to-many relations are NOT batched: their count needs a
        through-table join, which a plain FK subquery cannot reproduce, so we
        keep the per-relation ``.count()`` for those (a handful at most). Any
        relation whose subquery can't be built safely also falls back to
        ``.count()``. Labels, URLs, ordering, and count VALUES are unchanged.
        """
        # First pass: collect metadata for every relation the legacy loop would
        # have considered, and stage a Subquery annotation for each batchable
        # (reverse FK / O2O) relation. ``meta`` preserves iteration order so the
        # assembled list matches the legacy pre-sort order exactly (the final
        # sort by label makes order deterministic regardless).
        meta = []  # list of (relation, related_model, accessor_name, count_key|None)
        annotations = {}
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

                count_key = None
                if not relation.many_to_many and not self._related_count_uses_distinct(related_model):
                    # Reverse FK / one-to-one: batch via a correlated subquery
                    # through the related model's DEFAULT manager so the exact
                    # tenant + soft-delete filtering of the old .count() is kept.
                    try:
                        fk_name = relation.field.name
                        target = getattr(relation, 'field_name', None) or 'pk'
                        subquery = Subquery(
                            related_model._default_manager
                            .filter(**{fk_name: OuterRef(target)})
                            .order_by()
                            .values(fk_name)
                            .annotate(c=Count('pk'))
                            .values('c')[:1]
                        )
                        count_key = f'_relcount_{len(annotations)}'
                        annotations[count_key] = Coalesce(subquery, 0)
                    except Exception:
                        # Couldn't stage the subquery — fall back to .count().
                        count_key = None

                meta.append((relation, related_model, accessor_name, count_key))

        # Single query: annotate the one object row with every staged subquery
        # COUNT. _base_manager guarantees the pk row is returned irrespective of
        # the model's own scoping; the subqueries scope themselves independently.
        annotated = None
        if annotations:
            try:
                annotated = type(obj)._base_manager.filter(pk=obj.pk).annotate(**annotations).first()
            except Exception:
                annotated = None

        # Second pass: resolve each relation's count (from the annotated row when
        # available, else a direct .count()) and assemble the list identically.
        related_objects_list = []
        for relation, related_model, accessor_name, count_key in meta:
            count = None
            if count_key is not None and annotated is not None:
                count = getattr(annotated, count_key, None)
            if count is None:
                # M2M relations, un-batchable relations, or a failed batch query
                # keep the legacy per-relation count (identical to before).
                try:
                    count = getattr(obj, accessor_name).count()
                except Exception:
                    continue

            if count > 0:
                # Resolve the list viewname via get_model_viewname so core-app
                # reverse-relation targets (root-mounted, UN-namespaced) resolve
                # too — a hardcoded '{app}:{model}_list' silently dropped them.
                # App-namespaced targets still map to '{app}:{model}_list'.
                view_name = get_model_viewname(related_model, 'list')

                try:
                    base_url = reverse(view_name)
                    filter_field_name = relation.remote_field.name if relation.remote_field else obj._meta.model_name
                    filter_val = getattr(obj, 'slug', obj.pk)
                    url = f"{base_url}?{filter_field_name}={filter_val}"
                    label = str(related_model._meta.verbose_name_plural).title()

                    related_objects_list.append({
                        'label': label,
                        'count': count,
                        'url': url,
                    })
                except NoReverseMatch:
                    continue

        related_objects_list.sort(key=lambda x: x['label'])
        return related_objects_list

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()
        app_label = obj._meta.app_label
        model_name = obj._meta.model_name
        verbose_name = obj._meta.verbose_name
        verbose_name_plural = obj._meta.verbose_name_plural

        context['model'] = obj.__class__
        context['layout'] = self.layout

        mutation_allowed = user_can_mutate_model(self.request.user, obj.__class__)
        can_change = mutation_allowed and self.request.user.has_perm(
            f'{app_label}.change_{model_name}', obj=obj,
        )
        can_delete = mutation_allowed and self.request.user.has_perm(
            f'{app_label}.delete_{model_name}', obj=obj,
        )
        context['can_change'] = can_change
        context['can_delete'] = can_delete
        context['edit_url'] = None
        if can_change:
            try:
                context['edit_url'] = reverse(get_model_viewname(obj, 'update'), kwargs={'pk': obj.pk})
            except NoReverseMatch:
                logger.debug("Edit URL not resolvable for %s obj=%s", model_name, obj.pk)

        context['delete_url'] = None
        if can_delete:
            try:
                context['delete_url'] = reverse(get_model_viewname(obj, 'delete'), kwargs={'pk': obj.pk})
            except NoReverseMatch:
                logger.debug("Delete URL not resolvable for %s obj=%s", model_name, obj.pk)

        # Clone is offered generically for any model flagged cloneable (via
        # CloneableMixin) that has a clone view wired and that the user may add.
        context['clone_url'] = None
        if mutation_allowed and registry.model_has_feature(obj.__class__, 'cloneable') and \
                self.request.user.has_perm(f'{app_label}.add_{model_name}', obj=obj):
            try:
                context['clone_url'] = reverse(get_model_viewname(obj, 'clone'), kwargs={'pk': obj.pk})
            except NoReverseMatch:
                logger.debug("Clone URL not resolvable for %s obj=%s", model_name, obj.pk)

        context['title'] = str(obj)
        base_breadcrumbs = [
            (reverse('dashboard'), _('Dashboard')),
            (reverse(get_model_viewname(obj, 'list')), verbose_name_plural),
            (None, context['title']),
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()

        # A4: resolve ContentType once for this object — it is used by changelog,
        # journaling, image/file attachments, bookmarks, and watches below.
        _obj_ct_exists = ContentType.objects.filter(app_label='core', model='objectchange').exists()
        if _obj_ct_exists:
            obj_type = ContentType.objects.get_for_model(obj)
        else:
            obj_type = None

        if hasattr(obj, 'get_changelog_url'):
            context['changelog_url'] = obj.get_changelog_url()
        elif _obj_ct_exists:
            changelog_url = reverse('objectchange_list') + '?' + urlencode({'changed_object_type': obj_type.pk, 'changed_object_id': obj.pk})
            context['changelog_url'] = changelog_url

        if _obj_ct_exists:
            changelog_qs = ObjectChange.objects.filter(
                changed_object_type=obj_type,
                changed_object_id=obj.pk,
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
            if obj_type is None:
                obj_type = ContentType.objects.get_for_model(obj)
            journal_qs = JournalEntry.objects.filter(
                model=obj_type,
                object_id=obj.pk,
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
            context['custom_fields_display'] = get_custom_fields_display(obj)

        if registry.model_has_feature(obj.__class__, 'image_attachments'):
            if obj_type is None:
                obj_type = ContentType.objects.get_for_model(obj)
            context['image_attachments'] = ImageAttachment.objects.filter(
                model=obj_type, object_id=obj.pk,
            ).order_by('-created')[:20]
            context['has_image_attachments'] = True

        if registry.model_has_feature(obj.__class__, 'file_attachments'):
            if obj_type is None:
                obj_type = ContentType.objects.get_for_model(obj)
            context['file_attachments'] = FileAttachment.objects.filter(
                model=obj_type, object_id=obj.pk,
            ).order_by('-created')[:20]
            context['has_file_attachments'] = True

        if registry.model_has_feature(obj.__class__, 'subscribable'):
            if obj_type is None:
                obj_type = ContentType.objects.get_for_model(obj)
            context['has_subscriptions'] = True
            context['subscribable_content_type_id'] = obj_type.pk

            assignments_qs = SubscriptionAssignment.objects.filter(
                content_type=obj_type,
                object_id=obj.pk,
            ).select_related('subscription', 'subscription__provider', 'assigned_by')

            subs_table = SubscriptionAssignmentTable(assignments_qs, request=self.request)
            subs_table.exclude = ('content_type', 'object_id', 'assigned_object')
            RequestConfig(self.request, paginate=False).configure(subs_table)
            context['subscription_assignments_table'] = subs_table
            context['subscription_assignments_count'] = assignments_qs.count()

        if registry.model_has_feature(obj.__class__, 'bookmarkable'):
            if obj_type is None:
                obj_type = ContentType.objects.get_for_model(obj)
            context['is_bookmarkable'] = True
            context['bookmark_content_type_id'] = obj_type.pk
            if self.request.user.is_authenticated:
                context['is_bookmarked'] = Bookmark.objects.filter(
                    user=self.request.user,
                    model=obj_type,
                    object_id=obj.pk,
                ).exists()
            else:
                context['is_bookmarked'] = False

        if registry.model_has_feature(obj.__class__, 'watchable'):
            if obj_type is None:
                obj_type = ContentType.objects.get_for_model(obj)
            context['is_watchable'] = True
            context['watch_content_type_id'] = obj_type.pk
            if self.request.user.is_authenticated:
                context['is_watched'] = ObjectWatch.objects.filter(
                    user=self.request.user,
                    model=obj_type,
                    object_id=obj.pk,
                ).exists()
            else:
                context['is_watched'] = False

        if 'related_objects_list' not in context and self.disable_related_objects_list:
            context['related_objects_list'] = []
        elif 'related_objects_list' not in context:
            context['related_objects_list'] = self._build_related_objects_list(obj)

        context['help_url'] = get_help_url(self, app_label, model_name)
        return context
