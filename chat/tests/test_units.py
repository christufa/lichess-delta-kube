import pytest

from chess_chat.auth import NotAllowed, NotSignedIn
from chess_chat.ledger import Ledger
from chess_chat.pricing import UnknownPrice, cost_usd


def test_cost_opus():
    # 1M uncached input + 1M output on Opus 5.5 = $4 + $20.
    assert cost_usd("anthropic:claude-opus-5-5", input_tokens=1_000_000, output_tokens=1_000_000) == pytest.approx(24.0)


def test_cost_takes_cached_parts_out_of_input():
    # input_tokens counts cached tokens too (Pydantic AI); they're charged at the cache prices instead.
    usd = cost_usd("anthropic:claude-opus-5-5", input_tokens=1_150, output_tokens=20,
                   cache_read_tokens=1_000, cache_write_tokens=50)
    assert usd == pytest.approx((100 * 4 + 1_000 * 0.20 + 50 * 5 + 20 * 20) / 1e6)


def test_unknown_model_has_no_price():
    with pytest.raises(UnknownPrice):
        cost_usd("openai:something", input_tokens=1, output_tokens=1)


def test_ledger_per_user_and_total(tmp_path):
    led = Ledger(tmp_path / "s.sqlite3")
    led.add("a", 1.5)
    led.add("a", 0.5)
    led.add("b", 1.0)
    assert led.spent("a") == pytest.approx(2.0) and led.spent() == pytest.approx(3.0)
    assert Ledger(tmp_path / "s.sqlite3").spent() == pytest.approx(3.0)  # survives a restart
    assert led.spent(day="1999-01-01") == 0


def test_verifier(verifier, make_token):
    assert verifier.verify(f"Bearer {make_token()}").oid.startswith("3333")
    with pytest.raises(NotSignedIn):
        verifier.verify(None)
    with pytest.raises(NotSignedIn):
        verifier.verify("Bearer nonsense")
    with pytest.raises(NotSignedIn):
        verifier.verify(f"Bearer {make_token(aud='someone-else')}")
    with pytest.raises(NotAllowed):
        verifier.verify(f"Bearer {make_token(roles=())}")
    with pytest.raises(NotAllowed):
        verifier.verify(f"Bearer {make_token(scp='User.Read')}")
