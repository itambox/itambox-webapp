import logging
import re
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from core.managers import set_current_tenant
from itambox.middleware import set_current_user
from organization.models import Tenant, Membership, AssetHolder
from users.models import GroupMembership, UserGroup
from users.api.scim.serializers import (
    SCIMUserSerializer, SCIMGroupSerializer, SCIMServiceProviderConfigSerializer
)
from users.api.scim.filters import parse_scim_filter, SCIMFilterError
from users.api.scim.authentication import SCIMBearerTokenAuthentication

logger = logging.getLogger('itambox.scim.views')
User = get_user_model()

# Sentinel for "attribute not supplied by this SCIM request" (distinct from an explicit
# empty/false value) so partial PATCH/PUT updates only touch the fields actually sent.
_UNSET = object()


def link_or_create_assetholder(user, tenant):
    email = user.email
    upn = email or user.username
    first_name = user.first_name or user.username
    last_name = user.last_name or ""
    
    # Check if user already has an AssetHolder profile in this tenant
    holder = AssetHolder.objects.filter(user=user, tenant=tenant).first()
    if not holder:
        # Try to find existing AssetHolder by UPN or email but with no linked user
        holder = AssetHolder.objects.filter(upn=upn, tenant=tenant, user__isnull=True).first()
        if not holder and email:
            holder = AssetHolder.objects.filter(email=email, tenant=tenant, user__isnull=True).first()
            
    if holder:
        try:
            if not holder.user:
                holder.user = user
            holder.first_name = first_name
            holder.last_name = last_name
            if email:
                holder.email = email
            if not holder.tenant:
                holder.tenant = tenant
            holder.save()
        except Exception as e:
            logger.warning(f"Error linking/updating AssetHolder for user {user.username}: {e}")
    else:
        try:
            holder = AssetHolder.objects.create(
                user=user,
                first_name=first_name,
                last_name=last_name,
                upn=upn,
                email=email,
                tenant=tenant
            )
        except Exception as e:
            logger.warning(f"Constraint or validation error creating AssetHolder for user {user.username}: {e}")


def sync_group_members(tenant, group, member_ids):
    # 1. Add to UserGroup.members only users who ALREADY have a Membership in this
    #    tenant. A SCIM token is scoped to a single tenant, so group sync must never write
    #    cross-tenant membership or expose whether a global user id exists — that would be
    #    a cross-tenant access-control write and a username-enumeration oracle. New members
    #    are provisioned exclusively through SCIM /Users; unknown or non-member ids are skipped.
    memberships_by_user_id = {}
    for uid in member_ids:
        membership = Membership.objects.filter(user_id=uid, tenant=tenant).first()
        if membership:
            memberships_by_user_id[uid] = membership
            link_or_create_assetholder(membership.user, tenant)
        else:
            logger.warning(
                "SCIM group sync skipped user id %s: not a member of tenant %s "
                "(provision via SCIM /Users first).", uid, tenant.slug
            )

    # 2. Reconcile group.members to match valid_member_ids (within this tenant only).
    #    Use add/remove rather than set() so only the delta is applied; this keeps
    #    ChangeLoggingMixin from firing unnecessarily on unchanged members.
    valid_member_ids = set(memberships_by_user_id)
    current_members = set(group.members.filter(memberships__tenant=tenant).values_list('id', flat=True))
    to_add = valid_member_ids - current_members
    to_remove = current_members - valid_member_ids
    if to_add:
        group.members.add(*to_add)
    for user_id, membership in memberships_by_user_id.items():
        GroupMembership.objects.update_or_create(
            user_group=group,
            membership=membership,
            defaults={
                'source': GroupMembership.SOURCE_SCIM,
                'external_id': str(user_id),
            },
        )
    if to_remove:
        group.members.remove(*to_remove)


class SCIMTenantMixin:
    authentication_classes = [SCIMBearerTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.core.exceptions import FieldError as DjangoFieldError

        if isinstance(exc, DjangoValidationError):
            exc = exceptions.ValidationError(detail=exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)
        elif isinstance(exc, DjangoFieldError):
            exc = exceptions.ValidationError(detail=str(exc))

        response = super().handle_exception(exc)
        if response is not None:
            detail = response.data.get('detail') if isinstance(response.data, dict) else str(response.data)
            response.data = {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": str(response.status_code),
                "detail": detail
            }
        return response

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenant_slug = self.kwargs.get('tenant_slug')
        if not tenant_slug:
            raise exceptions.ValidationError("tenant_slug is required")
        
        try:
            self.tenant = Tenant._base_manager.get(slug=tenant_slug)
        except Tenant.DoesNotExist:
            raise exceptions.NotFound("Tenant not found.")

        set_current_tenant(self.tenant)
        # DRF authenticated the bearer token in super().initial(); bind the token's
        # owner as the current user so SCIM-driven changelog rows are attributed to
        # the acting service account rather than 'System' (CurrentUserMiddleware
        # captured AnonymousUser before DRF auth ran).
        if getattr(request, 'user', None) and request.user.is_authenticated:
            set_current_user(request.user)


class ServiceProviderConfigView(SCIMTenantMixin, APIView):
    def get(self, request, *args, **kwargs):
        config_data = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {
                "supported": True
            },
            "bulk": {
                "supported": False,
                "maxOperations": 1000,
                "maxPayloadSize": 1048576
            },
            "filter": {
                "supported": True,
                "maxResults": 200
            },
            "changePassword": {
                "supported": False
            },
            "sort": {
                "supported": False
            },
            "etag": {
                "supported": False
            },
            "authenticationSchemes": [
                {
                    "name": "OAuth Bearer Token",
                    "description": "External identity provisioning via Bearer Token",
                    "specUri": "http://tools.ietf.org/html/rfc6750",
                    "type": "oauthbearertoken",
                    "primary": True
                },
                {
                    "name": "HTTP Basic",
                    "description": "Standard basic authentication",
                    "specUri": "http://tools.ietf.org/html/rfc2617",
                    "type": "httpbasic"
                }
            ]
        }
        serializer = SCIMServiceProviderConfigSerializer(data=config_data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SCIMUserListView(SCIMTenantMixin, APIView):
    def get(self, request, *args, **kwargs):
        filter_str = request.query_params.get('filter')
        try:
            q_obj = parse_scim_filter(filter_str, 'user')
        except SCIMFilterError as e:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        
        queryset = User.objects.filter(memberships__tenant=self.tenant).filter(q_obj).distinct().prefetch_related('groups')
        
        try:
            start_index = int(request.query_params.get('startIndex', 1))
        except ValueError:
            start_index = 1
        try:
            count = int(request.query_params.get('count', 50))
        except ValueError:
            count = 50
        count = min(count, 200)  # Enforce maxResults upper bound
            
        if start_index < 1:
            start_index = 1
            
        total_results = queryset.count()
        sliced_queryset = queryset[start_index - 1 : start_index - 1 + count]
        
        serializer = SCIMUserSerializer(
            sliced_queryset, 
            many=True, 
            context={'request': request, 'tenant_slug': self.tenant.slug, 'tenant': self.tenant}
        )
        
        return Response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total_results,
            "itemsPerPage": len(serializer.data),
            "startIndex": start_index,
            "Resources": serializer.data
        }, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        username = request.data.get('userName')
        if not username:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "userName is required"
            }, status=status.HTTP_400_BAD_REQUEST)

        if username and len(username) > 150:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "userName exceeds maximum length of 150 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        email = ""
        emails = request.data.get('emails', [])
        if emails and isinstance(emails, list):
            email = emails[0].get('value', '')
            
        if email and len(email) > 254:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "email exceeds maximum length of 254 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        first_name = ""
        last_name = ""
        name_data = request.data.get('name')
        if name_data and isinstance(name_data, dict):
            first_name = name_data.get('givenName', '')
            last_name = name_data.get('familyName', '')

        if first_name and len(first_name) > 150:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "givenName exceeds maximum length of 150 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        if last_name and len(last_name) > 150:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "familyName exceeds maximum length of 150 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        active = request.data.get('active', True)
        if isinstance(active, str):
            active = (active.lower() == 'true')
        else:
            active = bool(active)

        user = User.objects.filter(username=username).first()
        if user:
            membership = Membership.objects.filter(user=user, tenant=self.tenant).first()
            if membership:
                return Response({
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "status": "409",
                    "detail": "User already exists in this tenant"
                }, status=status.HTTP_409_CONFLICT)
            
            with transaction.atomic():
                # SCIM provisions identity only: a bare membership with NO RoleAssignment
                # rows — permissions are granted in-app (or via UserGroup). Were a
                # provisioning config ever to map roles, it would create own-reach
                # assignments with granted_by=None — SCIM is trusted operator
                # configuration, deliberately unguarded.
                Membership.objects.create(user=user, tenant=self.tenant, is_active=active)
                link_or_create_assetholder(user, self.tenant)
        else:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    is_active=active
                )
                user.set_unusable_password()
                user.save()

                # See comment above: bare membership, assignments granted in-app.
                Membership.objects.create(user=user, tenant=self.tenant, is_active=active)
                link_or_create_assetholder(user, self.tenant)

        serializer = SCIMUserSerializer(
            user, 
            context={'request': request, 'tenant_slug': self.tenant.slug, 'tenant': self.tenant}
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SCIMUserDetailView(SCIMTenantMixin, APIView):
    def get(self, request, pk, *args, **kwargs):
        user = get_object_or_404(User.objects.filter(memberships__tenant=self.tenant).distinct(), id=pk)
        serializer = SCIMUserSerializer(
            user,
            context={'request': request, 'tenant_slug': self.tenant.slug, 'tenant': self.tenant}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def _apply_scim_identity(self, user, *, username=_UNSET, email=_UNSET,
                             first_name=_UNSET, last_name=_UNSET, active=_UNSET):
        """Apply SCIM-provisioned identity/active changes to the User.

        A SCIM token is bound to exactly one tenant.

        - ``active`` is applied PER-TENANT: it (de)activates this tenant's membership only.
          A multi-tenant user is therefore never globally locked out by one tenant's token
          (which would deny access in the other tenant). The global ``User.is_active`` is
          reconciled to mirror whether the user has any active membership left, so a fully
          de-provisioned user can no longer authenticate at all. Access gates
          (TenantMembershipBackend, TenantMiddleware) honour the membership flag, so an
          ``active=false`` here genuinely revokes access in this tenant — unlike before,
          when it was silently dropped for shared users.
        - identity (username/email/name) must NEVER be rewritten for a user who is also a
          member of another tenant — that is a cross-tenant write on a shared principal
          (it would hijack their identity in the other tenant). Those changes apply only to
          a user whose sole membership is this tenant. (DELETE still drops the membership.)
        """
        has_other = (
            Membership.objects.filter(user=user)
            .exclude(tenant=self.tenant)
            .exists()
        )

        if active is not _UNSET:
            membership = Membership.objects.filter(user=user, tenant=self.tenant).first()
            if membership is not None and membership.is_active != active:
                membership.is_active = active
                membership.save(update_fields=['is_active'])
            # Mirror the global flag to "has any active membership anywhere": clears login
            # only when the user is fully de-provisioned, never from a single tenant's token.
            any_active = Membership.objects.filter(user=user, is_active=True).exists()
            if user.is_active != any_active:
                user.is_active = any_active
                user.save(update_fields=['is_active'])

        if has_other:
            # Keep this tenant's AssetHolder linked, but leave the shared global identity alone.
            link_or_create_assetholder(user, self.tenant)
            return user

        # Sole-tenant user: the global identity is safe to update.
        identity_fields = []
        if username is not _UNSET:
            user.username = username
            identity_fields.append('username')
        if email is not _UNSET:
            user.email = email
            identity_fields.append('email')
        if first_name is not _UNSET:
            user.first_name = first_name
            identity_fields.append('first_name')
        if last_name is not _UNSET:
            user.last_name = last_name
            identity_fields.append('last_name')
        if identity_fields:
            user.save(update_fields=identity_fields)
        link_or_create_assetholder(user, self.tenant)
        return user

    def put(self, request, pk, *args, **kwargs):
        user = get_object_or_404(User.objects.filter(memberships__tenant=self.tenant).distinct(), id=pk)
        
        username = request.data.get('userName')
        if not username:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "userName is required"
            }, status=status.HTTP_400_BAD_REQUEST)

        if username and len(username) > 150:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "userName exceeds maximum length of 150 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        email = ""
        emails = request.data.get('emails', [])
        if emails and isinstance(emails, list):
            email = emails[0].get('value', '')
            
        if email and len(email) > 254:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "email exceeds maximum length of 254 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        first_name = ""
        last_name = ""
        name_data = request.data.get('name')
        if name_data and isinstance(name_data, dict):
            first_name = name_data.get('givenName', '')
            last_name = name_data.get('familyName', '')

        if first_name and len(first_name) > 150:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "givenName exceeds maximum length of 150 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        if last_name and len(last_name) > 150:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "familyName exceeds maximum length of 150 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        active = request.data.get('active', True)
        if isinstance(active, str):
            active = (active.lower() == 'true')
        else:
            active = bool(active)

        with transaction.atomic():
            user = self._apply_scim_identity(
                user, username=username, email=email,
                first_name=first_name, last_name=last_name, active=active,
            )

        serializer = SCIMUserSerializer(
            user,
            context={'request': request, 'tenant_slug': self.tenant.slug, 'tenant': self.tenant}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk, *args, **kwargs):
        user = get_object_or_404(User.objects.filter(memberships__tenant=self.tenant).distinct(), id=pk)

        # Parse the requested attribute changes into locals (sentinel = not supplied), then
        # apply them through the tenant-boundary guard so a shared multi-tenant user's global
        # identity/active is never rewritten from one tenant's SCIM token.
        new_username = _UNSET
        new_email = _UNSET
        new_first = _UNSET
        new_last = _UNSET
        new_active = _UNSET

        for op in request.data.get('Operations', []):
            op_type = op.get('op', '').lower()
            path = op.get('path', '')
            value = op.get('value')

            if op_type in ('add', 'replace'):
                if isinstance(value, dict) and not path:
                    for k, v in value.items():
                        if k == 'active':
                            new_active = bool(v)
                        elif k == 'userName':
                            new_username = v
                        elif k == 'emails':
                            if isinstance(v, list) and v:
                                new_email = v[0].get('value', '')
                            elif isinstance(v, str):
                                new_email = v
                        elif k == 'name':
                            if isinstance(v, dict):
                                new_first = v.get('givenName', user.first_name)
                                new_last = v.get('familyName', user.last_name)
                else:
                    path_lower = path.lower() if path else ""
                    if path_lower == 'active':
                        if isinstance(value, str):
                            new_active = (value.lower() == 'true')
                        else:
                            new_active = bool(value)
                    elif path_lower == 'username':
                        new_username = str(value)
                    elif path_lower in ('email', 'emails', 'emails.value'):
                        if isinstance(value, list) and value:
                            new_email = value[0].get('value', '')
                        else:
                            new_email = str(value)
                    elif path_lower.startswith('name.'):
                        sub = path_lower.split('.')[1]
                        if sub == 'givenname':
                            new_first = str(value)
                        elif sub == 'familyname':
                            new_last = str(value)
                    elif path_lower == 'name':
                        if isinstance(value, dict):
                            new_first = value.get('givenName', user.first_name)
                            new_last = value.get('familyName', user.last_name)

        with transaction.atomic():
            user = self._apply_scim_identity(
                user, username=new_username, email=new_email,
                first_name=new_first, last_name=new_last, active=new_active,
            )

        serializer = SCIMUserSerializer(
            user,
            context={'request': request, 'tenant_slug': self.tenant.slug, 'tenant': self.tenant}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk, *args, **kwargs):
        user = get_object_or_404(User.objects.filter(memberships__tenant=self.tenant).distinct(), id=pk)
        with transaction.atomic():
            # Remove only the membership for the current tenant. Delete per-instance
            # so each removal is change-logged (QuerySet.delete() bypasses
            # ChangeLoggingMixin / SoftDeleteMixin entirely).
            for membership in Membership.objects.filter(user=user, tenant=self.tenant):
                membership.delete()
            # Soft-delete the associated AssetHolder for this tenant if one exists.
            for holder in AssetHolder.objects.filter(user=user, tenant=self.tenant):
                holder.delete()
            # If user has no remaining memberships, deactivate instead of hard-deleting
            if not Membership.objects.filter(user=user).exists():
                user.is_active = False
                user.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SCIMGroupListView(SCIMTenantMixin, APIView):
    def get(self, request, *args, **kwargs):
        filter_str = request.query_params.get('filter')
        try:
            q_obj = parse_scim_filter(filter_str, 'group')
        except SCIMFilterError as e:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Groups are cross-tenant; a tenant's SCIM endpoint sees (read-only) the groups
        # that carry a role owned by THIS tenant.
        queryset = UserGroup.objects.filter(roles__tenant=self.tenant).filter(q_obj).distinct()
        
        try:
            start_index = int(request.query_params.get('startIndex', 1))
        except ValueError:
            start_index = 1
        try:
            count = int(request.query_params.get('count', 50))
        except ValueError:
            count = 50
        count = min(count, 200)  # Enforce maxResults upper bound
            
        if start_index < 1:
            start_index = 1
            
        total_results = queryset.count()
        sliced_queryset = queryset[start_index - 1 : start_index - 1 + count]
        
        serializer = SCIMGroupSerializer(sliced_queryset, many=True, context={'request': request, 'tenant_slug': self.tenant.slug})
        
        return Response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total_results,
            "itemsPerPage": len(serializer.data),
            "startIndex": start_index,
            "Resources": serializer.data
        }, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        # User groups are global and grant cross-tenant access; they are managed
        # centrally by global admins, not provisioned per-tenant via SCIM.
        return Response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "403",
            "detail": "User groups are managed centrally and cannot be created via tenant SCIM.",
        }, status=status.HTTP_403_FORBIDDEN)

    def _disabled_post(self, request, *args, **kwargs):
        name = request.data.get('displayName')
        if not name:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "displayName is required"
            }, status=status.HTTP_400_BAD_REQUEST)

        if name and len(name) > 100:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "displayName exceeds maximum length of 100 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        group = UserGroup.objects.filter(tenant=self.tenant, name=name).first()
        if group:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "409",
                "detail": "Group already exists"
            }, status=status.HTTP_409_CONFLICT)

        members = request.data.get('members', [])
        member_ids = set()
        if members and isinstance(members, list):
            for item in members:
                uid = item.get('value')
                if uid:
                    try:
                        member_ids.add(int(uid))
                    except (ValueError, TypeError):
                        return Response({
                            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                            "status": "400",
                            "detail": f"Invalid member ID: {uid}"
                        }, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            group = UserGroup.objects.create(
                tenant=self.tenant,
                name=name,
            )
            sync_group_members(self.tenant, group, member_ids)

        serializer = SCIMGroupSerializer(group, context={'request': request, 'tenant_slug': self.tenant.slug})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SCIMGroupDetailView(SCIMTenantMixin, APIView):
    def get(self, request, pk, *args, **kwargs):
        group = get_object_or_404(
            UserGroup.objects.filter(roles__tenant=self.tenant).distinct(), id=pk,
        )
        serializer = SCIMGroupSerializer(group, context={'request': request, 'tenant_slug': self.tenant.slug})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk, *args, **kwargs):
        return Response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "403",
            "detail": "User groups are managed centrally and cannot be modified via tenant SCIM.",
        }, status=status.HTTP_403_FORBIDDEN)

    def _disabled_put(self, request, pk, *args, **kwargs):
        group = get_object_or_404(UserGroup.objects.filter(tenant=self.tenant), id=pk)

        name = request.data.get('displayName')
        if not name:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "displayName is required"
            }, status=status.HTTP_400_BAD_REQUEST)

        if name and len(name) > 100:
            return Response({
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "400",
                "detail": "displayName exceeds maximum length of 100 characters."
            }, status=status.HTTP_400_BAD_REQUEST)

        members = request.data.get('members', [])
        member_ids = set()
        if members and isinstance(members, list):
            for item in members:
                uid = item.get('value')
                if uid:
                    try:
                        member_ids.add(int(uid))
                    except (ValueError, TypeError):
                        return Response({
                            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                            "status": "400",
                            "detail": f"Invalid member ID: {uid}"
                        }, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            group.name = name
            group.save()
            sync_group_members(self.tenant, group, member_ids)

        serializer = SCIMGroupSerializer(group, context={'request': request, 'tenant_slug': self.tenant.slug})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk, *args, **kwargs):
        return Response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "403",
            "detail": "User groups are managed centrally and cannot be modified via tenant SCIM.",
        }, status=status.HTTP_403_FORBIDDEN)

    def _disabled_patch(self, request, pk, *args, **kwargs):
        group = get_object_or_404(UserGroup.objects.filter(tenant=self.tenant), id=pk)

        # Build the desired member id set from the current group state, then apply PATCH ops.
        current_member_ids = set(
            group.members.filter(memberships__tenant=self.tenant).values_list('id', flat=True)
        )

        with transaction.atomic():
            for op in request.data.get('Operations', []):
                op_type = op.get('op', '').lower()
                path = op.get('path', '')
                value = op.get('value')

                val_list = []
                if isinstance(value, list):
                    val_list = value
                elif isinstance(value, dict):
                    val_list = [value]

                if op_type == 'add':
                    for item in val_list:
                        uid = item.get('value')
                        if uid:
                            try:
                                current_member_ids.add(int(uid))
                            except (ValueError, TypeError):
                                return Response({
                                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                                    "status": "400",
                                    "detail": f"Invalid member ID: {uid}"
                                }, status=status.HTTP_400_BAD_REQUEST)
                elif op_type == 'remove':
                    if path and 'value eq' in path:
                        match = re.search(r'value\s+eq\s+["\']?([^"\']+)["\']?', path, re.IGNORECASE)
                        if match:
                            uid = match.group(1)
                            try:
                                current_member_ids.discard(int(uid))
                            except (ValueError, TypeError):
                                return Response({
                                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                                    "status": "400",
                                    "detail": f"Invalid member ID: {uid}"
                                }, status=status.HTTP_400_BAD_REQUEST)
                    elif val_list:
                        for item in val_list:
                            uid = item.get('value')
                            if uid:
                                try:
                                    current_member_ids.discard(int(uid))
                                except (ValueError, TypeError):
                                    return Response({
                                        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                                        "status": "400",
                                        "detail": f"Invalid member ID: {uid}"
                                    }, status=status.HTTP_400_BAD_REQUEST)
                    elif not path and not val_list:
                        current_member_ids = set()
                elif op_type == 'replace':
                    if path == 'displayName' and isinstance(value, str):
                        if len(value) > 100:
                            return Response({
                                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                                "status": "400",
                                "detail": "displayName exceeds maximum length of 100 characters."
                            }, status=status.HTTP_400_BAD_REQUEST)
                        group.name = value
                    elif isinstance(value, dict) and 'displayName' in value:
                        display_name = value['displayName']
                        if display_name and len(display_name) > 100:
                            return Response({
                                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                                "status": "400",
                                "detail": "displayName exceeds maximum length of 100 characters."
                            }, status=status.HTTP_400_BAD_REQUEST)
                        group.name = display_name
                    elif path == 'members' or not path:
                        current_member_ids = set()
                        for item in val_list:
                            uid = item.get('value')
                            if uid:
                                try:
                                    current_member_ids.add(int(uid))
                                except (ValueError, TypeError):
                                    return Response({
                                        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                                        "status": "400",
                                        "detail": f"Invalid member ID: {uid}"
                                    }, status=status.HTTP_400_BAD_REQUEST)

            group.save()
            sync_group_members(self.tenant, group, current_member_ids)

        serializer = SCIMGroupSerializer(group, context={'request': request, 'tenant_slug': self.tenant.slug})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk, *args, **kwargs):
        return Response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "403",
            "detail": "User groups are managed centrally and cannot be deleted via tenant SCIM.",
        }, status=status.HTTP_403_FORBIDDEN)

    def _disabled_delete(self, request, pk, *args, **kwargs):
        group = get_object_or_404(UserGroup.objects.filter(tenant=self.tenant), id=pk)
        with transaction.atomic():
            # Deleting a UserGroup removes the group and its M2M links; it does NOT remove
            # TenantMemberships — users remain members of the tenant with whatever direct
            # roles/grants they have. Soft-delete via the model's delete() for change-logging.
            group.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
