from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers
from itambox.api.base import BaseModelSerializer
from itambox.api.fields import ContentTypeField, validate_gfk_target_tenant
from extras.models import (
    Tag, Dashboard, CustomField, CustomFieldset,
    EventRule, WebhookEndpoint, NotificationChannel, AlertRule, JournalEntry,
)


class TagSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:extras_api:tag-detail')

    class Meta:
        model = Tag
        fields = [
            'id', 'url', 'name', 'slug', 'color',
            'description', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'url', 'name', 'slug', 'color']


class CustomFieldSerializer(BaseModelSerializer):
    field_type_display = serializers.CharField(source='get_field_type_display', read_only=True)

    object_types = serializers.SlugRelatedField(
        many=True, read_only=True, slug_field='model'
    )

    class Meta:
        model = CustomField
        fields = [
            'id', 'name', 'label', 'field_type', 'field_type_display',
            'choices', 'required', 'object_types', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'label', 'field_type']


class CustomFieldsetSerializer(BaseModelSerializer):
    fields = CustomFieldSerializer(many=True, read_only=True)

    class Meta:
        model = CustomFieldset
        fields = ['id', 'name', 'fields', 'created_at', 'updated_at']
        brief_fields = ['id', 'name']


class DashboardSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Dashboard
        fields = ['id', 'user', 'layout', 'created', 'last_updated']
        brief_fields = ['id', 'user']


class WebhookEndpointSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:extras_api:webhookendpoint-detail')
    # The model's own URLField is named `url`, which collides with the API
    # self-link `url` above; expose the target endpoint as `target_url`.
    target_url = serializers.URLField(source='url', max_length=2000)
    http_method_display = serializers.CharField(source='get_http_method_display', read_only=True)
    # `secret` is stored encrypted (model.save() encrypts; secret_decrypted reads).
    # Accept it write-only and let model.save() encrypt; the ciphertext/plaintext
    # is NEVER serialized out (mirrors License.product_key being omitted entirely).
    secret = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = WebhookEndpoint
        fields = [
            'id', 'url', 'name', 'target_url', 'http_method', 'http_method_display',
            'headers', 'secret', 'enabled', 'retry_count', 'retry_backoff',
            'created_at', 'updated_at',
        ]
        brief_fields = ['id', 'url', 'name', 'enabled']


class EventRuleSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:extras_api:eventrule-detail')
    model = ContentTypeField(queryset=ContentType.objects.all())
    action_type_display = serializers.CharField(source='get_action_type_display', read_only=True)
    webhook = WebhookEndpointSerializer(read_only=True)
    # WebhookEndpoint.objects is the tenant-scoped manager, so a rule can only
    # point at a same-tenant (or system-wide) webhook; this mirrors
    # EventRule.clean()'s same-tenant guard at the write boundary.
    webhook_id = serializers.PrimaryKeyRelatedField(
        queryset=WebhookEndpoint.objects, source='webhook', write_only=True,
        required=False, allow_null=True,
    )

    class Meta:
        model = EventRule
        fields = [
            'id', 'url', 'name', 'model', 'events', 'conditions',
            'action_type', 'action_type_display', 'webhook', 'webhook_id',
            'action_config', 'enabled', 'created_at', 'updated_at',
        ]
        brief_fields = ['id', 'url', 'name', 'action_type', 'enabled']


class NotificationChannelSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:extras_api:notificationchannel-detail')
    channel_type_display = serializers.CharField(source='get_channel_type_display', read_only=True)

    class Meta:
        model = NotificationChannel
        # `config` is an opaque JSONField (SMTP settings, webhook URL, etc.); there
        # is no discrete secret column, so it is exposed plainly like other JSON.
        fields = [
            'id', 'url', 'name', 'channel_type', 'channel_type_display',
            'enabled', 'config', 'created_at', 'updated_at',
        ]
        brief_fields = ['id', 'url', 'name', 'channel_type', 'enabled']


class AlertRuleSerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:extras_api:alertrule-detail')
    alert_type_display = serializers.CharField(source='get_alert_type_display', read_only=True)
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    channels = NotificationChannelSerializer(many=True, read_only=True)
    # NotificationChannel.objects is tenant-scoped, so a rule can only notify
    # via same-tenant channels.
    channel_ids = serializers.PrimaryKeyRelatedField(
        queryset=NotificationChannel.objects, source='channels', write_only=True,
        many=True, required=False,
    )

    class Meta:
        model = AlertRule
        fields = [
            'id', 'url', 'name', 'description', 'alert_type', 'alert_type_display',
            'threshold_value', 'severity', 'severity_display', 'is_active', 'is_muted',
            'renotify_interval_days', 'last_fired_at', 'channels', 'channel_ids',
            'created_at', 'updated_at',
        ]
        brief_fields = ['id', 'url', 'name', 'alert_type', 'severity', 'is_active']


class JournalEntrySerializer(BaseModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:extras_api:journalentry-detail')
    model = ContentTypeField(queryset=ContentType.objects.all())
    # Author is read-only: stamped from the request on create (see validate) and
    # immutable thereafter — journal entries are an audit trail.
    user_display = serializers.StringRelatedField(source='user', read_only=True)

    class Meta:
        model = JournalEntry
        fields = [
            'id', 'url', 'model', 'object_id', 'user_display',
            'comment', 'created', 'created_at', 'updated_at',
        ]
        brief_fields = ['id', 'url', 'model', 'object_id']

    def validate(self, attrs):
        if self.instance is None:
            # Create: stamp the author and verify the journaled object is visible
            # within the active tenant. validate_gfk_target_tenant resolves via
            # the target's default manager AND compares obj.tenant to the active
            # tenant, so it also guards tenant-owned models whose default manager
            # is NOT tenant-scoping (Dashboard, Job, Token, TenantMembership) — a
            # plain .exists() check would let those through cross-tenant.
            request = self.context.get('request')
            if request is not None and request.user.is_authenticated:
                attrs['user'] = request.user
            validate_gfk_target_tenant(attrs.get('model'), attrs.get('object_id'))
        else:
            # Update: the journaled object (model/object_id) and the author are
            # immutable — an entry stays attached to its original subject and
            # author. Drop any attempt to change them.
            attrs.pop('model', None)
            attrs.pop('object_id', None)
            attrs.pop('user', None)
        return attrs
