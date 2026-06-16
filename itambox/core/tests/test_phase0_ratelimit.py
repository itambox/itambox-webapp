from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.cache import caches

User = get_user_model()


@override_settings(
    # Keep limits at their production defaults but make them explicit so the
    # test is independent of any environment overrides.
    RATELIMIT_LIMIT=5,
    RATELIMIT_PERIOD=60,
    RATELIMIT_CACHE='default',
)
class AuthenticatedRateLimitTestCase(TestCase):
    """
    Regression for the removed authenticated bypass in RateLimitMiddleware.

    Previously the middleware short-circuited for any authenticated user,
    letting a logged-in account hammer email-sending endpoints
    (/accounts/password_reset/, /organization/invite-user/, ...) without limit.
    The bypass is gone: sensitive paths are now IP-rate-limited regardless of
    auth, so an authenticated client must eventually receive HTTP 429.
    """

    # A sensitive path covered by RateLimitMiddleware.rate_limited_paths. GET is
    # enough — the middleware limits by path prefix, independent of HTTP method.
    # /accounts/login/ is Django's default LoginView: it renders the login form
    # (200) even for an authenticated user and needs no tenant context, so the
    # request reaches the rate-limit counter without the view 500-ing.
    LIMITED_PATH = '/accounts/login/'

    def setUp(self):
        # Counters live in the 'default' cache (LocMemCache under tests); clear
        # so counts start fresh and don't leak in from another test.
        caches['default'].clear()
        self.user = User.objects.create_user(
            username='ratelimit_user', password='password123'
        )
        self.client.force_login(self.user)

    def test_authenticated_user_is_eventually_rate_limited(self):
        # Hammer the path well past the limit (5/period). With the bypass gone,
        # an authenticated user must hit 429.
        statuses = [
            self.client.get(self.LIMITED_PATH).status_code
            for _ in range(10)
        ]
        self.assertIn(
            429, statuses,
            msg=(
                'Authenticated user was never rate limited on '
                f'{self.LIMITED_PATH}; the auth bypass appears to still exist. '
                f'Statuses: {statuses}'
            ),
        )

    def test_bypass_does_not_reset_counter_for_authenticated_user(self):
        # The 6th request (count already == limit) must be the 429, proving the
        # counter is being incremented for the authenticated user rather than
        # skipped.
        for _ in range(5):
            self.assertNotEqual(
                self.client.get(self.LIMITED_PATH).status_code, 429
            )
        self.assertEqual(self.client.get(self.LIMITED_PATH).status_code, 429)
