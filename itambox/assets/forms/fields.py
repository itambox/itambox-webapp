from django import forms
from django.core.exceptions import ValidationError

from assets.models import RepairEpisode


class StatusModelChoiceField(forms.ModelChoiceField):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, str) and not value.isdigit():
            from django.db.models import Q

            try:
                return self.queryset.get(Q(slug=value) | Q(name__iexact=value))
            except self.queryset.model.DoesNotExist:
                raise ValidationError(self.error_messages["invalid_choice"], code="invalid_choice") from None
        return super().to_python(value)


def selectable_episodes(current_id=None):
    """Repair-episode choices for the record and episode forms (#504).

    Lists the tenant's active episodes and keeps the episode a record already
    links to selectable even when it sits in the recycle bin: the link on the
    record must never be silently dropped by an edit that only changes another
    field.
    """
    queryset = RepairEpisode.objects.all()
    if current_id:
        queryset = queryset | RepairEpisode.all_objects.filter(pk=current_id)
    return queryset
