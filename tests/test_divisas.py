"""Divisas: cada cifra con su divisa real. Un franco suizo nunca sale como "$" ni una cifra en euros con "£".

Se ejecuta sin red:  python -m pytest -q
"""
import copy
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import enrich  # noqa: E402
import render_mail  # noqa: E402
import signals_eu  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def visible(h: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))


def eu_signals(tmp_path: Path) -> Path:
    d = json.loads((FIX / "signals_eu.json").read_text())
    for o in d["opportunities"]:
        o.setdefault("extra", {})
        if o["currency"] == "CHF":
            o["market_cap_local"] = 5_600_000_000
            o["extra"].update(target_price=123.45, target_upside=12.0, tv_currency="CHF")
        elif o["currency"] == "GBP":
            o["market_cap_local"] = 111_000_000
            o["extra"].update(target_price=4.31, target_upside=15.0, tv_currency="GBP")
    p = tmp_path / "signals.json"
    p.write_text(json.dumps(d))
    return p


def test_simbolos():
    assert render_mail.cur_sym("CHF") == "CHF"
    assert render_mail.cur_sym("GBP") == "£"
    assert render_mail.cur_sym("EUR") == "€"
    assert render_mail.cur_sym("USD") == "$"
    assert render_mail.cur_sym("SEK") == "SEK"  # sin símbolo conocido: código ISO, nunca "$" o "€"


def test_mail_eu_divisas(tmp_path):
    out = render_mail.render("eu", eu_signals(tmp_path))
    v = visible(out["html"])
    assert "$" not in v, "En el mail de Europa no puede salir '$' (ninguna acción cotiza en dólares)"
    assert "5.600 M CHF" in v and "objetivo 123,45 CHF" in v
    assert "111,00 M £" in v and "objetivo 4,31 £" in v
    assert re.search(r"\d CHF", v), "el precio de una acción suiza va en CHF"
    for item in out["meta"]["news_for"]:
        assert item["currency"] in ("EUR", "CHF", "GBP") and item["amounts_currency"] == "EUR"


def test_cap_sin_local_va_en_euros(tmp_path):
    d = json.loads((FIX / "signals_eu.json").read_text())
    for o in d["opportunities"]:
        o.pop("market_cap_local", None)
    p = tmp_path / "s.json"
    p.write_text(json.dumps(d))
    v = visible(render_mail.render("eu", p)["html"])
    assert not re.search(r"Cap\. [\d.,]+ M (CHF|£)", v), "la capitalización en euros no puede llevar el símbolo local"


def test_mail_us_dolares():
    v = visible(render_mail.render("us", FIX / "signals_us.json")["html"])
    # (El fixture lleva a propósito "1,5 M€", "£5" y "3 CHF" dentro de un texto libre: se miran las cifras del mail.)
    units = re.findall(r"(?:IMPORTE|PRECIO|Cap\.|Liquidez|objetivo) [\d.,]+ (?:M |mil )?(\S+)", v)
    assert units and all(u.startswith(("$", "M$")) for u in units), units


def test_objetivo_misma_divisa():
    base = {"close": 29.9, "currency": "EUR", "fundamental_currency_code": "EUR", "price_target_average": 50.15}
    assert round(enrich._extra(base)["target_upside"]) == 68
    # Objetivo en otra divisa que el precio (lo que pasaba sin price_conversion): sin dato, nunca un % falso.
    x = enrich._extra({**base, "fundamental_currency_code": "USD", "price_target_average": 55.88})
    assert x["target_upside"] is None and x["target_price"] is None
    # Londres: precio y objetivo en peniques; el objetivo se enseña en libras, como el precio.
    uk = enrich._extra({"close": 375.8, "currency": "GBX", "fundamental_currency_code": "GBX",
                        "price_target_average": 430.83})
    assert round(uk["target_upside"]) == 15 and uk["target_price"] == 4.31 and uk["tv_currency"] == "GBP"


def test_capitalizacion_divisa_cotizacion(monkeypatch):
    rows = {
        # Capitalización en USD (TradingView sin conversión): se pasa a CHF con los tipos del BCE.
        "CH0012005267": [{"name": "NOVN", "exchange": "SIX", "description": "", "close": 133.28, "currency": "CHF",
                          "float_shares_outstanding": 1.8e9, "total_shares_outstanding": 1.83e9,
                          "market_cap_basic": 295.7e9, "fundamental_currency_code": "USD",
                          "average_volume_30d_calc": 4e6, "type": "stock"}],
        # Londres en peniques: la capitalización también viene en peniques.
        "GB00B2PDGW16": [{"name": "SMWH", "exchange": "LSE", "description": "", "close": 375.8, "currency": "GBX",
                          "float_shares_outstanding": 1.4e8, "total_shares_outstanding": 1.505e8,
                          "market_cap_basic": 55.79e9, "fundamental_currency_code": "GBX",
                          "average_volume_30d_calc": 1e6, "type": "stock"}],
    }
    monkeypatch.setattr(signals_eu, "tv_lookup", lambda isins, market: {i: copy.deepcopy(rows[i]) for i in isins})
    d, err = signals_eu.market_data({"CH": ["CH0012005267"], "GB": ["GB00B2PDGW16"]}, {"CHF": 0.92, "GBP": 0.83, "USD": 1.1})
    assert not err
    ch, gb = d["CH0012005267"], d["GB00B2PDGW16"]
    assert ch["currency"] == "CHF" and 240e9 < ch["market_cap"] < 250e9
    assert gb["currency"] == "GBP" and gb["price"] == 3.758 and 550e6 < gb["market_cap"] < 570e6
