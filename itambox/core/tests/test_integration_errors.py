from unittest import TestCase
from unittest.mock import MagicMock, patch

import pytest

from core.errors import (
    FailureDisposition,
    IntegrationAuthenticationError,
    IntegrationConfigurationError,
    IntegrationContext,
    IntegrationContractError,
    IntegrationRateLimitedError,
    IntegrationRetryBudgetExceededError,
    IntegrationUnavailableError,
    IntegrationUntrustedNextLinkError,
    RetryBudget,
)
from core.integrations.intune import IntuneClient, _get_token, _graph_get_paginated

CONTEXT = IntegrationContext(
    provider="microsoft-graph",
    operation="devices.list",
    tenant_id=17,
    actor_id=23,
    request_id="request-123",
)


def response(status_code, *, json_data=None, headers=None):
    result = MagicMock()
    result.status_code = status_code
    result.headers = headers or {}
    result.json.return_value = json_data
    return result


class IntegrationErrorContractTests(TestCase):
    def test_error_has_orthogonal_disposition_and_safe_user_message(self):
        error = IntegrationAuthenticationError(context=CONTEXT, status_code=401)

        assert error.disposition is FailureDisposition.TERMINAL
        assert error.user_visible is True
        assert str(error) == error.user_message
        assert "secret" not in str(error).lower()
        assert error.log_extra() == {
            "integration": {
                "provider": "microsoft-graph",
                "operation": "devices.list",
                "tenant_id": 17,
                "actor_id": 23,
                "request_id": "request-123",
                "error_code": "integration.authentication",
                "disposition": "terminal",
                "retry_exhausted": False,
                "status_code": 401,
            }
        }

    def test_rate_limit_signal_is_internal_until_the_boundary_exhausts_its_budget(self):
        error = IntegrationRateLimitedError(context=CONTEXT, status_code=429)

        assert error.disposition is FailureDisposition.RETRYABLE
        assert error.user_visible is False

    def test_retry_budget_clamps_provider_delay_and_bounds_attempts(self):
        budget = RetryBudget(max_attempts=2, max_elapsed_seconds=20, max_delay_seconds=5)

        assert budget.next_delay(60, now=0) == 5
        assert budget.next_delay(3, now=5) == 3
        assert budget.next_delay(1, now=10) is None

    def test_retry_budget_stops_when_wall_clock_budget_is_exhausted(self):
        budget = RetryBudget(max_attempts=10, max_elapsed_seconds=20, max_delay_seconds=5)

        assert budget.next_delay(1, now=0) == 1
        assert budget.next_delay(1, now=20) is None


class IntuneTransportContractTests(TestCase):
    @patch("core.integrations.intune.requests.post")
    def test_token_authentication_failure_is_typed_and_secret_free(self, mock_post):
        mock_post.return_value = response(
            401,
            json_data={"error": "invalid_client", "client_secret": "do-not-leak"},
        )

        with pytest.raises(IntegrationAuthenticationError) as raised:
            _get_token("azure-tenant", "client-id", "client-secret", context=CONTEXT)

        error = raised.value
        assert error.disposition is FailureDisposition.TERMINAL
        assert "client-secret" not in str(error)
        assert "do-not-leak" not in str(error)

    @patch("core.integrations.intune.requests.post")
    def test_token_tls_failure_is_terminal_and_typed(self, mock_post):
        import requests

        mock_post.side_effect = requests.exceptions.SSLError("certificate detail")

        with pytest.raises(IntegrationConfigurationError) as raised:
            _get_token("azure-tenant", "client-id", "client-secret", context=CONTEXT)

        assert raised.value.disposition is FailureDisposition.TERMINAL
        assert raised.value.cause_type == "SSLError"
        assert "certificate detail" not in str(raised.value)

    @patch("core.integrations.intune.requests.get")
    def test_graph_tls_failure_is_terminal_and_typed(self, mock_get):
        import requests

        mock_get.side_effect = requests.exceptions.SSLError("certificate detail")

        with pytest.raises(IntegrationConfigurationError) as raised:
            _graph_get_paginated(
                "https://graph.microsoft.com/v1.0/devices",
                {},
                context=CONTEXT,
            )

        assert raised.value.disposition is FailureDisposition.TERMINAL
        assert raised.value.cause_type == "SSLError"

    @patch("core.integrations.intune.requests.post")
    def test_token_success_payload_is_validated(self, mock_post):
        mock_post.return_value = response(200, json_data={"not_access_token": "missing"})

        with pytest.raises(IntegrationContractError):
            _get_token("azure-tenant", "client-id", "client-secret", context=CONTEXT)

    @patch("core.integrations.intune.requests.get")
    def test_graph_url_allowlist_rejects_http_and_suffix_hosts(self, mock_get):
        for url in (
            "http://graph.microsoft.com/v1.0/devices",
            "https://graph.microsoft.com.evil.com/v1.0/devices",
        ):
            with self.subTest(url=url), pytest.raises(IntegrationUntrustedNextLinkError):
                _graph_get_paginated(url, {"Authorization": "Bearer secret"}, context=CONTEXT)

        mock_get.assert_not_called()

    @patch("core.integrations.intune.requests.get")
    def test_graph_forbidden_is_terminal_without_response_payload(self, mock_get):
        mock_get.return_value = response(403, json_data={"error": {"message": "sensitive provider detail"}})
        with pytest.raises(IntegrationAuthenticationError) as raised:
            _graph_get_paginated(
                "https://graph.microsoft.com/v1.0/devices", {"Authorization": "Bearer secret"}, context=CONTEXT
            )

        assert raised.value.disposition is FailureDisposition.TERMINAL
        assert "sensitive provider detail" not in str(raised.value)
        assert "Bearer secret" not in str(raised.value)

    @patch("core.integrations.intune.requests.get")
    def test_graph_pagination_has_a_page_ceiling(self, mock_get):
        mock_get.return_value = response(
            200,
            json_data={
                "value": [],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/page2",
            },
        )

        with pytest.raises(IntegrationContractError):
            _graph_get_paginated(
                "https://graph.microsoft.com/v1.0/page1",
                {},
                context=CONTEXT,
                max_pages=1,
            )

        assert mock_get.call_count == 1

    @patch("core.integrations.intune.requests.get")
    def test_next_link_cannot_redirect_bearer_to_another_host(self, mock_get):
        mock_get.return_value = response(
            200,
            json_data={"value": [], "@odata.nextLink": "https://user:secret@evil.example/steal"},
        )

        with (
            patch("core.integrations.intune.logger.error") as log_error,
            pytest.raises(IntegrationUntrustedNextLinkError),
        ):
            _graph_get_paginated(
                "https://graph.microsoft.com/v1.0/devices",
                {"Authorization": "Bearer secret"},
                context=CONTEXT,
            )

        assert mock_get.call_count == 1
        assert "secret" not in str(log_error.call_args)
        assert log_error.call_args.kwargs["extra"]["integration"]["object_id"] == "evil.example"

    @patch("core.integrations.intune.time.sleep")
    @patch("core.integrations.intune.requests.get")
    def test_graph_rate_limit_is_bounded_and_clamped(self, mock_get, mock_sleep):
        mock_get.side_effect = [
            response(429, headers={"Retry-After": "3600"}),
            response(429, headers={"Retry-After": "3600"}),
            response(429, headers={"Retry-After": "3600"}),
        ]
        budget = RetryBudget(max_attempts=2, max_elapsed_seconds=60, max_delay_seconds=30)

        with pytest.raises(IntegrationRetryBudgetExceededError) as raised:
            _graph_get_paginated(
                "https://graph.microsoft.com/v1.0/devices",
                {"Authorization": "Bearer secret"},
                context=CONTEXT,
                budget=budget,
            )

        assert raised.value.disposition is FailureDisposition.RETRYABLE
        assert raised.value.retry_exhausted is True
        assert mock_get.call_count == 3
        assert [call.args[0] for call in mock_sleep.call_args_list] == [30, 30]

    @patch("core.integrations.intune.requests.get")
    def test_graph_transport_failure_is_retryable(self, mock_get):
        import requests

        mock_get.side_effect = requests.ConnectionError("Bearer secret and response payload")

        with pytest.raises(IntegrationUnavailableError) as raised:
            _graph_get_paginated("https://graph.microsoft.com/v1.0/devices", {}, context=CONTEXT)

        assert raised.value.disposition is FailureDisposition.RETRYABLE
        assert raised.value.cause_type == "ConnectionError"
        assert "Bearer secret" not in str(raised.value)

    @patch("core.integrations.intune.requests.get")
    def test_graph_rate_limit_error_type_remains_available_for_adapters(self, mock_get):
        mock_get.return_value = response(429, headers={"Retry-After": "not-a-number"})
        budget = RetryBudget(max_attempts=0, max_elapsed_seconds=60, max_delay_seconds=30)

        with pytest.raises(IntegrationRateLimitedError) as raised:
            _graph_get_paginated(
                "https://graph.microsoft.com/v1.0/devices",
                {},
                context=CONTEXT,
                budget=budget,
            )

        assert raised.value.disposition is FailureDisposition.RETRYABLE
        assert raised.value.user_visible is False

    @patch("core.integrations.intune.requests.post")
    def test_token_rate_limit_is_retryable_and_redacts_retry_header(self, mock_post):
        mock_post.return_value = response(429, headers={"Retry-After": "3600"})

        with pytest.raises(IntegrationRateLimitedError) as raised:
            _get_token("azure-tenant", "client-id", "client-secret", context=CONTEXT)

        assert raised.value.disposition is FailureDisposition.RETRYABLE
        assert raised.value.user_visible is False
        assert raised.value.retry_after == 300

    @patch("core.integrations.intune.requests.get")
    def test_graph_server_failure_is_retryable(self, mock_get):
        mock_get.return_value = response(503, json_data={"error": {"message": "do-not-leak"}})

        with pytest.raises(IntegrationUnavailableError) as raised:
            _graph_get_paginated("https://graph.microsoft.com/v1.0/devices", {}, context=CONTEXT)

        assert raised.value.disposition is FailureDisposition.RETRYABLE
        assert "do-not-leak" not in str(raised.value)
        assert raised.value.__cause__ is None

    @patch("core.integrations.intune.time.sleep")
    @patch("core.integrations.intune.requests.get")
    def test_one_budget_bounds_rate_limits_across_multiple_calls(self, mock_get, mock_sleep):
        mock_get.side_effect = [
            response(429, headers={"Retry-After": "1"}),
            response(200, json_data={"value": []}),
            response(429, headers={"Retry-After": "1"}),
        ]
        budget = RetryBudget(max_attempts=1, max_elapsed_seconds=60, max_delay_seconds=30)

        assert _graph_get_paginated("https://graph.microsoft.com/v1.0/one", {}, context=CONTEXT, budget=budget) == []
        with pytest.raises(IntegrationRetryBudgetExceededError):
            _graph_get_paginated("https://graph.microsoft.com/v1.0/two", {}, context=CONTEXT, budget=budget)

        assert mock_get.call_count == 3
        assert [call.args[0] for call in mock_sleep.call_args_list] == [1]

    @patch("core.integrations.intune.requests.post")
    def test_token_cache_isolated_by_client_registration(self, mock_post):
        from core.integrations.intune import _TOKEN_CACHE

        _TOKEN_CACHE.clear()
        mock_post.side_effect = [
            response(200, json_data={"access_token": "token-a", "expires_in": 3600}),
            response(200, json_data={"access_token": "token-b", "expires_in": 3600}),
        ]

        assert _get_token("azure-tenant", "client-a", "secret-a", context=CONTEXT) == "token-a"
        assert _get_token("azure-tenant", "client-b", "secret-b", context=CONTEXT) == "token-b"
        assert mock_post.call_count == 2
        _TOKEN_CACHE.clear()

    @patch("core.integrations.intune._graph_get_paginated", return_value=[])
    @patch.object(IntuneClient, "_headers", return_value={})
    def test_device_id_is_quoted_before_graph_path_construction(self, mock_headers, mock_graph):
        client = IntuneClient("azure-tenant", "client-id", "client-secret", context=CONTEXT)

        client.get_detected_apps("../../users?select=mail")

        url = mock_graph.call_args.args[0]
        assert "/../" not in url
        assert "..%2F..%2F" in url

    @patch("core.integrations.intune._graph_get_paginated", return_value=[])
    @patch.object(IntuneClient, "_headers", return_value={})
    def test_client_uses_an_operation_scoped_budget(self, mock_headers, mock_graph):
        budgets = [RetryBudget(max_attempts=2), RetryBudget(max_attempts=2)]
        budget_factory = MagicMock(side_effect=budgets)
        client = IntuneClient(
            "azure-tenant",
            "client-id",
            "client-secret",
            context=CONTEXT,
            retry_budget_factory=budget_factory,
        )

        client.get_managed_devices()
        client.get_detected_apps("device-1")

        first_context = mock_graph.call_args_list[0].kwargs["context"]
        second_context = mock_graph.call_args_list[1].kwargs["context"]
        assert first_context.operation == "devices.list"
        assert second_context.operation == "device_apps.list"
        assert mock_graph.call_args_list[0].kwargs["budget"] is budgets[0]
        assert mock_graph.call_args_list[1].kwargs["budget"] is budgets[1]
        assert budget_factory.call_count == 2
        mock_headers.assert_called()

    @patch("core.integrations.intune._graph_get_paginated")
    @patch.object(IntuneClient, "_headers", return_value={})
    def test_graph_authentication_failure_invalidates_cached_token(self, mock_headers, mock_graph):
        from core.integrations.intune import _TOKEN_CACHE

        _TOKEN_CACHE[("azure-tenant", "client-id")] = {"token": "expired", "expires_at": 9999999999}
        mock_graph.side_effect = IntegrationAuthenticationError(context=CONTEXT, status_code=401)
        client = IntuneClient("azure-tenant", "client-id", "client-secret", context=CONTEXT)

        with pytest.raises(IntegrationAuthenticationError):
            client.get_managed_devices()

        assert ("azure-tenant", "client-id") not in _TOKEN_CACHE
