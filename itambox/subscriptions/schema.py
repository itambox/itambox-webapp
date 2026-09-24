import graphene
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.translation import gettext_lazy as _
from graphene_django import DjangoObjectType
from graphql import GraphQLError

from assets.models import Supplier
from core.graphql_utils import check_permission, generate_slug, get_object_or_denied, paginate_queryset
from procurement.models import Contract

from .models import Subscription, SubscriptionAssignment, SubscriptionStatusChoices


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
        from organization.models import Membership

        if not Membership.objects.filter(user=owner, tenant=active_tenant).exists():
            raise PermissionDenied(_("Owner must be a member of the active tenant."))
    return owner


# Node definitions


class ContentTypeNode(DjangoObjectType):
    class Meta:
        model = ContentType
        fields = ("id", "app_label", "model")


class SubscriptionNode(DjangoObjectType):
    cost_center_id = graphene.ID()
    cost_center_name = graphene.String()
    auto_renewal = graphene.Boolean(deprecation_reason="Use vendorContractAutoRenews; autoRenewal is removed in 2.0.")

    class Meta:
        model = Subscription
        fields = (
            "id",
            "name",
            "slug",
            "supplier",
            "type",
            "status",
            "start_date",
            "renewal_date",
            "renewal_cost",
            "currency",
            "billing_cycle",
            "term_months",
            "vendor_contract_auto_renews",
            "licensed_quantity",
            "contract_reference",
            "linked_contract",
            "cancellation_date",
            "owner",
            "description",
            "notes",
            "tenant",
            "created_at",
            "updated_at",
        )

    def resolve_cost_center_id(self, info):
        return self.cost_center_id

    def resolve_cost_center_name(self, info):
        return str(self.cost_center) if self.cost_center_id else None

    def resolve_auto_renewal(self, info):
        return self.vendor_contract_auto_renews


class SubscriptionAssignmentNode(DjangoObjectType):
    content_type = graphene.Field(ContentTypeNode)

    class Meta:
        model = SubscriptionAssignment
        fields = (
            "id",
            "subscription",
            "content_type",
            "object_id",
            "assigned_date",
            "assigned_by",
            "notes",
            "created_at",
            "updated_at",
        )


# Sortable fields configuration

SUBSCRIPTION_SORTABLE_FIELDS = {
    "name",
    "-name",
    "renewal_date",
    "-renewal_date",
    "renewal_cost",
    "-renewal_cost",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
}

# Queries


class Query(graphene.ObjectType):
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

    def resolve_subscriptions(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, "subscriptions.view_subscription")
        # TenantScopingSoftDeleteManager handles tenant scoping and active filtering.
        qs = Subscription.objects.select_related("supplier", "linked_contract", "tenant", "owner").all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in SUBSCRIPTION_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_subscription(self, info, id):
        check_permission(info, "subscriptions.view_subscription")
        try:
            return Subscription.objects.select_related("supplier", "linked_contract", "tenant", "owner").get(pk=id)
        except Subscription.DoesNotExist:
            return None

    def resolve_subscription_assignments(self, info, limit=None, offset=None, **kwargs):
        check_permission(info, "subscriptions.view_subscriptionassignment")
        active_tenant = getattr(info.context, "active_tenant", None)
        # SubscriptionAssignment has no direct tenant field, scope via its subscription
        qs = SubscriptionAssignment.objects.select_related(
            "subscription", "subscription__supplier", "assigned_by", "content_type"
        ).filter(subscription__tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        return paginate_queryset(qs, limit, offset)

    def resolve_subscription_assignment(self, info, id):
        check_permission(info, "subscriptions.view_subscriptionassignment")
        active_tenant = getattr(info.context, "active_tenant", None)
        try:
            return (
                SubscriptionAssignment.objects.select_related(
                    "subscription", "subscription__supplier", "assigned_by", "content_type"
                )
                .filter(subscription__tenant=active_tenant)
                .get(pk=id)
            )
        except SubscriptionAssignment.DoesNotExist:
            return None


# Subscription Mutations


def _resolve_cost_center(cost_center_id, user):
    """Resolve a CostCenter by PK. Returns None if cost_center_id is falsy.
    Uses apps.get_model to avoid a hard import while the model is being
    created concurrently by another agent."""
    if not cost_center_id:
        return None
    try:
        from django.apps import apps

        CostCenter = apps.get_model("organization", "CostCenter")
    except LookupError as exc:
        raise GraphQLError(str(_("CostCenter model is not yet available."))) from exc
    return get_object_or_denied(CostCenter, cost_center_id, user)


def _apply_subscription_renewal_terms(subscription, kwargs):
    legacy_value = kwargs.pop("auto_renewal", None)
    canonical_value = kwargs.pop("vendor_contract_auto_renews", None)
    if legacy_value is not None and canonical_value is not None and legacy_value != canonical_value:
        raise GraphQLError("autoRenewal conflicts with vendorContractAutoRenews.")
    value = canonical_value if canonical_value is not None else legacy_value
    if value is not None:
        subscription.vendor_contract_auto_renews = value


def _validate_subscription_status_input(kwargs, current_status=SubscriptionStatusChoices.ACTIVE):
    submitted_status = kwargs.pop("status", None)
    if submitted_status is not None and submitted_status != current_status:
        raise GraphQLError("Use the explicit suspend, resume, renew, or cancel subscription mutation.")


class CreateSubscription(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        slug = graphene.String()
        supplier_id = graphene.ID(required=True)
        type = graphene.String()
        status = graphene.String(deprecation_reason="Use explicit lifecycle mutations")
        start_date = graphene.Date()
        renewal_date = graphene.Date()
        renewal_cost = graphene.Float()
        currency = graphene.String()
        billing_cycle = graphene.String()
        term_months = graphene.Int()
        auto_renewal = graphene.Boolean()
        vendor_contract_auto_renews = graphene.Boolean()
        licensed_quantity = graphene.Int()
        contract_reference = graphene.String()
        linked_contract_id = graphene.ID()
        cost_center_id = graphene.ID()
        owner_id = graphene.ID()
        description = graphene.String()
        notes = graphene.String()

    subscription = graphene.Field(SubscriptionNode)

    def mutate(self, info, supplier_id, **kwargs):
        user = check_permission(info, "subscriptions.add_subscription")
        active_tenant = getattr(info.context, "active_tenant", None)

        supplier = get_object_or_denied(Supplier, supplier_id, user, tenant=active_tenant)
        subscription = Subscription(supplier=supplier, tenant=active_tenant)

        # tenant=active_tenant is None in a tenant-group / no-tenant token context, which would
        # mint a global subscription visible to every tenant — reserve that for superusers.
        if subscription.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can create global subscriptions."))

        if "owner_id" in kwargs:
            subscription.owner = _resolve_owner(kwargs.pop("owner_id"), user, active_tenant)

        if "cost_center_id" in kwargs:
            subscription.cost_center = _resolve_cost_center(kwargs.pop("cost_center_id"), user)
        if "linked_contract_id" in kwargs:
            subscription.linked_contract = get_object_or_denied(
                Contract, kwargs.pop("linked_contract_id"), user, tenant=active_tenant
            )

        _validate_subscription_status_input(kwargs, subscription.status)
        _apply_subscription_renewal_terms(subscription, kwargs)

        ALLOWED_FIELDS = {
            "name",
            "slug",
            "type",
            "start_date",
            "renewal_date",
            "renewal_cost",
            "currency",
            "billing_cycle",
            "term_months",
            "licensed_quantity",
            "contract_reference",
            "description",
            "notes",
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
                extensions={"validation_errors": e.message_dict if hasattr(e, "message_dict") else e.messages},
            ) from e
        subscription.save()
        return CreateSubscription(subscription=subscription)


class UpdateSubscription(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        slug = graphene.String()
        supplier_id = graphene.ID()
        type = graphene.String()
        status = graphene.String(deprecation_reason="Use explicit lifecycle mutations")
        start_date = graphene.Date()
        renewal_date = graphene.Date()
        renewal_cost = graphene.Float()
        currency = graphene.String()
        billing_cycle = graphene.String()
        term_months = graphene.Int()
        auto_renewal = graphene.Boolean()
        vendor_contract_auto_renews = graphene.Boolean()
        licensed_quantity = graphene.Int()
        contract_reference = graphene.String()
        linked_contract_id = graphene.ID()
        cost_center_id = graphene.ID()
        owner_id = graphene.ID()
        description = graphene.String()
        notes = graphene.String()

    subscription = graphene.Field(SubscriptionNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, "subscriptions.change_subscription")
        active_tenant = getattr(info.context, "active_tenant", None)

        subscription = get_object_or_denied(Subscription, id, user, tenant=active_tenant)
        check_permission(info, "subscriptions.change_subscription", obj=subscription)

        if "supplier_id" in kwargs:
            subscription.supplier = get_object_or_denied(
                Supplier, kwargs.pop("supplier_id"), user, tenant=active_tenant
            )

        if "owner_id" in kwargs:
            subscription.owner = _resolve_owner(kwargs.pop("owner_id"), user, active_tenant)

        if "cost_center_id" in kwargs:
            subscription.cost_center = _resolve_cost_center(kwargs.pop("cost_center_id"), user)
        if "linked_contract_id" in kwargs:
            linked_contract_id = kwargs.pop("linked_contract_id")
            subscription.linked_contract = (
                get_object_or_denied(Contract, linked_contract_id, user, tenant=active_tenant)
                if linked_contract_id
                else None
            )

        _validate_subscription_status_input(kwargs, subscription.status)
        _apply_subscription_renewal_terms(subscription, kwargs)

        ALLOWED_FIELDS = {
            "name",
            "slug",
            "type",
            "start_date",
            "renewal_date",
            "renewal_cost",
            "currency",
            "billing_cycle",
            "term_months",
            "licensed_quantity",
            "contract_reference",
            "description",
            "notes",
        }
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(subscription, key, val)

        try:
            subscription.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, "message_dict") else e.messages},
            ) from e
        subscription.save()
        return UpdateSubscription(subscription=subscription)


def _run_subscription_lifecycle(info, subscription_id, method_name, *args, **kwargs):
    user = check_permission(info, "subscriptions.change_subscription")
    active_tenant = getattr(info.context, "active_tenant", None)
    subscription = get_object_or_denied(Subscription, subscription_id, user, tenant=active_tenant)
    check_permission(info, "subscriptions.change_subscription", obj=subscription)
    subscription.snapshot()
    try:
        getattr(subscription, method_name)(*args, **kwargs)
    except ValidationError as exc:
        raise GraphQLError("Invalid subscription lifecycle transition", extensions={"errors": exc.messages}) from exc
    return subscription


class SuspendSubscription(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    subscription = graphene.Field(SubscriptionNode)

    def mutate(self, info, id):
        return SuspendSubscription(subscription=_run_subscription_lifecycle(info, id, "suspend"))


class ResumeSubscription(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    subscription = graphene.Field(SubscriptionNode)

    def mutate(self, info, id):
        return ResumeSubscription(subscription=_run_subscription_lifecycle(info, id, "resume"))


class RenewSubscription(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        renewal_date = graphene.Date(required=True)
        renewal_cost = graphene.Float()

    subscription = graphene.Field(SubscriptionNode)

    def mutate(self, info, id, renewal_date, renewal_cost=None):
        return RenewSubscription(
            subscription=_run_subscription_lifecycle(info, id, "renew", renewal_date, cost=renewal_cost)
        )


class CancelSubscription(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        cancellation_date = graphene.Date()
        reason = graphene.String()

    subscription = graphene.Field(SubscriptionNode)

    def mutate(self, info, id, cancellation_date=None, reason=""):
        return CancelSubscription(
            subscription=_run_subscription_lifecycle(
                info,
                id,
                "cancel",
                cancellation_date=cancellation_date,
                reason=reason,
            )
        )


class DeleteSubscription(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, "subscriptions.delete_subscription")
        active_tenant = getattr(info.context, "active_tenant", None)

        subscription = get_object_or_denied(Subscription, id, user, tenant=active_tenant)
        check_permission(info, "subscriptions.delete_subscription", obj=subscription)

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
        user = check_permission(info, "subscriptions.add_subscriptionassignment")
        active_tenant = getattr(info.context, "active_tenant", None)

        subscription = get_object_or_denied(Subscription, subscription_id, user, tenant=active_tenant)
        content_type = ContentType.objects.get(pk=content_type_id)

        # Verify the target object exists and is scoped to the tenant
        model_class = content_type.model_class()
        if not model_class:
            raise ValidationError(_("Invalid content type."))
        get_object_or_denied(model_class, object_id, user, tenant=active_tenant)

        assignment = SubscriptionAssignment(
            subscription=subscription, content_type=content_type, object_id=object_id, assigned_by=user
        )

        if "notes" in kwargs:
            assignment.notes = kwargs["notes"]

        try:
            assignment.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, "message_dict") else e.messages},
            ) from e
        assignment.save()
        return CreateSubscriptionAssignment(subscription_assignment=assignment)


class UpdateSubscriptionAssignment(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        notes = graphene.String()

    subscription_assignment = graphene.Field(SubscriptionAssignmentNode)

    def mutate(self, info, id, **kwargs):
        check_permission(info, "subscriptions.change_subscriptionassignment")
        active_tenant = getattr(info.context, "active_tenant", None)

        try:
            assignment = (
                SubscriptionAssignment.objects.select_related("subscription")
                .filter(subscription__tenant=active_tenant)
                .get(pk=id)
            )
        except SubscriptionAssignment.DoesNotExist:
            raise PermissionDenied(_("Permission denied.")) from None

        check_permission(info, "subscriptions.change_subscriptionassignment", obj=assignment)

        if "notes" in kwargs:
            assignment.notes = kwargs["notes"]

        try:
            assignment.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, "message_dict") else e.messages},
            ) from e
        assignment.save()
        return UpdateSubscriptionAssignment(subscription_assignment=assignment)


class DeleteSubscriptionAssignment(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        check_permission(info, "subscriptions.delete_subscriptionassignment")
        active_tenant = getattr(info.context, "active_tenant", None)

        try:
            assignment = (
                SubscriptionAssignment.objects.select_related("subscription")
                .filter(subscription__tenant=active_tenant)
                .get(pk=id)
            )
        except SubscriptionAssignment.DoesNotExist:
            raise PermissionDenied(_("Permission denied.")) from None

        check_permission(info, "subscriptions.delete_subscriptionassignment", obj=assignment)

        assignment.delete()
        return DeleteSubscriptionAssignment(success=True)


class Mutation(graphene.ObjectType):
    create_subscription = CreateSubscription.Field()
    update_subscription = UpdateSubscription.Field()
    suspend_subscription = SuspendSubscription.Field()
    resume_subscription = ResumeSubscription.Field()
    renew_subscription = RenewSubscription.Field()
    cancel_subscription = CancelSubscription.Field()
    delete_subscription = DeleteSubscription.Field()

    create_subscription_assignment = CreateSubscriptionAssignment.Field()
    update_subscription_assignment = UpdateSubscriptionAssignment.Field()
    delete_subscription_assignment = DeleteSubscriptionAssignment.Field()
