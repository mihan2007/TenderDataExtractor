#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_fields_reference.py - regenerates docs/FIELDS_REFERENCE.md from the code,
so the field documentation can never drift from FIELD_MAP / CSV_COLUMNS /
SPEC_COLUMNS.

    python3 tools/gen_fields_reference.py
"""
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import nocodb_client as nc  # noqa: E402
import save_tender as st  # noqa: E402

OUT = os.path.join(os.path.dirname(TOOLS), "docs", "FIELDS_REFERENCE.md")

TEMPLATE = """# Fields reference — where each field lives

A navigation index of every place a tender field is defined: what is extracted, what the column is called in XML / CSV / NocoDB, and where formatting happens. It does not repeat the instructions of `CLAUDE.md` — it gives exact addresses so a change touches every place it must.

> This file is generated from the code (`FIELD_MAP` / `CSV_COLUMNS` in `tools/save_tender.py`, `FIELD_MAP` / `SPEC_COLUMNS` in `tools/nocodb_client.py`). After changing a field, regenerate it with `python3 tools/gen_fields_reference.py`.

## How a field flows through the system

Each extracted field has one flat name (e.g. `address`) and up to four places where it lives:

1. **`CLAUDE.md`** — Block N: what to extract and how (instructions for the model).
2. **`tools/save_tender.py`** — `FIELD_MAP` (flat name → XML block/tag) and `CSV_COLUMNS` (column order of `all_tenders.csv`).
3. **`tools/nocodb_client.py`** — its own `FIELD_MAP` (flat name → NocoDB column title and type).
4. **NocoDB itself** — column settings (type, rich text). Missing columns are created by `nocodb_client.py --init-registry` or on the first sync.

Renaming or adding a field usually means editing several of these — this file exists so nothing is forgotten.

## Registry fields (one row per tender)

| Flat name (JSON / CSV) | XML | CSV | NocoDB column | NocoDB type |
|---|---|---|---|---|
{registry}

Additional registry columns written by the sync: **{spec_link}** (URL of the tender's specification table) and **{supplier_files}** (Attachment, step 7.1b).

Formatting rules applied by the scripts:

| Rule | Where |
|---|---|
| Number normalization of `start_price` (keeps ✅/🔺 markers) | `normalize_number()` in `tools/save_tender.py` |
| Missing `tender_url` → `—` | `DASH_FIELDS` / `apply_dash_placeholders()` in `tools/save_tender.py` |
| `source_file` `"a + b"` → one file per line | `format_source_files()` in `tools/save_tender.py` |
| URL columns never receive placeholders | `STRICT_URL_FIELDS` in `tools/nocodb_client.py` |
| Rich-text (Markdown) columns | `RICH_TEXT_COLUMNS` in `tools/nocodb_client.py` |

## Specification table (one table per tender, named by the internal number)

Column order = the "Items & prices" sheet of `Items_and_prices.xlsx` (step 5.1 in `CLAUDE.md`) plus the procurement-team columns (step 5.1h), which never come from the xlsx.

| Column | NocoDB type |
|---|---|
{spec}

Views created by the sync: **{agent_view}** (step 5.1f), **{proc_view}** (steps 5.1g/5.1h), **{cmp_view}** (step 5.1j, formula columns from `compare_formula_columns()`).

Value handling:

| Rule | Where |
|---|---|
| Numeric cells: text placeholders → empty value, text moved to Notes | `sanitize_spec_row()` |
| "Max price (line)" prefers "(X for N pcs)" totals | `parse_position_total()` |
| Foreign-currency prices → base currency (`--rate CODE=RATE`) | `convert_foreign_prices()` / `convert_to_base()` |
| Equivalent status → Yes / No / Check | `normalize_equivalent_status()` |
| Procurement values preserved on refill | `read_manual_fields()` / `restore_manual_fields()` |
| Post-write checks | `verify_main_record()` / `verify_spec_table()` |
"""


def build():
    rows = []
    for flat in st.CSV_COLUMNS:
        if flat in st.FIELD_MAP:
            block, tag = st.FIELD_MAP[flat]
            xml = f"`{block}/{tag}`"
        else:
            xml = f"`meta/{flat}`"
        title, uidt = nc.FIELD_MAP[flat]
        rows.append(f"| `{flat}` | {xml} | ✔ | {title} | {uidt} |")
    for flat in ("payment_days_summary", "application_documents"):
        title, uidt = nc.FIELD_MAP[flat]
        rows.append(f"| `{flat}` | — | — | {title} | {uidt} |")
    return TEMPLATE.format(
        registry="\n".join(rows),
        spec="\n".join(f"| {name} | {uidt} |" for name, uidt, _ in nc.SPEC_COLUMNS),
        spec_link=nc.SPEC_LINK_COLUMN, supplier_files=nc.SUPPLIER_FILES_COLUMN,
        agent_view=nc.AGENT_VIEW_TITLE, proc_view=nc.PROCUREMENT_VIEW_TITLE, cmp_view=nc.COMPARE_VIEW_TITLE)


if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(build())
    print(f"written: {OUT}")
