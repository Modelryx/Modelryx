"""Provider-specific chat protocols. Domain tools remain owned by Modelryx."""

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Protocol

from fastapi import HTTPException
from pydantic import ValidationError

from modelryx.core.config import Settings
from modelryx.services.ai_tool_protocol import ToolCall, ToolDefinition, ToolOutput, ToolTurn


def post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, allow_nan=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError("expected an object")
        return result
    except urllib.error.HTTPError as exc:
        # Provider bodies and URLs may contain credentials or engineering inputs.
        status = exc.code
        exc.close()
        raise HTTPException(502, f"AI provider request failed (HTTP {status})") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise HTTPException(502, "AI provider is unavailable or timed out") from None
    except (ValueError, UnicodeError):
        raise HTTPException(502, "AI provider returned invalid JSON") from None


def invalid_response() -> HTTPException:
    return HTTPException(502, "AI provider returned an invalid or incomplete chat response")


def text_result(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid_response()
    return value


class ChatAdapter(Protocol):
    @property
    def enabled(self) -> bool: ...

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "modelryx_structured_output",
    ) -> str | dict[str, Any]: ...

    def initial_tool_messages(self, user: str) -> list[dict[str, Any]]: ...

    def tool_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        require_tool: bool,
        timeout: float,
    ) -> ToolTurn: ...

    def tool_result_messages(self, turn: ToolTurn, outputs: list[ToolOutput]) -> list[dict]: ...


class BaseChatAdapter:
    default_base_url = ""

    def __init__(self, settings: Settings):
        self.settings = settings
        # Legacy gateway settings remain the default only for the compatible adapter.
        # Native providers must not accidentally send a legacy gateway key elsewhere.
        compatible = settings.ai_chat_provider == "openai_compatible"
        self.base_url = (
            settings.ai_chat_base_url
            or (settings.ai_gateway_base_url if compatible else None)
            or self.default_base_url
        )
        self.api_key = settings.ai_chat_api_key or (
            settings.ai_gateway_api_key if compatible else None
        )

    @property
    def enabled(self) -> bool:
        return bool(
            self.base_url
            and self.settings.ai_chat_model
            and (self.api_key or self.settings.ai_chat_provider == "openai_compatible")
        )

    def post(
        self,
        path: str,
        payload: dict,
        headers: dict[str, str],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise HTTPException(503, "AI chat provider is not configured")
        return post_json(
            f"{self.base_url.rstrip('/')}/{path.lstrip('/')}",
            payload,
            headers,
            min(timeout, self.settings.ai_request_timeout_seconds)
            if timeout is not None
            else self.settings.ai_request_timeout_seconds,
        )

    def initial_tool_messages(self, user: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": user}]


class OpenAIChatAdapter(BaseChatAdapter):
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        if settings.ai_chat_provider == "openai_compatible" and not (
            settings.ai_chat_base_url or settings.ai_gateway_base_url
        ):
            self.base_url = ""  # Preserve the legacy opt-in behavior.

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "modelryx_structured_output",
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.ai_chat_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.settings.ai_chat_provider == "openai_compatible":
            payload["temperature"] = 0
            payload["max_tokens"] = self.settings.ai_chat_max_output_tokens
        else:
            # Reasoning models do not universally accept temperature or max_tokens.
            payload["max_completion_tokens"] = self.settings.ai_chat_max_output_tokens
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                # Existing domain schemas contain optional/defaulted fields and
                # open metadata objects outside OpenAI's strict schema subset.
                # Preserve legacy gateway behavior; validate native output locally.
                "json_schema": {
                    "name": schema_name,
                    "strict": self.settings.ai_chat_provider == "openai_compatible",
                    "schema": schema,
                },
            }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = self.post("chat/completions", payload, headers)
        try:
            choice = response["choices"][0]
            if choice.get("finish_reason") not in (None, "stop"):
                raise invalid_response()
            if choice["message"].get("refusal"):
                raise invalid_response()
            return text_result(choice["message"]["content"])
        except (KeyError, IndexError, TypeError, AttributeError):
            raise invalid_response() from None

    def tool_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        require_tool: bool,
        timeout: float,
    ) -> ToolTurn:
        payload = {
            "model": self.settings.ai_chat_model,
            "messages": [{"role": "system", "content": system}, *messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        "strict": False,
                    },
                }
                for tool in tools
            ],
            "tool_choice": "required" if require_tool else "auto",
            "parallel_tool_calls": False,
            (
                "max_tokens"
                if self.settings.ai_chat_provider == "openai_compatible"
                else "max_completion_tokens"
            ): self.settings.ai_chat_max_output_tokens,
        }
        response = self.post(
            "chat/completions",
            payload,
            {
                "Authorization": f"Bearer {self.api_key}",
            }
            if self.api_key
            else {},
            timeout,
        )
        try:
            choice = response["choices"][0]
            message = choice["message"]
            if message.get("refusal") or choice["finish_reason"] not in {"stop", "tool_calls"}:
                raise invalid_response()
            calls = [
                ToolCall(
                    id=call["id"],
                    name=call["function"]["name"],
                    arguments=json.loads(call["function"]["arguments"]),
                )
                for call in message.get("tool_calls", [])
            ]
            if bool(calls) != (choice["finish_reason"] == "tool_calls"):
                raise invalid_response()
            text = message.get("content") or ""
            if not isinstance(text, str) or (not calls and not text.strip()):
                raise invalid_response()
            # Keep only protocol fields, never provider-generated roles.
            native = {"role": "assistant", "content": text or None}
            if calls:
                native["tool_calls"] = message["tool_calls"]
            return ToolTurn(text, calls, native)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
            raise invalid_response() from None

    def tool_result_messages(self, turn: ToolTurn, outputs: list[ToolOutput]) -> list[dict]:
        return [
            turn.message,
            *[
                {
                    "role": "tool",
                    "tool_call_id": output.call.id,
                    "content": json.dumps(output.result, allow_nan=False),
                }
                for output in outputs
            ],
        ]


class AnthropicChatAdapter(BaseChatAdapter):
    default_base_url = "https://api.anthropic.com/v1"

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "modelryx_structured_output",
    ) -> str | dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.ai_chat_model,
            "max_tokens": self.settings.ai_chat_max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if schema is not None:
            # A forced synthetic tool returns data only; it is never executed.
            payload["tools"] = [
                {
                    "name": schema_name,
                    "description": "Return the requested structured result.",
                    "input_schema": schema,
                }
            ]
            payload["tool_choice"] = {
                "type": "tool",
                "name": schema_name,
                "disable_parallel_tool_use": True,
            }
        response = self.post(
            "messages",
            payload,
            {
                "x-api-key": self.api_key or "",
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            if response.get("stop_reason") != ("tool_use" if schema is not None else "end_turn"):
                raise invalid_response()
            blocks = response["content"]
            if schema is not None:
                calls = [block for block in blocks if block["type"] == "tool_use"]
                if len(calls) != 1 or calls[0]["name"] != schema_name:
                    raise invalid_response()
                result = calls[0]["input"]
                if not isinstance(result, dict):
                    raise invalid_response()
                return result
            return text_result("".join(b["text"] for b in blocks if b["type"] == "text"))
        except (KeyError, IndexError, TypeError, AttributeError):
            raise invalid_response() from None

    def tool_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        require_tool: bool,
        timeout: float,
    ) -> ToolTurn:
        response = self.post(
            "messages",
            {
                "model": self.settings.ai_chat_model,
                "system": system,
                "messages": messages,
                "max_tokens": self.settings.ai_chat_max_output_tokens,
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.parameters,
                    }
                    for tool in tools
                ],
                "tool_choice": {
                    "type": "any" if require_tool else "auto",
                    "disable_parallel_tool_use": True,
                },
            },
            {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"},
            timeout,
        )
        try:
            blocks = response["content"]
            calls = [
                ToolCall(id=block["id"], name=block["name"], arguments=block["input"])
                for block in blocks
                if block["type"] == "tool_use"
            ]
            if response["stop_reason"] != ("tool_use" if calls else "end_turn"):
                raise invalid_response()
            text = "".join(b["text"] for b in blocks if b["type"] == "text")
            if not calls and not text.strip():
                raise invalid_response()
            return ToolTurn(text, calls, {"role": "assistant", "content": blocks})
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
            raise invalid_response() from None

    def tool_result_messages(self, turn: ToolTurn, outputs: list[ToolOutput]) -> list[dict]:
        return [
            turn.message,
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": output.call.id,
                        "content": json.dumps(output.result, allow_nan=False),
                        "is_error": output.is_error,
                    }
                    for output in outputs
                ],
            },
        ]


class GeminiChatAdapter(BaseChatAdapter):
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "modelryx_structured_output",
    ) -> str:
        generation: dict[str, Any] = {
            "maxOutputTokens": self.settings.ai_chat_max_output_tokens,
        }
        if schema is not None:
            generation.update(responseMimeType="application/json", responseJsonSchema=schema)
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": generation,
        }
        model = urllib.parse.quote(self.settings.ai_chat_model.removeprefix("models/"), safe="")
        response = self.post(
            f"models/{model}:generateContent",
            payload,
            {
                "x-goog-api-key": self.api_key or "",
            },
        )
        try:
            candidate = response["candidates"][0]
            if candidate.get("finishReason") != "STOP":
                raise invalid_response()
            return text_result(
                "".join(
                    part["text"]
                    for part in candidate["content"]["parts"]
                    if "text" in part and not part.get("thought")
                )
            )
        except (KeyError, IndexError, TypeError, AttributeError):
            raise invalid_response() from None

    def initial_tool_messages(self, user: str) -> list[dict[str, Any]]:
        return [{"role": "user", "parts": [{"text": user}]}]

    def tool_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        require_tool: bool,
        timeout: float,
    ) -> ToolTurn:
        model = urllib.parse.quote(self.settings.ai_chat_model.removeprefix("models/"), safe="")
        response = self.post(
            f"models/{model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": messages,
                "generationConfig": {"maxOutputTokens": self.settings.ai_chat_max_output_tokens},
                "tools": [
                    {
                        "functionDeclarations": [
                            {
                                "name": tool.name,
                                "description": tool.description,
                                "parametersJsonSchema": tool.parameters,
                            }
                            for tool in tools
                        ]
                    }
                ],
                "toolConfig": {
                    "functionCallingConfig": {"mode": "ANY" if require_tool else "AUTO"}
                },
            },
            {"x-goog-api-key": self.api_key or ""},
            timeout,
        )
        try:
            candidate = response["candidates"][0]
            if candidate["finishReason"] != "STOP":
                raise invalid_response()
            content = candidate["content"]
            calls = []
            for part in content["parts"]:
                if "functionCall" in part:
                    call = part["functionCall"]
                    calls.append(
                        ToolCall(
                            id=call.get("id") or f"gemini-{uuid.uuid4().hex}",
                            name=call["name"],
                            arguments=call.get("args", {}),
                        )
                    )
            text = "".join(
                p["text"] for p in content["parts"] if "text" in p and not p.get("thought")
            )
            if not calls and not text.strip():
                raise invalid_response()
            return ToolTurn(text, calls, {**content, "role": "model"})
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
            raise invalid_response() from None

    def tool_result_messages(self, turn: ToolTurn, outputs: list[ToolOutput]) -> list[dict]:
        parts = []
        native_calls = [p["functionCall"] for p in turn.message["parts"] if "functionCall" in p]
        for output, native in zip(outputs, native_calls, strict=True):
            result = {"name": output.call.name, "response": output.result}
            if native.get("id"):
                result["id"] = native["id"]
            parts.append({"functionResponse": result})
        return [turn.message, {"role": "user", "parts": parts}]


def create_chat_adapter(settings: Settings) -> ChatAdapter:
    adapters = {
        "openai_compatible": OpenAIChatAdapter,
        "openai": OpenAIChatAdapter,
        "anthropic": AnthropicChatAdapter,
        "gemini": GeminiChatAdapter,
    }
    return adapters[settings.ai_chat_provider](settings)
