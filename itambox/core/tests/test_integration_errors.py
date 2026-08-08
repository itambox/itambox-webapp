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
    RetryBudget,
)
from core.integrations.intune import _get_token, _graph_get_paginated

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
                "status_code": 401,
            }
        }

    def test_rate_limit_signal_is_internal_until_the_boundary_exhausts_its_budget(self):
        error = IntegrationRateLimitedError(context=CONTEXT, status_code=429)

        assert error.disposition is FailureDisposition.RETRYABLE
        assert error.user_visible is False

    def test_retry_budget_clamps_provider_delay_and_bounds_attempts(self):
        budget = RetryBudget(max_attempts=2, max_elapsed_seconds=20, max_delay_seconds=5)

        assert budget.next_delay(60, elapsed_seconds=0) == 5
        assert budget.next_delay(3, elapsed_seconds=5) == 3
        assert budget.next_delay(1, elapsed_seconds=10) is None

    def test_retry_budget_stops_when_wall_clock_budget_is_exhausted(self):
        budget = RetryBudget(max_attempts=10, max_elapsed_seconds=20, max_delay_seconds=5)

        assert budget.next_delay(1, elapsed_seconds=20) is None


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
    def test_token_success_payload_is_validated(self, mock_post):
        mock_post.return_value = response(200, json_data={"not_access_token": "missing"})

        with pytest.raises(IntegrationContractError):
            _get_token("azure-tenant", "client-id", "client-secret", context=CONTEXT)

    @patch("core.integrations.intune.requests.get")
    def test_graph_forbidden_is_terminal_without_response_payload(self, mock_get):
        mock_get.return_value = response(403, json_data={"error": {"message": "sensitive provider detail"}})

        with pytest.raises(IntegrationAuthenticationError) as raised:
            _graph_get_paginated("https://graph.example/devices", {"Authorization": "Bearer secret"}, context=CONTEXT)

        assert raised.value.disposition is FailureDisposition.TERMINAL
        assert "sensitive provider detail" not in str(raised.value)
        assert "Bearer secret" not in str(raised.value)

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
                "https://graph.example/devices",
                {"Authorization": "Bearer secret"},
                context=CONTEXT,
                budget=budget,
            )

        assert raised.value.disposition is FailureDisposition.TERMINAL
        assert mock_get.call_count == 3
        assert [call.args[0] for call in mock_sleep.call_args_list] == [30, 30]

    @patch("core.integrations.intune.requests.get")
    def test_graph_transport_failure_is_retryable(self, mock_get):
        import requests

        mock_get.side_effect = requests.ConnectionError("Bearer secret and response payload")

        with pytest.raises(IntegrationUnavailableError) as raised:
            _graph_get_paginated("https://graph.example/devices", {}, context=CONTEXT)

        assert raised.value.disposition is FailureDisposition.RETRYABLE
        assert "Bearer secret" not in str(raised.value)

    @patch("core.integrations.intune.requests.get")
    def test_graph_rate_limit_error_type_remains_available_for_adapters(self, mock_get):
        mock_get.return_value = response(429, headers={"Retry-After": "not-a-number"})
        budget = RetryBudget(max_attempts=0, max_elapsed_seconds=60, max_delay_seconds=30)

        with pytest.raises(IntegrationRateLimitedError) as raised:
            _graph_get_paginated(
                "https://graph.example/devices",
                {},
                context=CONTEXT,
                budget=budget,
            )

        assert raised.value.disposition is FailureDisposition.RETRYABLE
