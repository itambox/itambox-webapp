from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetRequest, AssetRole, AssetType, Manufacturer, StatusLabel
from core.tests.mixins import TenantTestMixin, grant
from organization.models import AssetHolder, Location, Role, Site, Tenant, TenantGroup

User = get_user_model()


class Issue497RequestActionAuthorizationTests(TenantTestMixin, TestCase):
    actions = ("approve", "deny", "cancel", "claim", "mark_fulfilled", "bulk_receive")
    allowed_actions = {
        "staff_only": {"cancel", "claim", "mark_fulfilled"},
        "approve_only": {"approve", "deny", "cancel"},
        "fulfill_only": {"claim", "mark_fulfilled", "bulk_receive"},
        "both": set(actions),
        "neither": set(),
        "requester": {"cancel", "claim"},
        "assigned_user": {"claim"},
        "unrelated": set(),
    }

    def setUp(self):
        self.setup_tenant_context(slug="issue497")
        self.set_active_tenant(self.tenant, self.tenant_membership)
        self.requester = self.tenant_user
        self.assigned_user = self._make_actor("assigned_user")
        self.neither = self._make_actor("neither")
        self.unrelated = self._make_actor("unrelated")
        self.staff_only = self._make_actor("staff_only", is_staff=True)
        self.approve_only = self._make_actor("approve_only", {"assets.approve_assetrequest"})
        self.fulfill_only = self._make_actor("fulfill_only", {"assets.fulfill_assetrequest"})
        self.both = self._make_actor("both", {"assets.approve_assetrequest", "assets.fulfill_assetrequest"})

        self.manufacturer = Manufacturer.objects.create(name="Issue 497 Vendor", slug="issue-497-vendor")
        self.asset_role = AssetRole.objects.create(name="Issue 497 Laptop", slug="issue-497-laptop")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Issue 497 Laptop",
            slug="issue-497-laptop-model",
            requestable=True,
            asset_role=self.asset_role,
        )
        self.deployable_status = StatusLabel.objects.create(
            name="Issue 497 Deployable",
            slug="issue-497-deployable",
            type=StatusLabel.TYPE_DEPLOYABLE,
        )
        self.site = Site.objects.create(name="Issue 497 Site", slug="issue-497-site", tenant=self.tenant)
        self.location = Location.objects.create(
            name="Issue 497 Location",
            slug="issue-497-location",
            site=self.site,
            tenant=self.tenant,
        )
        self.requester_holder = self._make_holder(self.requester, self.tenant, "requester")
        self.assigned_holder = self._make_holder(self.assigned_user, self.tenant, "assigned")

        self.actors = {
            "staff_only": self.staff_only,
            "approve_only": self.approve_only,
            "fulfill_only": self.fulfill_only,
            "both": self.both,
            "neither": self.neither,
            "requester": self.requester,
            "assigned_user": self.assigned_user,
            "unrelated": self.unrelated,
        }

    def _make_actor(self, label, permissions=(), is_staff=False):
        user = User.objects.create_user(
            username=f"issue497_{label}",
            password="password",
            is_staff=is_staff,
        )
        empty_role = Role.objects.create(tenant=self.tenant, name=f"Issue 497 {label} base", permissions=[])
        grant(user, self.tenant, empty_role)
        if permissions:
            action_role = Role.objects.create(
                tenant=self.tenant,
                name=f"Issue 497 {label} actions",
                permissions=sorted(permissions),
            )
            grant(user, self.tenant, action_role)
        return user

    @staticmethod
    def _make_holder(user, tenant, label):
        return AssetHolder.objects.create(
            user=user,
            first_name="Issue",
            last_name=f"497 {label}",
            upn=f"issue497_{label}_{user.pk}@example.com",
            tenant=tenant,
        )

    def _make_asset(self, tenant, suffix):
        return Asset.objects.create(
            name=f"Issue 497 Asset {suffix}",
            asset_tag=f"ISSUE497-{suffix}",
            asset_type=self.asset_type,
            asset_role=self.asset_role,
            status=self.deployable_status,
            requestable=True,
            tenant=tenant,
        )

    def _make_request(self, *, status, requester=None, tenant=None, asset=None, assigned_user=None):
        tenant = tenant or self.tenant
        req = AssetRequest(
            tenant=tenant,
            requester=requester or self.requester,
            asset_type=self.asset_type,
            asset=asset,
            assigned_user=assigned_user,
            status=status,
        )
        req._skip_duplicate_check = True
        req.save()
        return req

    def _make_action_request(self, action, actor_name):
        status = RequestStatusChoices.PENDING
        if action in {"claim", "mark_fulfilled", "bulk_receive"}:
            status = RequestStatusChoices.APPROVED
        requester = self.actors[actor_name] if actor_name == "requester" else self.requester
        assigned_user = self.assigned_holder if action == "claim" and actor_name == "assigned_user" else None
        asset = self._make_asset(self.tenant, f"{actor_name}-{action}") if action == "claim" else None
        return self._make_request(
            status=status,
            requester=requester,
            asset=asset,
            assigned_user=assigned_user,
        )

    def _bulk_formset_data(self, requests, suffix):
        data = {
            "form-TOTAL_FORMS": str(len(requests)),
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for index, req in enumerate(requests):
            row = f"form-{index}-"
            data.update(
                {
                    f"{row}request_id": str(req.pk),
                    f"{row}asset_tag": f"ISSUE497-RECEIVED-{suffix}-{index}",
                    f"{row}serial_number": f"ISSUE497-SERIAL-{suffix}-{index}",
                    f"{row}name": "Received Issue 497 Laptop",
                    f"{row}status": str(self.deployable_status.pk),
                    f"{row}location": str(self.location.pk),
                    f"{row}supplier": "",
                    f"{row}order_number": "",
                    f"{row}purchase_cost": "",
                    f"{row}purchase_date": "",
                }
            )
        return data

    def _post_action(self, action, req, actor_name):
        if action in {"approve", "deny", "cancel", "claim", "mark_fulfilled"}:
            url = reverse(f"assets:request_{action}", kwargs={"pk": req.pk})
            if action == "mark_fulfilled":
                return self.client.post(
                    url,
                    {"reason": "External delivery", "confirmed_no_handover": "on"},
                )
            return self.client.post(url, {})

        url = reverse("assets:request_bulk_receive")
        initial_response = self.client.post(url, {"pk": [str(req.pk)]})
        if initial_response.status_code != 200:
            return initial_response
        data = self._bulk_formset_data([req], actor_name)
        return self.client.post(url, data)

    def test_action_authorization_http_matrix(self):
        for actor_name, actor in self.actors.items():
            for action in self.actions:
                with self.subTest(actor=actor_name, action=action):
                    req = self._make_action_request(action, actor_name)
                    before_status = req.status
                    before_asset_id = req.asset_id
                    self.client_login_to_tenant(actor, self.tenant)
                    response = self._post_action(action, req, actor_name)
                    if action in self.allowed_actions[actor_name]:
                        self.assertEqual(response.status_code, 302)
                        req.refresh_from_db()
                        if action == "approve":
                            self.assertEqual(req.status, RequestStatusChoices.APPROVED)
                        elif action == "deny":
                            self.assertEqual(req.status, RequestStatusChoices.DENIED)
                        elif action == "cancel":
                            self.assertEqual(req.status, RequestStatusChoices.CANCELLED)
                        elif action in {"claim", "mark_fulfilled"}:
                            self.assertEqual(req.status, RequestStatusChoices.FULFILLED)
                        elif action == "bulk_receive":
                            self.assertIsNotNone(req.asset_id)
                    else:
                        self.assertIn(response.status_code, (403, 404))
                        req.refresh_from_db()
                        self.assertEqual(req.status, before_status)
                        self.assertEqual(req.asset_id, before_asset_id)

    def test_approve_only_and_fulfill_only_users_reach_request_list_and_detail(self):
        req = self._make_request(status=RequestStatusChoices.PENDING)
        for actor in (self.approve_only, self.fulfill_only):
            with self.subTest(actor=actor.username):
                self.client_login_to_tenant(actor, self.tenant)
                list_response = self.client.get(reverse("assets:request_list"))
                self.assertEqual(list_response.status_code, 200)
                listed_ids = [row.pk for row in list_response.context["object_list"]]
                self.assertIn(req.pk, listed_ids)
                detail_response = self.client.get(reverse("assets:request_detail", kwargs={"pk": req.pk}))
                self.assertEqual(detail_response.status_code, 200)
                self.assertEqual(detail_response.context["object"].pk, req.pk)

    def test_bulk_receive_context_requires_an_eligible_row_and_target_permission(self):
        req = self._make_request(status=RequestStatusChoices.APPROVED)
        self.client_login_to_tenant(self.approve_only, self.tenant)
        response = self.client.get(reverse("assets:request_list"))
        self.assertFalse(response.context["asset_request_bulk_receive_available"])

        self.client_login_to_tenant(self.fulfill_only, self.tenant)
        response = self.client.get(reverse("assets:request_list"))
        self.assertTrue(response.context["asset_request_bulk_receive_available"])

        req.status = RequestStatusChoices.CANCELLED
        req.save(update_fields=["status"])
        response = self.client.get(reverse("assets:request_list"))
        self.assertFalse(response.context["asset_request_bulk_receive_available"])

    def _group_actor_and_tenants(self, permission):
        group = TenantGroup.objects.create(name="Issue 497 Group", slug="issue-497-group")
        self.tenant.group = group
        self.tenant.save(update_fields=["group"])
        tenant_b = Tenant.objects.create(name="Issue 497 Tenant B", slug="issue-497-tenant-b", group=group)
        actor = self._make_actor(f"group_{permission.split('.')[1]}")
        action_role = Role.objects.create(
            tenant=self.tenant,
            name=f"Issue 497 group {permission}",
            permissions=[permission],
        )
        grant(actor, self.tenant, action_role)
        with self.tenant_context(tenant_b):
            empty_role = Role.objects.create(
                tenant=tenant_b,
                name="Issue 497 Tenant B empty role",
                permissions=[],
            )
            grant(actor, tenant_b, empty_role)

        self.client.force_login(actor)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session["active_tenant_group_id"] = group.pk
        session.save()
        return actor, tenant_b

    def _make_location_for_tenant(self, tenant, suffix):
        with self.tenant_context(tenant):
            site = Site.objects.create(
                name=f"Issue 497 Site {suffix}",
                slug=f"issue-497-site-{suffix}",
                tenant=tenant,
            )
            return Location.objects.create(
                name=f"Issue 497 Location {suffix}",
                slug=f"issue-497-location-{suffix}",
                site=site,
                tenant=tenant,
            )

    def test_tenant_a_grant_does_not_authorize_cancel_in_tenant_b_group_scope(self):
        actor, tenant_b = self._group_actor_and_tenants("assets.approve_assetrequest")
        self.assertFalse(actor.is_staff)
        request_a = self._make_request(status=RequestStatusChoices.PENDING)
        with self.tenant_context(tenant_b):
            request_b = self._make_request(status=RequestStatusChoices.PENDING, tenant=tenant_b)

        url_a = reverse("assets:request_cancel", kwargs={"pk": request_a.pk})
        self.assertEqual(self.client.post(url_a, {}).status_code, 302)
        response = self.client.post(reverse("assets:request_cancel", kwargs={"pk": request_b.pk}), {})
        self.assertIn(response.status_code, (403, 404))
        request_b.refresh_from_db()
        self.assertEqual(request_b.status, RequestStatusChoices.PENDING)

    def test_mixed_tenant_bulk_batch_is_denied_before_any_allocation(self):
        actor, tenant_b = self._group_actor_and_tenants("assets.fulfill_assetrequest")
        self.assertFalse(actor.is_staff)
        request_a = self._make_request(status=RequestStatusChoices.APPROVED)
        with self.tenant_context(tenant_b):
            request_b = self._make_request(status=RequestStatusChoices.APPROVED, tenant=tenant_b)
        location_b = self._make_location_for_tenant(tenant_b, "B")
        location_a = self.location
        asset_count = Asset._base_manager.count()

        data = self._bulk_formset_data([request_a, request_b], "mixed")
        for index, location in enumerate((location_a, location_b)):
            data[f"form-{index}-location"] = str(location.pk)
        response = self.client.post(reverse("assets:request_bulk_receive"), data)

        self.assertIn(response.status_code, (403, 404))
        request_a.refresh_from_db()
        request_b.refresh_from_db()
        self.assertIsNone(request_a.asset_id)
        self.assertIsNone(request_b.asset_id)
        self.assertEqual(Asset._base_manager.count(), asset_count)

    def _login_all_accessible(self, user):
        self.client.force_login(user)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session.pop("active_tenant_group_id", None)
        session["active_all_accessible"] = True
        session.save()

    def test_all_accessible_scope_bulk_receive_is_authorized_per_target(self):
        """The All-accessible scope must not blanket-deny a tenant-scoped fulfiller."""
        req = self._make_request(status=RequestStatusChoices.APPROVED)
        url = reverse("assets:request_bulk_receive")

        self._login_all_accessible(self.approve_only)
        denied = self.client.post(url, {"pk": [str(req.pk)]})
        self.assertIn(denied.status_code, (403, 404))
        req.refresh_from_db()
        self.assertIsNone(req.asset_id)

        self._login_all_accessible(self.fulfill_only)
        initial_response = self.client.post(url, {"pk": [str(req.pk)]})
        self.assertEqual(initial_response.status_code, 200)
        response = self.client.post(url, self._bulk_formset_data([req], "all-accessible"))
        self.assertEqual(response.status_code, 302)
        req.refresh_from_db()
        self.assertIsNotNone(req.asset_id)

    def test_bulk_receive_toolbar_is_available_for_a_target_scoped_fulfiller_in_all_accessible_scope(self):
        """The toolbar flag must follow the endpoint: no objectless permission check."""
        self._make_request(status=RequestStatusChoices.APPROVED)

        self._login_all_accessible(self.approve_only)
        denied = self.client.get(reverse("assets:request_list"))
        self.assertFalse(denied.context["asset_request_bulk_receive_available"])

        self._login_all_accessible(self.fulfill_only)
        allowed = self.client.get(reverse("assets:request_list"))
        self.assertTrue(allowed.context["asset_request_bulk_receive_available"])

    def test_bulk_receive_toolbar_is_available_for_an_approved_group_with_approved_units(self):
        """The endpoint expands groups, so a group-only page must offer the action."""
        group = self._make_request(status=RequestStatusChoices.APPROVED)
        group.is_group = True
        group.save(update_fields=["is_group"])
        unit = AssetRequest(
            tenant=self.tenant,
            requester=self.requester,
            asset_type=self.asset_type,
            parent=group,
            status=RequestStatusChoices.APPROVED,
        )
        unit._skip_duplicate_check = True
        unit.save()

        self.client_login_to_tenant(self.fulfill_only, self.tenant)
        response = self.client.get(reverse("assets:request_list"))
        self.assertEqual([row.pk for row in response.context["object_list"]], [group.pk])
        self.assertTrue(response.context["asset_request_bulk_receive_available"])

    def test_bulk_receive_toolbar_stays_hidden_for_a_group_without_approved_units(self):
        group = self._make_request(status=RequestStatusChoices.APPROVED)
        group.is_group = True
        group.save(update_fields=["is_group"])
        unit = AssetRequest(
            tenant=self.tenant,
            requester=self.requester,
            asset_type=self.asset_type,
            parent=group,
            status=RequestStatusChoices.PENDING,
        )
        unit._skip_duplicate_check = True
        unit.save()

        self.client_login_to_tenant(self.fulfill_only, self.tenant)
        response = self.client.get(reverse("assets:request_list"))
        self.assertEqual([row.pk for row in response.context["object_list"]], [group.pk])
        self.assertFalse(response.context["asset_request_bulk_receive_available"])

    def test_bulk_receive_selection_errors_are_visible_messages_not_a_403_page(self):
        """Malformed or repeated selections are input errors, not authorization failures."""
        self.client_login_to_tenant(self.fulfill_only, self.tenant)
        url = reverse("assets:request_bulk_receive")

        invalid = self.client.post(url, {"pk": ["not-a-number"]}, follow=True)
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "One or more selected requests are invalid.")

        req = self._make_request(status=RequestStatusChoices.APPROVED)
        repeated = self.client.post(url, {"pk": [str(req.pk), str(req.pk)]}, follow=True)
        self.assertEqual(repeated.status_code, 200)
        self.assertContains(repeated, "A request can appear only once in a bulk receipt.")
