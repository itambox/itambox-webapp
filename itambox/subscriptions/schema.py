import graphene
from graphene_django import DjangoObjectType
from .models import Provider, Subscription, SubscriptionAssignment
from organization.models import Tenant, TenantGroup, Contact, ContactRole, ContactAssignment
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from core.graphql_utils import check_permission, get_object_or_denied, generate_slug, paginate_queryset
from graphql import GraphQLError
from django.core.exceptions import ValidationError, PermissionDenied


def _resolve_owner(owner_id, user, active_tenant):
    """Resolve a User to assign as a subscription owner, enforcing that the
    target user is a member of the active tenant.

    Without this check, ``owner_id`` is resolved against the global (unscoped)
    User table, letting a user in tenant A assign a user from tenant B as the
    owner of A's subscription (cross-tenant assignment / existence oracle).
    """
    if not owner_id:
        return None
    user_model = get_user_model()
    owner = get_object_or_denied(user_model, owner_id, user)
    if active_tenant is not None:
        from organization.models import TenantMembership
        if not TenantMembership.objects.filter(user=owner, tenant=active_tenant).exists():
            raise PermissionDenied("Owner must be a member of the active tenant.")
    return owner


# Node definitions

class ContentTypeNode(DjangoObjectType):
    class Meta:
        model = ContentType
        fields = ("id", "app_label", "model")

class TenantGroupNode(DjangoObjectType):
    class Meta:
        model = TenantGroup
        fields = ("id", "name", "slug")

class ContactRoleNode(DjangoObjectType):
    class Meta:
        model = ContactRole
        fields = ("id", "name", "slug", "description")

class ContactNode(DjangoObjectType):
    class Meta:
        model = Contact
        fields = ("id", "name", "title", "phone", "email", "web_url", "description", "comments")

class ContactAssignmentNode(DjangoObjectType):
    class Meta:
        model = ContactAssignment
        fields = ("id", "contact", "role", "priority")

class ProviderNode(DjangoObjectType):
    contacts = graphene.List(ContactAssignmentNode)

    class Meta:
        model = Provider
        fields = (
            "id", "name", "slug", "account_id", "portal_url", "admin_notes",
            "is_active", "tenant", "tenant_group", "contacts", "created_at", "updated_at"
        )

    def resolve_contacts(self, info):
        return self.contacts.all()

class SubscriptionNode(DjangoObjectType):
    cost_center_id = graphene.ID()
    cost_center_name = graphene.String()

    class Meta:
        model = Subscription
        fields = (
            "id", "name", "slug", "provider", "type", "status",
            "start_date", "renewal_date", "renewal_cost", "currency",
            "billing_cycle", "term_months", "auto_renewal", "licensed_quantity",
            "contract_reference", "cancellation_date", "owner",
            "description", "notes", "tenant", "created_at", "updated_at"
        )

    def resolve_cost_center_id(self, info):
        return self.cost_center_id

    def resolve_cost_center_name(self, info):
        return str(self.cost_center) if self.cost_center_id else None

class SubscriptionAssignmentNode(DjangoObjectType):
    content_type = graphene.Field(ContentTypeNode)

    class Meta:
        model = SubscriptionAssignment
        fields = (
            "id", "subscription", "content_type", "object_id",
            "assigned_date", "assigned_by", "notes", "created_at", "updated_at"
        )

# Sortable fields configuration

PROVIDER_SORTABLE_FIELDS = {
    "name", "-name", "created_at", "-created_at", "updated_at", "-updated_at"
}

SUBSCRIPTION_SORTABLE_FIELDS = {
    "name", "-name", "renewal_date", "-renewal_date", "renewal_cost", "-renewal_cost",
    "created_at", "-created_at", "updated_at", "-updated_at"
}

# Queries

class Query(graphene.ObjectType):
    providers = graphene.List(
        ProviderNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
        is_active=graphene.Boolean(),
    )
    provider = graphene.Field(ProviderNode, id=graphene.ID(required=True))

    subscriptions = graphene.List(
        SubscriptionNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
        status=graphene.String(),
        type=graphene.String(),
    )
    subscription = graphene.Field(SubscriptionNode, id=graphene.ID(required=True))

    subscription_assignments = graphene.List(
        SubscriptionAssignmentNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        subscription_id=graphene.ID(),
    )
    subscription_assignment = graphene.Field(SubscriptionAssignmentNode, id=graphene.ID(required=True))

    def resolve_providers(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'subscriptions.view_provider')
        # TenantScopingManager automatically handles thread-local active tenant/group and global fallback scoping.
        qs = Provider.objects.prefetch_related('contacts__contact', 'contacts__role').all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in PROVIDER_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_provider(self, info, id):
        check_permission(info, 'subscriptions.view_provider')
        try:
            return Provider.objects.prefetch_related('contacts__contact', 'contacts__role').get(pk=id)
        except Provider.DoesNotExist:
            return None

    def resolve_subscriptions(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'subscriptions.view_subscription')
        # TenantScopingSoftDeleteManager handles tenant scoping and active filtering.
        qs = Subscription.objects.select_related('provider', 'tenant', 'owner').all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in SUBSCRIPTION_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_subscription(self, info, id):
        check_permission(info, 'subscriptions.view_subscription')
        try:
            return Subscription.objects.select_related('provider', 'tenant', 'owner').get(pk=id)
        except Subscription.DoesNotExist:
            return None

    def resolve_subscription_assignments(self, info, limit=None, offset=None, **kwargs):
        check_permission(info, 'subscriptions.view_subscriptionassignment')
        active_tenant = getattr(info.context, 'active_tenant', None)
        # SubscriptionAssignment has no direct tenant field, scope via its subscription
        qs = SubscriptionAssignment.objects.select_related(
            'subscription', 'subscription__provider', 'assigned_by', 'content_type'
        ).filter(subscription__tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        return paginate_queryset(qs, limit, offset)

    def resolve_subscription_assignment(self, info, id):
        check_permission(info, 'subscriptions.view_subscriptionassignment')
        active_tenant = getattr(info.context, 'active_tenant', None)
        try:
            return SubscriptionAssignment.objects.select_related(
                'subscription', 'subscription__provider', 'assigned_by', 'content_type'
            ).filter(subscription__tenant=active_tenant).get(pk=id)
        except SubscriptionAssignment.DoesNotExist:
            return None


# Provider Mutations

class CreateProvider(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        slug = graphene.String()
        account_id = graphene.String()
        portal_url = graphene.String()
        admin_notes = graphene.String()
        is_active = graphene.Boolean()
        tenant_id = graphene.ID()
        tenant_group_id = graphene.ID()

    provider = graphene.Field(ProviderNode)

    def mutate(self, info, **kwargs):
        user = check_permission(info, 'subscriptions.add_provider')
        active_tenant = getattr(info.context, 'active_tenant', None)

        provider = Provider()

        if 'tenant_id' in kwargs:
            tenant_id = kwargs.pop('tenant_id')
            if tenant_id:
                provider.tenant = get_object_or_denied(Tenant, tenant_id, user)
            else:
                provider.tenant = None
        else:
            provider.tenant = active_tenant

        if 'tenant_group_id' in kwargs:
            tenant_group_id = kwargs.pop('tenant_group_id')
            if tenant_group_id:
                provider.tenant_group = get_object_or_denied(TenantGroup, tenant_group_id, user)
                # If they set tenant_group, tenant must be null (based on constraint)
                provider.tenant = None
            else:
                provider.tenant_group = None
        else:
            active_tenant_group = getattr(info.context, 'active_tenant_group', None)
            if not provider.tenant and active_tenant_group:
                provider.tenant_group = active_tenant_group

        # Global object restriction for non-superusers
        if provider.tenant is None and provider.tenant_group is None and not user.is_superuser:
            raise PermissionDenied("Only superusers can create global providers.")

        ALLOWED_FIELDS = {
            'name', 'slug', 'account_id', 'portal_url', 'admin_notes', 'is_active'
        }
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(provider, key, val)

        generate_slug(provider)

        try:
            provider.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        provider.save()
        return CreateProvider(provider=provider)


class UpdateProvider(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        slug = graphene.String()
        account_id = graphene.String()
        portal_url = graphene.String()
        admin_notes = graphene.String()
        is_active = graphene.Boolean()
        tenant_id = graphene.ID()
        tenant_group_id = graphene.ID()

    provider = graphene.Field(ProviderNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'subscriptions.change_provider')
        active_tenant = getattr(info.context, 'active_tenant', None)
        
        provider = get_object_or_denied(Provider, id, user, tenant=active_tenant)
        check_permission(info, 'subscriptions.change_provider', obj=provider)

        # Global object restriction for non-superusers
        if provider.tenant is None and provider.tenant_group is None and not user.is_superuser:
            raise PermissionDenied("Only superusers can modify global providers.")

        if 'tenant_id' in kwargs:
            tenant_id = kwargs.pop('tenant_id')
            if tenant_id:
                provider.tenant = get_object_or_denied(Tenant, tenant_id, user)
                provider.tenant_group = None
            else:
                provider.tenant = None

        if 'tenant_group_id' in kwargs:
            tenant_group_id = kwargs.pop('tenant_group_id')
            if tenant_group_id:
                provider.tenant_group = get_object_or_denied(TenantGroup, tenant_group_id, user)
                provider.tenant = None
            else:
                provider.tenant_group = None

        # Double check post-update status
        if provider.tenant is None and provider.tenant_group is None and not user.is_superuser:
            raise PermissionDenied("Only superusers can make providers global.")

        ALLOWED_FIELDS = {
            'name', 'slug', 'account_id', 'portal_url', 'admin_notes', 'is_active'
        }
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(provider, key, val)

        try:
            provider.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        provider.save()
        return UpdateProvider(provider=provider)


class DeleteProvider(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'subscriptions.delete_provider')
        active_tenant = getattr(info.context, 'active_tenant', None)

        provider = get_object_or_denied(Provider, id, user, tenant=active_tenant)
        check_permission(info, 'subscriptions.delete_provider', obj=provider)

        # Global object restriction for non-superusers
        if provider.tenant is None and provider.tenant_group is None and not user.is_superuser:
            raise PermissionDenied("Only superusers can delete global providers.")

        provider.delete()
        return DeleteProvider(success=True)


# Subscription Mutations

def _resolve_cost_center(cost_center_id, user):
    """Resolve a CostCenter by PK. Returns None if cost_center_id is falsy.
    Uses apps.get_model to avoid a hard import while the model is being
    created concurrently by another agent."""
    if not cost_center_id:
        return None
    try:
        from django.apps import apps
        CostCenter = apps.get_model('organization', 'CostCenter')
    except LookupError:
        raise GraphQLError("CostCenter model is not yet available.")
    return get_object_or_denied(CostCenter, cost_center_id, user)


class CreateSubscription(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        slug = graphene.String()
        provider_id = graphene.ID(required=True)
        type = graphene.String()
        status = graphene.String()
        start_date = graphene.Date()
        renewal_date = graphene.Date()
        renewal_cost = graphene.Float()
        currency = graphene.String()
        billing_cycle = graphene.String()
        term_months = graphene.Int()
        auto_renewal = graphene.Boolean()
        licensed_quantity = graphene.Int()
        contract_reference = graphene.String()
        cost_center_id = graphene.ID()
        cancellation_date = graphene.Date()
        owner_id = graphene.ID()
        description = graphene.String()
        notes = graphene.String()

    subscription = graphene.Field(SubscriptionNode)

    def mutate(self, info, provider_id, **kwargs):
        user = check_permission(info, 'subscriptions.add_subscription')
        active_tenant = getattr(info.context, 'active_tenant', None)

        provider = get_object_or_denied(Provider, provider_id, user, tenant=active_tenant)
        subscription = Subscription(provider=provider, tenant=active_tenant)

        if 'owner_id' in kwargs:
            subscription.owner = _resolve_owner(kwargs.pop('owner_id'), user, active_tenant)

        if 'cost_center_id' in kwargs:
            subscription.cost_center = _resolve_cost_center(kwargs.pop('cost_center_id'), user)

        ALLOWED_FIELDS = {
            'name', 'slug', 'type', 'status', 'start_date', 'renewal_date', 'renewal_cost',
            'currency', 'billing_cycle', 'term_months', 'auto_renewal', 'licensed_quantity',
            'contract_reference', 'cancellation_date', 'description', 'notes'
        }
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(subscription, key, val)

        generate_slug(subscription)

        try:
            subscription.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        subscription.save()
        return CreateSubscription(subscription=subscription)


class UpdateSubscription(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        slug = graphene.String()
        provider_id = graphene.ID()
        type = graphene.String()
        status = graphene.String()
        start_date = graphene.Date()
        renewal_date = graphene.Date()
        renewal_cost = graphene.Float()
        currency = graphene.String()
        billing_cycle = graphene.String()
        term_months = graphene.Int()
        auto_renewal = graphene.Boolean()
        licensed_quantity = graphene.Int()
        contract_reference = graphene.String()
        cost_center_id = graphene.ID()
        cancellation_date = graphene.Date()
        owner_id = graphene.ID()
        description = graphene.String()
        notes = graphene.String()

    subscription = graphene.Field(SubscriptionNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'subscriptions.change_subscription')
        active_tenant = getattr(info.context, 'active_tenant', None)

        subscription = get_object_or_denied(Subscription, id, user, tenant=active_tenant)
        check_permission(info, 'subscriptions.change_subscription', obj=subscription)

        if 'provider_id' in kwargs:
            subscription.provider = get_object_or_denied(Provider, kwargs.pop('provider_id'), user, tenant=active_tenant)

        if 'owner_id' in kwargs:
            subscription.owner = _resolve_owner(kwargs.pop('owner_id'), user, active_tenant)

        if 'cost_center_id' in kwargs:
            subscription.cost_center = _resolve_cost_center(kwargs.pop('cost_center_id'), user)

        ALLOWED_FIELDS = {
            'name', 'slug', 'type', 'status', 'start_date', 'renewal_date', 'renewal_cost',
            'currency', 'billing_cycle', 'term_months', 'auto_renewal', 'licensed_quantity',
            'contract_reference', 'cancellation_date', 'description', 'notes'
        }
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(subscription, key, val)

        try:
            subscription.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        subscription.save()
        return UpdateSubscription(subscription=subscription)


class DeleteSubscription(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'subscriptions.delete_subscription')
        active_tenant = getattr(info.context, 'active_tenant', None)

        subscription = get_object_or_denied(Subscription, id, user, tenant=active_tenant)
        check_permission(info, 'subscriptions.delete_subscription', obj=subscription)

        subscription.delete()
        return DeleteSubscription(success=True)


# Subscription Assignment Mutations

class CreateSubscriptionAssignment(graphene.Mutation):
    class Arguments:
        subscription_id = graphene.ID(required=True)
        content_type_id = graphene.ID(required=True)
        object_id = graphene.ID(required=True)
        notes = graphene.String()

    subscription_assignment = graphene.Field(SubscriptionAssignmentNode)

    def mutate(self, info, subscription_id, content_type_id, object_id, **kwargs):
        user = check_permission(info, 'subscriptions.add_subscriptionassignment')
        active_tenant = getattr(info.context, 'active_tenant', None)

        subscription = get_object_or_denied(Subscription, subscription_id, user, tenant=active_tenant)
        content_type = ContentType.objects.get(pk=content_type_id)
        
        # Verify the target object exists and is scoped to the tenant
        model_class = content_type.model_class()
        if not model_class:
            raise ValidationError("Invalid content type.")
        get_object_or_denied(model_class, object_id, user, tenant=active_tenant)

        assignment = SubscriptionAssignment(
            subscription=subscription,
            content_type=content_type,
            object_id=object_id,
            assigned_by=user
        )

        if 'notes' in kwargs:
            assignment.notes = kwargs['notes']

        try:
            assignment.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        assignment.save()
        return CreateSubscriptionAssignment(subscription_assignment=assignment)


class UpdateSubscriptionAssignment(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        notes = graphene.String()

    subscription_assignment = graphene.Field(SubscriptionAssignmentNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'subscriptions.change_subscriptionassignment')
        active_tenant = getattr(info.context, 'active_tenant', None)

        try:
            assignment = SubscriptionAssignment.objects.select_related('subscription').filter(
                subscription__tenant=active_tenant
            ).get(pk=id)
        except SubscriptionAssignment.DoesNotExist:
            raise PermissionDenied("Permission denied.")

        check_permission(info, 'subscriptions.change_subscriptionassignment', obj=assignment)

        if 'notes' in kwargs:
            assignment.notes = kwargs['notes']

        try:
            assignment.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        assignment.save()
        return UpdateSubscriptionAssignment(subscription_assignment=assignment)


class DeleteSubscriptionAssignment(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'subscriptions.delete_subscriptionassignment')
        active_tenant = getattr(info.context, 'active_tenant', None)

        try:
            assignment = SubscriptionAssignment.objects.select_related('subscription').filter(
                subscription__tenant=active_tenant
            ).get(pk=id)
        except SubscriptionAssignment.DoesNotExist:
            raise PermissionDenied("Permission denied.")

        check_permission(info, 'subscriptions.delete_subscriptionassignment', obj=assignment)

        assignment.delete()
        return DeleteSubscriptionAssignment(success=True)


class Mutation(graphene.ObjectType):
    create_provider = CreateProvider.Field()
    update_provider = UpdateProvider.Field()
    delete_provider = DeleteProvider.Field()

    create_subscription = CreateSubscription.Field()
    update_subscription = UpdateSubscription.Field()
    delete_subscription = DeleteSubscription.Field()

    create_subscription_assignment = CreateSubscriptionAssignment.Field()
    update_subscription_assignment = UpdateSubscriptionAssignment.Field()
    delete_subscription_assignment = DeleteSubscriptionAssignment.Field()
