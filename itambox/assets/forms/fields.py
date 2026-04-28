from django import forms
from django.core.exceptions import ValidationError


class StatusModelChoiceField(forms.ModelChoiceField):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, str) and not value.isdigit():
            from django.db.models import Q
            try:
                return self.queryset.get(Q(slug=value) | Q(name__iexact=value))
            except self.queryset.model.DoesNotExist:
                raise ValidationError(self.error_messages['invalid_choice'], code='invalid_choice')
        return super().to_python(value)
