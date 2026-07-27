from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class ValidationAppError(AppError):
    status_code = 422
    code = "validation_error"


def error_envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    headers = {}
    if isinstance(exc, RateLimitedError):
        headers["Retry-After"] = "60"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(exc.code, exc.message),
        headers=headers,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    import logging

    logging.getLogger("app.errors").exception("unhandled_error")
    return JSONResponse(
        status_code=500,
        content=error_envelope("internal_error", "An unexpected error occurred"),
    )
