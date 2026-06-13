import logging
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Count
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.response import Response

from itambox.api.viewsets import ITAMBoxReadOnlyModelViewSet, ITAMBoxModelViewSet
from users.models import UserPreference, Token
from .serializers import UserSerializer, GroupSerializer, UserConfigSerializer, TokenSerializer

logger = logging.getLogger(__name__)
User = get_user_model()


class UserViewSet(ITAMBoxReadOnlyModelViewSet):
    queryset = User.objects.all().prefetch_related('groups')
    serializer_class = UserSerializer


class GroupViewSet(ITAMBoxReadOnlyModelViewSet):
    queryset = Group.objects.all().annotate(user_count=Count('user'))
    serializer_class = GroupSerializer


class TokenViewSet(ITAMBoxModelViewSet):
    serializer_class = TokenSerializer

    def get_queryset(self):
        return Token.objects.select_related('user').filter(user=self.request.user)

    def _pin_user(self, serializer):
        # A user must never be able to provision a token bound to another account
        # (privilege escalation). Only superusers may set an explicit `user`;
        # everyone else is pinned to themselves regardless of any supplied user_id.
        if self.request.user.is_superuser and serializer.validated_data.get('user'):
            serializer.save()
        else:
            serializer.save(user=self.request.user)

    def perform_create(self, serializer):
        self._pin_user(serializer)

    def perform_update(self, serializer):
        self._pin_user(serializer)


class UserConfigView(RetrieveUpdateAPIView):
    serializer_class = UserConfigSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        preference, _ = UserPreference.objects.get_or_create(user=self.request.user)
        return preference

    def partial_update(self, request, *args, **kwargs):
        preference = self.get_object()
        incoming_data = request.data
        current_data = preference.data if preference.data is not None else {}
        logger.debug("Received PATCH data in UserConfigView: %s", incoming_data)
        logger.debug("Current data BEFORE merge: %s", current_data)

        if 'tables' in incoming_data:
            if 'tables' not in current_data:
                current_data['tables'] = {}
            for app_label, models in incoming_data['tables'].items():
                if app_label not in current_data['tables']:
                    current_data['tables'][app_label] = {}
                for model_name, config in models.items():
                    current_data['tables'][app_label][model_name] = config

        preference.data = current_data
        preference.save()
        logger.debug("Current data AFTER merge & save: %s", preference.data)

        serializer = self.get_serializer(preference)
        return Response(serializer.data)
