#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
save_tender.py - saves the extracted data of one tender (step 7 in CLAUDE.md).

Takes care of the mechanical work so the LLM doesn't have to:
  - builds valid XML (correct escaping, multi-line values preserved);
  - normalizes numeric fields (Block 2 rules in CLAUDE.md);
  - sanitizes the output file name;
  - assigns or reuses the tender's internal number (step 1a): uses a
    user-supplied value as-is, reuses the number already on record for the
    same tender number, or auto-assigns the next "<prefix><N>" number and
    persists the counter;
  - appends a row to the summary CSV, or updates the existing row for the
    same tender number (upsert - no duplicates on re-runs);
  - validates the XML before writing it.

The LLM only extracts the data and passes it as ONE flat JSON object whose
keys are the CSV column names (see CSV_COLUMNS). Fields missing from the
documents carry the NOT_SPECIFIED placeholder.

Usage:
    python save_tender.py \
        --json /path/to/tender_data.json \
        --extract-dir "/path/to/<tender>/AIExtract" \
        --csv-path "/path/to/all_tenders.csv" \
        --source-file "notice.pdf + specification.xlsx" \
        [--internal-number-state /path/to/internal_number_state.txt]

Prints a JSON result:
    {"xml_path": "...", "csv_path": "...", "csv_row_action": "appended"|"updated",
     "internal_number": "T-1"}
"""

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config  # noqa: E402

NOT_SPECIFIED = "Not specified in the documents"

BLOCKS = {
    "block1_product": ["name", "quantity", "condition", "delivery_place", "delivery_term",
                       "procedure_type", "price_reduction", "price_reduction_date"],
    "block2_price": ["start_price", "currency", "payment_terms"],
    "block3_security": ["bid_security", "contract_security"],
    "block4_dates": ["publication_date", "submission_deadline", "auction_date",
                     "results_date", "contract_date", "last_modified"],
    "block5_buyer": ["name", "registration_id", "contact", "address"],
    "block6_procedure": ["procurement_method", "platform", "platform_url",
                         "purchase_number", "tender_url", "stage", "sme"],
    "block7_domestic_preference": ["applied", "measure_type", "restricted_items", "basis"],
}

# Flat field name (JSON key / CSV column) -> (block, XML tag)
FIELD_MAP = {
    "product_name": ("block1_product", "name"),
    "quantity": ("block1_product", "quantity"),
    "condition": ("block1_product", "condition"),
    "delivery_place": ("block1_product", "delivery_place"),
    "delivery_term": ("block1_product", "delivery_term"),
    "procedure_type": ("block1_product", "procedure_type"),
    "price_reduction": ("block1_product", "price_reduction"),
    "price_reduction_date": ("block1_product", "price_reduction_date"),

    "start_price": ("block2_price", "start_price"),
    "currency": ("block2_price", "currency"),
    "payment_terms": ("block2_price", "payment_terms"),

    "bid_security": ("block3_security", "bid_security"),
    "contract_security": ("block3_security", "contract_security"),

    "publication_date": ("block4_dates", "publication_date"),
    "submission_deadline": ("block4_dates", "submission_deadline"),
    "auction_date": ("block4_dates", "auction_date"),
    "results_date": ("block4_dates", "results_date"),
    "contract_date": ("block4_dates", "contract_date"),
    "last_modified": ("block4_dates", "last_modified"),

    "buyer": ("block5_buyer", "name"),
    "buyer_registration_id": ("block5_buyer", "registration_id"),
    "contact": ("block5_buyer", "contact"),
    "address": ("block5_buyer", "address"),

    "procurement_method": ("block6_procedure", "procurement_method"),
    "platform": ("block6_procedure", "platform"),
    "platform_url": ("block6_procedure", "platform_url"),
    "purchase_number": ("block6_procedure", "purchase_number"),
    "tender_url": ("block6_procedure", "tender_url"),
    "stage": ("block6_procedure", "stage"),
    "sme": ("block6_procedure", "sme"),

    "domestic_preference_applied": ("block7_domestic_preference", "applied"),
    "domestic_preference_measure": ("block7_domestic_preference", "measure_type"),
    "domestic_preference_items": ("block7_domestic_preference", "restricted_items"),
    "domestic_preference_basis": ("block7_domestic_preference", "basis"),
}

# Summary CSV column order (must match the CSV template in CLAUDE.md). The three
# identifying columns - internal number, tender number, tender URL - come first.
CSV_COLUMNS = [
    "internal_number", "purchase_number", "tender_url",
    "source_file", "extracted_date", "product_name", "quantity", "condition",
    "delivery_place", "delivery_term", "procedure_type", "price_reduction",
    "price_reduction_date", "start_price", "currency", "payment_terms",
    "bid_security", "contract_security", "publication_date", "submission_deadline",
    "auction_date", "results_date", "contract_date", "last_modified", "buyer",
    "buyer_registration_id", "contact", "address", "procurement_method", "platform",
    "platform_url", "stage", "sme",
    "domestic_preference_applied", "domestic_preference_measure",
    "domestic_preference_items", "domestic_preference_basis",
]

NUMERIC_FIELDS = ("start_price",)

# A missing tender URL is shown as a plain dash instead of the long placeholder.
DASH = "—"
DASH_FIELDS = ("tender_url",)


def apply_dash_placeholders(fields):
    """Missing/empty/NOT_SPECIFIED values of DASH_FIELDS become "—" (in place)."""
    for flat_name in DASH_FIELDS:
        value = str(fields.get(flat_name) or "").strip()
        if not value or value == NOT_SPECIFIED:
            fields[flat_name] = DASH
    return fields


# --- internal number (step 1a) ------------------------------------------------
TRAILING_DIGITS_RE = re.compile(r"(\d+)\s*$")


def _internal_number_settings():
    cfg = load_config().get("internal_number", {})
    return str(cfg.get("prefix", "T-")), int(cfg.get("start", 1))


def _state_path(explicit_path, csv_path):
    if explicit_path:
        return explicit_path
    base_dir = os.path.dirname(os.path.abspath(csv_path)) or "."
    return os.path.join(base_dir, "internal_number_state.txt")


def _read_counter(state_path, start):
    """Last issued integer, or start-1 when there is no (readable) state file."""
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return start - 1


def _write_counter(state_path, value):
    parent = os.path.dirname(state_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        f.write(str(value))


def find_existing_row(csv_path, purchase_number):
    """The CSV row already saved for this tender number (used to reuse its
    internal number on a re-save), or None."""
    if not os.path.isfile(csv_path):
        return None
    if not purchase_number or purchase_number == NOT_SPECIFIED:
        return None
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("purchase_number") or "").strip() == purchase_number:
                return row
    return None


def resolve_internal_number(candidate, state_path, prefix=None, start=None):
    """Final internal number for this tender; keeps the counter file in sync.

    - candidate given -> used as-is; if its trailing digits are above the
      counter, the counter is bumped so auto-numbering never collides with it.
    - candidate empty -> "<prefix><counter + 1>" and the counter is persisted.
    """
    if prefix is None or start is None:
        cfg_prefix, cfg_start = _internal_number_settings()
        prefix = cfg_prefix if prefix is None else prefix
        start = cfg_start if start is None else start
    candidate = str(candidate or "").strip()
    last = _read_counter(state_path, start)
    if candidate and candidate != NOT_SPECIFIED:
        match = TRAILING_DIGITS_RE.search(candidate)
        if match and int(match.group(1)) > last:
            _write_counter(state_path, int(match.group(1)))
        return candidate
    next_n = last + 1
    _write_counter(state_path, next_n)
    return f"{prefix}{next_n}"


# --- value normalization ---------------------------------------------------------
NUMBER_TOKEN_RE = re.compile(r"\d(?:[\d\s.,']*\d)?")


def parse_number_token(token):
    """Turns one numeric token in any common notation into a float or None:
    "1 500 000,00", "1,500,000.00", "1.500.000,00", "1'500'000.00", "1500000"."""
    cleaned = re.sub(r"[\s']", "", token)
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[-1]) != 3:
            cleaned = cleaned.replace(",", ".")       # decimal comma
        else:
            cleaned = cleaned.replace(",", "")        # thousands separators
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    elif cleaned.count(".") == 1:
        integer_part, _, frac_part = cleaned.partition(".")
        if len(frac_part) == 3 and len(integer_part) <= 3 and integer_part != "0":
            cleaned = cleaned.replace(".", "")        # "1.500" = one thousand five hundred
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_number(raw):
    """
    "EUR 1,500,000.00" / "1 500 000,00 EUR" -> "1500000.00"
    "1500000" -> "1500000.00"
    placeholder / empty / not a number -> returned unchanged.

    A leading cross-check marker from step 5.2 ("✅ " confirmed, "🔺 " unresolved
    discrepancy, usually followed by an explanation) is kept, together with
    the text after the number - only the number itself is normalized.
    """
    if raw is None:
        return NOT_SPECIFIED
    raw = str(raw).strip()
    if not raw or raw == NOT_SPECIFIED:
        return NOT_SPECIFIED

    marker_match = re.match(r"^(✅|🔺)\s*", raw)
    marker = marker_match.group(0) if marker_match else ""
    body = raw[marker_match.end():] if marker_match else raw

    # A contiguous token that starts AND ends with a digit, so a trailing
    # abbreviation dot ("approx.") can never be mistaken for a decimal point.
    match = NUMBER_TOKEN_RE.search(body)
    if not match:
        return raw
    value = parse_number_token(match.group(0))
    if value is None:
        return raw

    normalized = f"{value:.2f}"
    if marker:
        return f"{marker}{normalized}{body[match.end():]}"
    return normalized


def ascii_slug(text):
    """Accent/script-safe ASCII version of `text` for file names."""
    text = unicodedata.normalize("NFKD", str(text))
    return text.encode("ascii", "ignore").decode("ascii")


def sanitize_filename_part(text):
    text = re.sub(r'[«»(),.:;"\']', "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return re.sub(r"[^\w\-]", "", text, flags=re.UNICODE)


def format_source_files(raw):
    """"a.docx + b.pdf" -> "a.docx\\nb.pdf" (one file per line). A single file
    is returned unchanged."""
    if raw is None:
        return raw
    parts = [p.strip() for p in str(raw).split(" + ") if p.strip()]
    if len(parts) <= 1:
        return raw
    return "\n".join(parts)


def build_filename(fields, extracted_date):
    purchase_number = str(fields.get("purchase_number") or "").strip()
    if purchase_number and purchase_number != NOT_SPECIFIED:
        base = sanitize_filename_part(purchase_number)
    else:
        buyer = str(fields.get("buyer") or "").strip()
        base = sanitize_filename_part(ascii_slug(buyer))[:40] if buyer else ""
    return f"{base or 'TENDER'}_{extracted_date}.xml"


def build_xml(fields, source_file, extracted_date):
    root = ET.Element("tender")
    meta = ET.SubElement(root, "meta")
    ET.SubElement(meta, "source_file").text = source_file
    ET.SubElement(meta, "extracted_date").text = extracted_date
    ET.SubElement(meta, "internal_number").text = str(fields.get("internal_number") or NOT_SPECIFIED)

    reverse_map = {(block, tag): flat for flat, (block, tag) in FIELD_MAP.items()}
    for block_name, tags in BLOCKS.items():
        block_el = ET.SubElement(root, block_name)
        for tag in tags:
            flat_name = reverse_map.get((block_name, tag))
            value = fields.get(flat_name) if flat_name else None
            if value is None or str(value).strip() == "":
                value = NOT_SPECIFIED
            ET.SubElement(block_el, tag).text = str(value)

    # ET.indent() only touches whitespace BETWEEN elements, so multi-line values
    # (including deliberate blank lines, e.g. before "Items:") round-trip intact.
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def validate_xml(xml_text):
    try:
        ET.fromstring(xml_text)
        return True, None
    except ET.ParseError as e:
        return False, str(e)


def upsert_csv_row(csv_path, row):
    """Appends the row, or replaces the existing row with the same tender number."""
    rows = []
    if os.path.isfile(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

    key = str(row.get("purchase_number") or "").strip()
    updated = False
    if key and key != NOT_SPECIFIED:
        for i, existing in enumerate(rows):
            if str(existing.get("purchase_number") or "").strip() == key:
                rows[i] = row
                updated = True
                break
    if not updated:
        rows.append(row)

    parent_dir = os.path.dirname(csv_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({col: (r.get(col) or NOT_SPECIFIED) for col in CSV_COLUMNS})
    return updated


def main():
    parser = argparse.ArgumentParser(description="Save tender data to XML and update the summary CSV")
    parser.add_argument("--json", required=True, help="JSON file with the extracted fields")
    parser.add_argument("--extract-dir", required=True, help="AIExtract folder to save the XML into")
    parser.add_argument("--csv-path", required=True, help="Path to the summary all_tenders.csv")
    parser.add_argument("--source-file", required=True, help="Processed source files joined by ' + '")
    parser.add_argument("--internal-number-state",
                        help="Internal-number counter file (default: internal_number_state.txt next to --csv-path)")
    args = parser.parse_args()

    with open(args.json, "r", encoding="utf-8") as f:
        fields = json.load(f)

    apply_dash_placeholders(fields)
    for numeric_field in NUMERIC_FIELDS:
        if numeric_field in fields:
            fields[numeric_field] = normalize_number(fields[numeric_field])

    extracted_date = fields.get("extracted_date") or date.today().isoformat()

    # Internal number priority: (1) value given in the JSON, (2) the number
    # already on record for this tender number, (3) next auto-assigned number.
    purchase_number = str(fields.get("purchase_number") or "").strip()
    provided = str(fields.get("internal_number") or "").strip()
    if not provided or provided == NOT_SPECIFIED:
        existing_row = find_existing_row(args.csv_path, purchase_number)
        if existing_row:
            existing_internal = str(existing_row.get("internal_number") or "").strip()
            if existing_internal and existing_internal != NOT_SPECIFIED:
                provided = existing_internal
    state_path = _state_path(args.internal_number_state, args.csv_path)
    fields["internal_number"] = resolve_internal_number(provided, state_path)

    source_file = format_source_files(args.source_file)
    xml_text = build_xml(fields, source_file, extracted_date)
    ok, err = validate_xml(xml_text)
    if not ok:
        print(f"Error: generated XML is not valid ({err}); nothing was saved.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.extract_dir, exist_ok=True)
    xml_path = os.path.join(args.extract_dir, build_filename(fields, extracted_date))
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_text)

    csv_row = {"internal_number": fields["internal_number"], "source_file": source_file,
               "extracted_date": extracted_date}
    for flat_name in FIELD_MAP:
        value = fields.get(flat_name, NOT_SPECIFIED)
        csv_row[flat_name] = value if value not in (None, "") else NOT_SPECIFIED
    updated = upsert_csv_row(args.csv_path, csv_row)

    print(json.dumps({
        "xml_path": xml_path,
        "csv_path": args.csv_path,
        "csv_row_action": "updated" if updated else "appended",
        "internal_number": fields["internal_number"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
