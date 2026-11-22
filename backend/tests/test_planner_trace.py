from __future__ import annotations

import json
import pytest

from backend.app.core.llm_config import GenerationOptions, LLMConfiguration, LLMProfile
from backend.app.core.errors import AppError
from backend.app.models.domain import DataType, RowRules, TargetColumn, TransformationSpec
from backend.app.services.planner import LLMPlanner


def baseline_spec() -> TransformationSpec:
    return TransformationSpec(
        sheet_name="ОСВ",
        header_row=2,
        data_start_row=3,
        data_end_row=5,
        column_mapping={"account": 1},
        target_columns=[TargetColumn(key="account", label="Счёт", kind="text", required=True)],
        row_rules=RowRules(inherit_fields=["account"]),
        confidence=0.9,
    )


PROFILE = {
    "sheet_count": 1,
    "sheets": [
        {
            "name": "ОСВ",
            "max_row": 5,
            "max_column": 1,
            "sample_rows": [{"row": 2, "values": ["Счёт"]}],
        }
    ],
}


def test_llm_cannot_remove_required_constraint():
    baseline = baseline_spec()
    candidate = baseline.model_copy(deep=True)
    candidate.target_columns[0].required = False
    with pytest.raises(AppError, match="целевую схему"):
        LLMPlanner._validate_candidate(candidate, baseline, PROFILE, None)


def test_ollama_planner_records_exact_request_and_response(monkeypatch):
    baseline = baseline_spec()
    configuration = LLMConfiguration(
        active_profile="local",
        profiles={
            "local": LLMProfile(
                protocol="ollama",
                base_url="http://127.0.0.1:11434",
                model="max-gpu-qwen:latest",
                thinking=False,
                no_think_suffix=True,
                keep_alive="10m",
                generation=GenerationOptions(context_size=16384),
            )
        },
    )
    posted: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "model": "max-gpu-qwen:latest",
                "message": {"role": "assistant", "content": baseline.model_dump_json()},
                "done": True,
            }

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, *, json):
            posted["url"] = url
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr("backend.app.services.llm.base.httpx.Client", FakeClient)
    events: list[tuple[str, dict]] = []

    result = LLMPlanner(configuration).refine(
        PROFILE,
        baseline,
        DataType.TRIAL_BALANCE,
        trace=lambda event_type, details: events.append((event_type, details)),
    )

    assert result == baseline
    assert [event[0] for event in events] == ["LLM_REQUEST", "LLM_RESPONSE", "LLM_SPEC_ACCEPTED"]
    assert posted["url"] == "http://127.0.0.1:11434/api/chat"
    assert posted["json"]["think"] is False
    assert posted["json"]["options"]["num_ctx"] == 16384
    assert posted["json"]["messages"][1]["content"].endswith("\n/no_think")
    assert events[0][1]["profile"] == "local"
    assert json.loads(events[1][1]["payload"]["message"]["content"])["sheet_name"] == "ОСВ"


def test_openai_compatible_planner_uses_json_schema(monkeypatch):
    baseline = baseline_spec()
    configuration = LLMConfiguration(
        active_profile="remote",
        profiles={
            "remote": LLMProfile(
                protocol="openai",
                base_url="http://192.168.1.65:1234/v1",
                model="qwen/qwen3.5-9b",
                structured_output=True,
            )
        },
    )
    posted: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "model": "qwen/qwen3.5-9b",
                "choices": [{"message": {"role": "assistant", "content": baseline.model_dump_json()}}],
            }

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            posted["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, *, json):
            posted["url"] = url
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr("backend.app.services.llm.base.httpx.Client", FakeClient)

    result = LLMPlanner(configuration).refine(PROFILE, baseline, DataType.TRIAL_BALANCE)

    assert result == baseline
    assert posted["url"] == "http://192.168.1.65:1234/v1/chat/completions"
    assert posted["json"]["model"] == "qwen/qwen3.5-9b"
    assert posted["client_kwargs"]["timeout"].connect == 10
    assert "think" not in posted["json"]
    assert not posted["json"]["messages"][1]["content"].endswith("/no_think")
    response_format = posted["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "TransformationSpec"


def test_planner_falls_back_to_next_profile(monkeypatch):
    baseline = baseline_spec()
    configuration = LLMConfiguration(
        active_profile="remote",
        fallback_profiles=["local"],
        profiles={
            "remote": LLMProfile(
                protocol="openai",
                base_url="http://192.0.2.1:1234/v1",
                model="unavailable",
            ),
            "local": LLMProfile(
                protocol="ollama",
                base_url="http://127.0.0.1:11434",
                model="max-gpu-qwen:latest",
            ),
        },
    )

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"message": {"role": "assistant", "content": baseline.model_dump_json()}}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, *, json):
            if url.startswith("http://192.0.2.1"):
                raise ConnectionError("remote unavailable")
            return FakeResponse()

    monkeypatch.setattr("backend.app.services.llm.base.httpx.Client", FakeClient)
    events: list[tuple[str, dict]] = []

    result = LLMPlanner(configuration).refine(
        PROFILE,
        baseline,
        DataType.TRIAL_BALANCE,
        trace=lambda event_type, details: events.append((event_type, details)),
    )

    assert result == baseline
    assert "LLM_PROFILE_FALLBACK" in [event[0] for event in events]
    fallback = next(details for event, details in events if event == "LLM_PROFILE_FALLBACK")
    assert fallback["from_profile"] == "remote"
    assert fallback["to_profile"] == "local"
