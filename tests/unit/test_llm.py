"""LlmClient: structured JSON, one retry on invalid JSON, errors reported not raised, logging,
budget. The provider SDK is a stub: nothing here touches the network."""

import json
from types import SimpleNamespace

from app.schemas.enums import AgentStatus, AgentTask
from app.services.llm import (
    Budget,
    FakeLlmClient,
    LlmConfig,
    LoggedLlm,
    OpenAiCompatClient,
    UnavailableLlm,
    make_llm_client,
)

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


def reply(content: str, tokens_in: int = 100, tokens_out: int = 20):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=tokens_out),
    )


class StubSdk:
    """Answers from a list in order; an Exception in the list is raised."""

    def __init__(self, answers) -> None:
        self.answers, self.requests = list(answers), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def client(answers, **config) -> tuple[OpenAiCompatClient, StubSdk]:
    sdk = StubSdk(answers)
    cfg = LlmConfig(
        model="gpt-6-luna", endpoint="https://x",
        api_key="secret-key", **config,
    )  # fmt: skip
    return OpenAiCompatClient(cfg, sdk), sdk


def call(c):
    return c.complete_json(
        task="screen", system="SYS", user="USER", schema=SCHEMA, reasoning_effort="low"
    )


# --- the request -------------------------------------------------------------------------------


def test_the_request_is_text_only_with_a_strict_json_schema_and_no_tools() -> None:
    c, sdk = client([reply('{"a": "x"}')])
    result = call(c)
    (request,) = sdk.requests
    assert request["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"] == {
        "name": "screen",
        "strict": True,
        "schema": SCHEMA,
    }
    assert request["model"] == "gpt-6-luna"  # Azure takes the deployment name here
    assert request["reasoning_effort"] == "low" and request["timeout"] == 60.0
    forbidden = {"tools", "functions", "tool_choice", "function_call", "parallel_tool_calls"}
    assert not forbidden & set(request)  # no tools, no function calling, no browsing
    assert result.data == {"a": "x"} and result.status == AgentStatus.OK


def test_tokens_latency_and_cost_come_back() -> None:
    c, _ = client([reply('{"a": "x"}', 2_000_000, 1_000_000)], price_in=10.0, price_out=40.0)
    result = call(c)
    assert (result.tokens_in, result.tokens_out) == (2_000_000, 1_000_000)
    assert result.cost_inr == 60.0  # 2 x 10 + 1 x 40
    assert (
        result.model == "gpt-6-luna" and result.latency_ms >= 0
    )


# --- retry and failure -------------------------------------------------------------------------


def test_invalid_json_is_retried_once() -> None:
    c, sdk = client([reply("not json", 100, 5), reply('{"a": "ok"}', 100, 7)])
    result = call(c)
    assert len(sdk.requests) == 2 and result.status == AgentStatus.OK and result.data == {"a": "ok"}
    assert (result.tokens_in, result.tokens_out) == (200, 12)  # both attempts are paid for


def test_invalid_json_twice_is_reported_not_raised() -> None:
    c, sdk = client([reply("nope"), reply("<html>")])
    result = call(c)
    assert len(sdk.requests) == 2
    assert (
        result.status == AgentStatus.INVALID_JSON
        and result.data is None
        and "attempt 2" in result.detail
    )


def test_a_json_list_is_not_an_answer() -> None:
    c, _ = client([reply("[1, 2]"), reply('{"a": "fine"}')])
    assert call(c).data == {"a": "fine"}


def test_a_provider_error_is_reported_and_not_retried() -> None:
    c, sdk = client([TimeoutError("timed out")])
    result = call(c)
    assert len(sdk.requests) == 1
    assert result.status == AgentStatus.ERROR and "TimeoutError" in result.detail


def test_an_empty_answer_counts_as_invalid() -> None:
    c, _ = client([reply(""), reply("")])
    assert call(c).status == AgentStatus.INVALID_JSON


# --- configuration -----------------------------------------------------------------------------


def test_config_is_read_from_the_environment_and_the_key_is_never_shown() -> None:
    cfg = LlmConfig.from_env(
        {
            "AWS_GPT_LUNA_MODEL": "gpt-6-luna", "AWS_GPT_LUNA_KEY": "s3cret",
            "AWS_GPT_LUNA_ENDPOINT": "https://e", "AWS_REGION": "ap-south-1",
        }
    )  # fmt: skip
    assert cfg.region == "ap-south-1" and cfg.problem() is None
    assert "s3cret" not in repr(cfg) and "s3cret" not in str(cfg)


def test_configuration_problems_are_named() -> None:
    assert "AWS_GPT_LUNA_KEY" in LlmConfig.from_env({}).problem()
    assert "AWS_GPT_LUNA_ENDPOINT" in LlmConfig.from_env({"AWS_GPT_LUNA_KEY": "k"}).problem()
    both = {"AWS_GPT_LUNA_KEY": "k", "AWS_GPT_LUNA_ENDPOINT": "https://e"}
    assert LlmConfig.from_env(both).problem() is None


def test_an_unconfigured_client_reports_error_instead_of_raising() -> None:
    llm = make_llm_client(LlmConfig())
    assert isinstance(llm, UnavailableLlm)
    result = llm.complete_json(task="screen", system="", user="", schema={}, reasoning_effort="low")
    assert result.status == AgentStatus.ERROR and "AWS_GPT_LUNA_KEY" in result.detail


# --- logging and budget ------------------------------------------------------------------------


def test_every_call_is_logged_with_its_versions_and_cost() -> None:
    fake = FakeLlmClient({"screen": {"x": 1}}, cost_inr=1.25)
    llm = LoggedLlm(fake)
    budget = Budget(0.0, 10.0)
    done = llm.call(
        budget=budget, task=AgentTask.SCREEN, prompt_version="v1", guidance_version=3,
        system="s", user="u", schema=SCHEMA, effort="low", run_id="run-1", item_id="item-1",
    )  # fmt: skip
    r = done.record
    assert (r.task, r.status, r.prompt_version, r.guidance_version) == (
        AgentTask.SCREEN,
        AgentStatus.OK,
        "v1",
        3,
    )
    assert (r.tokens_in, r.tokens_out, r.cost_inr, r.run_id, r.item_id) == (
        800,
        150,
        1.25,
        "run-1",
        "item-1",
    )
    assert r.model == "gpt-6-luna" and r.deployment == "fake-deployment"
    assert budget.spent == 1.25


def test_failures_are_logged_too() -> None:
    llm = LoggedLlm(FakeLlmClient({"screen": RuntimeError("down")}))
    done = llm.call(
        budget=Budget(0, None), task=AgentTask.SCREEN, prompt_version="v1", guidance_version=None,
        system="s", user="u", schema=SCHEMA, effort="low",
    )  # fmt: skip
    assert done.record.status == AgentStatus.ERROR and "down" in done.record.detail


def test_a_used_up_budget_sends_nothing() -> None:
    fake = FakeLlmClient({"screen": {"x": 1}})
    done = LoggedLlm(fake).call(
        budget=Budget(10.0, 10.0), task=AgentTask.SCREEN, prompt_version="v1",
        guidance_version=None, system="s", user="u", schema=SCHEMA, effort="low",
    )  # fmt: skip
    assert (
        fake.calls == [] and done.record.status == AgentStatus.BUDGET and done.record.cost_inr == 0
    )


def test_no_cap_means_no_limit() -> None:
    assert not Budget(10_000.0, None).exhausted()


def test_fake_client_records_requests_and_scripts_failures() -> None:
    fake = FakeLlmClient(
        {"screen": "not json", "extract": ValueError("x"), "sweep": lambda s, u: {"ok": 1}}
    )
    assert (
        fake.complete_json(
            task="screen", system="S", user="U", schema={}, reasoning_effort="low"
        ).status
        == AgentStatus.INVALID_JSON
    )
    assert (
        fake.complete_json(
            task="extract", system="S", user="U", schema={}, reasoning_effort="low"
        ).status
        == AgentStatus.ERROR
    )
    assert fake.complete_json(
        task="sweep", system="S", user="U", schema={}, reasoning_effort="low"
    ).data == {"ok": 1}
    assert [c["task"] for c in fake.calls] == ["screen", "extract", "sweep"]
    json.dumps(fake.calls[0]["schema"])  # requests are plain data
