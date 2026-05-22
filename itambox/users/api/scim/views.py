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
from organization.models import Tenant, TenantRole, TenantMembership, AssetHolder
from users.api.scim.serializers import (
    SCIMUserSerializer, SCIMGroupSerializer, SCIMServiceProviderConfigSerializer
)
from users.api.scim.filters import parse_scim_filter, SCIMFilterError
from users.api.scim.authentication import SCIMBearerTokenAuthentication

logger = logging.getLogger('itambox.scim.views')
User = get_user_model()


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


def sync_group_members(tenant, role, member_ids):
    # 1. For users in member_ids, ensure they have TenantMembership with this role
    for uid in member_ids:
        try:
            user = User.objects.get(id=uid)
            membership = TenantMembership.objects.filter(user=user, tenant=tenant).first()
            if membership:
                membership.role = role
                membership.save()
            else:
                TenantMembership.objects.create(user=user, tenant=tenant, role=role)
            # Ensure they have matching AssetHolder profile
            link_or_create_assetholder(user, tenant)
        except User.DoesNotExist:
            logger.warning(f"SCIM Group member user ID {uid} not found.")

    # 2. For users who currently have TenantMembership with this role, but are NOT in member_ids:
    to_remove = TenantMembership.objects.filter(tenant=tenant, role=role).exclude(user_id__in=member_ids)
    to_remove.delete()


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
            membership = TenantMembership.objects.filter(user=user, tenant=self.tenant).first()
            if membership:
                return Response({
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "status": "409",
                    "detail": "User already exists in this tenant"
                }, status=status.HTTP_409_CONFLICT)
            
            with transaction.atomic():
                role, _ = TenantRole.objects.get_or_create(
                    tenant=self.tenant,
                    name="Member",
                    defaults={
                        "description": "Default member role",
                        "permissions": ["assets.view_asset", "extras.view_dashboard"]
                    }
                )
                TenantMembership.objects.create(user=user, tenant=self.tenant, role=role)
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

                role, _ = TenantRole.objects.get_or_create(
                    tenant=self.tenant,
                    name="Member",
                    defaults={
                        "description": "Default member role",
                        "permissions": ["assets.view_asset", "extras.view_dashboard"]
                    }
                )
                TenantMembership.objects.create(user=user, tenant=self.tenant, role=role)
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
            user.username = username
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.is_active = active
            user.save()
            
            link_or_create_assetholder(user, self.tenant)

        serializer = SCIMUserSerializer(
            user, 
            context={'request': request, 'tenant_slug': self.tenant.slug, 'tenant': self.tenant}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk, *args, **kwargs):
        user = get_object_or_404(User.objects.filter(memberships__tenant=self.tenant).distinct(), id=pk)
        
        with transaction.atomic():
            for op in request.data.get('Operations', []):
                op_type = op.get('op', '').lower()
                path = op.get('path', '')
                value = op.get('value')
                
                if op_type in ('add', 'replace'):
                    if isinstance(value, dict) and not path:
                        for k, v in value.items():
                            if k == 'active':
                                user.is_active = bool(v)
                            elif k == 'userName':
                                user.username = v
                            elif k == 'emails':
                                if isinstance(v, list) and v:
                                    user.email = v[0].get('value', '')
                                elif isinstance(v, str):
                                    user.email = v
                            elif k == 'name':
                                if isinstance(v, dict):
                                    user.first_name = v.get('givenName', user.first_name)
                                    user.last_name = v.get('familyName', user.last_name)
                    else:
                        path_lower = path.lower() if path else ""
                        if path_lower == 'active':
                            if isinstance(value, str):
                                user.is_active = (value.lower() == 'true')
                            else:
                                user.is_active = bool(value)
                        elif path_lower == 'username':
                            user.username = str(value)
                        elif path_lower in ('email', 'emails', 'emails.value'):
                            if isinstance(value, list) and value:
                                user.email = value[0].get('value', '')
                            else:
                                user.email = str(value)
                        elif path_lower.startswith('name.'):
                            sub = path_lower.split('.')[1]
                            if sub == 'givenname':
                                user.first_name = str(value)
                            elif sub == 'familyname':
                                user.last_name = str(value)
                        elif path_lower == 'name':
                            if isinstance(value, dict):
                                user.first_name = value.get('givenName', user.first_name)
                                user.last_name = value.get('familyName', user.last_name)
            
            user.save()
            link_or_create_assetholder(user, self.tenant)

        serializer = SCIMUserSerializer(
            user, 
            context={'request': request, 'tenant_slug': self.tenant.slug, 'tenant': self.tenant}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk, *args, **kwargs):
        user = get_object_or_404(User.objects.filter(memberships__tenant=self.tenant).distinct(), id=pk)
        with transaction.atomic():
            # Remove only the membership for the current tenant (soft-delete)
            TenantMembership.objects.filter(user=user, tenant=self.tenant).delete()
            # Delete the associated AssetHolder for this tenant if one exists
            AssetHolder.objects.filter(user=user, tenant=self.tenant).delete()
            # If user has no remaining memberships, deactivate instead of hard-deleting
            if not TenantMembership.objects.filter(user=user).exists():
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
        
        queryset = TenantRole.objects.filter(tenant=self.tenant).filter(q_obj).distinct()
        
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
        
        serializer = SCIMGroupSerializer(sliced_queryset, many=True, context={'request': request})
        
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

        role = TenantRole.objects.filter(tenant=self.tenant, name=name).first()
        if role:
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
            role = TenantRole.objects.create(
                tenant=self.tenant,
                name=name,
                permissions=["assets.view_asset", "extras.view_dashboard"]
            )
            sync_group_members(self.tenant, role, member_ids)

        serializer = SCIMGroupSerializer(role, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SCIMGroupDetailView(SCIMTenantMixin, APIView):
    def get(self, request, pk, *args, **kwargs):
        role = get_object_or_404(TenantRole.objects.filter(tenant=self.tenant), id=pk)
        serializer = SCIMGroupSerializer(role, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk, *args, **kwargs):
        role = get_object_or_404(TenantRole.objects.filter(tenant=self.tenant), id=pk)
        
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
            role.name = name
            role.save()
            sync_group_members(self.tenant, role, member_ids)

        serializer = SCIMGroupSerializer(role, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk, *args, **kwargs):
        role = get_object_or_404(TenantRole.objects.filter(tenant=self.tenant), id=pk)
        
        current_member_ids = set(role.memberships.values_list('user_id', flat=True))
        
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
                        role.name = value
                    elif isinstance(value, dict) and 'displayName' in value:
                        display_name = value['displayName']
                        if display_name and len(display_name) > 100:
                            return Response({
                                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                                "status": "400",
                                "detail": "displayName exceeds maximum length of 100 characters."
                            }, status=status.HTTP_400_BAD_REQUEST)
                        role.name = display_name
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
            
            role.save()
            sync_group_members(self.tenant, role, current_member_ids)

        serializer = SCIMGroupSerializer(role, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk, *args, **kwargs):
        role = get_object_or_404(TenantRole.objects.filter(tenant=self.tenant), id=pk)
        with transaction.atomic():
            TenantMembership.objects.filter(tenant=self.tenant, role=role).delete()
            role.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
