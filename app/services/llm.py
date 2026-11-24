"""LLM access for the Luna agent: one small interface, two providers, a fake for tests.

`LlmClient.complete_json` sends TEXT ONLY (system + user) and asks for JSON that matches a schema.
There are no tools, no function calling and no browsing in any request (CLAUDE.md rule 11).

- Invalid JSON is retried once; a second failure is reported as INVALID_JSON.
- A timeout or provider error is reported as ERROR (no retry, no crash): the caller carries on
  without suggestions.
- Every call comes back with model, deployment, tokens, latency and cost so the caller can log it
  to `agent_run` (`LoggedLlm`), and a daily budget stops further calls.

Settings come from the environment (.env): AWS_GPT_LUNA_KEY / _ENDPOINT / _MODEL, AWS_REGION
The model is called through an OpenAI-compatible endpoint (AWS_GPT_LUNA_ENDPOINT).
The key is never logged.
"""

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from app.repositories.base import Repository
from app.schemas.entities import AgentRun, now_ist
from app.schemas.enums import AgentStatus, AgentTask

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_TIMEOUT = 60.0
# Placeholder prices in INR per million tokens; set the real ones with LLM_PRICE_*_INR_PER_1M.
DEFAULT_PRICE_IN = 10.0
DEFAULT_PRICE_OUT = 40.0


@dataclass
class LlmResult:
    data: dict[str, Any] | None
    status: AgentStatus = AgentStatus.OK
    model: str = DEFAULT_MODEL
    deployment: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_inr: float = 0.0
    detail: str | None = None


class LlmClient(Protocol):
    model: str
    deployment: str | None

    def complete_json(
        self, *, task: str, system: str, user: str, schema: dict[str, Any], reasoning_effort: str
    ) -> LlmResult: ...


# --- configuration -----------------------------------------------------------------------------


@dataclass(frozen=True)
class LlmConfig:
    model: str = DEFAULT_MODEL
    endpoint: str = ""
    region: str = ""
    api_key: str = field(default="", repr=False)  # never printed
    timeout: float = DEFAULT_TIMEOUT
    price_in: float = DEFAULT_PRICE_IN
    price_out: float = DEFAULT_PRICE_OUT

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LlmConfig":
        e = env if env is not None else os.environ

        def first(*names: str, default: str = "") -> str:
            return next((e[n].strip() for n in names if e.get(n, "").strip()), default)

        return cls(
            model=first("AWS_GPT_LUNA_MODEL", default=DEFAULT_MODEL),
            endpoint=first("AWS_GPT_LUNA_ENDPOINT"),
            region=first("AWS_REGION"),
            api_key=first("AWS_GPT_LUNA_KEY"),
            timeout=float(first("LLM_TIMEOUT_SECONDS", default=str(DEFAULT_TIMEOUT))),
            price_in=float(first("LLM_PRICE_IN_INR_PER_1M", default=str(DEFAULT_PRICE_IN))),
            price_out=float(first("LLM_PRICE_OUT_INR_PER_1M", default=str(DEFAULT_PRICE_OUT))),
        )

    def problem(self) -> str | None:
        """Why this configuration cannot be used, or None when it can."""
        if not self.api_key:
            return "AWS_GPT_LUNA_KEY is not set"
        if not self.endpoint:
            return "AWS_GPT_LUNA_ENDPOINT is not set"
        return None


class UnavailableLlm:
    """Stands in when the provider is not configured: every call reports ERROR, never raises."""

    deployment = None

    def __init__(self, reason: str, model: str = DEFAULT_MODEL) -> None:
        self.model, self._reason = model, reason

    def complete_json(self, **_: Any) -> LlmResult:
        return LlmResult(None, AgentStatus.ERROR, self.model, detail=self._reason)


def make_llm_client(config: LlmConfig | None = None, sdk: Any = None) -> LlmClient:
    config = config or LlmConfig.from_env()
    problem = config.problem()
    return UnavailableLlm(problem, config.model) if problem else OpenAiCompatClient(config, sdk)


# --- the real client ---------------------------------------------------------------------------


class OpenAiCompatClient:
    """OpenAI-compatible chat completions (AWS endpoint). Text in, JSON out."""

    def __init__(self, config: LlmConfig, sdk: Any = None) -> None:
        self._config = config
        self.model = config.model
        self.deployment = None  # no separate deployment name: the model id is sent as is
        self._sdk = sdk  # injected in tests; built on first use otherwise

    def _client(self) -> Any:
        if self._sdk is None:
            c = self._config
            from openai import OpenAI

            self._sdk = OpenAI(base_url=c.endpoint, api_key=c.api_key, timeout=c.timeout)
        return self._sdk

    def complete_json(
        self, *, task: str, system: str, user: str, schema: dict[str, Any], reasoning_effort: str
    ) -> LlmResult:
        started = time.monotonic()
        tokens_in = tokens_out = 0
        detail = None
        for attempt in (1, 2):  # one retry, only for invalid JSON
            try:
                response = self._client().chat.completions.create(
                    model=self.deployment or self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    # Structured output. There is deliberately no `tools` / `functions` argument.
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": task, "strict": True, "schema": schema},
                    },
                    reasoning_effort=reasoning_effort,
                    timeout=self._config.timeout,
                )
            except Exception as exc:  # timeout, rate limit, network, auth: report, do not raise
                return self._result(
                    None, AgentStatus.ERROR, started, tokens_in, tokens_out,
                    f"{type(exc).__name__}: {str(exc)[:200]}",
                )  # fmt: skip
            usage = getattr(response, "usage", None)
            tokens_in += int(getattr(usage, "prompt_tokens", 0) or 0)
            tokens_out += int(getattr(usage, "completion_tokens", 0) or 0)
            content = (response.choices[0].message.content or "") if response.choices else ""
            try:
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise ValueError("the answer is not a JSON object")
                return self._result(data, AgentStatus.OK, started, tokens_in, tokens_out, None)
            except ValueError as exc:
                detail = f"Invalid JSON (attempt {attempt}): {str(exc)[:120]}"
        return self._result(None, AgentStatus.INVALID_JSON, started, tokens_in, tokens_out, detail)

    def _result(self, data, status, started, tokens_in, tokens_out, detail) -> LlmResult:  # type: ignore[no-untyped-def]
        cost = tokens_in / 1e6 * self._config.price_in + tokens_out / 1e6 * self._config.price_out
        return LlmResult(
            data, status, self.model, self.deployment, tokens_in, tokens_out,
            int((time.monotonic() - started) * 1000), round(cost, 4), detail,
        )  # fmt: skip


# --- a fake for tests --------------------------------------------------------------------------


class FakeLlmClient:
    """Scripted answers, no network. Records every request so tests can inspect what was sent.

    `script[task]` is a dict (the JSON answer), a str (invalid JSON), an Exception (provider error),
    or a callable (system, user) -> one of those. Tasks are "screen", "extract" and "sweep".
    """

    model = DEFAULT_MODEL
    deployment: str | None = "fake-deployment"

    def __init__(self, script: dict[str, Any] | None = None, cost_inr: float = 0.5) -> None:
        self.script = dict(script or {})  # a copy: tests change it per case
        self.calls: list[dict[str, Any]] = []
        self._cost = cost_inr
        self._lock = threading.Lock()

    def complete_json(
        self, *, task: str, system: str, user: str, schema: dict[str, Any], reasoning_effort: str
    ) -> LlmResult:
        with self._lock:
            self.calls.append(
                {"task": task, "system": system, "user": user, "schema": schema,
                 "reasoning_effort": reasoning_effort}
            )  # fmt: skip
        answer = self.script.get(task)
        if callable(answer):
            answer = answer(system, user)
        base = {"model": self.model, "deployment": self.deployment, "tokens_in": 800,
                "tokens_out": 150, "latency_ms": 5, "cost_inr": self._cost}  # fmt: skip
        if isinstance(answer, Exception):
            return LlmResult(None, AgentStatus.ERROR, detail=str(answer), **base)
        if isinstance(answer, str) or answer is None:
            return LlmResult(None, AgentStatus.INVALID_JSON, detail="not valid JSON", **base)
        return LlmResult(answer, AgentStatus.OK, **base)


# --- logging and budget ------------------------------------------------------------------------


class Budget:
    """Today's spend against the daily cap. Thread-safe: calls run up to four at a time."""

    def __init__(self, spent: float, cap: float | None) -> None:
        self._spent, self._cap, self._lock = spent, cap, threading.Lock()

    @property
    def spent(self) -> float:
        return self._spent

    def exhausted(self) -> bool:
        with self._lock:
            return self._cap is not None and self._spent >= self._cap

    def add(self, cost: float) -> None:
        with self._lock:
            self._spent += cost


def todays_spend(repo: Repository, day: date | None = None) -> float:
    return sum(r.cost_inr for r in repo.list_agent_runs(day or now_ist().date()))


@dataclass
class Call:
    """One LLM call: what came back and the `agent_run` row to store for it."""

    result: LlmResult
    record: AgentRun


class LoggedLlm:
    """Wraps a client: checks the budget first, then records every call (also failures)."""

    def __init__(self, client: LlmClient, new_id: Callable[[], str] | None = None) -> None:
        self.client = client
        self._new_id = new_id or (lambda: f"agr-{uuid.uuid4().hex[:10]}")

    def call(
        self,
        *,
        budget: Budget,
        task: AgentTask,
        prompt_version: str,
        guidance_version: int | None,
        system: str,
        user: str,
        schema: dict[str, Any],
        effort: str,
        run_id: str | None = None,
        item_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> Call:
        if budget.exhausted():
            result = LlmResult(
                None, AgentStatus.BUDGET, self.client.model, self.client.deployment,
                detail="The daily budget is used up; nothing was sent",
            )  # fmt: skip
        else:
            result = self.client.complete_json(
                task=task.value.lower(), system=system, user=user, schema=schema,
                reasoning_effort=effort,
            )  # fmt: skip
            budget.add(result.cost_inr)
        record = AgentRun(
            id=self._new_id(),
            run_id=run_id,
            task=task,
            item_id=item_id,
            snapshot_id=snapshot_id,
            model=result.model,
            deployment=result.deployment,
            prompt_version=prompt_version,
            guidance_version=guidance_version,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
            cost_inr=result.cost_inr,
            status=result.status,
            detail=result.detail,
            created_at=now_ist(),
        )
        return Call(result, record)
