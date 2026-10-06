"""Rendered request UI distinguishes completion from recorded handover."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context
from django.template.loader import get_template
from django.template.loader_tags import BlockNode
from django.test import RequestFactory
from django.utils.translation import override

from assets.forms.request_forms import AssetRequestManualCompletionForm
from assets.services.request_fulfillment import request_fulfillment_label
from assets.tables import AssetRequestTable


def render_detail_block(name, **values):
    template = get_template("assets/requests/assetrequest_detail.html").template
    block = next(node for node in template.nodelist.get_nodes_by_type(BlockNode) if node.name == name)
    request = RequestFactory().get("/extras/")
    # The shared request-action predicate reads these attributes on a real user;
    # the stub mirrors that contract so the fail-closed check is exercised, not bypassed.
    request.user = SimpleNamespace(pk=1, is_staff=True, is_authenticated=True, is_active=True)
    context = Context({"request": request, **values})
    with context.bind_template(template):
        return block.render(context)


def request_record(status="fulfilled", **values):
    return SimpleNamespace(
        pk=7,
        status=status,
        requester_id=None,
        tenant_id=None,
        assigned_user=None,
        get_status_display=lambda: status.title(),
        **values,
    )


def test_table_unknown_completion_is_not_a_plain_success_badge():
    table = AssetRequestTable([])
    html = table.render_status("fulfilled", request_record())
    assert "Handover evidence not verified" in html
    assert "bg-success" not in html


@pytest.mark.parametrize(
    "label",
    [
        "Fulfilled: Manually completed; no handover booked",
        "Fulfilled: handover recorded",
        "Fulfilled: mixed manual and recorded handover",
    ],
)
def test_table_uses_batched_operational_label(label):
    table = AssetRequestTable([])
    table.fulfillment_labels = {7: label}
    html = table.render_status("fulfilled", request_record())
    assert label in html


def test_detail_exposes_manual_reason_without_rendering_html():
    html = render_detail_block(
        "details_tab",
        object=request_record(),
        fulfillment_label="Fulfilled: Manually completed; no handover booked",
        fulfillment_method="manual",
        fulfillment_evidence={"method": "manual", "reason": "Delivered externally <script>alert(1)</script>"},
    )
    assert "Manually completed" in html
    assert "no handover booked" in html
    assert "Delivered externally &lt;script&gt;" in html
    assert "<script>" not in html


def test_detail_allocated_request_explicitly_awaits_handover():
    html = render_detail_block(
        "details_tab",
        object=request_record(status="approved"),
        fulfillment_label="Approved: allocated, awaiting handover",
    )
    assert "allocated, awaiting handover" in html


def test_manual_action_opens_confirmation_instead_of_posting_immediately():
    html = render_detail_block("page_actions", object=request_record(status="approved"))
    assert "Complete manually" in html
    assert "hx-get=" in html
    assert "request_mark_fulfilled" not in html  # URL name must be resolved, not displayed.


def manual_form_context():
    request = RequestFactory().get("/assets/requests/7/mark-fulfilled/")
    request.user = AnonymousUser()
    request.session = {}
    return {
        "object": request_record(status="approved"),
        "form": AssetRequestManualCompletionForm(),
        "request": request,
        "user": request.user,
    }


def test_manual_full_page_form_is_not_hidden_inside_a_modal():
    html = get_template("assets/requests/assetrequest_mark_fulfilled.html").render(manual_form_context())
    assert 'class="modal modal-blur fade"' not in html


def test_manual_form_has_real_post_fallback_and_disclosure():
    html = get_template("assets/requests/assetrequest_mark_fulfilled.html").render(manual_form_context())
    assert 'method="post"' in html
    assert 'action="' in html
    assert 'name="reason"' in html
    assert 'name="confirmed_no_handover"' in html
    assert "without creating an assignment or stock booking" in html


@pytest.mark.parametrize(
    "method,expected",
    [
        ("manual", "Erfüllt: manuell abgeschlossen; keine Übergabe gebucht"),
        ("checkout", "Erfüllt: Übergabe gebucht"),
        (None, "Erfüllt: Übergabenachweis nicht verifiziert"),
    ],
)
def test_german_completion_labels_use_compiled_catalog(method, expected):
    record = request_record(is_group=False, asset_id=None)
    evidence = {"method": method} if method else {}
    with override("de"):
        assert str(request_fulfillment_label(record, evidence=evidence)) == expected
