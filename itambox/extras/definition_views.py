"""Command-backed browser views for global definition and choice management.

URL registration is intentionally kept outside this module; the shared URL owner
can wire these views without changing the command or presentation contract.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from core.context import get_current_all_accessible
from extras.definition_forms import (
    ChoiceCreateForm,
    ChoiceRetireForm,
    ChoiceSetCreateForm,
    ChoiceSetRetireForm,
    ChoiceSetUpdateForm,
    ChoiceUpdateForm,
)
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet
from extras.services.definition_command_contracts import (
    CustomFieldChoiceCreateInputDTO,
    CustomFieldChoiceSetCreateInputDTO,
    CustomFieldChoiceSetUpdateInputDTO,
    CustomFieldChoiceUpdateInputDTO,
)
from extras.services.definition_commands import (
    create_custom_field_choice,
    create_custom_field_choice_set,
    deprecate_custom_field_choice,
    deprecate_custom_field_choice_set,
    update_custom_field_choice,
    update_custom_field_choice_set,
)
from extras.services._definition_command_support import resource_revision_for_definition
from extras.services.specifications.contracts import QualifiedIdentity
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor


def _actor_for_user(user):
    return ActorContextDTO(
        actor_id=int(user.pk),
        authentication_revision=authentication_revision_for_actor(user),
    )


def _safe_reverse(name, **kwargs):
    try:
        return reverse(name, kwargs=kwargs)
    except NoReverseMatch:
        return None


def _identity_or_none(value):
    value = (value or "").strip()
    return QualifiedIdentity(value) if value else None


def _add_command_issues(form, result):
    for issue in getattr(result, "issues", ()):
        field_name = issue.field_key or (issue.path[0] if issue.path else None)
        if field_name not in form.fields:
            field_name = None
        form.add_error(field_name, issue.message_key)


class _DefinitionPermissionMixin(PermissionRequiredMixin):
    raise_exception = True
    require_global_configuration = False

    def _has_global_configuration_scope(self):
        return bool(self.request.user.is_superuser or get_current_all_accessible())

    def has_permission(self):
        if not super().has_permission():
            return False
        return not self.require_global_configuration or self._has_global_configuration_scope()


class _LocalDefinitionMutationMixin(_DefinitionPermissionMixin):
    require_global_configuration = True

    def _is_local_definition(self, definition):
        return definition.management_kind == CustomFieldChoiceSet.MANAGEMENT_LOCAL

    def has_permission(self):
        return super().has_permission() and self._is_local_definition(self.object)

    @staticmethod
    def current_revision(definition):
        return str(resource_revision_for_definition(definition))

    def _render_form_context(self, **extra):
        context = {"object": self.object}
        context.update(extra)
        return context


class ChoiceSetListView(LoginRequiredMixin, _DefinitionPermissionMixin, ListView):
    model = CustomFieldChoiceSet
    permission_required = "extras.view_customfieldchoiceset"
    template_name = "extras/definitions/choice_set_list.html"
    context_object_name = "choice_sets"

    def get_queryset(self):
        queryset = super().get_queryset().order_by("namespace", "slug")
        if self._has_global_configuration_scope():
            queryset = queryset.annotate(
                active_field_count=Count(
                    "fields",
                    filter=Q(fields__lifecycle=CustomField.LIFECYCLE_ACTIVE),
                    distinct=True,
                ),
                active_choice_count=Count(
                    "choices",
                    filter=Q(choices__lifecycle=CustomFieldChoice.LIFECYCLE_ACTIVE),
                    distinct=True,
                ),
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_configure_global"] = self._has_global_configuration_scope()
        context["choice_set_rows"] = [
            {
                "choice_set": choice_set,
                "detail_url": _safe_reverse("extras:definition_choice_set_detail", pk=choice_set.pk),
                "edit_url": _safe_reverse("extras:definition_choice_set_edit", pk=choice_set.pk),
            }
            for choice_set in context["choice_sets"]
        ]
        context["create_url"] = _safe_reverse("extras:definition_choice_set_add")
        return context


class ChoiceSetDetailView(LoginRequiredMixin, _DefinitionPermissionMixin, DetailView):
    model = CustomFieldChoiceSet
    permission_required = "extras.view_customfieldchoiceset"
    template_name = "extras/definitions/choice_set_detail.html"
    context_object_name = "choice_set"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("choices").order_by("namespace", "slug")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show_usage = self._has_global_configuration_scope()
        usage = None
        if show_usage:
            usage = {
                "active_field_count": CustomField.objects.filter(
                    choice_set=self.object,
                    lifecycle=CustomField.LIFECYCLE_ACTIVE,
                ).count(),
                "active_choice_count": self.object.choices.filter(
                    lifecycle=CustomFieldChoice.LIFECYCLE_ACTIVE,
                ).count(),
            }
        can_configure = self._has_global_configuration_scope()
        is_local = self.object.management_kind == CustomFieldChoiceSet.MANAGEMENT_LOCAL
        context.update(
            {
                "choice_rows": [
                    {
                        "choice": choice,
                        "edit_url": _safe_reverse(
                            "extras:definition_choice_edit",
                            choice_set_pk=self.object.pk,
                            pk=choice.pk,
                        ),
                        "retire_url": _safe_reverse(
                            "extras:definition_choice_retire",
                            choice_set_pk=self.object.pk,
                            pk=choice.pk,
                        ),
                    }
                    for choice in self.object.choices.all()
                ],
                "show_usage_impact": show_usage,
                "usage_impact": usage,
                "can_configure_global": can_configure,
                "can_edit": can_configure and is_local,
                "edit_url": _safe_reverse("extras:definition_choice_set_edit", pk=self.object.pk),
                "retire_url": _safe_reverse("extras:definition_choice_set_retire", pk=self.object.pk),
                "choice_add_url": _safe_reverse("extras:definition_choice_add", choice_set_pk=self.object.pk),
            }
        )
        return context


class ChoiceSetCreateView(LoginRequiredMixin, _DefinitionPermissionMixin, CreateView):
    form_class = ChoiceSetCreateForm
    permission_required = "extras.add_customfieldchoiceset"
    template_name = "extras/definitions/choice_set_form.html"

    def form_valid(self, form):
        result = create_custom_field_choice_set(
            actor=_actor_for_user(self.request.user),
            definition=CustomFieldChoiceSetCreateInputDTO(
                namespace=form.cleaned_data["namespace"],
                slug=form.cleaned_data["slug"],
                label=form.cleaned_data["label"],
            ),
        )
        if getattr(result, "outcome", None) == "rejected":
            _add_command_issues(form, result)
            return self.form_invalid(form)
        self.object = CustomFieldChoiceSet.objects.get(pk=result.definition_id)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return _safe_reverse("extras:definition_choice_set_detail", pk=self.object.pk) or "/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add choice set")
        return context


class ChoiceSetUpdateView(LoginRequiredMixin, _LocalDefinitionMutationMixin, UpdateView):
    model = CustomFieldChoiceSet
    form_class = ChoiceSetUpdateForm
    permission_required = "extras.change_customfieldchoiceset"
    template_name = "extras/definitions/choice_set_form.html"
    context_object_name = "choice_set"

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["expected_resource_revision"] = self.current_revision(self.object)
        return kwargs

    def form_valid(self, form):
        result = update_custom_field_choice_set(
            actor=_actor_for_user(self.request.user),
            choice_set_id=self.object.pk,
            expected_resource_revision=form.cleaned_data["expected_resource_revision"],
            changes=CustomFieldChoiceSetUpdateInputDTO(
                label=form.cleaned_data.get("label"),
                replaced_by=_identity_or_none(form.cleaned_data.get("replacement_identity")),
            ),
        )
        if getattr(result, "outcome", None) == "rejected":
            _add_command_issues(form, result)
            return self.form_invalid(form)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return _safe_reverse("extras:definition_choice_set_detail", pk=self.object.pk) or "/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit choice set")
        context["definition"] = self.object
        return context


class ChoiceSetRetireView(LoginRequiredMixin, _LocalDefinitionMutationMixin, FormView):
    model = CustomFieldChoiceSet
    form_class = ChoiceSetRetireForm
    permission_required = "extras.change_customfieldchoiceset"
    template_name = "extras/definitions/retire_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(CustomFieldChoiceSet, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(instance=self.object, expected_resource_revision=self.current_revision(self.object))
        return kwargs

    def form_valid(self, form):
        result = deprecate_custom_field_choice_set(
            actor=_actor_for_user(self.request.user),
            choice_set_id=self.object.pk,
            expected_resource_revision=form.cleaned_data["expected_resource_revision"],
            replacement_identity=_identity_or_none(form.cleaned_data.get("replacement_identity")),
        )
        if getattr(result, "outcome", None) == "rejected":
            _add_command_issues(form, result)
            return self.form_invalid(form)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return _safe_reverse("extras:definition_choice_set_detail", pk=self.object.pk) or "/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"definition": self.object, "form_title": _("Retire choice set")})
        return context


class _ChoiceMutationMixin(_LocalDefinitionMutationMixin):
    model = CustomFieldChoice

    def _is_local_definition(self, definition):
        return definition.choice_set.management_kind == CustomFieldChoiceSet.MANAGEMENT_LOCAL

    def get_queryset(self):
        return super().get_queryset().select_related("choice_set")


class ChoiceCreateView(LoginRequiredMixin, _DefinitionPermissionMixin, CreateView):
    form_class = ChoiceCreateForm
    permission_required = "extras.add_customfieldchoice"
    template_name = "extras/definitions/choice_form.html"
    require_global_configuration = True

    def dispatch(self, request, *args, **kwargs):
        self.choice_set = get_object_or_404(CustomFieldChoiceSet, pk=kwargs["choice_set_pk"])
        if self.choice_set.management_kind != CustomFieldChoiceSet.MANAGEMENT_LOCAL:
            return HttpResponse(status=403)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        result = create_custom_field_choice(
            actor=_actor_for_user(self.request.user),
            definition=CustomFieldChoiceCreateInputDTO(
                choice_set_id=self.choice_set.pk,
                key=form.cleaned_data["key"],
                label=form.cleaned_data["label"],
                position=form.cleaned_data["position"],
            ),
        )
        if getattr(result, "outcome", None) == "rejected":
            _add_command_issues(form, result)
            return self.form_invalid(form)
        self.object = CustomFieldChoice.objects.get(pk=result.definition_id)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return _safe_reverse("extras:definition_choice_set_detail", pk=self.choice_set.pk) or "/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"choice_set": self.choice_set, "form_title": _("Add choice")})
        return context


class ChoiceUpdateView(LoginRequiredMixin, _ChoiceMutationMixin, UpdateView):
    form_class = ChoiceUpdateForm
    permission_required = "extras.change_customfieldchoice"
    template_name = "extras/definitions/choice_form.html"
    context_object_name = "choice"

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.choice_set = self.object.choice_set
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["expected_resource_revision"] = self.current_revision(self.object)
        return kwargs

    def form_valid(self, form):
        result = update_custom_field_choice(
            actor=_actor_for_user(self.request.user),
            choice_id=self.object.pk,
            expected_resource_revision=form.cleaned_data["expected_resource_revision"],
            changes=CustomFieldChoiceUpdateInputDTO(
                label=form.cleaned_data.get("label"),
                position=form.cleaned_data.get("position"),
                replaced_by=_identity_or_none(form.cleaned_data.get("replacement_identity")),
            ),
        )
        if getattr(result, "outcome", None) == "rejected":
            _add_command_issues(form, result)
            return self.form_invalid(form)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return _safe_reverse("extras:definition_choice_set_detail", pk=self.choice_set.pk) or "/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"choice_set": self.choice_set, "form_title": _("Edit choice")})
        return context


class ChoiceRetireView(LoginRequiredMixin, _ChoiceMutationMixin, FormView):
    form_class = ChoiceRetireForm
    permission_required = "extras.change_customfieldchoice"
    template_name = "extras/definitions/retire_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(CustomFieldChoice, pk=kwargs["pk"])
        self.choice_set = self.object.choice_set
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(instance=self.object, expected_resource_revision=self.current_revision(self.object))
        return kwargs

    def form_valid(self, form):
        result = deprecate_custom_field_choice(
            actor=_actor_for_user(self.request.user),
            choice_id=self.object.pk,
            expected_resource_revision=form.cleaned_data["expected_resource_revision"],
            replacement_identity=_identity_or_none(form.cleaned_data.get("replacement_identity")),
        )
        if getattr(result, "outcome", None) == "rejected":
            _add_command_issues(form, result)
            return self.form_invalid(form)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return _safe_reverse("extras:definition_choice_set_detail", pk=self.choice_set.pk) or "/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"definition": self.object, "choice_set": self.choice_set, "form_title": _("Retire choice")})
        return context


__all__ = [
    "ChoiceCreateView",
    "ChoiceRetireView",
    "ChoiceSetCreateView",
    "ChoiceSetDetailView",
    "ChoiceSetListView",
    "ChoiceSetRetireView",
    "ChoiceSetUpdateView",
    "ChoiceUpdateView",
]
