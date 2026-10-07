"""Reorder the choices of one local choice set through the definition commands."""

from __future__ import annotations

from collections.abc import Sequence

from django.db import DEFAULT_DB_ALIAS, transaction

from extras.models import CustomFieldChoice, CustomFieldChoiceSet
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


def _check_reorder(choice_set, choices, keys, expected_resource_revision, using):
    """Return a rejection for stale revisions or non-permutations, else ``None``."""
    if str(resource_revision_for_definition(choice_set, using=using)) != expected_resource_revision:
        return reject_for(choice_set, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
    if set(keys) != set(choices):
        return reject_for(choice_set, issue("REFERENCE_CONFLICT", path=("keys",)))
    return None


def _set_position(actor, choice, position, using):
    """Persist one new position through the definition command.

    The command locks and writes its own instance, so the caller's copy keeps the
    pre-write version; re-read the row to send the revision the command expects.
    """
    fresh = CustomFieldChoice.objects.using(using).get(pk=choice.pk)
    return update_custom_field_choice(
        actor=actor,
        choice_id=choice.pk,
        expected_resource_revision=ResourceRevision(str(resource_revision_for_definition(fresh, using=using))),
        changes=CustomFieldChoiceUpdateInputDTO(position=position),
        using=using,
    )


def _apply_positions(actor, choices, keys, using):
    """Move each choice to its new position; return the first rejection or ``None``.

    ``CustomFieldChoice`` carries a unique ``(choice_set, position)`` constraint
    that the definition command validates immediately, so writing a new position
    straight onto a taken slot is rejected as a transient collision. Movers are
    parked above every current position first; the final pass then only writes
    into slots that are free (either vacated by a mover or already correct).
    """
    movers = [
        (position, choices[key]) for position, key in enumerate(keys, start=1) if choices[key].position != position
    ]
    if not movers:
        return None
    park_base = max([choice.position for choice in choices.values()] + [len(keys)]) + 1
    for index, (_, choice) in enumerate(movers, start=1):
        result = _set_position(actor, choice, park_base + index, using)
        if isinstance(result, DefinitionRejectedDTO):
            return result
    for position, choice in movers:
        result = _set_position(actor, choice, position, using)
        if isinstance(result, DefinitionRejectedDTO):
            return result
    return None


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
            rejected = _check_reorder(choice_set, choices, keys, expected_resource_revision, using)
            if rejected is None:
                rejected = _apply_positions(actor, choices, keys, using)
            if rejected is not None:
                raise _Abort(rejected)
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
