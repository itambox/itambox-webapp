from rest_framework import serializers

from itambox.api.utils import get_serializer_for_model


class GFKSerializerField(serializers.Field):
    def to_representation(self, instance, **kwargs):
        if instance is None:
            return None
        serializer = get_serializer_for_model(instance)
        context = {'request': self.context['request']}
        return serializer(instance, nested=True, context=context).data
