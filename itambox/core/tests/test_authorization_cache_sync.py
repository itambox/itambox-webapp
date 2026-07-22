from types import SimpleNamespace
from unittest import mock

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from core.auth.cache import (
    invalidate_authorization_topology,
    invalidate_user_authorization_cache,
    synchronize_authorization_cache,
)
from core.views.graphql import GraphQLView, PrivateGraphQLView
from itambox.middleware import (
    CurrentUserMiddleware,
    get_current_request_id,
    get_current_user,
)


class RequestLocalAuthorizationSyncTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(pk=42, is_authenticated=True)
        self.request = RequestFactory().get('/')
        self.request.user = self.user
        self.middleware = CurrentUserMiddleware(get_response=lambda request: None)
        self.tokens = self.middleware.process_request(self.request)

    def tearDown(self):
        self.middleware.process_response(self.request, None, self.tokens)

    @mock.patch(
        'core.auth.cache.cache.get_many',
        return_value={
            'itambox:authz-version:42': 'user-v1',
            'itambox:authz-topology-version': 'topology-v1',
        },
    )
    def test_repeated_sync_reads_shared_generations_once_per_request(self, get_many):
        synchronize_authorization_cache(self.user)
        synchronize_authorization_cache(self.user)

        self.assertEqual(get_many.call_count, 1)

    @mock.patch('core.auth.cache._repeat_after_commit')
    @mock.patch('core.auth.cache._publish_user_version')
    @mock.patch(
        'core.auth.cache.cache.get_many',
        return_value={
            'itambox:authz-version:42': 'user-v1',
            'itambox:authz-topology-version': 'topology-v1',
        },
    )
    def test_user_invalidation_resyncs_other_instance_in_same_request(
        self,
        get_many,
        _publish_user_version,
        _repeat_after_commit,
    ):
        other_instance = SimpleNamespace(pk=self.user.pk, is_authenticated=True)
        synchronize_authorization_cache(other_instance)
        other_instance._perms_tenant_1 = {'assets.view_asset'}

        invalidate_user_authorization_cache(self.user)
        synchronize_authorization_cache(other_instance)

        self.assertEqual(get_many.call_count, 2)
        self.assertFalse(hasattr(other_instance, '_perms_tenant_1'))

    @mock.patch('core.auth.cache._repeat_after_commit')
    @mock.patch('core.auth.cache._publish_topology_version')
    @mock.patch(
        'core.auth.cache.cache.get_many',
        return_value={
            'itambox:authz-version:42': 'user-v1',
            'itambox:authz-topology-version': 'topology-v1',
        },
    )
    def test_topology_invalidation_resyncs_user_in_same_request(
        self,
        get_many,
        _publish_topology_version,
        _repeat_after_commit,
    ):
        synchronize_authorization_cache(self.user)
        self.user._perms_tenant_1 = {'assets.view_asset'}

        invalidate_authorization_topology()
        synchronize_authorization_cache(self.user)

        self.assertEqual(get_many.call_count, 2)
        self.assertFalse(hasattr(self.user, '_perms_tenant_1'))

    @mock.patch(
        'core.auth.cache.cache.get_many',
        return_value={
            'itambox:authz-version:42': 'user-v1',
            'itambox:authz-topology-version': 'topology-v1',
        },
    )
    def test_same_user_instance_resyncs_in_next_request(self, get_many):
        synchronize_authorization_cache(self.user)
        self.user._perms_tenant_1 = {'assets.view_asset'}
        self.middleware.process_response(self.request, None, self.tokens)

        self.tokens = self.middleware.process_request(self.request)
        synchronize_authorization_cache(self.user)

        self.assertEqual(get_many.call_count, 2)
        self.assertFalse(hasattr(self.user, '_perms_tenant_1'))

    @mock.patch('core.auth.cache._repeat_after_commit')
    @mock.patch('core.auth.cache._publish_user_version')
    @mock.patch(
        'core.auth.cache.cache.get_many',
        return_value={
            'itambox:authz-version:42': 'user-v1',
            'itambox:authz-topology-version': 'topology-v1',
        },
    )
    def test_new_request_invalidation_clears_memos_from_previous_request(
        self,
        get_many,
        _publish_user_version,
        _repeat_after_commit,
    ):
        stale_instance = SimpleNamespace(pk=self.user.pk, is_authenticated=True)
        synchronize_authorization_cache(stale_instance)
        stale_instance._perms_tenant_1 = {'assets.view_asset'}
        self.middleware.process_response(self.request, None, self.tokens)

        self.tokens = self.middleware.process_request(self.request)
        invalidate_user_authorization_cache(self.user)
        synchronize_authorization_cache(stale_instance)

        self.assertEqual(get_many.call_count, 2)
        self.assertFalse(hasattr(stale_instance, '_perms_tenant_1'))

    @mock.patch('core.auth.cache._repeat_after_commit')
    @mock.patch('core.auth.cache._publish_topology_version')
    @mock.patch(
        'core.auth.cache.cache.get_many',
        return_value={
            'itambox:authz-version:42': 'user-v1',
            'itambox:authz-topology-version': 'topology-v1',
        },
    )
    def test_new_request_topology_invalidation_clears_previous_memos(
        self,
        get_many,
        _publish_topology_version,
        _repeat_after_commit,
    ):
        stale_instance = SimpleNamespace(pk=self.user.pk, is_authenticated=True)
        synchronize_authorization_cache(stale_instance)
        stale_instance._perms_tenant_1 = {'assets.view_asset'}
        self.middleware.process_response(self.request, None, self.tokens)

        self.tokens = self.middleware.process_request(self.request)
        invalidate_authorization_topology()
        synchronize_authorization_cache(stale_instance)

        self.assertEqual(get_many.call_count, 2)
        self.assertFalse(hasattr(stale_instance, '_perms_tenant_1'))

    @mock.patch('core.auth.cache.cache.get_many', side_effect=ConnectionError('offline'))
    def test_cache_outage_never_enables_request_shortcut(self, get_many):
        self.user._perms_tenant_1 = {'assets.view_asset'}
        synchronize_authorization_cache(self.user)
        self.assertFalse(hasattr(self.user, '_perms_tenant_1'))

        self.user._perms_tenant_1 = {'assets.view_asset'}
        synchronize_authorization_cache(self.user)

        self.assertEqual(get_many.call_count, 2)
        self.assertFalse(hasattr(self.user, '_perms_tenant_1'))

    @mock.patch('core.auth.cache._repeat_after_commit')
    @mock.patch('core.auth.cache._publish_user_version')
    @mock.patch(
        'core.auth.cache.cache.get_many',
        return_value={
            'itambox:authz-version:42': 'user-v1',
            'itambox:authz-topology-version': 'topology-v1',
        },
    )
    def test_nested_request_preserves_outer_invalidation_epoch(
        self,
        get_many,
        _publish_user_version,
        _repeat_after_commit,
    ):
        outer_instance = SimpleNamespace(pk=self.user.pk, is_authenticated=True)
        synchronize_authorization_cache(outer_instance)
        invalidate_user_authorization_cache(self.user)

        nested_request = RequestFactory().get('/nested/')
        nested_request.user = self.user
        nested_tokens = self.middleware.process_request(nested_request)
        try:
            nested_instance = SimpleNamespace(pk=self.user.pk, is_authenticated=True)
            synchronize_authorization_cache(nested_instance)
        finally:
            self.middleware.process_response(nested_request, None, nested_tokens)

        synchronize_authorization_cache(outer_instance)

        self.assertEqual(get_many.call_count, 3)


class GraphQLTokenAuthorizationContextTests(SimpleTestCase):
    def test_token_authentication_reuses_outer_authorization_request(self):
        request = RequestFactory().post(
            '/graphql/',
            data='{}',
            content_type='application/json',
        )
        request.user = SimpleNamespace(is_authenticated=False)
        authenticated_user = SimpleNamespace(
            pk=42,
            is_authenticated=True,
            is_active=True,
        )
        middleware = CurrentUserMiddleware(get_response=lambda _request: None)
        tokens = middleware.process_request(request)
        outer_request_id = get_current_request_id()

        try:
            with (
                mock.patch(
                    'core.views.graphql.TokenAuthentication.authenticate',
                    return_value=(authenticated_user, object()),
                ),
                mock.patch('core.views.graphql.TenantMiddleware.process_request'),
                mock.patch(
                    'rest_framework.throttling.AnonRateThrottle.allow_request',
                    return_value=True,
                ),
                mock.patch(
                    'rest_framework.throttling.UserRateThrottle.allow_request',
                    return_value=True,
                ),
                mock.patch.object(
                    GraphQLView,
                    'dispatch',
                    return_value=HttpResponse(status=200),
                ),
            ):
                response = PrivateGraphQLView.dispatch(
                    PrivateGraphQLView.__new__(PrivateGraphQLView),
                    request,
                )

            self.assertEqual(response.status_code, 200)
            self.assertIs(get_current_user(), authenticated_user)
            self.assertEqual(get_current_request_id(), outer_request_id)
        finally:
            middleware.process_response(request, None, tokens)
