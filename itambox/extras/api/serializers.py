from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers
from itambox.api.base import BaseModelSerializer
from itambox.api.fields import ContentTypeField, validate_gfk_target_tenant
from extras.models import (
    Tag, Dashboard, CustomField, CustomFieldset,
    EventRule, WebhookEndpoint, NotificationChannel, AlertRule, JournalEntry,
)


# Keys in NotificationChannel.config that hold credentials (Slack/Teams incoming-
# webhook URLs, bearer tokens, etc.). These are redacted on READ so an API reader
# cannot exfiltrate them, and preserved on WRITE so a read-modify-write round-trip
# does not persist the mask. Mirrors WebhookEndpoint.secret being write-only.
_SECRET_CONFIG_HINTS = ('webhook_url', 'secret', 'password', 'token', 'api_key', 'apikey', 'auth')
_REDACTED_PLACEHOLDER = '•' * 8  # eight bullets


def _is_secret_config_key(key):
    k = str(key).lower()
    return any(hint in k for hint in _SECRET_CONFIG_HINTS)


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

    def validate_target_url(self, value):
        # SSRF guard at the API write boundary (BaseModelSerializer does not run full_clean,
        # so WebhookEndpoint.clean() would not fire otherwise). Reject internal targets at
        # create/update instead of only at dispatch time.
        from django.core.exceptions import ValidationError as DjangoValidationError
        from core.validators import validate_external_url
        try:
            validate_external_url(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value


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
        # `config` is a JSONField that can carry a credential (Slack/Teams
        # webhook_url). Secret-ish keys are redacted on read (to_representation) and
        # preserved on write (validate_config) so the URL is never exposed via the API.
        fields = [
            'id', 'url', 'name', 'channel_type', 'channel_type_display',
            'enabled', 'config', 'created_at', 'updated_at',
        ]
        brief_fields = ['id', 'url', 'name', 'channel_type', 'enabled']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        cfg = data.get('config')
        if isinstance(cfg, dict):
            data['config'] = {
                k: (_REDACTED_PLACEHOLDER if (_is_secret_config_key(k) and v not in (None, '')) else v)
                for k, v in cfg.items()
            }
        return data

    def validate_config(self, value):
        # Restore redacted secrets from the stored config so a read-modify-write
        # round-trip (which echoes back the placeholder) does not overwrite the real
        # value; drop the placeholder entirely when there is nothing to restore.
        if isinstance(value, dict):
            existing = (self.instance.config or {}) if self.instance else {}
            cleaned = {}
            for k, v in value.items():
                if _is_secret_config_key(k) and v == _REDACTED_PLACEHOLDER:
                    if k in existing:
                        cleaned[k] = existing[k]
                    # else: nothing to restore -> drop the placeholder
                else:
                    cleaned[k] = v
            # SSRF guard: a Slack/Teams channel's webhook_url is an outbound target — reject
            # internal URLs at write time (a newly-supplied value, not a restored placeholder).
            url_val = cleaned.get('webhook_url')
            if url_val and url_val != _REDACTED_PLACEHOLDER:
                from django.core.exceptions import ValidationError as DjangoValidationError
                from core.validators import validate_external_url
                try:
                    validate_external_url(url_val)
                except DjangoValidationError as exc:
                    raise serializers.ValidationError({'webhook_url': exc.messages})
            return cleaned
        return value


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
