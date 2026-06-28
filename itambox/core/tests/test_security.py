from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from model_bakery import baker

from core.validators import validate_file_attachment, validate_image_attachment

User = get_user_model()


class MockUploadedFile(SimpleUploadedFile):
    def __init__(self, name, content, size_bytes):
        super().__init__(name, content)
        self._size = size_bytes

    @property
    def size(self):
        return self._size

    @size.setter
    def size(self, value):
        self._size = value

class SecurityHardeningTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user_ct = ContentType.objects.get_for_model(User)
        self.user_instance = baker.make(User)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_file_attachment_size_limit(self):
        # 10 MB is the limit. 10MB + 1 byte should fail.
        oversized_file = MockUploadedFile("test.txt", b"x", 10 * 1024 * 1024 + 1)
        with self.assertRaises(ValidationError) as ctx:
            validate_file_attachment(oversized_file)
        self.assertIn("File size must not exceed 10 MB", str(ctx.exception))

        # 10 MB should pass
        valid_size_file = MockUploadedFile("test.txt", b"x", 10 * 1024 * 1024)
        validate_file_attachment(valid_size_file)

    def test_file_attachment_forbidden_extensions(self):
        forbidden = [".exe", ".bat", ".sh", ".php", ".html", ".svg", ".xml"]
        for ext in forbidden:
            forbidden_file = SimpleUploadedFile(f"malicious{ext}", b"echo 'bad'")
            with self.assertRaises(ValidationError) as ctx:
                validate_file_attachment(forbidden_file)
            self.assertIn("are not allowed for security reasons", str(ctx.exception))

        # Safe extension should pass
        safe_file = SimpleUploadedFile("document.pdf", b"pdf data")
        validate_file_attachment(safe_file)

    def test_image_attachment_size_limit(self):
        # 5 MB is the limit. 5MB + 1 byte should fail.
        oversized_image = MockUploadedFile("image.png", b"x", 5 * 1024 * 1024 + 1)
        with self.assertRaises(ValidationError) as ctx:
            validate_image_attachment(oversized_image)
        self.assertIn("Image size must not exceed 5 MB", str(ctx.exception))

        # 5 MB should pass
        valid_size_image = MockUploadedFile("image.png", b"x", 5 * 1024 * 1024)
        validate_image_attachment(valid_size_image)

    def test_image_attachment_format_whitelist(self):
        allowed = [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]
        for ext in allowed:
            valid_image = SimpleUploadedFile(f"image{ext}", b"image-data")
            validate_image_attachment(valid_image)

        # SVG or txt or other formats should fail
        forbidden = [".svg", ".txt", ".exe", ".pdf"]
        for ext in forbidden:
            invalid_image = SimpleUploadedFile(f"image{ext}", b"bad-data")
            with self.assertRaises(ValidationError) as ctx:
                validate_image_attachment(invalid_image)
            self.assertIn("is not supported", str(ctx.exception))

    @override_settings(RATELIMIT_LIMIT=3, RATELIMIT_PERIOD=60)
    def test_rate_limiting_middleware(self):
        login_url = reverse('login')
        
        # 1st request -> OK
        response1 = self.client.get(login_url)
        self.assertNotEqual(response1.status_code, 429)
        
        # 2nd request -> OK
        response2 = self.client.get(login_url)
        self.assertNotEqual(response2.status_code, 429)
        
        # 3rd request -> OK
        response3 = self.client.get(login_url)
        self.assertNotEqual(response3.status_code, 429)
        
        # 4th request -> Blocked (429)
        response4 = self.client.get(login_url)
        self.assertEqual(response4.status_code, 429)
        self.assertIn("Too many requests", response4.content.decode())

    @override_settings(RATELIMIT_LIMIT=3, RATELIMIT_PERIOD=60)
    def test_rate_limiting_ignores_unlisted_paths(self):
        search_url = reverse('search')
        
        # Multiple requests should proceed normally
        for _ in range(5):
            response = self.client.get(search_url)
            self.assertNotEqual(response.status_code, 429)
