"""Unit tests for validator.py."""
from validator import validate_llm_output


def test_valid_simple_selector():
    html = '<input name="email" type="email">'
    result = validate_llm_output(html, {
        "selector": "input[name='email']", "action": "fill", "confidence": 0.9,
    })
    assert result.valid
    assert result.match_count == 1


def test_hallucinated_selector():
    html = '<button class="btn">Submit</button>'
    result = validate_llm_output(html, {
        "selector": "[data-testid='submit']", "action": "click", "confidence": 0.9,
    })
    assert not result.valid
    assert result.failure_type == "no_match"


def test_ambiguous_selector():
    html = '<button class="btn">A</button><button class="btn">B</button>'
    result = validate_llm_output(html, {
        "selector": "button.btn", "action": "click", "confidence": 0.9,
    })
    assert not result.valid
    assert result.failure_type == "ambiguous"
    assert result.match_count == 2


def test_confidently_wrong_click_on_input():
    html = '<input name="email" type="email">'
    result = validate_llm_output(html, {
        "selector": "input[name='email']", "action": "click", "confidence": 0.95,
    })
    assert not result.valid
    assert result.failure_type == "confidently_wrong"


def test_null_selector():
    result = validate_llm_output("<html></html>", {
        "selector": None, "action": None, "confidence": 0.0,
    })
    assert not result.valid
    assert result.failure_type == "null_selector"
