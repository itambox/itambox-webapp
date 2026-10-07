import graphene
from django.contrib.contenttypes.models import ContentType
from graphene_django import DjangoObjectType

from core.graphql_utils import check_permission, paginate_queryset

from .models import Subscription, SubscriptionAssignment

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
