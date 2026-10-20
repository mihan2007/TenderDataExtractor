"""Today's exchange rates for converting foreign-currency prices into the base currency.

rates() -> {"USD": 0.92, "GBP": 1.17, ..., "_date": "2026-10-20"}
           (= units of BASE_CURRENCY per 1 unit of each currency)

Source: European Central Bank daily reference rates (XML); fallback: the
Frankfurter API (same ECB data, JSON). Cached for 3 hours. If both are
unreachable -> NoRates: the bot does not convert and asks for a manual price.
The base currency must be one the ECB publishes (EUR, USD, GBP, CHF, ...).
"""
import asyncio
import os
import time
import xml.etree.ElementTree as ET

import aiohttp

BASE_CURRENCY = (os.getenv("BASE_CURRENCY") or "EUR").strip().upper()
ECB_XML = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
FRANKFURTER_JSON = "https://api.frankfurter.app/latest?from=EUR"
SYMBOLS = {"€": "EUR", "EURO": "EUR", "$": "USD", "US$": "USD", "DOLLAR": "USD", "£": "GBP",
           "¥": "CNY", "RMB": "CNY", "YUAN": "CNY", "CHF": "CHF", "FR.": "CHF", "₹": "INR", "₺": "TRY"}
_cache = {"t": 0, "rates": None}


class NoRates(Exception):
    pass


def norm_currency(cur) -> str:
    c = str(cur or BASE_CURRENCY).strip().upper()
    if not c:
        return BASE_CURRENCY
    if len(c) == 3 and c.isalpha():
        return "CNY" if c == "RMB" else c
    for k, v in SYMBOLS.items():
        if c.startswith(k):
            return v
    return c


def _to_base(eur_rates: dict, date: str) -> dict:
    """ECB quotes "1 EUR = x CUR"; convert to "1 CUR = y BASE"."""
    eur_rates = {**eur_rates, "EUR": 1.0}
    base_per_eur = eur_rates.get(BASE_CURRENCY)
    if not base_per_eur:
        raise NoRates(f"the ECB does not publish a rate for the base currency {BASE_CURRENCY}")
    out = {code: base_per_eur / r for code, r in eur_rates.items() if r}
    out["_date"] = date
    return out


def parse_ecb_xml(raw: bytes) -> dict:
    root = ET.fromstring(raw)
    rates, day = {}, ""
    for el in root.iter():
        if el.tag.endswith("Cube") and el.get("time"):
            day = el.get("time")
        if el.tag.endswith("Cube") and el.get("currency"):
            rates[el.get("currency")] = float(el.get("rate"))
    return _to_base(rates, day)


def parse_frankfurter(data: dict) -> dict:
    return _to_base({k: float(v) for k, v in (data.get("rates") or {}).items()}, str(data.get("date") or ""))


async def rates() -> dict:
    if _cache["rates"] and time.time() - _cache["t"] < 3 * 3600:
        return _cache["rates"]
    errors = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=True) as s:
        for url, kind in ((ECB_XML, "xml"), (FRANKFURTER_JSON, "json")):
            try:
                async with s.get(url, headers={"User-Agent": "TenderDataExtractor-price-bot"}) as r:
                    if r.status != 200:
                        raise RuntimeError(f"HTTP {r.status}")
                    res = parse_ecb_xml(await r.read()) if kind == "xml" else \
                        parse_frankfurter(await r.json(content_type=None))
                if res.get("USD"):
                    _cache.update(t=time.time(), rates=res)
                    return res
            except NoRates:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError, ET.ParseError) as e:
                errors.append(f"{url}: {e}")
    raise NoRates("exchange rates unavailable (" + "; ".join(errors)[:200] + ")")
