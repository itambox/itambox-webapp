from collections import OrderedDict

from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

# Based on NetBox utilities.api.fields

class ChoiceField(serializers.ChoiceField):
    """
    Represent a ChoiceSet field using its value/label pairs.
    Includes a representation for the display label.
    """
    def __init__(self, choices, **kwargs):
        self.choiceset = choices # Expecting an instance of a ChoiceSet subclass
        # Initialize with the actual choices (value, label) from the ChoiceSet iterator
        super().__init__(choices=list(self.choiceset), **kwargs)

    def to_representation(self, value):
        # Get the display label using the underlying ChoiceSet's dictionary
        # This assumes ChoiceSet has a .get(value) method or similar, which we might need to add.
        # For now, let's access the internal choices dictionary directly.
        try:
            choices_dict = OrderedDict(self.choices)
            label = choices_dict[value]
        except KeyError:
            # Handle cases where the value might not be in choices (e.g., old data)
            label = str(value) # Fallback to the raw value

        return {
            'value': value,
            'label': label
        }

class ContentTypeField(serializers.RelatedField):
    """
    Represent a ContentType for API serializers.
    """
    # queryset = ContentType.objects.all() # Moved to __init__

    default_error_messages = {
        'invalid_format': 'Invalid ContentType format. Use "app_label.model".',
        'does_not_exist': 'Invalid ContentType: {value}',
    }

    def __init__(self, **kwargs):
        # Set queryset only if the field is writable
        if not kwargs.get('read_only', False):
            self.queryset = ContentType.objects.all()
        else:
            self.queryset = None # No queryset needed for read_only
        super().__init__(**kwargs)

    def to_representation(self, value):
        return f'{value.app_label}.{value.model}'

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail('invalid_format')
        try:
            app_label, model = data.split('.')
        except ValueError:
            self.fail('invalid_format')

        try:
            # Use self.get_queryset() which is standard for RelatedField
            return self.get_queryset().get(app_label=app_label, model=model)
        except ContentType.DoesNotExist:
            self.fail('does_not_exist', value=data)
        except AttributeError: # Handle case where queryset is None (read_only=True)
             # This path shouldn't be reached if read_only=True, but handle defensively
             # Or raise a different error? For now, rely on DRF preventing writes to read_only fields.
             pass 