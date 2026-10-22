from __future__ import annotations

from decimal import Decimal

from dr_core.config import Settings
from dr_core.providers.models import LLMResponse
from dr_core.research.usage import UsageRecordingLLM


class _Provider:
    name = "fake-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return LLMResponse(
            text="ok",
            model=self.name,
            input_tokens=40,
            output_tokens=20,
        )


def test_usage_wrapper_delegates_and_reports_the_response():
    provider = _Provider()
    recorded: list[LLMResponse] = []
    llm = UsageRecordingLLM(provider, recorded.append)

    response = llm.generate(
        "prompt",
        system="system",
        max_tokens=64,
        temperature=0.2,
    )

    assert llm.name == "fake-model"
    assert response.text == "ok"
    assert provider.calls == [
        {
            "prompt": "prompt",
            "system": "system",
            "max_tokens": 64,
            "temperature": 0.2,
        }
    ]
    assert recorded == [response]


def test_usage_wrapper_does_not_report_failed_calls():
    class _Failing(_Provider):
        def generate(self, prompt: str, **_: object) -> LLMResponse:
            raise RuntimeError("endpoint down")

    recorded: list[LLMResponse] = []
    llm = UsageRecordingLLM(_Failing(), recorded.append)

    try:
        llm.generate("prompt")
    except RuntimeError:
        pass
    assert recorded == []


def test_llm_usage_cost_is_zero_without_prices():
    settings = Settings(_env_file=None)
    assert settings.llm_usage_cost(1_000_000, 1_000_000) == Decimal("0")


def test_llm_usage_cost_uses_per_million_rates():
    settings = Settings(
        _env_file=None,
        llm_cost_per_1m_input_tokens=2.5,
        llm_cost_per_1m_output_tokens=10,
    )
    # (2.5 * 40 + 10 * 20) / 1_000_000
    assert settings.llm_usage_cost(40, 20) == Decimal("0.0003")
    assert settings.llm_usage_cost(1_000_000, 0) == Decimal("2.5")
    assert settings.llm_usage_cost(0, 1_000_000) == Decimal("10")
    assert settings.llm_usage_cost(0, 0) == Decimal("0")
    assert settings.llm_usage_cost(-5, -5) == Decimal("0")

    # the run column stores 6 decimals
    single = Settings(_env_file=None, llm_cost_per_1m_input_tokens=1)
    assert single.llm_usage_cost(1, 0) == Decimal("0.000001")
