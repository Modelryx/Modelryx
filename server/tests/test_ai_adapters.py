import io
import json
import urllib.error

import pytest
from fastapi import HTTPException
from modelryx.core.config import Settings
from modelryx.services.ai import AiGateway
from pydantic import ValidationError

SCHEMA = {
    "type": "object",
    "properties": {"count": {"type": "integer", "minimum": 0}},
    "required": ["count"],
    "additionalProperties": False,
}


def settings(provider="openai_compatible", **kwargs):
    return Settings(_env_file=None, ai_chat_provider=provider, ai_chat_model="test-model", **kwargs)


def respond(monkeypatch, body):
    captured = {}

    def urlopen(request, timeout):
        captured.update(
            url=request.full_url,
            headers={k.lower(): v for k, v in request.header_items()},
            body=json.loads(request.data),
            timeout=timeout,
        )
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return captured


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_native_provider_requires_explicit_key_and_keeps_embeddings_separate(provider):
    gateway = AiGateway(
        settings(
            provider,
            ai_gateway_base_url="https://legacy.test/v1",
            ai_gateway_api_key="legacy",
            ai_embedding_model="embedding-model",
        )
    )
    assert not gateway.chat_enabled
    assert gateway.embeddings_enabled
    with pytest.raises(HTTPException) as error:
        gateway.answer("question", [])
    assert error.value.status_code == 503


def test_disabled_legacy_gateway_does_not_default_to_external_provider():
    assert not AiGateway(settings()).chat_enabled
    assert not AiGateway(settings(ai_chat_api_key="key")).chat_enabled


@pytest.mark.parametrize("provider", ["openai_compatible", "openai"])
def test_openai_chat_and_structured_output(monkeypatch, provider):
    captured = respond(
        monkeypatch,
        {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"count":2}'}}],
        },
    )
    gateway = AiGateway(
        settings(
            provider,
            ai_chat_api_key="chat-key",
            ai_chat_base_url="https://chat.test/v1/",
            ai_request_timeout_seconds=7,
            ai_chat_max_output_tokens=512,
        )
    )
    assert gateway.structured_chat(system="system", payload={"query": "parts"}, schema=SCHEMA) == {
        "count": 2,
    }
    assert captured["url"] == "https://chat.test/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer chat-key"
    assert captured["timeout"] == 7
    assert captured["body"]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert captured["body"]["messages"][0] == {"role": "system", "content": "system"}
    token_field = "max_tokens" if provider == "openai_compatible" else "max_completion_tokens"
    assert captured["body"][token_field] == 512


def test_legacy_gateway_configuration_still_works(monkeypatch):
    captured = respond(monkeypatch, {"choices": [{"message": {"content": "Grounded answer"}}]})
    gateway = AiGateway(
        settings(
            ai_gateway_base_url="http://local.test/v1",
            ai_gateway_api_key="legacy-key",
        )
    )
    assert gateway.chat_enabled
    assert gateway.answer("question", []) == "Grounded answer"
    assert captured["url"] == "http://local.test/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer legacy-key"


def test_anthropic_structured_data_uses_forced_tool(monkeypatch):
    captured = respond(
        monkeypatch,
        {
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": "result", "input": {"count": 3}}],
        },
    )
    gateway = AiGateway(settings("anthropic", ai_chat_api_key="claude-key"))
    assert gateway.structured_chat(
        system="system",
        payload={"q": "parts"},
        schema=SCHEMA,
        schema_name="result",
    ) == {"count": 3}
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "claude-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["body"]["system"] == "system"
    assert captured["body"]["tools"][0]["input_schema"] == SCHEMA
    assert captured["body"]["tool_choice"]["name"] == "result"


def test_anthropic_text_ignores_non_text_blocks(monkeypatch):
    respond(
        monkeypatch,
        {
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "private"},
                {"type": "text", "text": "Verified "},
                {"type": "text", "text": "answer"},
            ],
        },
    )
    assert AiGateway(settings("anthropic", ai_chat_api_key="key")).answer("question", []) == (
        "Verified answer"
    )


def test_gemini_structured_output_and_header_auth(monkeypatch):
    captured = respond(
        monkeypatch,
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {"text": "internal thought", "thought": True},
                            {"text": '{"count":4}'},
                        ]
                    },
                }
            ],
        },
    )
    gateway = AiGateway(settings("gemini", ai_chat_api_key="gemini-key"))
    assert gateway.structured_chat(system="system", payload={"q": "parts"}, schema=SCHEMA) == {
        "count": 4,
    }
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "gemini-key"
    assert "gemini-key" not in captured["url"]
    assert captured["body"]["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert captured["body"]["systemInstruction"]["parts"] == [{"text": "system"}]


def test_gemini_plain_answer_does_not_request_json(monkeypatch):
    captured = respond(
        monkeypatch,
        {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Answer"}]}}],
        },
    )
    assert AiGateway(settings("gemini", ai_chat_api_key="key")).answer("question", []) == "Answer"
    assert "responseJsonSchema" not in captured["body"]["generationConfig"]


def test_native_openai_accepts_existing_domain_schema(monkeypatch):
    from modelryx.engineering_search.models import SearchRequest

    captured = respond(monkeypatch, {"choices": [{"message": {"content": "{}"}}]})
    gateway = AiGateway(settings("openai", ai_chat_api_key="key"))
    result = gateway.structured_chat(
        system="system", payload={}, schema=SearchRequest.model_json_schema()
    )
    assert SearchRequest.model_validate(result).entity_type == "item"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["body"]["response_format"]["json_schema"]["strict"] is False


@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
def test_native_structured_results_use_shared_schema_validation(monkeypatch, provider):
    body = (
        {
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "name": "modelryx_structured_output", "input": {"count": -1}}
            ],
        }
        if provider == "anthropic"
        else {
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": '{"count":-1}'}]}}
            ]
        }
    )
    respond(monkeypatch, body)
    with pytest.raises(HTTPException) as error:
        AiGateway(settings(provider, ai_chat_api_key="key")).structured_chat(
            system="system",
            payload={},
            schema=SCHEMA,
        )
    assert error.value.status_code == 502


@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("openai", {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}),
        ("openai", {"choices": [{"message": {"content": None, "refusal": "refused"}}]}),
        ("openai", {"choices": [{"message": {"content": {"invalid": "type"}}}]}),
        ("anthropic", {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "x"}]}),
        ("anthropic", {"stop_reason": "end_turn", "content": []}),
        ("gemini", {"candidates": [{"finishReason": "SAFETY"}]}),
        ("gemini", {"candidates": [{"finishReason": "MAX_TOKENS"}]}),
        ("gemini", {"promptFeedback": {"blockReason": "SAFETY"}}),
    ],
)
def test_incomplete_or_blocked_answers_are_not_success(monkeypatch, provider, body):
    respond(monkeypatch, body)
    with pytest.raises(HTTPException) as error:
        AiGateway(settings(provider, ai_chat_api_key="key")).answer("question", [])
    assert error.value.status_code == 502


@pytest.mark.parametrize(
    "content", ['{"count":-1}', '{"count":"2"}', '{"count":2,"extra":true}', "[]", "not json"]
)
def test_structured_response_is_validated_locally(monkeypatch, content):
    respond(monkeypatch, {"choices": [{"message": {"content": content}}]})
    gateway = AiGateway(settings("openai", ai_chat_api_key="key"))
    with pytest.raises(HTTPException) as error:
        gateway.structured_chat(system="system", payload={}, schema=SCHEMA)
    assert error.value.status_code == 502


def test_anthropic_rejects_unrequested_tool(monkeypatch):
    respond(
        monkeypatch,
        {
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "name": "execute_command", "input": {"count": 2}},
            ],
        },
    )
    with pytest.raises(HTTPException):
        AiGateway(settings("anthropic", ai_chat_api_key="key")).structured_chat(
            system="system",
            payload={},
            schema=SCHEMA,
        )


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("secret request"),
        urllib.error.URLError("secret URL"),
        urllib.error.HTTPError("https://secret", 429, "secret", {}, io.BytesIO(b"secret body")),
    ],
)
def test_transport_errors_are_sanitized(monkeypatch, failure):
    def urlopen(*args, **kwargs):
        raise failure

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(HTTPException) as error:
        AiGateway(settings("openai", ai_chat_api_key="key")).answer("question", [])
    assert error.value.status_code == 502
    assert "secret" not in error.value.detail


@pytest.mark.parametrize("raw", [b"not json", b"[]"])
def test_invalid_provider_json_is_normalized(monkeypatch, raw):
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(raw))
    with pytest.raises(HTTPException) as error:
        AiGateway(settings("gemini", ai_chat_api_key="key")).answer("question", [])
    assert error.value.status_code == 502


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ai_chat_provider": "unknown"},
        {"ai_chat_max_output_tokens": 0},
        {"ai_request_timeout_seconds": 0},
    ],
)
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)
