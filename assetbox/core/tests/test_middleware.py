from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from assetbox.middleware import CurrentUserMiddleware, get_current_user, get_current_request_id

User = get_user_model()

class CurrentUserMiddlewareTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='testuser', password='password123', is_superuser=True)

    def test_current_user_middleware_contextvars(self):
        """Test that CurrentUserMiddleware correctly sets and cleans up request user and request ID."""
        request = self.factory.get('/')
        request.user = self.user
        
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)
        
        self.assertEqual(get_current_user(), self.user)
        self.assertIsNotNone(get_current_request_id())
        
        response = middleware.process_response(request, None)
        self.assertIsNone(get_current_user())
        self.assertIsNone(get_current_request_id())
