"""Shared safe HTML-to-PDF rendering.

The renderer is intentionally below report exporters and background tasks. It
contains no task, view, or report imports and refuses remote or traversing
resource URLs to preserve the existing SSRF boundary.
"""

import io
import os

from django.conf import settings


def pdf_safe_link_callback(uri, rel):
    """Allow data URIs and files below configured static/media roots only."""
    if uri.startswith("data:"):
        return uri

    for url_prefix, root in (
        (getattr(settings, "STATIC_URL", None), getattr(settings, "STATIC_ROOT", None)),
        (getattr(settings, "MEDIA_URL", None), getattr(settings, "MEDIA_ROOT", None)),
    ):
        if url_prefix and root and uri.startswith(url_prefix):
            root_abs = os.path.abspath(root)
            candidate = os.path.abspath(os.path.join(root_abs, uri[len(url_prefix) :].lstrip("/")))
            if os.path.commonpath([root_abs, candidate]) == root_abs and os.path.isfile(candidate):
                return candidate
            return ""
    return ""


def html_to_pdf_bytes(html_content):
    """Render HTML to PDF bytes via xhtml2pdf and the safe link callback."""
    # inline import: heavy-import: xhtml2pdf is only needed when rendering PDF bytes
    from xhtml2pdf import pisa

    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(html_content, dest=pdf_buffer, link_callback=pdf_safe_link_callback)
    if pisa_status.err:
        raise RuntimeError(f"xhtml2pdf rendering failed with status code {pisa_status.err}")
    return pdf_buffer.getvalue()
