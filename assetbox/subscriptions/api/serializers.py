from rest_framework import serializers
from django.contrib.contenttypes.models import ContentType

from subscriptions.models import Provider, Subscription, SubscriptionAssignment
from extras.api.serializers import TagSerializer # Reuse tag serializer

class ProviderSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = Provider
        fields = (
            'id', 'name', 'account_id', 'portal_url', 'admin_notes', 
            'support_contact', 'tags', 'created_at', 'updated_at'
        )

class SubscriptionSerializer(serializers.ModelSerializer):
    provider = ProviderSerializer(read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    type = serializers.CharField(source='get_type_display', read_only=True) # Display human-readable type

    class Meta:
        model = Subscription
        fields = (
            'id', 'name', 'provider', 'type', 'start_date', 'renewal_date', 
            'renewal_cost', 'term_months', 'description', 'notes', 'tags',
            'created_at', 'updated_at'
        )

class SubscriptionAssignmentSerializer(serializers.ModelSerializer):
    subscription = SubscriptionSerializer(read_only=True)
    # Represent GFK target generically
    assigned_object_type = serializers.SlugRelatedField(
        queryset=ContentType.objects.all(),
        slug_field='model',
        source='content_type'
    )
    assigned_object = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = SubscriptionAssignment
        fields = (
            'id', 'subscription', 'assigned_object_type', 'object_id', 
            'assigned_object', 'assigned_date', 'notes',
            'created_at', 'updated_at'
        )

    def get_assigned_object(self, obj):
        # Return a simple string representation or a nested serializer if needed
        if obj.assigned_object:
            return str(obj.assigned_object)
        return None 