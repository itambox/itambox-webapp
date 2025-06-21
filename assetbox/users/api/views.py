# assetbox/users/api/views.py
import logging
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Count
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView
from users.models import UserPreference
from .serializers import UserSerializer, GroupSerializer, UserConfigSerializer
from collections import OrderedDict

logger = logging.getLogger(__name__)
User = get_user_model()

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for viewing Users"""
    queryset = User.objects.all().prefetch_related('groups')
    serializer_class = UserSerializer

class GroupViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for viewing Groups"""
    queryset = Group.objects.all().annotate(user_count=Count('user'))
    serializer_class = GroupSerializer

# Placeholder ViewSets for future implementation
# class TokenViewSet(viewsets.ModelViewSet):
#     # ... To be implemented ...
#     pass

# class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
#     # ... To be implemented ...
#     pass

class UserConfigView(RetrieveUpdateAPIView):
    """API endpoint for viewing and updating the current user's configuration."""
    serializer_class = UserConfigSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        # Retrieve or create the preference object for the request user
        preference, _ = UserPreference.objects.get_or_create(user=self.request.user)
        return preference

    def partial_update(self, request, *args, **kwargs):
        preference = self.get_object()
        incoming_data = request.data
        current_data = preference.data if preference.data is not None else {} # Ensure dict
        logger.debug("Received PATCH data in UserConfigView: %s", incoming_data)
        logger.debug("Current data BEFORE merge: %s", current_data)

        # --- Custom Deep Merge Logic --- 
        # This logic specifically merges the 'tables' structure
        # TODO: Extend if other top-level keys need PATCH support
        if 'tables' in incoming_data:
            if 'tables' not in current_data:
                current_data['tables'] = {}
            for app_label, models in incoming_data['tables'].items():
                if app_label not in current_data['tables']:
                    current_data['tables'][app_label] = {}
                for model_name, config in models.items():
                    # Overwrite or create the config for the specific table
                    current_data['tables'][app_label][model_name] = config
        
        # Assign the merged data back and save
        preference.data = current_data
        preference.save()
        logger.debug("Current data AFTER merge & save: %s", preference.data)

        # Return the updated data using the serializer
        serializer = self.get_serializer(preference)
        return Response(serializer.data)

    # We are overriding partial_update, so no need for perform_update override here
    # def perform_update(self, serializer): ...

# --- Custom API Root View for Users App --- 
class UsersAPIRootView(APIView):
    """API root view for the Users app."""
    _ignore_model_permissions = True

    def get_view_name(self):
        return "Users API Root"

    def get(self, request, format=None):
        # Use the fully qualified namespace 'api:users_api'
        return Response(OrderedDict((
            ('users', reverse('api:users_api:user-list', request=request, format=format)),
            ('groups', reverse('api:users_api:group-list', request=request, format=format)),
            ('config', reverse('api:users_api:user-config', request=request, format=format)),
            # ('tokens', reverse('api:users_api:token-list', request=request, format=format)),
            # ('permissions', reverse('api:users_api:permission-list', request=request, format=format)),
        ))) 