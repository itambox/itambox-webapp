import re
from django.core.exceptions import ValidationError
from django.conf import settings

class CustomValidator:
    """
    Validates model objects against dynamic validation rules defined
    in the settings configurations.
    """
    @classmethod
    def validate_object(cls, instance):
        model_label = instance._meta.label
        rules = getattr(settings, 'CUSTOM_VALIDATORS', {}).get(model_label, [])
        if not rules:
            return

        errors = {}
        for rule in rules:
            target_field = rule.get('field')
            if not target_field or not hasattr(instance, target_field):
                continue

            val = getattr(instance, target_field)

            # 1. Regex validation pattern
            regex_pattern = rule.get('regex')
            if regex_pattern is not None:
                val_str = str(val) if val is not None else ''
                if not re.match(regex_pattern, val_str):
                    error_msg = rule.get('error_message', "Field fails matching validation layout rules.")
                    if target_field not in errors:
                        errors[target_field] = []
                    errors[target_field].append(error_msg)

            # 2. Value range constraints
            min_val = rule.get('min_value')
            max_val = rule.get('max_value')
            if val is not None:
                try:
                    if min_val is not None and val < min_val:
                        if target_field not in errors:
                            errors[target_field] = []
                        errors[target_field].append(f"Must be at least {min_val}.")
                    if max_val is not None and val > max_val:
                        if target_field not in errors:
                            errors[target_field] = []
                        errors[target_field].append(f"Cannot exceed {max_val}.")
                except TypeError:
                    # Ignore or skip type mismatch errors if values are not comparable
                    pass

        if errors:
            raise ValidationError(errors)
