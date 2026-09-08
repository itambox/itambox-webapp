"""Real HTTP lifecycle and authorization tests for the Type Library adapter.

These tests intentionally run only against the parent-integrated PostgreSQL lane.
They never replace command calls with mocks or force-authentication shortcuts.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from rest_framework.test import APIClient

from assets.models.catalog import AssetType, AssetTypeFieldset, Category, Manufacturer
from core.models import ObjectChange
from core.tests.mixins import grant
from extras.models import (
    CustomField,
    CustomFieldChoice,
    CustomFieldChoiceSet,
    CustomFieldset,
    CustomFieldsetField,
    SpecificationLibrary,
    SpecificationLibraryRelease,
)
from organization.models import Role, Tenant
from users.models import Token

pytestmark = pytest.mark.django_db(transaction=True)

PREVIEW_URL = "/api/assets/type-libraries/preview/"
APPLY_URL = "/api/assets/type-libraries/apply/"
EXPORT_URL = "/api/assets/type-libraries/export/"
FIXTURE_ROOT = Path(__file__).parents[2] / "tests" / "fixtures" / "type_library"

User = get_user_model()

_SNAPSHOT_MODELS = (
    SpecificationLibrary,
    SpecificationLibraryRelease,
    CustomFieldChoice,
    CustomFieldChoiceSet,
    CustomField,
    CustomFieldset,
    CustomFieldsetField,
    AssetType,
    AssetTypeFieldset,
    Category,
    Manufacturer,
)


def _fixture_text(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


def _library_permissions() -> tuple[Permission, Permission]:
    content_type = ContentType.objects.get(app_label="extras", model="specificationlibrary")
    return (
        Permission.objects.get(
            content_type=ContentType.objects.get_for_model(CustomField), codename="change_customfield"
        ),
        Permission.objects.get(content_type=content_type, codename="manage_specification_library"),
    )


def _provision_actor() -> tuple[object, Token, Token]:
    tenant = Tenant.objects.create(name="Type Library HTTP Tenant", slug="type-library-http")
    actor = User.objects.create_user(
        username="type-library-http-actor",
        email="type-library-http-actor@example.com",
        password="not-used-by-token-test",
    )
    role = Role.objects.create(tenant=tenant, name="Type Library HTTP Role", permissions=[])
    grant(actor, tenant, role)
    actor.user_permissions.add(*_library_permissions())
    # Preview/apply require the capability AND the concrete plan's model operations.
    for model in (
        CustomField,
        CustomFieldChoiceSet,
        CustomFieldChoice,
        CustomFieldset,
        AssetType,
        Category,
        Manufacturer,
    ):
        actor.user_permissions.add(
            *Permission.objects.filter(
                content_type=ContentType.objects.get_for_model(model),
                codename__in=[f"{action}_{model._meta.model_name}" for action in ("add", "change", "view")],
            )
        )
    write_token = Token.objects.create(user=actor, tenant=tenant, write_enabled=True)
    read_token = Token.objects.create(user=actor, tenant=tenant, write_enabled=False)
    return actor, write_token, read_token


def _client_for_token(token: Token) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def _snapshot_rows() -> dict[str, list[dict[str, object]]]:
    snapshot = {
        model._meta.label_lower: list(model._base_manager.order_by("pk").values()) for model in _SNAPSHOT_MODELS
    }
    snapshot["core.objectchange"] = list(ObjectChange._base_manager.order_by("pk").values())
    return snapshot


def _post(client: APIClient, url: str, payload: dict[str, object]):
    response = client.post(url, payload, format="json")
    assert response.status_code < 500, response.data
    return response


@pytest.fixture(autouse=True)
def _clear_request_cache():
    cache.clear()
    yield
    cache.clear()


def test_real_http_lifecycle_exports_and_idempotent_import():
    _actor, write_token, _read_token = _provision_actor()
    client = _client_for_token(write_token)
    release_text = _fixture_text("example-laptop-library-v1.json")

    preview = _post(client, PREVIEW_URL, {"document": release_text})
    assert preview.status_code == 200
    assert preview.data["can_apply"] is True
    assert preview.data["plan"]["namespace"] == "example"
    assert preview.data["preview_token"]

    first_apply = _post(
        client,
        APPLY_URL,
        {
            "document": release_text,
            "preview_token": preview.data["preview_token"],
            "plan": preview.data["plan"],
        },
    )
    assert first_apply.status_code == 200, first_apply.data
    assert first_apply.data["no_op"] is False
    first_snapshot = _snapshot_rows()
    assert SpecificationLibrary.objects.filter(namespace="example").count() == 1
    assert SpecificationLibraryRelease.objects.filter(library__namespace="example").count() == 1

    original = _post(
        client,
        EXPORT_URL,
        {"namespace": "example", "mode": "original_release"},
    )
    assert original.status_code == 200, original.data
    assert original.data["mode"] == "original_release"
    assert original.data["document"]["kind"] == "itambox.type-library.release"
    assert original.data["document"]["library"]["namespace"] == "example"

    effective = _post(
        client,
        EXPORT_URL,
        {
            "namespace": "example",
            "mode": "effective_snapshot",
            "acknowledge_retained_history": True,
        },
    )
    assert effective.status_code == 200, effective.data
    assert effective.data["mode"] == "effective_snapshot"
    assert effective.data["document"]["kind"] == "itambox.type-library.snapshot"
    assert effective.data["document"]["upstream"]["library"]["namespace"] == "example"

    repeat_preview = _post(client, PREVIEW_URL, {"document": release_text})
    assert repeat_preview.status_code == 200, repeat_preview.data
    repeat_apply = _post(
        client,
        APPLY_URL,
        {
            "document": release_text,
            "preview_token": repeat_preview.data["preview_token"],
            "plan": repeat_preview.data["plan"],
        },
    )
    assert repeat_apply.status_code == 200, repeat_apply.data
    assert repeat_apply.data["no_op"] is True
    assert _snapshot_rows() == first_snapshot


def test_real_http_stale_tampered_plan_and_revoked_permission_preserve_state_and_audit():
    _actor, write_token, _read_token = _provision_actor()
    client = _client_for_token(write_token)
    release = json.loads(_fixture_text("example-laptop-library-v1.json"))
    first = _post(client, PREVIEW_URL, {"document": json.dumps(release, ensure_ascii=False)})
    assert first.status_code == 200, first.data
    first_apply = _post(
        client,
        APPLY_URL,
        {
            "document": json.dumps(release, ensure_ascii=False),
            "preview_token": first.data["preview_token"],
            "plan": first.data["plan"],
        },
    )
    assert first_apply.status_code == 200, first_apply.data

    release["library"]["release"] = 2
    release["definitions"]["fields"][0]["label"] = "HTTP successor label"
    successor_text = json.dumps(release, ensure_ascii=False)
    successor_preview = _post(client, PREVIEW_URL, {"document": successor_text})
    assert successor_preview.status_code == 200, successor_preview.data
    before_rejection = _snapshot_rows()

    tampered_plan = deepcopy(successor_preview.data["plan"])
    tampered_plan["plan_digest"] = "sha256:" + "0" * 64
    tampered = _post(
        client,
        APPLY_URL,
        {
            "document": successor_text,
            "preview_token": successor_preview.data["preview_token"],
            "plan": tampered_plan,
        },
    )
    assert tampered.status_code == 412, tampered.data
    assert tampered.data["error"]["code"] == "STALE_PLAN"
    assert _snapshot_rows() == before_rejection

    change_permission, _manage_permission = _library_permissions()
    _actor.user_permissions.remove(change_permission)
    revoked = _post(
        client,
        APPLY_URL,
        {
            "document": successor_text,
            "preview_token": successor_preview.data["preview_token"],
            "plan": successor_preview.data["plan"],
        },
    )
    assert revoked.status_code == 404, revoked.data
    assert revoked.data["error"]["code"] == "OBJECT_UNAVAILABLE"
    assert _snapshot_rows() == before_rejection


def test_real_token_authentication_rejects_read_only_write_token_without_mutation():
    _actor, _write_token, read_token = _provision_actor()
    client = _client_for_token(read_token)
    before = _snapshot_rows()

    response = client.post(
        PREVIEW_URL,
        {"document": _fixture_text("example-laptop-library-v1.json")},
        format="json",
    )

    assert response.status_code == 401
    assert _snapshot_rows() == before
