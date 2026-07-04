"""Tests for Sprinklr IntuitionX LLM router integration."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from langchain_core.messages import HumanMessage

from src.integrations.llm.sprinklr_router import (
    SprinklrRouterError,
    SprinklrRouterTransport,
    create_sprinklr_chat_model,
)


class _CaptureTransport(httpx.BaseTransport):
    """Test httpx transport that records the last request and returns a fake 200 completion."""
    def __init__(self) -> None:
        """Initialize _CaptureTransport with the provided arguments."""
        self.last_request: httpx.Request | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Handle Request for _CaptureTransport."""
        self.last_request = request
        payload = {
            "object": "chat-completion",
            "choices": [
                {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "success": True,
        }
        return httpx.Response(200, json=payload, request=request)


class _ErrorTransport(httpx.BaseTransport):
    """Test httpx transport that returns a configurable error status/body for router failures."""
    def __init__(self, status_code: int, body: str) -> None:
        """Initialize _ErrorTransport with the provided arguments."""
        self.status_code = status_code
        self.body = body

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Handle Request for _ErrorTransport."""
        return httpx.Response(self.status_code, content=self.body.encode(), request=request)


def test_sprinklr_transport_rewrites_path_and_injects_body():
    """Test Sprinklr Transport Rewrites Path And Injects Body for this module."""
    capture = _CaptureTransport()
    transport = SprinklrRouterTransport(
        transport=capture,
        extra_body={
            "client_identifier": "backend-platform-dev",
            "partner_id": 66006006,
            "tracking_params": {"feature": "SPRINKLR_JIRA_SOLVER"},
        },
    )
    original = httpx.Request(
        "POST",
        "http://example.com/chat/completions",
        content=json.dumps({"model": "gpt-5.2-2025-12-11", "messages": []}).encode(),
        headers={"content-type": "application/json", "content-length": "42"},
    )

    transport.handle_request(original)

    assert capture.last_request is not None
    assert str(capture.last_request.url) == "http://example.com/chat-completion"
    body = json.loads(capture.last_request.content)
    assert body["client_identifier"] == "backend-platform-dev"
    assert body["partner_id"] == 66006006
    assert body["tracking_params"] == {"feature": "SPRINKLR_JIRA_SOLVER"}
    assert body["model"] == "gpt-5.2-2025-12-11"


def test_sprinklr_transport_strips_authorization_header():
    """Test Sprinklr Transport Strips Authorization Header for this module."""
    capture = _CaptureTransport()
    transport = SprinklrRouterTransport(
        transport=capture,
        extra_body={"client_identifier": "backend-platform-dev"},
    )
    original = httpx.Request(
        "POST",
        "http://example.com/chat/completions",
        content=json.dumps({"model": "gpt-4o", "messages": []}).encode(),
        headers={
            "content-type": "application/json",
            "authorization": "Bearer sprinklr",
            "api-key": "secret",
        },
    )

    transport.handle_request(original)

    assert capture.last_request is not None
    header_names = {k.lower() for k, _ in capture.last_request.headers.items()}
    assert "authorization" not in header_names
    assert "api-key" not in header_names
    assert "content-type" in header_names


def test_sprinklr_transport_curl_contract_body():
    """Test Sprinklr Transport Curl Contract Body for this module."""
    capture = _CaptureTransport()
    transport = SprinklrRouterTransport(
        transport=capture,
        extra_body={
            "client_identifier": "backend-platform-dev",
            "partner_id": 66006006,
            "tracking_params": {"feature": "SPRINKLR_JIRA_SOLVER"},
        },
    )
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ]
    original = httpx.Request(
        "POST",
        "http://qa6-intuitionx-llm-router-v2.sprinklr.com/chat/completions",
        content=json.dumps({"model": "gpt-5.2-2025-12-11", "messages": messages}).encode(),
        headers={"content-type": "application/json"},
    )

    transport.handle_request(original)

    assert capture.last_request is not None
    assert str(capture.last_request.url).endswith("/chat-completion")
    body = json.loads(capture.last_request.content)
    assert body["model"] == "gpt-5.2-2025-12-11"
    assert body["messages"] == messages
    assert body["client_identifier"] == "backend-platform-dev"
    assert body["partner_id"] == 66006006
    assert body["tracking_params"] == {"feature": "SPRINKLR_JIRA_SOLVER"}


def test_sprinklr_transport_normalizes_response_object():
    """Test Sprinklr Transport Normalizes Response Object for this module."""
    capture = _CaptureTransport()
    transport = SprinklrRouterTransport(
        transport=capture,
        extra_body={"client_identifier": "dev"},
    )
    request = httpx.Request(
        "POST",
        "http://example.com/chat/completions",
        content=json.dumps({"model": "gpt-4o", "messages": []}).encode(),
        headers={"content-type": "application/json"},
    )

    response = transport.handle_request(request)
    payload = response.json()
    assert payload["object"] == "chat.completion"


def test_sprinklr_transport_returns_json_error_on_403():
    """Test Sprinklr Transport Returns Json Error On 403 for this module."""
    transport = SprinklrRouterTransport(
        transport=_ErrorTransport(403, "<!doctype html><title>403</title>403 Forbidden"),
        extra_body={"client_identifier": "dev"},
    )
    request = httpx.Request(
        "POST",
        "http://example.com/chat/completions",
        content=json.dumps({"model": "gpt-4o", "messages": []}).encode(),
        headers={"content-type": "application/json"},
    )

    response = transport.handle_request(request)
    assert response.status_code == 403
    payload = response.json()
    assert "403 Forbidden" in payload["error"]["message"]


@patch.dict(
    "os.environ",
    {
        "SPRINKLR_LLM_BASE_URL": "http://sprinklr-router.test",
        "SPRINKLR_LLM_CLIENT_IDENTIFIER": "backend-platform-dev",
        "SPRINKLR_LLM_PARTNER_ID": "66006006",
        "SPRINKLR_LLM_TRACKING_FEATURE": "SPRINKLR_JIRA_SOLVER",
    },
    clear=False,
)
def test_create_sprinklr_chat_model_invokes_router():
    """Test Create Sprinklr Chat Model Invokes Router for this module."""
    capture = _CaptureTransport()
    with patch(
        "src.integrations.llm.sprinklr_router.SprinklrRouterTransport",
        side_effect=lambda **kwargs: SprinklrRouterTransport(transport=capture, **kwargs),
    ):
        llm = create_sprinklr_chat_model("gpt-5.2-2025-12-11")
        response = llm.invoke([HumanMessage(content="hello")])

    assert response.content == "ok"
    assert capture.last_request is not None
    assert str(capture.last_request.url) == "http://sprinklr-router.test/chat-completion"
    body = json.loads(capture.last_request.content)
    assert body["tracking_params"]["feature"] == "SPRINKLR_JIRA_SOLVER"
    header_names = {k.lower() for k, _ in capture.last_request.headers.items()}
    assert "authorization" not in header_names


@patch.dict(
    "os.environ",
    {
        "SPRINKLR_LLM_TRACKING_PARAMS": '{"feature": "CUSTOM_FEATURE", "team": "platform"}',
    },
    clear=False,
)
def test_sprinklr_tracking_params_json_override():
    """Test Sprinklr Tracking Params Json Override for this module."""
    from src.integrations.llm import sprinklr_router

    extra = sprinklr_router._sprinklr_extra_body()
    assert extra["tracking_params"] == {"feature": "CUSTOM_FEATURE", "team": "platform"}


@patch.dict("os.environ", {"SPRINKLR_LLM_TRACKING_PARAMS": "not-json"}, clear=False)
def test_sprinklr_tracking_params_json_invalid():
    """Test Sprinklr Tracking Params Json Invalid for this module."""
    from src.integrations.llm import sprinklr_router

    with pytest.raises(ValueError, match="SPRINKLR_LLM_TRACKING_PARAMS"):
        sprinklr_router._sprinklr_extra_body()
