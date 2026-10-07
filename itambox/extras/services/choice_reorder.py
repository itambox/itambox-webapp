"""Reorder the choices of one local choice set through the definition commands."""

from __future__ import annotations

from collections.abc import Sequence

from django.db import DEFAULT_DB_ALIAS, transaction

from extras.models import CustomFieldChoiceSet
from extras.services._definition_command_support import issue, reject_for, resource_revision_for_definition
from extras.services.definition_command_contracts import (
    CustomFieldChoiceSetUpdateInputDTO,
    CustomFieldChoiceUpdateInputDTO,
    DefinitionRejectedDTO,
)
from extras.services.definition_commands import update_custom_field_choice, update_custom_field_choice_set
from extras.services.specifications.contracts import ResourceRevision
from organization.services.access_scope import ActorContextDTO


class _Abort(Exception):
    def __init__(self, result: DefinitionRejectedDTO):
        super().__init__("choice reorder rejected")
        self.result = result


def reorder_custom_field_choices(
    *,
    actor: ActorContextDTO,
    choice_set_id: int,
    expected_resource_revision: str,
    keys: Sequence[str],
    using: str = DEFAULT_DB_ALIAS,
):
    """Apply ``keys`` as the new order; ``keys`` must permute the current choice keys."""
    keys = tuple(keys)
    choice_set = CustomFieldChoiceSet.objects.using(using).filter(pk=choice_set_id).first()
    if choice_set is None:
        return reject_for(None, issue("OBJECT_UNAVAILABLE", message_key="specifications.object_unavailable"))
    if len(set(keys)) != len(keys):
        return reject_for(choice_set, issue("DUPLICATE_FIELD", path=("keys",)))
    try:
        with transaction.atomic(using=using):
            choices = {c.key: c for c in choice_set.choices.using(using).all()}
            if str(resource_revision_for_definition(choice_set, using=using)) != expected_resource_revision:
                raise _Abort(reject_for(choice_set, issue("STALE_RESOURCE", message_key="specifications.stale_resource")))
            if set(keys) != set(choices):
                raise _Abort(reject_for(choice_set, issue("REFERENCE_CONFLICT", path=("keys",))))
            for position, key in enumerate(keys, start=1):
                choice = choices[key]
                if choice.position == position:
                    continue
                result = update_custom_field_choice(
                    actor=actor,
                    choice_id=choice.pk,
                    expected_resource_revision=ResourceRevision(
                        str(resource_revision_for_definition(choice, using=using))
                    ),
                    changes=CustomFieldChoiceUpdateInputDTO(position=position),
                    using=using,
                )
                if isinstance(result, DefinitionRejectedDTO):
                    raise _Abort(result)
            choice_set.refresh_from_db(using=using)
            result = update_custom_field_choice_set(
                actor=actor,
                choice_set_id=choice_set_id,
                expected_resource_revision=ResourceRevision(
                    str(resource_revision_for_definition(choice_set, using=using))
                ),
                changes=CustomFieldChoiceSetUpdateInputDTO(),
                using=using,
            )
            if isinstance(result, DefinitionRejectedDTO):
                raise _Abort(result)
            return result
    except _Abort as abort:
        return abort.result
