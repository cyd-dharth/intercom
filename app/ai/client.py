import logging
import time
import uuid
from typing import TypeVar

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.db import get_pool
from app.repositories import ai as ai_repo
from app.repositories import workspaces as workspaces_repo
from app.ai.budget import is_budget_exceeded

logger = logging.getLogger("app.ai.client")

ModelT = TypeVar("ModelT", bound=BaseModel)

# Price per model, dollars per million tokens: (input, output). Gemini 2.0 Flash and
# Flash-Lite pricing, hardcoded since the provider exposes no pricing API and this is
# a demo cost signal, not a billing system.
_PRICE_PER_MILLION_TOKENS_USD = {
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    "text-embedding-004": (0.0, 0.0),
    "gemini-embedding-2": (0.0, 0.0),
}

_CIRCUIT_FAILURE_THRESHOLD = 5
_CIRCUIT_OPEN_SECONDS = 60

_consecutive_failures = 0
_circuit_opened_at: float | None = None

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _cost_micros(model: str, input_tokens: int, output_tokens: int) -> int:
    input_price, output_price = _PRICE_PER_MILLION_TOKENS_USD.get(model, (0.0, 0.0))
    dollars = (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
    return int(dollars * 1_000_000)


def _circuit_is_open() -> bool:
    global _circuit_opened_at
    if _circuit_opened_at is None:
        return False
    if time.monotonic() - _circuit_opened_at >= _CIRCUIT_OPEN_SECONDS:
        _circuit_opened_at = None
        return False
    return True


def _record_success() -> None:
    global _consecutive_failures, _circuit_opened_at
    _consecutive_failures = 0
    _circuit_opened_at = None


def _record_failure() -> None:
    global _consecutive_failures, _circuit_opened_at
    _consecutive_failures += 1
    if _consecutive_failures >= _CIRCUIT_FAILURE_THRESHOLD:
        _circuit_opened_at = time.monotonic()


def _strip_defaults(node: object) -> object:
    """Gemini's structured-output schema rejects the `default` keyword. Pydantic
    models with `= []`-style optional fields (as CLAUDE.md's ConversationSummary
    requires) emit it, so strip it recursively before handing the JSON schema to
    the API, rather than changing the schema's field defaults."""
    if isinstance(node, dict):
        return {k: _strip_defaults(v) for k, v in node.items() if k != "default"}
    if isinstance(node, list):
        return [_strip_defaults(v) for v in node]
    return node


async def _call_model(prompt: str, schema: type[BaseModel], model: str, timeout_seconds: int) -> tuple[str, int, int]:
    """Raises on any failure. Returns (raw_text, input_tokens, output_tokens)."""
    client = _get_client()
    response_schema = _strip_defaults(schema.model_json_schema())
    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            http_options=genai_types.HttpOptions(timeout=timeout_seconds * 1000),
        ),
    )
    usage = response.usage_metadata
    input_tokens = usage.prompt_token_count if usage else 0
    output_tokens = usage.candidates_token_count if usage else 0
    return response.text or "", input_tokens, output_tokens


async def generate_json(
    *,
    prompt: str,
    schema: type[ModelT],
    workspace_id: uuid.UUID,
    kind: str,
    prompt_version: str,
) -> tuple[ModelT | None, str]:
    """Provider agnostic structured generation per CLAUDE.md section 10.1. Never raises;
    always returns (parsed_or_none, status). Always writes one ai_calls row."""
    if not settings.ai_enabled():
        return None, "ai_disabled"

    pool = get_pool()
    async with pool.acquire() as conn:
        workspace = await workspaces_repo.get_workspace_by_id(conn, workspace_id)
        daily_budget_cents = workspace["ai_daily_budget_cents"] if workspace else settings.ai_daily_budget_cents
        if await is_budget_exceeded(conn, workspace_id, daily_budget_cents):
            return None, "budget_exceeded"

    if _circuit_is_open():
        return None, "circuit_open"

    start = time.monotonic()
    model = settings.llm_model_primary
    status = "ok"
    error_text: str | None = None
    parsed: ModelT | None = None
    input_tokens = 0
    output_tokens = 0

    try:
        raw_text, input_tokens, output_tokens = await _call_model(prompt, schema, model, settings.llm_timeout_seconds)
        try:
            parsed = schema.model_validate_json(raw_text)
            _record_success()
        except ValidationError as exc:
            status = "schema_retry"
            repair_prompt = f"{prompt}\n\nYour previous response failed validation with this error:\n{exc}\n\nReturn only corrected JSON matching the schema."
            raw_text, retry_input, retry_output = await _call_model(repair_prompt, schema, model, settings.llm_timeout_seconds)
            input_tokens += retry_input
            output_tokens += retry_output
            try:
                parsed = schema.model_validate_json(raw_text)
                _record_success()
            except ValidationError as retry_exc:
                error_text = str(retry_exc)
                parsed = None
                _record_failure()
    except Exception:
        model = settings.llm_model_fallback
        try:
            raw_text, input_tokens, output_tokens = await _call_model(prompt, schema, model, 5)
            parsed = schema.model_validate_json(raw_text)
            status = "fallback"
            _record_success()
        except Exception as fallback_exc:
            status = "error"
            error_text = str(fallback_exc)
            model = settings.llm_model_primary
            _record_failure()

    latency_ms = int((time.monotonic() - start) * 1000)
    cost_micros = _cost_micros(model, input_tokens, output_tokens)

    async with pool.acquire() as conn:
        await ai_repo.record_ai_call(
            conn,
            workspace_id,
            kind,
            model,
            prompt_version,
            input_tokens,
            output_tokens,
            cost_micros,
            latency_ms,
            status,
            error_text,
        )

    if parsed is None:
        return None, status if status != "ok" else "error"
    return parsed, status


async def embed_text(text: str, workspace_id: uuid.UUID | None = None) -> list[float] | None:
    """Returns an embedding vector, or None on any failure (caller degrades to lexical
    only search per section 10.3). Does not consume the daily LLM budget or open the
    generation circuit breaker; embeddings are cheap and on a separate failure path."""
    if not settings.ai_enabled():
        return None
    start = time.monotonic()
    status = "ok"
    error_text: str | None = None
    embedding: list[float] | None = None
    try:
        client = _get_client()
        response = await client.aio.models.embed_content(
            model=settings.embedding_model,
            contents=text,
            config=genai_types.EmbedContentConfig(output_dimensionality=settings.embedding_dimensions),
        )
        embedding = list(response.embeddings[0].values)
    except Exception as exc:
        status = "error"
        error_text = str(exc)
        logger.warning("embedding failed", extra={"extra_fields": {"error": error_text[:500]}})

    latency_ms = int((time.monotonic() - start) * 1000)
    pool = get_pool()
    async with pool.acquire() as conn:
        await ai_repo.record_ai_call(
            conn,
            workspace_id,
            "embed",
            settings.embedding_model,
            None,
            None,
            None,
            0,
            latency_ms,
            status,
            error_text,
        )
    return embedding
