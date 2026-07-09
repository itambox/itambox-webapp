import logging
import re
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from itambox.middleware import set_current_user
from organization.models import Provider
from users.models import UserGroup
from organization.models import Membership
from users.api.scim.serializers import (
    SCIMUserSerializer, SCIMGroupSerializer, SCIMServiceProviderConfigSerializer
)
from users.api.scim.provider_authentication import SCIMProviderBearerTokenAuthentication

logger = logging.getLogger('itambox.scim.provider_views')
User = get_user_model()

# Sentinel for "attribute not supplied by this SCIM request" (distinct from an explicit
# empty/false value) so partial PATCH/PUT updates only touch the fields actually sent.
_UNSET = object()


def sync_provider_group_members(provider, group, member_ids):
    """Reconcile ``group.members`` to ``member_ids``, restricted to provider staff.

    Only users with an active ``Membership`` in THIS provider may be added. A
    provider SCIM token is scoped to a single provider, so group sync must never write a
    user who is not provider staff (that would be a cross-scope write and a user-id
    enumeration oracle). New staff are provisioned exclusively through SCIM /Users; unknown
    or non-staff ids are skipped.
    """
    valid_member_ids = set()
    for uid in member_ids:
        membership = Membership.objects.filter(
            user_id=uid, provider=provider, is_active=True
        ).first()
        if membership:
            valid_member_ids.add(uid)
        else:
            logger.warning(
                "SCIM provider group sync skipped user id %s: not active staff of provider %s "
                "(provision via SCIM /Users first).", uid, provider.slug
            )

    # Apply only the delta (add/remove) so ChangeLoggingMixin does not fire on unchanged
    # members.
    current_members = set(group.members.values_list('id', flat=True))
    to_add = valid_member_ids - current_members
    to_remove = current_members - valid_member_ids
    if to_add:
        group.members.add(*to_add)
    if to_remove:
        group.members.remove(*to_remove)


class SCIMProviderMixin:
    authentication_classes = [SCIMProviderBearerTokenAuthentication]
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
        provider_slug = self.kwargs.get('provider_slug')
        if not provider_slug:
            raise exceptions.ValidationError("provider_slug is required")

        try:
            self.provider = Provider._base_manager.get(slug=provider_slug)
        except Provider.DoesNotExist:
            raise exceptions.NotFound("Provider not found.")

        # Provider models are global (above tenants) — no set_current_tenant needed. Bind the
        # token's owner as the current user so SCIM-driven changelog rows are attributed to
        # the acting service account rather than 'System' (CurrentUserMiddleware captured
        # AnonymousUser before DRF auth ran).
        if getattr(request, 'user', None) and request.user.is_authenticated:
            set_current_user(request.user)


class ProviderServiceProviderConfigView(SCIMProviderMixin, APIView):
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


class SCIMProviderUserListView(SCIMProviderMixin, APIView):
    def get(self, request, *args, **kwargs):
        queryset = User.objects.filter(
            memberships__provider=self.provider, memberships__is_active=True,
        ).distinct()

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
            context={'request': request, 'tenant_slug': self.provider.slug}
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
            existing = Membership.objects.filter(user=user, provider=self.provider).first()
            if existing:
                return Response({
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "status": "409",
                    "detail": "User already exists in this provider"
                }, status=status.HTTP_409_CONFLICT)

            with transaction.atomic():
                Membership.objects.create(user=user, provider=self.provider, tenant_scope=Membership.SCOPE_EXPLICIT, is_active=active)
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

                Membership.objects.create(user=user, provider=self.provider, tenant_scope=Membership.SCOPE_EXPLICIT, is_active=active)

        serializer = SCIMUserSerializer(
            user,
            context={'request': request, 'tenant_slug': self.provider.slug}
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SCIMProviderUserDetailView(SCIMProviderMixin, APIView):
    def _staff_queryset(self):
        return User.objects.filter(
            memberships__provider=self.provider, memberships__is_active=True,
        ).distinct()

    def get(self, request, pk, *args, **kwargs):
        user = get_object_or_404(self._staff_queryset(), id=pk)
        serializer = SCIMUserSerializer(
            user,
            context={'request': request, 'tenant_slug': self.provider.slug}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def _apply_scim_identity(self, user, *, username=_UNSET, email=_UNSET,
                             first_name=_UNSET, last_name=_UNSET, active=_UNSET):
        """Apply SCIM-provisioned identity/active changes for this provider.

        A provider SCIM token is bound to exactly one provider.

        - ``active`` is applied PER-PROVIDER: it (de)activates this provider's membership
          only. The global ``User.is_active`` is reconciled to whether the user has ANY
          active provider membership left, so a fully de-provisioned user can no longer
          authenticate — but is never globally locked out from a single provider's token.
        - identity (username/email/name) must NEVER be rewritten for a user who is also a
          member of another provider or any tenant — that is a cross-scope write on a shared
          principal. Those changes apply only to a user whose sole membership is this provider.
        """
        # inline import: avoids users <-> organization import cycle at module load
        from organization.models import Membership as _M

        has_other_provider = (
            Membership.objects.filter(user=user, provider__isnull=False)
            .exclude(provider=self.provider)
            .exists()
        )
        has_tenant = _M.objects.filter(user=user, tenant__isnull=False).exists()
        has_other = has_other_provider or has_tenant

        if active is not _UNSET:
            membership = Membership.objects.filter(user=user, provider=self.provider).first()
            if membership is not None and membership.is_active != active:
                membership.is_active = active
                membership.save(update_fields=['is_active'])
            # Mirror the global flag to "has any active provider membership". (Tenant-only
            # users are governed by their tenant memberships, not by this provider token.)
            any_active = Membership.objects.filter(user=user, provider__isnull=False, is_active=True).exists()
            if not any_active and not has_tenant:
                if user.is_active:
                    user.is_active = False
                    user.save(update_fields=['is_active'])
            elif any_active and not user.is_active:
                user.is_active = True
                user.save(update_fields=['is_active'])

        if has_other:
            # Leave the shared global identity alone.
            return user

        # Sole-membership user: the global identity is safe to update.
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
        return user

    def put(self, request, pk, *args, **kwargs):
        user = get_object_or_404(self._staff_queryset(), id=pk)

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
            context={'request': request, 'tenant_slug': self.provider.slug}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk, *args, **kwargs):
        user = get_object_or_404(self._staff_queryset(), id=pk)

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
            context={'request': request, 'tenant_slug': self.provider.slug}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk, *args, **kwargs):
        user = get_object_or_404(self._staff_queryset(), id=pk)
        with transaction.atomic():
            # Remove only the membership for this provider. Delete per-instance so each
            # removal is change-logged (QuerySet.delete() bypasses ChangeLoggingMixin).
            for membership in Membership.objects.filter(user=user, provider=self.provider):
                membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SCIMProviderGroupListView(SCIMProviderMixin, APIView):
    def get(self, request, *args, **kwargs):
        queryset = UserGroup.objects.filter(provider=self.provider)

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

        serializer = SCIMGroupSerializer(
            sliced_queryset, many=True, context={'request': request, 'tenant_slug': self.provider.slug}
        )

        return Response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total_results,
            "itemsPerPage": len(serializer.data),
            "startIndex": start_index,
            "Resources": serializer.data
        }, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
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

        # Group names are unique PER PROVIDER (active rows): reject only a duplicate within
        # THIS provider, surfacing a clean 409 rather than a DB IntegrityError. A different
        # provider may legitimately reuse the same displayName.
        if UserGroup.objects.filter(provider=self.provider, name=name).exists():
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
                provider=self.provider,
                name=name,
            )
            sync_provider_group_members(self.provider, group, member_ids)

        serializer = SCIMGroupSerializer(group, context={'request': request, 'tenant_slug': self.provider.slug})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SCIMProviderGroupDetailView(SCIMProviderMixin, APIView):
    def get(self, request, pk, *args, **kwargs):
        group = get_object_or_404(UserGroup.objects.filter(provider=self.provider), id=pk)
        serializer = SCIMGroupSerializer(group, context={'request': request, 'tenant_slug': self.provider.slug})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk, *args, **kwargs):
        group = get_object_or_404(UserGroup.objects.filter(provider=self.provider), id=pk)

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
            sync_provider_group_members(self.provider, group, member_ids)

        serializer = SCIMGroupSerializer(group, context={'request': request, 'tenant_slug': self.provider.slug})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk, *args, **kwargs):
        group = get_object_or_404(UserGroup.objects.filter(provider=self.provider), id=pk)

        # Build the desired member id set from the current group state, then apply PATCH ops.
        current_member_ids = set(group.members.values_list('id', flat=True))

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
            sync_provider_group_members(self.provider, group, current_member_ids)

        serializer = SCIMGroupSerializer(group, context={'request': request, 'tenant_slug': self.provider.slug})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk, *args, **kwargs):
        group = get_object_or_404(UserGroup.objects.filter(provider=self.provider), id=pk)
        with transaction.atomic():
            # Soft-delete via the model's delete() for change-logging.
            group.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
