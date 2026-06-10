from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from itambox.api.base import BaseModelSerializer
from subscriptions.models import Provider, Subscription, SubscriptionAssignment
from organization.api.serializers import NestedTenantSerializer, NestedTenantGroupSerializer, ContactAssignmentSerializer
from organization.models import Tenant, TenantGroup
from extras.api.serializers import TagSerializer

User = get_user_model()


class ProviderSerializer(BaseModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    subscription_count = serializers.IntegerField(read_only=True)
    slug = serializers.SlugField(required=False, allow_blank=True)
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects.all(),
        source='tenant', write_only=True, required=False, allow_null=True
    )
    tenant_group = NestedTenantGroupSerializer(read_only=True)
    tenant_group_id = serializers.PrimaryKeyRelatedField(
        queryset=TenantGroup.objects.all(),
        source='tenant_group', write_only=True, required=False, allow_null=True
    )
    contacts = ContactAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = Provider
        fields = (
            'id', 'name', 'slug', 'tenant', 'tenant_id', 'tenant_group', 'tenant_group_id',
            'account_id', 'portal_url', 'admin_notes', 'is_active', 'subscription_count',
            'tags', 'contacts', 'created_at', 'updated_at'
        )
        read_only_fields = ('created_at', 'updated_at')
        brief_fields = ('id', 'name', 'slug', 'is_active', 'subscription_count')
        validators = []


class SubscriptionSerializer(BaseModelSerializer):
    provider = ProviderSerializer(read_only=True)
    provider_id = serializers.PrimaryKeyRelatedField(
        queryset=Provider.objects.all(), source='provider', write_only=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects.all(),
        source='tenant', write_only=True, required=False, allow_null=True
    )
    owner = serializers.StringRelatedField(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source='owner', write_only=True, required=False, allow_null=True
    )
    tags = TagSerializer(many=True, read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    billing_cycle_display = serializers.CharField(source='get_billing_cycle_display', read_only=True)
    days_until_renewal = serializers.IntegerField(read_only=True)
    annual_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Subscription
        fields = (
            'id', 'name', 'slug', 'provider', 'provider_id', 'type', 'type_display',
            'status', 'status_display', 'tenant', 'tenant_id', 'owner', 'owner_id',
            'start_date', 'renewal_date', 'renewal_cost', 'currency',
            'billing_cycle', 'billing_cycle_display', 'term_months', 'auto_renewal',
            'licensed_quantity', 'contract_reference', 'cost_center',
            'cancellation_date', 'days_until_renewal', 'annual_cost',
            'description', 'notes', 'tags', 'created_at', 'updated_at'
        )
        read_only_fields = ('created_at', 'updated_at', 'days_until_renewal', 'annual_cost')
        brief_fields = ('id', 'name', 'slug', 'provider', 'status', 'status_display', 'days_until_renewal')


class SubscriptionAssignmentSerializer(BaseModelSerializer):
    subscription = SubscriptionSerializer(read_only=True)
    subscription_id = serializers.PrimaryKeyRelatedField(
        queryset=Subscription.objects.all(), source='subscription', write_only=True
    )
    assigned_object = serializers.SerializerMethodField(read_only=True)
    assigned_by = serializers.StringRelatedField(read_only=True)
    assigned_by_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source='assigned_by', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = SubscriptionAssignment
        fields = (
            'id', 'subscription', 'subscription_id',
            'content_type', 'object_id',
            'assigned_object', 'assigned_by', 'assigned_by_id',
            'assigned_date', 'notes', 'created_at', 'updated_at'
        )
        read_only_fields = ('created_at', 'updated_at', 'assigned_date')
        brief_fields = ('id', 'subscription', 'assigned_object')

    def get_assigned_object(self, obj):
        if obj.assigned_object:
            return {
                'id': obj.object_id,
                'type': obj.content_type.model,
                'name': str(obj.assigned_object),
            }
        return None

    def create(self, validated_data):
        return super().create(validated_data)
