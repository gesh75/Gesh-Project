from pathlib import Path

OWNER_CONSOLE = Path(__file__).resolve().parents[1] / "templates" / "owner_console.html"


def test_debate_balance_zero_bull_weight_does_not_use_default():
    html = OWNER_CONSOLE.read_text()

    assert "Number(dec.bull_weight??50)" in html
    assert "Number(dec.bull_weight)||50" not in html
