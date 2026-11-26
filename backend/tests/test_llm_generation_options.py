import tomllib

import httpx
import pytest
from pydantic import ValidationError

from backend.app.core.errors import AppError
from backend.app.core.llm_config import GenerationOptions, LLMConfiguration, LLMProfile
from backend.app.services.llm import build_adapter
from backend.app.services.llm_settings import dump_llm_configuration
from backend.app.services.pipeline import _failure_details


def profile(protocol="openai", effort="none"):
    return LLMProfile(protocol=protocol, base_url="http://unused.test/v1", model="test",
                      reasoning_effort=effort,
                      generation=GenerationOptions(max_tokens=4096, candidate_max_tokens=512))


@pytest.mark.parametrize("protocol", ["openai", "ollama"])
def test_operation_limits_and_protocol_specific_thinking(protocol):
    adapter = build_adapter("test", profile(protocol))
    for operation, limit in [("CandidateChoice", 512), ("TransformationSpec", 4096), ("HeaderProposal", 4096)]:
        _, payload = adapter.build_request([], {}, operation)
        assert (payload["max_tokens"] if protocol == "openai" else payload["options"]["num_predict"]) == limit
        if protocol == "openai":
            assert payload["reasoning_effort"] == "none"
        else:
            assert payload["think"] is False
            assert "reasoning_effort" not in payload
    adapter.profile.generation.max_tokens = 128
    _, payload = adapter.build_request([], {}, "CandidateChoice")
    assert (payload["max_tokens"] if protocol == "openai" else payload["options"]["num_predict"]) == 128


def test_settings_round_trip_and_unspecified_effort():
    configuration = LLMConfiguration(active_profile="test", profiles={"test": profile()})
    serialized = dump_llm_configuration(configuration)
    restored = LLMConfiguration.model_validate(tomllib.loads(serialized))
    assert restored == configuration
    adapter = build_adapter("test", profile(effort=None))
    _, payload = adapter.build_request([], {}, "CandidateChoice")
    assert "reasoning_effort" not in payload
    assert "reasoning_effort" not in dump_llm_configuration(LLMConfiguration(
        active_profile="test", profiles={"test": adapter.profile}))


def test_invalid_settings_rejected():
    with pytest.raises(ValidationError):
        profile(effort="off")
    with pytest.raises(ValidationError):
        GenerationOptions(candidate_max_tokens=0)


@pytest.mark.parametrize("protocol", ["openai", "ollama"])
def test_truncated_json_is_not_accepted(monkeypatch, protocol):
    client = httpx.AsyncClient
    payload = {"choices": [{"finish_reason": "length", "message": {"content": '{"candidate_id":0}'}}]} if protocol == "openai" else {
        "done_reason": "length", "message": {"content": '{"candidate_id":0}'}}
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)), **kwargs))
    with pytest.raises(AppError) as exc:
        build_adapter("test", profile(protocol)).complete([], {}, "CandidateChoice")
    assert exc.value.code == "LLM_RESPONSE_TRUNCATED"
    assert "512" in exc.value.message
    assert "лимит токенов" in _failure_details(exc.value, "PLANNING")["recommendation"]
