"""Unit tests for the output-side token governor (pure, no I/O).

Covers: explicit-detail detection (EN+FA, user sovereignty), tiny-turn
allowlist classification (fail-open: unknown shapes stay standard), and
file-echo detection (diffs vs full pastes).
"""
from genesis_memory.core.output_governor import (
    TINY_TURN_MAX_TOKENS,
    detect_file_echo,
    is_tiny_turn,
    wants_detail,
)


def test_wants_detail_english():
    for text in [
        "Explain in detail please",
        "Give me a thorough analysis",
        "step by step guide",
        "Walk me through the code",
        "comprehensive overview",
        "elaborate on that",
        "exhaustive list",
        "long-form answer",
        "be verbose",
    ]:
        assert wants_detail(text) is True, text


def test_wants_detail_must_not_fire_on_brief():
    """The existing proxy diet test prompt must stay terse."""
    assert wants_detail("Explain event loops briefly") is False
    assert wants_detail("ok") is False
    assert wants_detail("") is False
    assert wants_detail(None) is False
    assert wants_detail("thanks!") is False


def test_wants_detail_persian():
    for text in [
        "لطفا مفصل توضیح بده",
        "قدم‌به‌قدم بگو",
        "قدم به قدم راهنمایی کن",
        "توضیح جامع بده",
        "کد کامل بده",
    ]:
        assert wants_detail(text) is True, text
    # جامعه (society) must not trip جامع; prose stays terse.
    assert wants_detail("وضعیت جامعه چطوره") is False


def test_tiny_turn_allowlist():
    for text in ["thanks", "Thanks!", "ok", "noted", "got it", "yes",
                 "good morning", "see you"]:
        assert is_tiny_turn(text) is True, text


def test_tiny_turn_persian():
    for text in ["ممنون", "مرسی!", "دمت گرم", "باشه", "چشم",
                 "سلام", "بله", "نه", "آره"]:
        assert is_tiny_turn(text) is True, text


def test_tiny_turn_fail_open():
    """Anything unrecognized is a standard turn — never capped."""
    assert is_tiny_turn("write a REST API") is False
    assert is_tiny_turn("why?") is False
    assert is_tiny_turn("ok, now build X") is False
    assert is_tiny_turn("x" * 121) is False
    assert is_tiny_turn("run ```pytest``` now") is False
    assert is_tiny_turn("") is False
    assert is_tiny_turn(None) is False
    assert TINY_TURN_MAX_TOKENS == 256


def _prompt_with_file(n=20):
    return "file a:\n" + "\n".join(f"line{i} code here {i}" for i in range(n))


def test_detect_file_echo_true():
    prompt = _prompt_with_file(20)
    block = "\n".join(f"line{i} code here {i}" for i in range(20))
    completion = "here is the file:\n```python\n" + block + "\n```\ndone"
    hit, ratio, lines = detect_file_echo(prompt, completion)
    assert hit is True
    assert ratio == 1.0
    assert lines == 20


def test_detect_file_echo_ignores_short_blocks():
    prompt = _prompt_with_file(20)
    completion = "```python\nline0 code here 0\nline1 code here 1\n```"
    hit, ratio, lines = detect_file_echo(prompt, completion)
    assert hit is False


def test_detect_file_echo_original_content_passes():
    prompt = _prompt_with_file(20)
    completion = "```python\n" + "\n".join(
        f"brand new function {i}()" for i in range(20)) + "\n```"
    hit, ratio, lines = detect_file_echo(prompt, completion)
    assert hit is False
    assert ratio == 0.0


def test_detect_file_echo_bad_inputs():
    assert detect_file_echo("", "```\nx\n```") == (False, 0.0, 0)
    assert detect_file_echo("prompt", "") == (False, 0.0, 0)
    assert detect_file_echo(None, None) == (False, 0.0, 0)
