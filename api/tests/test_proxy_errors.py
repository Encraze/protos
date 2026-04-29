import pytest

from app.proxy.errors import (
    AuthenticationError,
    GatewayTimeoutError,
    InsufficientScopeError,
    InvalidRequestError,
    ProviderKeyMissingError,
    UnsupportedModelError,
    UpstreamRequestError,
    UpstreamUnavailableError,
    error_envelope,
)


def test_envelope_shape_for_authentication_error():
    err = AuthenticationError("missing bearer token")
    body, status_code = error_envelope(err)
    assert status_code == 401
    assert body == {
        "error": {
            "message": "missing bearer token",
            "type": "authentication_error",
            "code": None,
            "param": None,
        }
    }


def test_envelope_shape_for_unsupported_model_includes_supported_list():
    err = UnsupportedModelError("foo-bar", supported=["gpt-4o", "claude-opus-4-7"])
    body, status_code = error_envelope(err)
    assert status_code == 400
    assert body["error"]["type"] == "unsupported_model"
    assert "foo-bar" in body["error"]["message"]
    assert body["error"]["param"] == "model"


@pytest.mark.parametrize(
    "exc_cls,expected_type,expected_status",
    [
        (lambda: InsufficientScopeError(required="chat:write"), "insufficient_scope", 403),
        (lambda: ProviderKeyMissingError(provider="openai"), "provider_key_missing", 400),
        (lambda: InvalidRequestError("bad body"), "invalid_request_error", 400),
        (lambda: UpstreamRequestError("upstream said 401"), "upstream_request_error", 502),
        (lambda: UpstreamUnavailableError("retries exhausted"), "upstream_unavailable", 502),
        (lambda: GatewayTimeoutError("upstream timed out"), "gateway_timeout", 504),
    ],
)
def test_envelope_for_typed_errors(exc_cls, expected_type, expected_status):
    body, status_code = error_envelope(exc_cls())
    assert status_code == expected_status
    assert body["error"]["type"] == expected_type


def test_envelope_for_unknown_exception_is_internal_error():
    body, status_code = error_envelope(RuntimeError("oops"))
    assert status_code == 500
    assert body["error"]["type"] == "internal_error"
    assert body["error"]["message"] == "internal error"
