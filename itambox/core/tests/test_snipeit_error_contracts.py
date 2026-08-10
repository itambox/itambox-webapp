from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.errors import (
    IntegrationAuthenticationError,
    IntegrationConfigurationError,
    IntegrationContext,
    IntegrationContractError,
    IntegrationNotFoundError,
    IntegrationRateLimitedError,
    IntegrationRequestError,
    IntegrationRetryBudgetExceededError,
    IntegrationUnavailableError,
    RetryBudget,
)
from core.importers.snipeit import SnipeITClient
from core.importers.snipeit.contracts import StageReporter, StageResult


def _response(status_code=200, *, payload=None, headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = {} if payload is None else payload
    return response


def _client(*, budget=None):
    client = SnipeITClient(
        "https://url-user:url-password@snipe.example?api_key=url-secret",
        "bearer-secret",
        context=IntegrationContext(
            provider="snipe-it",
            operation="import",
            tenant_id=7,
            actor_id=11,
            request_id="request-13",
        ),
        retry_budget_factory=(lambda: budget) if budget else None,
    )
    client._session.get = MagicMock()
    return client


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, IntegrationAuthenticationError),
        (403, IntegrationAuthenticationError),
        (404, IntegrationNotFoundError),
        (400, IntegrationRequestError),
        (500, IntegrationUnavailableError),
        (503, IntegrationUnavailableError),
    ],
)
def test_http_failures_have_typed_dispositions_and_safe_messages(status_code, error_type):
    client = _client()
    client._session.get.return_value = _response(status_code)

    with pytest.raises(error_type) as raised:
        client.get_detail("/api/v1/hardware?token=query-secret")

    error = raised.value
    assert error.context.operation == "detail.get"
    assert error.context.tenant_id == 7
    assert error.status_code == status_code
    assert "snipe.example" not in str(error)
    assert "bearer-secret" not in str(error)
    assert "query-secret" not in str(error)


@pytest.mark.parametrize(
    ("request_error", "error_type"),
    [
        (requests.exceptions.InvalidURL("url-secret"), IntegrationConfigurationError),
        (requests.exceptions.SSLError("certificate bearer-secret"), IntegrationConfigurationError),
        (requests.exceptions.Timeout("query-secret"), IntegrationUnavailableError),
        (requests.exceptions.ConnectionError("url-secret"), IntegrationUnavailableError),
    ],
)
def test_transport_failures_expose_only_the_exception_type(request_error, error_type):
    client = _client()
    client._session.get.side_effect = request_error

    with pytest.raises(error_type) as raised:
        client.get_detail("/api/v1/hardware")

    error = raised.value
    assert error.cause_type == type(request_error).__name__
    assert error.__cause__ is None
    assert "secret" not in str(error)


def test_rate_limit_retries_with_a_bounded_budget_and_structured_log(caplog):
    budget = RetryBudget(max_attempts=1, max_elapsed_seconds=10, max_delay_seconds=0)
    client = _client(budget=budget)
    client._session.get.side_effect = [
        _response(429, headers={"Retry-After": "not-a-number"}),
        _response(429, headers={"Retry-After": "3000"}),
    ]

    with (
        patch("core.importers.snipeit.client.time.sleep") as sleep,
        pytest.raises(IntegrationRetryBudgetExceededError) as raised,
    ):
        client.get_detail("/api/v1/hardware?token=query-secret")

    assert raised.value.retry_exhausted is True
    assert raised.value.disposition.value == "retryable"
    assert client._session.get.call_count == 2
    sleep.assert_called_once_with(0.0)
    record = next(record for record in caplog.records if hasattr(record, "integration"))
    assert record.integration["tenant_id"] == 7
    assert record.integration["actor_id"] == 11
    assert record.integration["request_id"] == "request-13"
    assert "secret" not in caplog.text


def test_zero_retry_budget_surfaces_rate_limit_without_sleeping():
    budget = RetryBudget(max_attempts=0)
    client = _client(budget=budget)
    client._session.get.return_value = _response(429)

    with patch("core.importers.snipeit.client.time.sleep") as sleep, pytest.raises(IntegrationRateLimitedError):
        client.get_detail("/api/v1/hardware")

    sleep.assert_not_called()


@pytest.mark.parametrize("retry_after", ["nan", "inf", "-inf"])
def test_non_finite_retry_after_is_rejected(retry_after):
    assert SnipeITClient._parse_retry_after({"Retry-After": retry_after}) is None


@pytest.mark.parametrize("payload", [[], {"rows": "not-a-list", "total": 1}, {"rows": [], "total": "one"}])
def test_collection_response_shape_is_a_typed_contract_error(payload):
    client = _client()
    client._session.get.return_value = _response(payload=payload)

    with pytest.raises(IntegrationContractError):
        list(client.get_all("/api/v1/hardware"))


def test_invalid_json_is_a_typed_contract_error_without_payload_leak():
    client = _client()
    response = _response()
    response.json.side_effect = ValueError("response-secret")
    client._session.get.return_value = response

    with pytest.raises(IntegrationContractError) as raised:
        client.get_detail("/api/v1/hardware")

    assert raised.value.cause_type == "ValueError"
    assert raised.value.__cause__ is None
    assert "response-secret" not in str(raised.value)


def test_client_disables_redirects_for_bearer_requests():
    client = _client()
    client._session.get.return_value = _response(payload={"id": 1})

    client.get_detail("/api/v1/hardware/1")

    assert client._session.get.call_args.kwargs["allow_redirects"] is False
    requested_url = client._session.get.call_args.args[0]
    assert "url-user" not in requested_url
    assert "url-password" not in requested_url
    assert "api_key" not in requested_url


def test_stage_reporter_row_failure_log_redacts_payload_identity_and_exception_text(caplog):
    tenant = MagicMock(pk=7)
    user = MagicMock(pk=11)
    stdout = StringIO()
    reporter = StageReporter(stdout=stdout, job=None, default_tenant=tenant, user=user)
    result = StageResult("assets")

    reporter.row_failure(
        result,
        "assets.persist",
        RuntimeError("asset-tag SECRET-ASSET customer@example.test bearer-secret"),
    )

    assert result.counts.failed == 1
    record = next(record for record in caplog.records if hasattr(record, "integration"))
    assert record.integration["operation"] == "assets.persist"
    assert record.integration["tenant_id"] == 7
    assert record.integration["actor_id"] == 11
    combined = caplog.text + stdout.getvalue()
    assert "SECRET-ASSET" not in combined
    assert "customer@example.test" not in combined
    assert "bearer-secret" not in combined
    assert "424242" not in combined


def test_stage_reporter_row_failure_supports_logging_without_optional_sinks(caplog):
    reporter = StageReporter(default_tenant=MagicMock(pk=7), user=MagicMock(pk=11))
    result = StageResult("assets")

    reporter.row_failure(result, "assets.persist", RuntimeError("bearer-secret"))

    record = next(record for record in caplog.records if hasattr(record, "integration"))
    assert record.integration["operation"] == "assets.persist"
    assert "bearer-secret" not in caplog.text
