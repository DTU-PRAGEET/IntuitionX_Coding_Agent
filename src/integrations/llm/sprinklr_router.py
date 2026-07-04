"""Sprinklr IntuitionX LLM router — OpenAI-compatible /chat-completion endpoint."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

_SPRINKLR_FORBIDDEN_HEADERS = frozenset(
    {"authorization", "content-length", "api-key", "x-api-key"}
)


class SprinklrRouterError(Exception):
    """Raised when the Sprinklr LLM router rejects or returns an error response."""

    def __init__(self, status_code: int, detail: str) -> None:
        """Initialize SprinklrRouterError with the provided arguments."""
        self.status_code = status_code
        super().__init__(detail)


class SprinklrRouterTransport(httpx.BaseTransport):
    """Rewrite OpenAI /chat/completions calls to Sprinklr /chat-completion."""

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        *,
        extra_body: dict[str, Any],
    ) -> None:
        """Initialize SprinklrRouterTransport with the provided arguments."""
        self._transport = transport or httpx.HTTPTransport()
        self._extra_body = extra_body

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Handle Request for SprinklrRouterTransport."""
        url_str = str(request.url).replace("/chat/completions", "/chat-completion")
        if not url_str.endswith("/chat-completion"):
            raise SprinklrRouterError(
                0,
                f"Sprinklr transport expected /chat-completion path, got: {url_str}",
            )
        url = httpx.URL(url_str)

        body = json.loads(request.content) if request.content else {}
        body.update(self._extra_body)
        content = json.dumps(body).encode()

        headers = [
            (key, value)
            for key, value in request.headers.items()
            if key.lower() not in _SPRINKLR_FORBIDDEN_HEADERS
        ]

        rewritten = httpx.Request(request.method, url, headers=headers, content=content)
        try:
            response = self._transport.handle_request(rewritten)
            response.read()
        except Exception:
            raise

        if response.status_code >= 400:
            detail = _format_router_error(response)
            error_payload = {
                "error": {
                    "message": detail,
                    "type": "sprinklr_router_error",
                    "code": str(response.status_code),
                }
            }
            return httpx.Response(
                status_code=response.status_code,
                headers={"content-type": "application/json"},
                content=json.dumps(error_payload).encode(),
                request=rewritten,
            )

        return _normalize_openai_response(response, rewritten)


def _format_router_error(response: httpx.Response) -> str:
    """Format Router Error for this module."""
    text = response.text.strip()
    if response.status_code == 403:
        return (
            "Sprinklr router rejected request (403 Forbidden). "
            "Check VPN/network access, SPRINKLR_LLM_CLIENT_IDENTIFIER, "
            "SPRINKLR_LLM_PARTNER_ID, and that no Authorization header is sent."
        )
    if text.startswith("<!") or "<html" in text[:200].lower():
        return f"Sprinklr router HTTP {response.status_code}: non-JSON error page"
    try:
        payload = response.json()
        err = payload.get("error") or payload.get("message") or text[:500]
        return f"Sprinklr router HTTP {response.status_code}: {err}"
    except json.JSONDecodeError:
        return f"Sprinklr router HTTP {response.status_code}: {text[:500]}"


def _normalize_openai_response(
    response: httpx.Response,
    request: httpx.Request,
) -> httpx.Response:
    """Adapt Sprinklr response shape for LangChain OpenAI client parsing."""
    raw = response.content
    if not raw:
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=b"",
            request=request,
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=raw,
            request=request,
        )

    if payload.get("object") == "chat-completion":
        payload = dict(payload)
        payload["object"] = "chat.completion"

    return httpx.Response(
        status_code=response.status_code,
        headers=response.headers,
        content=json.dumps(payload).encode(),
        request=request,
    )


def _sprinklr_extra_body() -> dict[str, Any]:
    """Sprinklr Extra Body for this module."""
    tracking_feature = os.getenv("SPRINKLR_LLM_TRACKING_FEATURE", "SPRINKLR_JIRA_SOLVER")
    partner_id_raw = os.getenv("SPRINKLR_LLM_PARTNER_ID", "66006006")
    try:
        partner_id: int | str = int(partner_id_raw)
    except ValueError:
        partner_id = partner_id_raw

    extra: dict[str, Any] = {
        "client_identifier": os.getenv("SPRINKLR_LLM_CLIENT_IDENTIFIER", "backend-platform-dev"),
        "partner_id": partner_id,
        "tracking_params": {"feature": tracking_feature},
    }
    tracking_raw = os.getenv("SPRINKLR_LLM_TRACKING_PARAMS", "").strip()
    if tracking_raw:
        try:
            extra["tracking_params"] = json.loads(tracking_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "SPRINKLR_LLM_TRACKING_PARAMS must be valid JSON when set"
            ) from exc
    return extra


def _sprinklr_http_client(transport: SprinklrRouterTransport, timeout: float) -> httpx.Client:
    """Build httpx client with optional corporate proxy from environment."""
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    return httpx.Client(
        transport=transport,
        timeout=timeout,
        proxy=proxy or None,
        trust_env=True,
    )


def create_sprinklr_chat_model(model_name: str) -> BaseChatModel:
    """Return a LangChain chat model backed by the Sprinklr LLM router."""
    base_url = os.getenv(
        "SPRINKLR_LLM_BASE_URL",
        "http://qa6-intuitionx-llm-router-v2.sprinklr.com",
    ).rstrip("/")
    api_key = os.getenv("SPRINKLR_LLM_API_KEY", "not-used")
    timeout = float(os.getenv("SPRINKLR_LLM_TIMEOUT", "120"))

    transport = SprinklrRouterTransport(extra_body=_sprinklr_extra_body())
    http_client = _sprinklr_http_client(transport, timeout)

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        http_socket_options=(),
    )


def probe_sprinklr_router(
    *,
    model_name: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Send a minimal ping to verify Sprinklr router connectivity."""
    base_url = os.getenv(
        "SPRINKLR_LLM_BASE_URL",
        "http://qa6-intuitionx-llm-router-v2.sprinklr.com",
    ).rstrip("/")
    model = model_name or os.getenv("SPRINKLR_LLM_MODEL", "gpt-5.2-2025-12-11")
    req_timeout = timeout if timeout is not None else float(os.getenv("SPRINKLR_LLM_TIMEOUT", "30"))

    transport = SprinklrRouterTransport(
        transport=httpx.HTTPTransport(),
        extra_body=_sprinklr_extra_body(),
    )
    request = httpx.Request(
        "POST",
        f"{base_url}/chat/completions",
        content=json.dumps(
            {"model": model, "messages": [{"role": "user", "content": "ping"}]}
        ).encode(),
        headers={
            "content-type": "application/json",
            "authorization": "Bearer should-be-stripped",
        },
    )
    with _sprinklr_http_client(transport, req_timeout) as _client:
        response = transport.handle_request(request)
    if response.status_code >= 400:
        raise SprinklrRouterError(response.status_code, _format_router_error(response))
    return response.json()
