from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from core.validators import validate_file_attachment


class UploadSizeSettingsTests(SimpleTestCase):
    """WS7-6: the body/upload ceilings must exceed the 10 MB file validator so the 2.5 MB
    Django default doesn't reject a large multipart upload before the validators run."""

    def test_upload_ceilings_cover_validator_limit(self):
        self.assertGreaterEqual(settings.FILE_UPLOAD_MAX_MEMORY_SIZE, 10 * 1024 * 1024)
        self.assertGreaterEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, 10 * 1024 * 1024)


class FileAttachmentExtensionTests(SimpleTestCase):
    """WS7-7: dangerous extensions must be rejected by the extension gate alone (the libmagic
    signature check degrades to a no-op when libmagic is unavailable in the image)."""

    def test_dangerous_extensions_rejected(self):
        for name in ('x.xhtml', 'x.mhtml', 'x.jar', 'x.iso', 'x.hta', 'x.svgz', 'x.jnlp'):
            with self.assertRaises(ValidationError, msg=name):
                validate_file_attachment(SimpleUploadedFile(name, b'data'))

    def test_safe_extension_passes_extension_gate(self):
        validate_file_attachment(SimpleUploadedFile('doc.pdf', b'%PDF-1.4 minimal'))
