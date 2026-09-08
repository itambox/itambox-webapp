"""Dedicated URLConf for the Type Library REST integration tests.

The shared router remains parent-owned; this module makes the three dedicated
views addressable without changing production URL registration.
"""

from django.urls import path

from assets.api.type_library import (
    TypeLibraryApplyAPIView,
    TypeLibraryExportAPIView,
    TypeLibraryPreviewAPIView,
)

urlpatterns = [
    path("api/assets/type-libraries/preview/", TypeLibraryPreviewAPIView.as_view(), name="test-type-library-preview"),
    path("api/assets/type-libraries/apply/", TypeLibraryApplyAPIView.as_view(), name="test-type-library-apply"),
    path("api/assets/type-libraries/export/", TypeLibraryExportAPIView.as_view(), name="test-type-library-export"),
]
