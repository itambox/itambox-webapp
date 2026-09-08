"""Forms and safe transport helpers for the Type Library browser workflow."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from assets.services.type_library.planning import LibraryPlan, LibraryPlanAction

MAX_LIBRARY_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_LIBRARY_PLAN_PAYLOAD_BYTES = 2 * 1024 * 1024
_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_RESOLUTION_CHOICES = (
    ("keep_local", _("Keep local")),
    ("take_upstream", _("Take upstream")),
    ("abort", _("Abort import")),
)


def resolution_field_name(action_id: str) -> str:
    """Return a deterministic HTML field name for one signed action ID."""

    return "resolution_" + re.sub(r"[^a-zA-Z0-9_]", "_", action_id)


def serialize_library_plan(plan: LibraryPlan) -> str:
    """Serialize the exact preview plan carried into the later apply request."""

    if not isinstance(plan, LibraryPlan):
        raise TypeError("plan must be a LibraryPlan")
    payload = {
        "version": 1,
        "namespace": plan.namespace,
        "incoming_release": plan.incoming_release,
        "source_digest": plan.source_digest,
        "snapshot_digest": plan.snapshot_digest,
        "baseline_digest": plan.baseline_digest,
        "current_digest": plan.current_digest,
        "actions": [_serialize_action(action) for action in plan.actions],
        "resolutions": {key: value for key, value in plan.resolutions},
        "can_apply": plan.can_apply,
        "plan_digest": plan.plan_digest,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_LIBRARY_PLAN_PAYLOAD_BYTES:
        raise ValueError("library plan exceeds the form payload limit")
    return encoded


def deserialize_library_plan(value: str) -> LibraryPlan:
    """Rebuild a plan submitted by the browser; the signed token remains authority."""

    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_LIBRARY_PLAN_PAYLOAD_BYTES:
        raise ValidationError(_("The preview plan is too large or invalid."))
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(_("The preview plan is invalid.")) from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValidationError(_("The preview plan is invalid."))
    try:
        actions = tuple(_deserialize_action(item) for item in payload["actions"])
        resolutions = tuple(sorted((str(key), str(decision)) for key, decision in payload["resolutions"].items()))
        if any(decision not in {choice[0] for choice in _RESOLUTION_CHOICES} for _, decision in resolutions):
            raise ValueError("invalid resolution")
        if type(payload["incoming_release"]) is not int or payload["incoming_release"] < 1:
            raise ValueError("invalid release")
        plan = LibraryPlan(
            namespace=_required_string(payload, "namespace"),
            incoming_release=payload["incoming_release"],
            source_digest=_required_string(payload, "source_digest"),
            snapshot_digest=_optional_string(payload, "snapshot_digest"),
            baseline_digest=_optional_string(payload, "baseline_digest"),
            current_digest=_optional_string(payload, "current_digest"),
            actions=actions,
            conflicts=tuple(action for action in actions if action.action == "conflict"),
            resolutions=resolutions,
            can_apply=not any(
                action.action == "conflict" and action.decision in {"abort", "conflict"} for action in actions
            ),
            plan_digest=_required_string(payload, "plan_digest"),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValidationError(_("The preview plan is invalid.")) from exc
    return plan


def _serialize_action(action: LibraryPlanAction) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "action": action.action,
        "identity": action.identity,
        "path": list(action.path),
        "baseline": action.baseline,
        "local": action.local,
        "incoming": action.incoming,
        "decision": action.decision,
        "reason": action.reason,
    }


def _deserialize_action(value: object) -> LibraryPlanAction:
    if not isinstance(value, dict):
        raise ValueError("invalid plan action")
    path = value["path"]
    if not isinstance(path, list) or not all(isinstance(item, str) for item in path):
        raise ValueError("invalid action path")
    action = _required_string(value, "action")
    if action not in {"create", "update", "unchanged", "deprecate", "conflict", "reference"}:
        raise ValueError("invalid action kind")
    decision = _required_string(value, "decision")
    return LibraryPlanAction(
        action_id=_required_string(value, "action_id"),
        action=action,
        identity=_required_string(value, "identity"),
        path=tuple(path),
        baseline=value.get("baseline"),
        local=value.get("local"),
        incoming=value.get("incoming"),
        decision=decision,
        reason=_required_string(value, "reason"),
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if type(value) is not str or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and type(value) is not str:
        raise ValueError(f"{key} must be a string or null")
    return value


class LibraryUploadForm(forms.Form):
    """Choose one bounded local JSON document for validation and preview."""

    document = forms.FileField(
        label=_("Library JSON file"),
        widget=forms.ClearableFileInput(attrs={"accept": ".json,application/json"}),
    )

    def clean_document(self):
        document = self.cleaned_data["document"]
        if document.size > MAX_LIBRARY_DOCUMENT_BYTES:
            raise ValidationError(_("Library JSON files must be 10 MiB or smaller."))
        if hasattr(document, "seek"):
            document.seek(0)
        return document


class LibraryApplyForm(forms.Form):
    """Carry the original source, plan and signed token into an explicit apply."""

    source_document = forms.CharField(widget=forms.HiddenInput, required=True)
    plan_payload = forms.CharField(widget=forms.HiddenInput, required=True)
    preview_token = forms.CharField(widget=forms.HiddenInput, required=True)

    def __init__(self, *args, conflicts: Iterable[LibraryPlanAction] = (), **kwargs):
        super().__init__(*args, **kwargs)
        self._conflict_field_ids: dict[str, str] = {}
        for conflict in conflicts:
            field_name = resolution_field_name(conflict.action_id)
            self._conflict_field_ids[field_name] = conflict.action_id
            self.fields[field_name] = forms.ChoiceField(
                label=_("Resolution for %(path)s") % {"path": ".".join(conflict.path)},
                choices=_RESOLUTION_CHOICES,
                required=True,
                initial=conflict.decision
                if conflict.decision in {choice[0] for choice in _RESOLUTION_CHOICES}
                else "abort",
            )

    def clean_source_document(self):
        value = self.cleaned_data["source_document"]
        if len(value.encode("utf-8")) > MAX_LIBRARY_DOCUMENT_BYTES:
            raise ValidationError(_("The retained library document is too large."))
        return value

    def clean_plan_payload(self):
        value = self.cleaned_data["plan_payload"]
        deserialize_library_plan(value)
        return value

    def resolutions(self) -> dict[str, str]:
        if not self.is_valid():
            raise ValueError("call resolutions after form validation")
        return {action_id: self.cleaned_data[field_name] for field_name, action_id in self._conflict_field_ids.items()}


class LibraryExportForm(forms.Form):
    """Select an explicit provenance-preserving export mode."""

    mode = forms.ChoiceField(
        label=_("Export mode"),
        choices=(
            ("effective_snapshot", _("Effective snapshot")),
            ("original_release", _("Original release")),
            ("fork", _("Fork into a new namespace")),
        ),
    )
    new_namespace = forms.CharField(label=_("New namespace"), max_length=62, required=False)
    acknowledge_retained_history = forms.BooleanField(
        label=_("Acknowledge retained historical definitions"),
        required=False,
    )

    def __init__(self, *args, current_namespace: str | None = None, **kwargs):
        self.current_namespace = current_namespace
        super().__init__(*args, **kwargs)

    def clean_new_namespace(self):
        value = self.cleaned_data.get("new_namespace", "").strip()
        if value and _NAMESPACE_RE.fullmatch(value) is None:
            raise ValidationError(_("Use lowercase letters, digits and single hyphens for a namespace."))
        return value

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("mode")
        namespace = cleaned.get("new_namespace", "")
        if mode == "fork":
            if not namespace:
                self.add_error("new_namespace", _("A fork needs a different namespace."))
            elif self.current_namespace and namespace == self.current_namespace:
                self.add_error("new_namespace", _("A fork needs a different namespace."))
        elif namespace:
            self.add_error("new_namespace", _("A new namespace is only used for a fork."))
        return cleaned


__all__ = [
    "LibraryApplyForm",
    "LibraryExportForm",
    "LibraryUploadForm",
    "MAX_LIBRARY_DOCUMENT_BYTES",
    "deserialize_library_plan",
    "resolution_field_name",
    "serialize_library_plan",
]
