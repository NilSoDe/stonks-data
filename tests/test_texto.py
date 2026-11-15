"""Vigila que no vuelvan los "?" / "�" / "Ã©" ni los datos nulos pintados, y otras regresiones del mail.

Se ejecuta sin red:  python -m pytest -q
"""
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import net  # noqa: E402
import render_infografia  # noqa: E402
import render_mail  # noqa: E402
import sec_edgar  # noqa: E402
import signals  # noqa: E402
import signals_eu  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
URL = "https://nilsode.github.io/stonks-data/us/2026-11-13/"
# Lo que tiene que llegar intacto al lector: acentos, eñes, diéresis, ß, divisas, rayas, comillas y símbolos.
TOKENS = ["Müller-Weiß", "Société Générale", "1,5 M€", "—", "«ojo»", "£5", "3 CHF", "’ya’", "“citado”", "≥ 10 %", "→"]
MOJIBAKE = re.compile(r"�|Ã[\x80-\xbf©¡³±¼¶¤]|â€|Â[\xa0 £·]")
NULOS = re.compile(r"\b(None|nan|NaN|null|undefined|inf)\b")


def visible(h: str) -> str:
    """Texto que ve el lector: sin etiquetas (los '?' de las URL no cuentan) y con las entidades resueltas."""
    return html.unescape(re.sub(r"<[^>]+>", " ", h))


def check_html(h: str) -> str:
    assert h.isascii(), "el HTML debe ser ASCII puro (entidades numéricas)"
    h.encode("latin-1")  # sobrevive aunque alguien lo guarde en Latin-1, que es donde salían los '?'
    txt = visible(h)
    assert not MOJIBAKE.search(txt), MOJIBAKE.search(txt)
    assert "?" not in re.sub(r"\{\{\w+\}\}", "", txt), "hay un '?' en el texto visible"
    assert not NULOS.search(txt), NULOS.search(txt)
    return txt


# --------------------------------------------------------------------------- codificación al leer las fuentes

@pytest.mark.parametrize("enc", ["utf-8", "utf-8-sig", "cp1252", "latin-1"])
def test_text_decodifica_sin_inventar(enc):
    s = "Jürgen Großmann; José Peña; Société Générale; £5"
    assert net.text(s.encode(enc)) == s
    assert net.text("Precio 5 €".encode("cp1252")) == "Precio 5 €"


def _resp(body: bytes, ctype: str) -> requests.Response:
    r = requests.Response()
    r._content, r.status_code = body, 200
    r.headers["content-type"] = ctype
    return r


@pytest.mark.parametrize("body,ctype", [
    ("Müller é ñ €".encode("utf-8"), "text/html"),                 # sin charset: requests supondría ISO-8859-1
    ("Müller é ñ €".encode("utf-8"), "text/xml"),
    ("Müller é ñ €".encode("cp1252"), "text/html; charset=windows-1252"),
    ("Müller é ñ".encode("latin-1"), "text/csv"),
])
def test_respuestas_sin_charset(body, ctype):
    r = _resp(body, ctype)
    assert net.response_text(r).startswith("Müller é ñ")
    assert net.fix_encoding(r).text.startswith("Müller é ñ")


FORM4 = """<SEC-DOCUMENT>
<ACCEPTANCE-DATETIME>20261112163043
<XML>
<ownershipDocument><documentType>4</documentType>
<issuer><issuerName>Nestlé Señales S.A.</issuerName><issuerTradingSymbol>NSRGY</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>Peña Núñez José</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-11-10</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares>
<transactionPricePerShare><value>10.5</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>
</XML>
</SEC-DOCUMENT>"""


def test_form4_de_la_sec_con_acentos():
    # La SEC sirve el .txt sin declarar codificación; antes "é" llegaba como "Ã©".
    row, _ = sec_edgar.parse_filing(net.text(FORM4.encode("utf-8")), "edgar/data/1/0000000000-26-000001.txt")
    assert row["Insider Name"] == "Peña Núñez José" and row["Company Name"] == "Nestlé Señales S.A."
    assert row["Title"] == "CEO" and row["Value"] == "+$10,500"


# --------------------------------------------------------------------------- mails

@pytest.mark.parametrize("kind,url", [("us", ""), ("us", URL), ("eu", ""), ("eu", URL.replace("/us/", "/eu/"))])
def test_mail_sin_interrogantes(kind, url):
    out = render_mail.render(kind, FIX / f"signals_{kind}.json", report_url=url)
    txt = check_html(out["html"])
    for tok in TOKENS:
        assert tok in txt, f"se ha perdido «{tok}»"
    assert ("Señales" in txt or "Señal" in txt) and "Zürich" in txt
    if kind == "us":
        assert "Íñigo José Peña" in txt or "Peña" in txt
        assert "declarada también por" in txt and "Großmann" in txt
    else:
        assert "Íñigo Peñalver-Núñez" in txt and "Compañía Española de Señales" in txt and "Münchener" in txt
    assert out["meta"]["html_bytes"] == len(out["html"].encode()) <= render_mail.BUDGET
    assert ("VER INFORME COMPLETO" in out["html"]) == bool(url)
    assert out["meta"]["report_url"] == url


def test_penny_stock_resaltada():
    out = render_mail.render("us", FIX / "signals_us.json")["html"]
    assert out.count("PENNY STOCK") == 1 and out.count("border:2px solid #E4559A;background:#FBE6EF") == 1


@pytest.mark.parametrize("kind", ["us", "eu"])
def test_version_web_completa(kind):
    d = json.loads((FIX / f"signals_{kind}.json").read_text(encoding="utf-8"))
    h = render_mail.render(kind, FIX / f"signals_{kind}.json", web=True)["html"]
    txt = check_html(h)
    assert "{{" not in h and 'content="noindex,nofollow,noarchive"' in h and '<meta charset="utf-8">' in h
    assert len(re.findall(r'<a id="c-', h)) == len(d["opportunities"]) and '<a id="descartadas">' in h
    for tok in TOKENS:
        assert tok in txt


def test_presupuesto_y_enlaces_cuando_no_cabe(tmp_path):
    """Con muchas oportunidades el cuerpo no pasa del presupuesto y lo que no cabe enlaza a su ficha completa."""
    d = json.loads((FIX / "signals_us.json").read_text(encoding="utf-8"))
    base = [o for o in d["opportunities"] if o.get("extra")]
    d["opportunities"] = [dict(base[i % len(base)], ticker=f"T{i:02d}") for i in range(45)]
    src = tmp_path / "signals.json"
    src.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    out = render_mail.render("us", src, report_url=URL)
    m = out["meta"]
    assert m["html_bytes"] <= render_mail.BUDGET and m["html_bytes"] + 4000 <= m["max_bytes"] < 102_400
    assert 0 < m["n_cards"] < 45
    for i in range(m["n_cards"], 45):  # cada fila de "Otras oportunidades" lleva a su tarjeta completa
        assert f'{URL}mail.html#c-T{i:02d}"' in out["html"]
    check_html(out["html"])


def test_infografia_ascii():
    h = render_infografia.render(FIX / "signals_us.json")
    assert h is not None
    h = render_infografia.ascii_safe(h)
    assert h.isascii() and "�" not in h


# --------------------------------------------------------------------------- lógica de datos

def _row(ticker, name, title, qty, owned, price="$8.08", day="2026-11-06"):
    return {"Ticker": ticker, "Insider Name": name, "Title": title, "Trade Date": day, "Price": price, "Qty": qty,
            "Owned": owned, "Value": "+$100,000", "Trade Type": "P - Purchase"}


def test_compras_duplicadas():
    rows = [_row("ETRA", "Gordon Carl L", "Dir, 10%", "+92,116", "2,194,579"),
            _row("ETRA", "Orbimed Advisors LLC", "Dir, 10%", "+92,116", "2,194,579"),      # declaración conjunta
            _row("CTEV", "Ares Management LLC", "10%", "+115,579", "2,049,518"),
            _row("CTEV", "Ares Management LLC", "10%", "+115,579", "2,049,518"),          # mismo Form 4 dos veces
            _row("OBIO", "Fain Eric S", "Dir", "+20,000", "119,630", "$5.00"),
            _row("OBIO", "Pacitti David", "Dir", "+20,000", "74,750", "$5.00")]           # dos personas distintas
    out, removed = signals.dedupe_rows(rows)
    assert removed == 2 and [r["Insider Name"] for r in out] == ["Orbimed Advisors LLC", "Ares Management LLC",
                                                               "Fain Eric S", "Pacitti David"]
    assert out[0]["_joint"] == ["Gordon Carl L"] and "_joint" not in out[1]


def test_europa_un_pais_caido_no_rompe_ni_duplica(tmp_path, monkeypatch):
    """Si un país falla, los demás siguen y sus compras no se cuentan dos veces (pasaba con el bucle de reintentos)."""
    trade = {"country": "CH", "isin": "CH0000000001", "company": "Prueba AG", "insider": "Directivo sin nombre",
             "role_raw": "ejecutivo", "price": 10.0, "currency": "CHF", "value": 80_000.0,
             "trade_date": date(2026, 11, 12), "pub_date": None, "place": "", "conditions": "", "nid": "n1",
             "source": "SIX"}

    def boom(*a, **k):
        raise RuntimeError("fuente caída")

    monkeypatch.setattr(signals_eu, "DATA", tmp_path)
    monkeypatch.setattr(signals_eu.time, "sleep", lambda s: None)
    monkeypatch.setattr(signals_eu, "fx_rates", lambda: (dict(signals_eu.FX_FALLBACK), False))
    monkeypatch.setattr(signals_eu, "fetch_de", boom)
    monkeypatch.setattr(signals_eu, "fetch_ch", lambda *a, **k: ([dict(trade)], {"ok": True, "notifications": 1}, {"n1"}))
    monkeypatch.setattr(signals_eu, "fetch_fr", boom)
    for name in ("fetch_es", "fetch_it", "fetch_gb"):
        monkeypatch.setattr(signals_eu, name, lambda *a, **k: ([], {"ok": True, "documents": 3, "announcements": 3,
                                                                   "errors": 0, "unparsed": 0}))
    monkeypatch.setattr(signals_eu, "market_data", lambda by, fx: ({}, []))
    monkeypatch.setattr(signals_eu, "add_extra", lambda ops, kind: None)
    assert signals_eu.build(date(2026, 11, 13), historical=True) == 0
    d = json.loads((tmp_path / "historico" / "signals-2026-11-13.json").read_text(encoding="utf-8"))
    assert d["status"]["DE"]["ok"] is False and d["status"]["FR"]["ok"] is False and d["status"]["CH"]["ok"] is True
    assert sum("fuente oficial ha fallado" in a for a in d["alerts"]) == 2
    assert len(d["discarded"]) == 1 and d["discarded"][0]["value"] == round(80_000 / signals_eu.FX_FALLBACK["CHF"])


def test_reintentos_con_espera(monkeypatch):
    calls, sleeps = [], []

    def fake(method, url, **kw):
        calls.append(url)
        r = _resp(b"ok tinytable" if len(calls) >= 3 else b"mantenimiento", "text/html")
        r.status_code = 503 if len(calls) == 1 else 200
        return r

    monkeypatch.setattr(net.requests, "request", fake)
    monkeypatch.setattr(net.time, "sleep", lambda s: sleeps.append(s))
    assert "tinytable" in net.get_text("http://openinsider.com/x", expect="tinytable", retries=4)
    assert len(calls) == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]   # 503, página sin tabla y, al fin, bien
