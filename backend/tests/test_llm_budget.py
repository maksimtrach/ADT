import asyncio
import json
import time
import tomllib

import httpx
import pytest

from backend.app.core.errors import AppError
from backend.app.core.llm_config import LLMConfiguration, LLMProfile
from backend.app.services.llm.budget import LLMTimeBudget, post_with_deadline
from backend.app.services.llm_settings import dump_llm_configuration
from backend.app.services.planner import LLMPlanner
from backend.tests.test_candidate_requests import selection


def planner_with_clock():
    config = LLMConfiguration(active_profile="first", fallback_profiles=["second"], total_timeout_seconds=10,
                              profiles={name: LLMProfile(protocol="openai", base_url="http://unused.test/v1",
                                                         model=name, timeout_seconds=8)
                                        for name in ("first", "second")})
    planner = LLMPlanner(config)
    now = [0.0]
    budget = LLMTimeBudget(10, clock=lambda: now[0])
    planner.budget = budget
    for adapter in planner.adapters:
        adapter.budget = budget
    return planner, now


@pytest.mark.parametrize("spent,expected_calls", [(7, 2), (10, 1)])
def test_fallback_only_gets_remaining_budget(monkeypatch, spent, expected_calls):
    planner, now = planner_with_clock()
    calls = []
    def post(endpoint, payload, headers, timeout, connect_timeout):
        calls.append((payload["model"], timeout))
        if len(calls) == 1:
            assert timeout == 8
            now[0] += spent
            raise httpx.ReadTimeout("test timeout")
        assert timeout == 3
        return httpx.Response(200, request=httpx.Request("POST", endpoint), json={"choices": [{
            "message": {"content": json.dumps({"candidate_id": 0, "reason": "По данным"})}}]})
    monkeypatch.setattr("backend.app.services.llm.base.post_with_deadline", post)
    if expected_calls == 1:
        with pytest.raises(AppError) as error:
            planner.choose_candidate(selection())
        assert error.value.code == "LLM_TIMEOUT"
        assert "first" in error.value.message
        assert "10" in error.value.message
    else:
        assert planner.choose_candidate(selection()).spec.column_mapping["account_name"] == 2
    assert len(calls) == expected_calls


def test_budget_is_shared_across_stages_and_rejects_new_request(monkeypatch):
    planner, now = planner_with_clock()
    adapter = planner.adapters[0]
    def post(endpoint, payload, headers, timeout, connect_timeout):
        now[0] += 6
        return httpx.Response(200, request=httpx.Request("POST", endpoint), json={"choices": [{"message": {"content": "{}"}}]})
    monkeypatch.setattr("backend.app.services.llm.base.post_with_deadline", post)
    adapter.complete([], {}, "Header")
    assert planner.adapters[1].budget.remaining == 4
    now[0] += 4
    monkeypatch.setattr("backend.app.services.llm.base.post_with_deadline", lambda *a: pytest.fail("Бюджет исчерпан"))
    with pytest.raises(AppError, match="не отправлен") as error:
        planner.adapters[1].complete([], {}, "Footer")
    assert error.value.code == "LLM_TIMEOUT"


def test_deadline_cancels_slow_request_and_closes_client(monkeypatch):
    closed = []
    cancelled = []
    class SlowClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
        async def post(self, *args, **kwargs):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
    monkeypatch.setattr("backend.app.services.llm.budget.httpx.AsyncClient", SlowClient)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        post_with_deadline("http://unused.test", {}, {}, 0.02, 5)
    assert time.monotonic() - started < 0.5
    assert closed == [True]
    assert cancelled == [True]


def test_budget_round_trip():
    planner, _ = planner_with_clock()
    decoded = LLMConfiguration.model_validate(tomllib.loads(dump_llm_configuration(planner.configuration)))
    assert decoded.total_timeout_seconds == 10
