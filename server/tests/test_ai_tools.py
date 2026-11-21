import io
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from modelryx.assistant.models import AssistantContext, AssistantMessageRequest
from modelryx.assistant.tool_loop import run_tool_loop
from modelryx.assistant.tools import (
    EngineeringToolRegistry,
    EvidenceResult,
    RegisteredTool,
    RevisionArguments,
    SearchArguments,
)
from modelryx.core.auth import Principal
from modelryx.core.config import Settings
from modelryx.services.ai_adapters import create_chat_adapter
from modelryx.services.ai_tool_protocol import ToolCall, ToolDefinition, ToolTurn
from pydantic import BaseModel, ConfigDict, ValidationError


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str


def config(provider="openai", **kwargs):
    return Settings(
        _env_file=None,
        ai_chat_provider=provider,
        ai_chat_model="test-model",
        ai_chat_api_key="key",
        **kwargs,
    )


def registry():
    result = Mock(spec=EngineeringToolRegistry)
    definition = ToolDefinition("search_items", "Search records", Arguments.model_json_schema())
    result.tools = {"search_items": RegisteredTool(definition, Arguments, Mock())}
    result.definitions = [definition]
    result.execute.return_value = EvidenceResult({"count": 1}, "One matching revision.")
    result.validate_context.return_value = {}
    return result


def call(call_id="c1", name="search_items", **arguments):
    return ToolCall(id=call_id, name=name, arguments=arguments or {"query": "housing"})


def adapter(*turns):
    result = Mock()
    result.initial_tool_messages.return_value = [{"role": "user", "content": "question"}]
    result.tool_turn.side_effect = list(turns)
    result.tool_result_messages.return_value = [{"role": "tool", "content": "result"}]
    return result


@pytest.mark.parametrize("provider", ["openai", "openai_compatible", "anthropic", "gemini"])
def test_native_round_trip_executes_tool_then_returns_grounded_answer(monkeypatch, provider):
    first = {
        "openai": {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "search_items",
                                    "arguments": '{"query":"housing"}',
                                },
                            }
                        ]
                    },
                }
            ]
        },
        "anthropic": {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "search_items",
                    "input": {"query": "housing"},
                },
            ],
        },
        "gemini": {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "search_items",
                                    "args": {"query": "housing"},
                                    "id": "c1",
                                },
                                "thoughtSignature": "opaque-signature",
                            }
                        ],
                    },
                }
            ]
        },
    }
    final = {
        "openai": {
            "choices": [
                {"finish_reason": "stop", "message": {"content": "One matching revision. [T1]"}}
            ]
        },
        "anthropic": {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "One matching revision. [T1]"}],
        },
        "gemini": {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "One matching revision. [T1]"}]},
                }
            ]
        },
    }
    protocol = "openai" if provider == "openai_compatible" else provider
    responses = iter([first[protocol], final[protocol]])
    requests = []

    def urlopen(request, timeout):
        assert 0 < timeout <= 30
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps(next(responses)).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    settings = config(provider, ai_chat_base_url="https://test.invalid/v1")
    tools = registry()
    run = run_tool_loop(create_chat_adapter(settings), tools, settings, "Find housing", {})
    assert run.answer == "One matching revision. [T1]"
    assert not run.warnings
    assert tools.execute.call_args.args[0] == "search_items"
    assert tools.execute.call_args.args[1].query == "housing"
    assert run.executions[0].status == "COMPLETED"
    if protocol == "openai":
        assert requests[0]["tool_choice"] == "required"
        assert requests[1]["messages"][-1]["tool_call_id"] == "c1"
        assert json.loads(requests[1]["messages"][-1]["content"])["evidence_id"] == "T1"
    elif protocol == "anthropic":
        assert requests[0]["tool_choice"]["type"] == "any"
        assert requests[1]["messages"][-1]["content"][0]["tool_use_id"] == "c1"
    else:
        assert requests[0]["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
        assert requests[1]["contents"][-2]["parts"][0]["thoughtSignature"] == "opaque-signature"
        response = requests[1]["contents"][-1]["parts"][0]["functionResponse"]
        assert response["name"] == "search_items" and response["id"] == "c1"


@pytest.mark.parametrize(
    "bad_call",
    [
        call(name="delete_item"),
        call(query="housing", tenant_id="foreign"),
        call(query=123),
        call(query="x" * 17000),
        call(query=float("nan")),
    ],
)
def test_unadvertised_tools_invalid_scope_and_oversized_args_never_execute(bad_call):
    tools = registry()
    model = adapter(ToolTurn("", [bad_call]), ToolTurn("No results"))
    run = run_tool_loop(model, tools, config(), "question", {})
    tools.execute.assert_not_called()
    assert run.executions[0].status == "FAILED"
    output = model.tool_result_messages.call_args.args[1][0]
    assert output.is_error and output.result["code"] == "invalid_tool_or_arguments"
    assert "No verified" in run.answer


def test_tool_can_correct_an_invalid_call_within_budget():
    tools = registry()
    model = adapter(
        ToolTurn("", [call(name="bad")]), ToolTurn("", [call("c2")]), ToolTurn("One match [T1]")
    )
    run = run_tool_loop(model, tools, config(), "question", {})
    assert run.answer == "One match [T1]"
    assert [item.status for item in run.executions] == ["FAILED", "COMPLETED"]
    tools.execute.assert_called_once()


@pytest.mark.parametrize("answer", ["Invented result", "Invented citation [T99]"])
def test_invalid_final_citations_fall_back_to_server_summary(answer):
    run = run_tool_loop(
        adapter(ToolTurn("", [call()]), ToolTurn(answer)), registry(), config(), "question", {}
    )
    assert run.answer == "[T1] One matching revision."
    assert run.warnings


def test_provider_failure_after_tool_keeps_verified_evidence():
    model = adapter(ToolTurn("", [call()]), HTTPException(502, "provider failed"))
    run = run_tool_loop(model, registry(), config(), "question", {})
    assert run.answer == "[T1] One matching revision."
    assert "unavailable" in run.warnings[0]


def test_denied_item_is_not_sent_to_provider_or_returned():
    tools = registry()
    tools.execute.side_effect = HTTPException(404, "secret item number")
    model = adapter(ToolTurn("", [call()]), ToolTurn("Invented secret [T1]"))
    run = run_tool_loop(model, tools, config(), "question", {})
    output = model.tool_result_messages.call_args.args[1][0]
    assert output.is_error and output.result["code"] == "tool_unavailable"
    assert "secret" not in json.dumps(output.result)
    assert "secret" not in run.answer
    assert run.references == []


def test_revoked_or_changed_sources_remove_previously_retrieved_data():
    tools = registry()
    # Initial verification and first result succeed, subsequent verification fails.
    tools.verify.side_effect = [
        None,
        None,
        HTTPException(404, "revoked"),
        HTTPException(404, "revoked"),
    ]
    model = adapter(ToolTurn("", [call()]))
    run = run_tool_loop(model, tools, config(), "question", {})
    assert "no longer available" in run.answer
    assert run.references == [] and "One matching" not in run.answer
    assert model.tool_turn.call_count == 1


def test_batch_over_call_limit_executes_nothing():
    tools = registry()
    run = run_tool_loop(
        adapter(ToolTurn("", [call(), call("c2")])),
        tools,
        config(ai_tool_max_calls=1),
        "question",
        {},
    )
    tools.execute.assert_not_called()
    assert "call limit" in run.warnings[0]


def test_round_limit_stops_repeated_tool_requests():
    tools = registry()
    model = adapter(ToolTurn("", [call()]), ToolTurn("", [call("c2")]))
    run = run_tool_loop(model, tools, config(ai_tool_max_rounds=2), "question", {})
    assert tools.execute.call_count == 2 and "round limit" in run.warnings[0]


def test_replayed_call_id_is_rejected():
    tools = registry()
    run = run_tool_loop(
        adapter(ToolTurn("", [call()]), ToolTurn("", [call()])), tools, config(), "question", {}
    )
    tools.execute.assert_called_once()
    assert "Duplicate" in run.warnings[0]


def test_deadline_prevents_tool_execution_after_slow_provider(monkeypatch):
    current = [100.0]
    monkeypatch.setattr("modelryx.assistant.tool_loop.time.monotonic", lambda: current[0])
    model = adapter()

    def slow_turn(**kwargs):
        current[0] += 31
        return ToolTurn("", [call()])

    model.tool_turn.side_effect = slow_turn
    tools = registry()
    run = run_tool_loop(model, tools, config(), "question", {})
    tools.execute.assert_not_called()
    assert "deadline" in run.warnings[0]


@pytest.mark.parametrize(
    "search_request",
    [
        {"limit": 21},
        {"offset": 1001},
        {"similarity": {"source_revision_id": str(uuid.uuid4())}},
        {"include_facets": True},
        {"tenant_id": "foreign"},
    ],
)
def test_domain_search_rejects_unsupported_or_unbounded_options(search_request):
    with pytest.raises(ValidationError):
        SearchArguments(request=search_request)


def test_search_defaults_are_bounded_and_expose_limits_in_schema():
    assert SearchArguments(request={}).request.limit == 20
    schema = SearchArguments.model_json_schema()["$defs"]["ToolSearchRequest"]
    assert schema["properties"]["limit"]["maximum"] == 20


def test_revision_handler_uses_existing_authorization_boundary(monkeypatch):
    import modelryx.assistant.tools as domain

    db = Mock()
    principal = Principal("reader", "Reader", "viewer")
    args = RevisionArguments(item_id=uuid.uuid4(), revision_id=uuid.uuid4())
    authorize = Mock(side_effect=HTTPException(404, "Item revision not found"))
    monkeypatch.setattr(domain, "readable_item_revision", authorize)
    build = Mock()
    monkeypatch.setattr(domain, "build_revision_documents", build)
    with pytest.raises(HTTPException):
        EngineeringToolRegistry(db, principal).get_item_revision(args)
    authorize.assert_called_once_with(db, args.item_id, args.revision_id, principal)
    build.assert_not_called()


def test_search_handler_passes_principal_and_validated_request(monkeypatch):
    import modelryx.assistant.tools as domain

    db, principal = Mock(), Principal("reader", "Reader", "viewer")
    args = SearchArguments(request={"filters": {"material_presence": "missing"}})
    execute = Mock(
        return_value=SimpleNamespace(
            interpretation=args.request,
            results=[],
            total=0,
            warnings=[],
            coverage=SimpleNamespace(model_dump=lambda **kwargs: {"matched": 0}),
        )
    )
    monkeypatch.setattr(domain, "execute_search", execute)
    result = EngineeringToolRegistry(db, principal).search_items(args)
    execute.assert_called_once_with(db, principal, args.request)
    assert result.data["results"] == [] and result.data["total"] == 0


def test_context_ids_are_authorized_before_provider_use(monkeypatch):
    import modelryx.assistant.tools as domain

    authorize = Mock(side_effect=HTTPException(404, "Not found"))
    monkeypatch.setattr(domain, "readable_item_revision", authorize)
    context = AssistantContext.model_validate(
        {
            "item": {
                "item_id": str(uuid.uuid4()),
                "revision_id": str(uuid.uuid4()),
            }
        }
    )
    with pytest.raises(HTTPException):
        EngineeringToolRegistry(Mock(), Mock()).validate_context(context)
    authorize.assert_called_once()


def test_assistant_endpoint_persists_each_tool_execution(monkeypatch):
    import modelryx.api.assistant as api
    from modelryx.models import AiToolExecution

    db = Mock()
    saved = []

    def add(row):
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()
        saved.append(row)

    db.add.side_effect = add
    model = adapter(ToolTurn("", [call()]), ToolTurn("One matching revision. [T1]"))
    monkeypatch.setattr(
        api, "AiGateway", lambda _: SimpleNamespace(chat_enabled=True, chat_adapter=model)
    )
    monkeypatch.setattr(api, "EngineeringToolRegistry", lambda *args: registry())
    monkeypatch.setattr(api, "user_for_principal", lambda *args: SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(api, "record_audit", Mock())
    response = api.create_assistant_message(
        AssistantMessageRequest(message="Find housing", context=AssistantContext()),
        SimpleNamespace(state=SimpleNamespace(request_id="trace")),
        db,
        Principal("reader", "Reader", "viewer"),
        config(ai_tool_calling_enabled=True),
    )
    assert response.intent == "TOOL_ASSISTED" and response.trace_id == "trace"
    executions = [row for row in saved if isinstance(row, AiToolExecution)]
    assert len(executions) == 1 and executions[0].tool_name == "search_items"
    assert executions[0].arguments_json == {"query": "housing"}
    assert executions[0].status == "COMPLETED"
    db.commit.assert_called_once()


def test_tool_mode_preserves_specialized_deterministic_handlers(monkeypatch):
    import modelryx.api.assistant as api

    db = Mock()
    db.add.side_effect = lambda row: setattr(row, "id", getattr(row, "id", None) or uuid.uuid4())
    monkeypatch.setattr(api, "AiGateway", lambda _: SimpleNamespace(chat_enabled=True))
    monkeypatch.setattr(api, "user_for_principal", lambda *args: SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(api, "record_audit", Mock())
    loop = Mock()
    monkeypatch.setattr(api, "run_tool_loop", loop)
    handler = Mock()
    handler.execute.return_value = (
        "get_bom",
        SimpleNamespace(
            answer="Recorded BOM",
            references=[],
            actions=[],
            summary={},
        ),
        1.0,
    )
    monkeypatch.setattr(api, "AssistantToolGateway", lambda *args: handler)
    response = api.create_assistant_message(
        AssistantMessageRequest(message="Show the BOM", context=AssistantContext()),
        SimpleNamespace(state=SimpleNamespace(request_id="trace")),
        db,
        Principal("reader", "Reader", "viewer"),
        config(ai_tool_calling_enabled=True),
    )
    assert response.intent == "BOM_QUERY" and response.answer == "Recorded BOM"
    loop.assert_not_called()


def test_failed_tool_statement_rolls_back_savepoint(monkeypatch):
    db = Mock()
    transaction = Mock()
    db.begin_nested.return_value = transaction
    transaction.__enter__ = Mock(return_value=transaction)
    transaction.__exit__ = Mock(return_value=False)
    db.scalar.return_value = "0"
    tools = EngineeringToolRegistry(db, Mock())
    handler = Mock(side_effect=HTTPException(404, "not found"))
    tool = tools.tools["get_item_revision"]
    tools.tools["get_item_revision"] = RegisteredTool(tool.definition, tool.arguments, handler)
    args = RevisionArguments(item_id=uuid.uuid4(), revision_id=uuid.uuid4())
    with pytest.raises(HTTPException):
        tools.execute("get_item_revision", args, 2.5)
    assert db.execute.call_args.args[1]["timeout"] == "2500ms"
    assert transaction.__exit__.call_args.args[0] is HTTPException


@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("openai", {"choices": [{"finish_reason": "length", "message": {"tool_calls": []}}]}),
        (
            "openai",
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {"name": "search_items", "arguments": "not JSON"},
                                },
                            ]
                        },
                    }
                ]
            },
        ),
        ("anthropic", {"stop_reason": "max_tokens", "content": []}),
        (
            "anthropic",
            {
                "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "c1", "name": "search_items", "input": []}],
            },
        ),
        ("gemini", {"candidates": [{"finishReason": "SAFETY"}]}),
        (
            "gemini",
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "search_items", "args": []}},
                            ]
                        },
                    }
                ]
            },
        ),
    ],
)
def test_malformed_tool_turn_never_executes_handler(monkeypatch, provider, body):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: io.BytesIO(
            json.dumps(body).encode(),
        ),
    )
    settings = config(provider)
    tools = registry()
    run = run_tool_loop(create_chat_adapter(settings), tools, settings, "question", {})
    tools.execute.assert_not_called()
    assert "No verified" in run.answer and run.warnings
