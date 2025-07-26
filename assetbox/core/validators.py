import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class CustomValidator:
    def validate(self, instance, request=None):
        raise NotImplementedError


CUSTOM_VALIDATORS = {}


def register_validator(model_path, validator):
    CUSTOM_VALIDATORS.setdefault(model_path, []).append(validator)


def get_validators(model):
    model_path = f'{model._meta.app_label}.{model._meta.model_name}'
    return CUSTOM_VALIDATORS.get(model_path, [])


def parse_json_rules(instance, rules):
    errors = {}

    for field_name, field_rules in rules.items():
        if field_name == '__all__':
            continue

        value = getattr(instance, field_name, None)

        if field_rules.get('required', False) and not value:
            errors[field_name] = ValidationError(
                _('%(field)s is required.'),
                params={'field': field_name},
            )
            continue

        if value is None or value == '':
            continue

        if 'min_length' in field_rules and isinstance(value, str):
            min_length = field_rules['min_length']
            if len(value) < min_length:
                errors[field_name] = ValidationError(
                    _('%(field)s must be at least %(min)d characters.'),
                    params={'field': field_name, 'min': min_length},
                )

        if 'max_length' in field_rules and isinstance(value, str):
            max_length = field_rules['max_length']
            if len(value) > max_length:
                errors[field_name] = ValidationError(
                    _('%(field)s must be at most %(max)d characters.'),
                    params={'field': field_name, 'max': max_length},
                )

        if 'min' in field_rules and isinstance(value, (int, float)):
            min_val = field_rules['min']
            if value < min_val:
                errors[field_name] = ValidationError(
                    _('%(field)s must be at least %(min)s.'),
                    params={'field': field_name, 'min': min_val},
                )

        if 'max' in field_rules and isinstance(value, (int, float)):
            max_val = field_rules['max']
            if value > max_val:
                errors[field_name] = ValidationError(
                    _('%(field)s must be at most %(max)s.'),
                    params={'field': field_name, 'max': max_val},
                )

        if 'pattern' in field_rules and isinstance(value, str):
            pattern = field_rules['pattern']
            try:
                if not re.match(pattern, value):
                    errors[field_name] = ValidationError(
                        _('%(field)s does not match required pattern.'),
                        params={'field': field_name},
                    )
            except re.error:
                pass

    if errors:
        raise ValidationError(errors)
