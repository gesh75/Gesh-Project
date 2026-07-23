"""Template composition failure modes must fail CLOSED for a customer."""
from __future__ import annotations

import pytest

import server


def test_missing_slot_for_a_block_is_not_silently_dropped(monkeypatch, caplog):
    """A block with no slot must be logged, never quietly omitted for the owner."""
    monkeypatch.setattr(server, "_owner_blocks",
                        lambda: {"nonexistent_block": "<div>x</div>"}, raising=True)
    with caplog.at_level("ERROR"):
        out = server._render_dashboard("owner")
    assert "unresolved owner console slots" in caplog.text.lower() or \
           "owner console slots unresolved" in caplog.text.lower()
    assert "GESH_SLOT" not in out


def test_unreadable_partial_degrades_without_leaking_to_the_customer(monkeypatch, caplog):
    def _boom():
        raise OSError("partial gone")
    monkeypatch.setattr(server, "_owner_blocks", _boom, raising=True)
    with pytest.raises(OSError):
        server._render_dashboard("owner")
    # the customer path never reads it, so it must still render
    out = server._render_dashboard("customer")
    assert "GESH_SLOT" not in out and "</html>" in out


def test_missing_partial_file_logs_and_returns_no_blocks(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(server, "OWNER_PARTIAL", tmp_path / "absent.html", raising=True)
    with caplog.at_level("ERROR"):
        blocks = server._owner_blocks()
    assert blocks == {}
    assert "owner console partial missing" in caplog.text


def test_duplicate_slot_markers_are_all_filled():
    """A slot repeated by a template edit must not leave one copy unresolved —
    `re.sub` replaces every occurrence, `str.replace(..., 1)` would not."""
    name = next(iter(server._owner_blocks()))
    doubled = (server.HTML.read_text()
               .replace(f"<!--GESH_SLOT:{name}-->",
                        f"<!--GESH_SLOT:{name}--><!--GESH_SLOT:{name}-->", 1))
    filled = server._SLOT_RE.sub(lambda m: "FILLED", doubled)
    assert "GESH_SLOT" not in filled
    assert filled.count("FILLED") == len(server._SLOT_RE.findall(server.HTML.read_text())) + 1


def test_customer_render_is_slot_free_and_well_formed():
    out = server._render_dashboard("customer")
    assert "GESH_SLOT" not in out
    assert out.lower().count("<script") == out.lower().count("</script>")
    assert out.count("</html>") == 1
