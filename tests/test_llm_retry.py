from pydantic import BaseModel

import app.llm as llm


class Out(BaseModel):
    value: str


class FakeLLM:
    def __init__(self, errors):
        self.errors, self.calls = list(errors), []

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        if self.errors:
            raise self.errors.pop(0)
        return Out(value="ok")


def test_format_error_is_retried(monkeypatch):
    fake = FakeLLM([Exception("Error code: 400 tool_use_failed: model did not call a tool")])
    monkeypatch.setattr(llm, "get_llm", lambda: fake)
    assert llm.invoke_structured(Out, []).value == "ok"
    assert len(fake.calls) == 2 and "ONLY by calling" in fake.calls[1][-1].content


def test_other_errors_are_not_retried(monkeypatch):
    fake = FakeLLM([RuntimeError("network down")])
    monkeypatch.setattr(llm, "get_llm", lambda: fake)
    try:
        llm.invoke_structured(Out, [])
        raise AssertionError("should have raised")
    except RuntimeError:
        assert len(fake.calls) == 1