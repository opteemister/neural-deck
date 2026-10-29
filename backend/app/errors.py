"""Application errors and the single error response format."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .schemas.common import ErrorBody, ErrorEnvelope

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})
        self.retryable = retryable


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def error_response(
    request: Request,
    *,
    code: str,
    message: str,
    status_code: int,
    details: Mapping[str, Any] | None = None,
    retryable: bool = False,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            details=dict(details or {}),
            request_id=request_id(request),
            retryable=retryable,
        )
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def openai_error_response(
    *,
    message: str,
    status_code: int,
    error_type: str = "invalid_request_error",
    code: str | None = None,
) -> JSONResponse:
    """Return the wire error object expected by OpenAI-compatible clients."""

    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": None,
                "code": code,
            }
        },
    )


def _is_openai_route(request: Request) -> bool:
    return request.url.path == "/v1" or request.url.path.startswith("/v1/")


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "Ошибка приложения: method=%s path=%s status=%s code=%s message=%s request_id=%s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.code,
        exc.message,
        request_id(request),
    )
    if _is_openai_route(request):
        return openai_error_response(
            message=exc.message,
            status_code=exc.status_code,
            error_type=exc.code,
            code=exc.code,
        )
    return error_response(
        request,
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
        retryable=exc.retryable,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Report the field and reason, excluding submitted values (commands and
    # environment variables may contain secrets).
    errors = [
        {"loc": error["loc"], "type": error["type"], "msg": error["msg"]}
        for error in exc.errors()
    ]
    logger.warning(
        "Ошибка валидации запроса: method=%s path=%s request_id=%s errors=%s",
        request.method,
        request.url.path,
        request_id(request),
        errors,
    )
    if _is_openai_route(request):
        return openai_error_response(
            message="Invalid request body",
            status_code=400 if request.url.path.startswith("/v1/images/") else 422,
            error_type="invalid_request_error",
            code="invalid_request_error",
        )
    return error_response(
        request,
        code="validation_error",
        message="Ошибка проверки входных данных",
        status_code=400 if request.url.path.startswith("/api/proxy/images/") else 422,
        details={"errors": jsonable_encoder(errors)},
    )


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "http_error"
    message = str(exc.detail) if isinstance(exc.detail, str) else "Ошибка HTTP-запроса"
    logger.warning(
        "HTTP-ошибка: method=%s path=%s status=%s message=%s request_id=%s",
        request.method,
        request.url.path,
        exc.status_code,
        message,
        request_id(request),
    )
    if _is_openai_route(request):
        return openai_error_response(
            message=message,
            status_code=exc.status_code,
            error_type="invalid_request_error" if exc.status_code < 500 else "server_error",
            code=code,
        )
    return error_response(
        request,
        code=code,
        message=message,
        status_code=exc.status_code,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Внутренняя ошибка запроса: method=%s path=%s request_id=%s",
        request.method,
        request.url.path,
        request_id(request),
    )
    if _is_openai_route(request):
        return openai_error_response(
            message="Internal server error",
            status_code=500,
            error_type="server_error",
            code="internal_error",
        )
    return error_response(
        request,
        code="internal_error",
        message="Внутренняя ошибка сервиса",
        status_code=500,
    )
