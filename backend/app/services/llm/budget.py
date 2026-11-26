"""One monotonic deadline shared by all LLM calls in a processing run."""
import asyncio
from time import monotonic

import httpx

from backend.app.core.errors import AppError


class LLMRequestError(AppError):
    """A timeout/transport failure may use a fallback only if time remains."""


class LLMTimeBudget:
    def __init__(self, seconds, clock=monotonic):
        self.seconds = seconds
        self.clock = clock
        self.started_at = None

    def start(self):
        if self.started_at is None:
            self.started_at = self.clock()

    @property
    def elapsed(self):
        return 0.0 if self.started_at is None else max(0.0, self.clock() - self.started_at)

    @property
    def remaining(self):
        return max(0.0, self.seconds - self.elapsed)

    def require_time(self, model):
        if self.remaining <= 0:
            raise LLMRequestError(504, f"Общий бюджет ожидания LLM ({self.seconds} с) исчерпан. "
                                  f"Запрос к модели «{model}» не отправлен.", "LLM_TIMEOUT")


async def _post(endpoint, payload, headers, timeout, connect_timeout):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(timeout, connect_timeout)),
        trust_env=False, headers=headers,
    ) as client:
        return await client.post(endpoint, json=payload)


def post_with_deadline(endpoint, payload, headers, timeout, connect_timeout):
    # wait_for limits the entire exchange, including a slowly arriving response.
    # Unlike a worker-thread timeout it cancels the socket operation and closes the client.
    async def run():
        return await asyncio.wait_for(_post(endpoint, payload, headers, timeout, connect_timeout), timeout)
    return asyncio.run(run())


def transport_error(adapter, error, duration=0):
    if isinstance(error, AppError):
        return error
    label = f"«{adapter.profile.model}» (профиль {adapter.profile_name})"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        budget = adapter.budget
        return LLMRequestError(504, f"Модель {label} не ответила за {duration:.1f} с. "
                               f"Общее ожидание LLM: {budget.elapsed:.1f} из {budget.seconds} с.", "LLM_TIMEOUT")
    return LLMRequestError(503, f"Модель {label} недоступна: {type(error).__name__}.", "LLM_UNAVAILABLE")
