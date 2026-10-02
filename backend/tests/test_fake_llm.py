"""T0.3 — FakeLLM scripted responses, in order (deterministic test double)."""
import pytest

from app.llm import ChatResponse, FakeLLM


@pytest.mark.asyncio
async def test_fake_llm_scripted_order():
    responses = [
        ChatResponse(content="first", tool_calls=None),
        ChatResponse(content="second", tool_calls=None),
        ChatResponse(content="third", tool_calls=None),
    ]
    fake = FakeLLM(responses)

    r1 = await fake.chat(messages=[{"role": "user", "content": "hi"}])
    r2 = await fake.chat(messages=[{"role": "user", "content": "hi again"}])
    r3 = await fake.chat(messages=[{"role": "user", "content": "hi once more"}])

    assert r1.content == "first"
    assert r2.content == "second"
    assert r3.content == "third"


@pytest.mark.asyncio
async def test_fake_llm_raises_when_script_exhausted():
    fake = FakeLLM([ChatResponse(content="only one", tool_calls=None)])
    await fake.chat(messages=[])
    with pytest.raises(IndexError):
        await fake.chat(messages=[])
