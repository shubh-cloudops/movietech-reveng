from contextlib import contextmanager
from types import SimpleNamespace

import pydantic
import pytest

from movietech.claude import structured_call


class Answer(pydantic.BaseModel):
    text: str


def broken_json():
    try:
        Answer.model_validate_json('{"text": "cut of')
    except pydantic.ValidationError as e:
        return e


class FlakyClient:
    """Streams broken JSON `failures` times, then a valid answer."""

    def __init__(self, failures):
        self.failures, self.calls = failures, 0
        self.beta = SimpleNamespace(messages=self)
        self.message = SimpleNamespace(
            parsed_output=Answer(text="ok"), stop_reason="end_turn", model="claude-haiku-5-5",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0))

    @contextmanager
    def stream(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise broken_json()
        yield SimpleNamespace(get_final_message=lambda: self.message)


def call(client, tmp_path):
    return structured_call(client=client, model="claude-haiku-5-5", system="s", user="u",
                           output_format=Answer, effort="high", max_tokens=1000, max_usd=1.0,
                           stage="review_cost_ops", run_dir=tmp_path, cap_env="X")


def test_broken_json_is_asked_again_once(tmp_path):
    client = FlakyClient(failures=1)
    assert call(client, tmp_path).text == "ok"
    assert client.calls == 2


def test_broken_json_twice_names_the_stage_and_the_fix(tmp_path):
    with pytest.raises(RuntimeError, match="review_cost_ops.*claude-sonnet-5-5"):
        call(FlakyClient(failures=2), tmp_path)
