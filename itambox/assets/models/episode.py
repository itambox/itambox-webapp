"""RepairEpisode — optional grouping of the records of one repair/replacement story (#504)."""

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.managers import TenantScopingAllObjectsManager, TenantScopingSoftDeleteManager
from core.mixins import JournalingMixin, SoftDeleteMixin
from core.models import BaseModel, ChangeLoggingMixin


class RepairEpisode(JournalingMixin, SoftDeleteMixin, ChangeLoggingMixin, BaseModel):
    """A repair/replacement episode: the story of one failing asset.

    The episode is a small, optional hub: the unit it is about (``asset``) and,
    when one exists, the unit that stood in for it (``substitute_asset`` — a
    temporary loaner or a permanent replacement). The records of the story
    (``AssetMaintenance``, ``AssetReservation``, ``AssetDisposal``) may
    reference the episode through their own optional ``episode`` FK; nothing is
    forced into an episode and every link is change-logged like any other field.

    Deliberately NOT a ticket or workflow engine: there is no episode status and
    no state machine. Existing status labels and record states stay the only
    source of truth; the episode only groups records so the asset detail page
    can tell the full story.

    Tenant-scoped through the parent asset so multi-tenant boundary checks flow
    through the same ``tenant_lookup`` pattern as the records it groups.
    """

    tenant_lookup = "asset__tenant"
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    @property
    def tenant(self):
        return self.asset.tenant if self.asset_id else None

    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="repair_episodes",
        db_index=True,
        verbose_name=_("Asset"),
        help_text=_("The unit this repair or replacement episode is about."),
    )
    substitute_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="substitute_episodes",
        verbose_name=_("Loaner / Substitute"),
        help_text=_("The unit that stood in for the asset (temporary loan or permanent replacement)."),
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Repair Episode")
        verbose_name_plural = _("Repair Episodes")

    def __str__(self):
        return f"Repair episode for {self.asset}"

    def get_absolute_url(self):
        return reverse("assets:repairepisode_detail", kwargs={"pk": self.pk})

    def clean(self):
        super().clean()
        if self.asset_id and self.substitute_asset_id:
            if self.asset_id == self.substitute_asset_id:
                raise ValidationError({"substitute_asset": _("The substitute cannot be the asset itself.")})
            if self.asset.tenant_id != self.substitute_asset.tenant_id:
                raise ValidationError(
                    {"substitute_asset": _("The substitute must belong to the same tenant as the asset.")}
                )
