"""Pure helpers of the price bot that need no Telegram / network."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "price_bot"))

import docs  # noqa: E402
import fx  # noqa: E402

ECB_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time="2026-10-20"><Cube currency="USD" rate="1.0800"/><Cube currency="GBP" rate="0.8500"/></Cube></Cube>
</gesmes:Envelope>"""


def test_ecb_rates_to_eur_base(monkeypatch):
    monkeypatch.setattr(fx, "BASE_CURRENCY", "EUR")
    r = fx.parse_ecb_xml(ECB_SAMPLE)
    assert r["_date"] == "2026-10-20"
    assert abs(r["USD"] - 1 / 1.08) < 1e-9 and r["EUR"] == 1.0


def test_ecb_rates_to_usd_base(monkeypatch):
    monkeypatch.setattr(fx, "BASE_CURRENCY", "USD")
    r = fx.parse_ecb_xml(ECB_SAMPLE)
    assert abs(r["EUR"] - 1.08) < 1e-9 and r["USD"] == 1.0


def test_norm_currency():
    assert fx.norm_currency("€") == "EUR"
    assert fx.norm_currency("usd") == "USD"
    assert fx.norm_currency("RMB") == "CNY"


def test_docs_plain_and_unsupported():
    assert docs.extract("a.csv", b"part;price\nPT-200;980")["kind"] == "text"
    try:
        docs.extract("a.doc", b"x")
    except docs.DocError as e:
        assert "legacy" in str(e)
    else:
        raise AssertionError("expected DocError")
