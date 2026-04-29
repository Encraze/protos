from __future__ import annotations

from typing import Any


class GatewayError(Exception):
    error_type: str = "internal_error"
    http_status: int = 500
    code: str | None = None
    param: str | None = None

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(GatewayError):
    error_type = "authentication_error"
    http_status = 401


class InsufficientScopeError(GatewayError):
    error_type = "insufficient_scope"
    http_status = 403

    def __init__(self, *, required: str) -> None:
        super().__init__(f"token lacks required scope: {required}")
        self.code = required


class InvalidRequestError(GatewayError):
    error_type = "invalid_request_error"
    http_status = 400


class UnsupportedModelError(GatewayError):
    error_type = "unsupported_model"
    http_status = 400
    param = "model"

    def __init__(self, model: str, *, supported: list[str]) -> None:
        super().__init__(
            f"model '{model}' is not supported; supported models: {', '.join(supported)}"
        )
        self.supported = supported


class ProviderKeyMissingError(GatewayError):
    error_type = "provider_key_missing"
    http_status = 400

    def __init__(self, *, provider: str) -> None:
        super().__init__(f"no active provider key for provider '{provider}'")
        self.code = provider


class UpstreamRequestError(GatewayError):
    error_type = "upstream_request_error"
    http_status = 502


class UpstreamUnavailableError(GatewayError):
    error_type = "upstream_unavailable"
    http_status = 502


class GatewayTimeoutError(GatewayError):
    error_type = "gateway_timeout"
    http_status = 504


def error_envelope(exc: BaseException) -> tuple[dict[str, Any], int]:
    if isinstance(exc, GatewayError):
        return (
            {
                "error": {
                    "message": exc.message,
                    "type": exc.error_type,
                    "code": exc.code,
                    "param": exc.param,
                }
            },
            exc.http_status,
        )
    return (
        {
            "error": {
                "message": "internal error",
                "type": "internal_error",
                "code": None,
                "param": None,
            }
        },
        500,
    )
