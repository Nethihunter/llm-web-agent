"""Unit tests for html_processor.py."""
from html_processor import (
    extract_clean_html, get_dom_signature, reduction_stats,
)


def test_reduction_removes_scripts():
    raw = """
    <html><head><script>alert('x')</script><style>.a{}</style></head>
    <body><button id="ok">Click</button></body></html>
    """
    clean = extract_clean_html(raw)
    assert "<script>" not in clean
    assert "<style>" not in clean
    assert "button" in clean


def test_reduction_keeps_priority_attributes():
    raw = '<button id="submit" data-testid="btn" class="a b c d e f">OK</button>'
    clean = extract_clean_html(raw)
    assert 'id="submit"' in clean
    assert 'data-testid="btn"' in clean
    # class capped to 4 entries
    assert clean.count(" ") < 20


def test_reduction_stats():
    raw = "<button>" + "x" * 1000 + "</button>"
    clean = extract_clean_html(raw)
    stats = reduction_stats(raw, clean)
    assert stats["reduction_pct"] > 50
    assert stats["raw_chars"] > stats["clean_chars"]


def test_dom_signature_stable():
    html1 = '<button id="a">X</button><input name="b">'
    html2 = '<input name="b"><button id="a">X</button>'
    assert get_dom_signature(html1) == get_dom_signature(html2)


def test_dom_signature_different_content():
    html1 = '<button>Login</button>'
    html2 = '<button>Register</button>'
    assert get_dom_signature(html1) != get_dom_signature(html2)
