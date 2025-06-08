# core/api/views.py
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from core.models import UserPreference, ObjectChange
from .serializers import UserPreferenceSerializer, ObjectChangeSerializer

class UserPreferenceViewSet(viewsets.ViewSet):
    """
    ViewSet for the UserPreference model, handling get/set/delete via API.
    Uses the request user implicitly.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserPreferenceSerializer

    def get_object(self):
        # Get or create UserPreference for the current user
        obj, created = UserPreference.objects.get_or_create(user=self.request.user)
        return obj

    def list(self, request):
        """ Get the preferences for the current user. """
        instance = self.get_object()
        serializer = self.serializer_class(instance)
        return Response(serializer.data)

    def create(self, request):
        """ Create or update preferences for the current user. """
        instance = self.get_object()
        # We expect the full data payload to be sent for update
        serializer = self.serializer_class(instance, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request):
        """ Delete all preferences for the current user. """
        instance = self.get_object()
        # In NetBox, this might delete specific keys, but here we delete the whole pref object
        # Or modify to delete only the 'tables' key:
        # instance.data.pop('tables', None)
        # instance.save()
        # Let's keep it simple for now: delete the whole object or a specific table key if needed.
        # For resetting a specific table: a different approach might be needed in JS/client-side.
        # For now, DELETE removes the whole preference object for simplicity.
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # We don't need retrieve, update, partial_update as we handle everything
    # via list (GET) and create (POST/PUT) for the single user pref object.
    # The destroy handles DELETE.

class ObjectChangeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API endpoint for viewing ObjectChanges (changelog).
    """
    queryset = ObjectChange.objects.all().prefetch_related(
        'user', 'changed_object_type', 'related_object_type'
    )
    serializer_class = ObjectChangeSerializer
    permission_classes = [IsAdminUser] # Adjust permissions as needed