"""Parsing supplier prices out of free text / documents with DeepSeek.

Input: what a team member sent (a supplier's message, quotation lines, an
invoice, a price list - as text or as images) plus the item list of the
selected tender. Output: a list of offers with item number, supplier, unit
price (in the document's currency), VAT flag, match quality and a comment.
The model only parses - the bot itself writes to NocoDB.
"""
import asyncio
import base64
import json

import aiohttp

API_URL = "https://api.deepseek.com/chat/completions"
MAX_TEXT = 15000
VAT_INCL, VAT_EXCL, VAT_UNKNOWN = "incl. VAT", "excl. VAT", "VAT not specified"

SYSTEM_PROMPT = """You assist the procurement team of a company that takes part in public tenders.
A team member sent you a text with supplier prices (a message, lines from a quotation, a note) or a document:
an invoice, a commercial offer / quotation, a price list (as text or as an image - photo, scan, screenshot).
You also get the tender's item list: No. | item name | brand/model (with part number) | manufacturer | qty.
The document may be in any language.

Task: find every price offer in the document and match each one to a tender item.

SUPPLIER (supplier):
- The seller who issued the document: invoice/quotation header, "Supplier", "Seller", "Vendor", company
  details with a tax/VAT number at the top, the signature, the e-mail domain. Give the full legal name,
  e.g. "Acme Industrial GmbH".
- NOT the supplier: the buyer / consignee / payer (our companies: {our}), or the manufacturer of the goods
  in a line (Siemens, Agilent, Mettler Toledo are brands, not the seller).
- If the seller is not in the text/image (e.g. a screenshot of a table without a header) - supplier = null.
- supplier_tax_id - the seller's VAT/tax/registration number if stated, otherwise null.

VAT (vat) - "{incl}", "{excl}" or "{unknown}", as stated in the document:
- "price incl. VAT", "VAT included", "gross", a VAT column with "Total incl. VAT" -> "{incl}".
- "prices excl. VAT", "net", "plus VAT", "VAT exempt", "Subtotal ... VAT ... Total due" where the line
  price is the net amount -> "{excl}".

PRICE:
- price - the price for ONE unit of the item's unit of measure, as a number in the document's currency
  (no thousands separators, dot as decimal separator). If a line only has the total for the whole
  quantity, divide by the quantity and say so in comment.
- currency - ISO code: "EUR", "USD", "GBP", "CNY"... (€ -> EUR, $ -> USD, £ -> GBP).

MATCHING (position_no, match):
- position_no - the item "No." from the list if the product in the document is that same item (by part
  number / model; if those are missing - unambiguously by name). Otherwise null, explained in reason.
- match = "full" - an EXACT match: the part number / model in the document equals the one in the item
  (ignoring spaces, dashes and case; "402–890.134" = "402-890.134"), it is not an equivalent or another
  variant, and the unit/pack size is the same.
- match = "partial" - everything else: different part number or model, an equivalent/replacement, a
  different variant/configuration/pack size, one document line covering several items, price per pack
  instead of per piece, no part number and a match by name only, any doubt.
- match_note - for "partial", briefly WHAT does not match (empty for "full").
- Lines that are not tender items (shipping, other goods) - position_no = null, reason "not in the tender".
- Lines without a price ("on request", "out of stock", empty price) - leave them out of items, mention in note.
- One document line = one entry. If a line covers two items, match it to the first one, match = "partial",
  and say in match_note that the line covers two items.

comment - briefly: lead time, availability, quotation/invoice number and date, price validity, payment terms,
any recalculation. Do not repeat the price or the supplier.
confidence - "high" if both the item and the price are certain, otherwise "low".
Invent nothing: use only what is in the text or the image.

Answer strictly in JSON:
{{"supplier":"Acme Industrial GmbH","supplier_tax_id":"DE123456789","vat":"{incl}","currency":"EUR",
"items":[{{"position_no":"5","item_text":"ABC-123 Pressure sensor ...","price":120.5,"currency":"EUR",
"vat":"{incl}","supplier":null,"match":"full","match_note":"","comment":"Quote 15 of 2026-10-01, lead time 30 days",
"confidence":"high","reason":"part number ABC-123 matches"}}],"note":""}}
Give supplier/vat/currency inside items only when they differ from the document-level values."""


class AiError(Exception):
    pass


def positions_table(rows, max_rows=400) -> str:
    lines = []
    for r in rows[:max_rows]:
        name = " ".join(str(r.get("Item name") or "").split())[:160]
        lines.append(" | ".join([str(r.get("No.") or "").strip(), name,
                                 str(r.get("Brand/model") or "").strip(),
                                 str(r.get("Manufacturer") or "").strip(),
                                 f"{r.get('Qty') or ''} {r.get('Unit') or ''}".strip()]))
    return "\n".join(lines)


def _norm_vat(v):
    return v if v in (VAT_INCL, VAT_EXCL) else VAT_UNKNOWN


async def parse_offer(api_key: str, model: str, text: str, rows, tender_code: str,
                      images=None, source: str = "", our_companies: str = "",
                      default_currency: str = "EUR") -> dict:
    """images - JPEG bytes of a photo/scan of the document.
    source - where the text came from, e.g. 'file "invoice.pdf"'."""
    intro = f"Tender {tender_code}. Items:\n{positions_table(rows)}\n\n"
    if source:
        intro += f"Source: {source}\n"
    intro += f"Team member's text / document:\n{(text or '(no text - see the images)')[:MAX_TEXT]}"
    if images:
        user_content = [{"type": "text", "text": intro}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}}
            for b in images]
    else:
        user_content = intro
    system = SYSTEM_PROMPT.format(our=our_companies or "(not configured)",
                                  incl=VAT_INCL, excl=VAT_EXCL, unknown=VAT_UNKNOWN)
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180), trust_env=True) as s:
        for attempt in range(3):
            try:
                async with s.post(API_URL, headers=headers, json=body) as r:
                    raw = await r.text()
                    if r.status in (429, 500, 502, 503):
                        last = f"HTTP {r.status}"
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    if r.status != 200:
                        raise AiError(f"DeepSeek HTTP {r.status}: {raw[:200]}")
                    content = json.loads(raw)["choices"][0]["message"]["content"]
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = str(e) or type(e).__name__
                await asyncio.sleep(3 * (attempt + 1))
        else:
            raise AiError(f"DeepSeek is unavailable: {last}")
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise AiError("DeepSeek did not return JSON")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AiError("DeepSeek returned an unexpected answer")
    doc_supplier = str(data.get("supplier") or "").strip()[:200] or None
    doc_vat = data.get("vat")
    doc_cur = data.get("currency") or default_currency
    clean = []
    for it in items[:80]:
        if not isinstance(it, dict):
            continue
        price = it.get("price")
        try:
            price = round(float(str(price).replace(" ", "").replace(",", ".")), 2) if price not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        clean.append({
            "position_no": str(it.get("position_no")).strip() if it.get("position_no") not in (None, "") else None,
            "item_text": str(it.get("item_text") or "")[:150],
            "supplier": (str(it.get("supplier")).strip()[:200] if it.get("supplier") else None) or doc_supplier,
            "price_orig": price if price and price > 0 else None,
            "currency": str(it.get("currency") or doc_cur or default_currency),
            "vat": _norm_vat(it.get("vat") or doc_vat),
            "match": "full" if it.get("match") == "full" else "partial",
            "match_note": str(it.get("match_note") or "")[:200],
            "comment": str(it.get("comment") or "")[:300],
            "confidence": "high" if it.get("confidence") == "high" else "low",
            "reason": str(it.get("reason") or "")[:200],
        })
    return {"items": clean, "note": str(data.get("note") or "")[:400],
            "supplier": doc_supplier, "supplier_tax_id": str(data.get("supplier_tax_id") or "") or None}
