# Fields reference — where each field lives

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
| `internal_number` | `meta/internal_number` | ✔ | Internal number | SingleLineText |
| `purchase_number` | `block6_procedure/purchase_number` | ✔ | Tender number | SingleLineText |
| `tender_url` | `block6_procedure/tender_url` | ✔ | Tender URL | URL |
| `source_file` | `meta/source_file` | ✔ | Source files | LongText |
| `extracted_date` | `meta/extracted_date` | ✔ | Extracted on | SingleLineText |
| `product_name` | `block1_product/name` | ✔ | Subject of procurement | LongText |
| `quantity` | `block1_product/quantity` | ✔ | Quantity | LongText |
| `condition` | `block1_product/condition` | ✔ | Condition | SingleLineText |
| `delivery_place` | `block1_product/delivery_place` | ✔ | Delivery place | LongText |
| `delivery_term` | `block1_product/delivery_term` | ✔ | Delivery term | LongText |
| `procedure_type` | `block1_product/procedure_type` | ✔ | Procedure type | SingleLineText |
| `price_reduction` | `block1_product/price_reduction` | ✔ | Price-reduction stage | SingleLineText |
| `price_reduction_date` | `block1_product/price_reduction_date` | ✔ | Price-reduction date | SingleLineText |
| `start_price` | `block2_price/start_price` | ✔ | Starting price | SingleLineText |
| `currency` | `block2_price/currency` | ✔ | Currency | SingleLineText |
| `payment_terms` | `block2_price/payment_terms` | ✔ | Payment terms | LongText |
| `bid_security` | `block3_security/bid_security` | ✔ | Bid security | LongText |
| `contract_security` | `block3_security/contract_security` | ✔ | Performance security | LongText |
| `publication_date` | `block4_dates/publication_date` | ✔ | Publication date | SingleLineText |
| `submission_deadline` | `block4_dates/submission_deadline` | ✔ | Submission deadline | SingleLineText |
| `auction_date` | `block4_dates/auction_date` | ✔ | Auction date | SingleLineText |
| `results_date` | `block4_dates/results_date` | ✔ | Award date | SingleLineText |
| `contract_date` | `block4_dates/contract_date` | ✔ | Contract signing | LongText |
| `last_modified` | `block4_dates/last_modified` | ✔ | Last modified | SingleLineText |
| `buyer` | `block5_buyer/name` | ✔ | Buyer | SingleLineText |
| `buyer_registration_id` | `block5_buyer/registration_id` | ✔ | Buyer registration ID | SingleLineText |
| `contact` | `block5_buyer/contact` | ✔ | Buyer contact | LongText |
| `address` | `block5_buyer/address` | ✔ | Buyer address | LongText |
| `procurement_method` | `block6_procedure/procurement_method` | ✔ | Procurement method | SingleLineText |
| `platform` | `block6_procedure/platform` | ✔ | Platform | SingleLineText |
| `platform_url` | `block6_procedure/platform_url` | ✔ | Platform URL | URL |
| `stage` | `block6_procedure/stage` | ✔ | Stage | SingleLineText |
| `sme` | `block6_procedure/sme` | ✔ | SME | SingleLineText |
| `domestic_preference_applied` | `block7_domestic_preference/applied` | ✔ | Domestic preference: applied | SingleLineText |
| `domestic_preference_measure` | `block7_domestic_preference/measure_type` | ✔ | Domestic preference: measure | SingleLineText |
| `domestic_preference_items` | `block7_domestic_preference/restricted_items` | ✔ | Domestic preference: items | LongText |
| `domestic_preference_basis` | `block7_domestic_preference/basis` | ✔ | Domestic preference: basis | LongText |
| `payment_days_summary` | — | — | Payment term (short) | SingleLineText |
| `application_documents` | — | — | Required bid documents | LongText |

Additional registry columns written by the sync: **Specification link** (URL of the tender's specification table) and **Supplier request files** (Attachment, step 7.1b).

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
| No. | SingleLineText |
| Item name | LongText |
| Technical requirements | LongText |
| Documentation errors | LongText |
| Brand/model | SingleLineText |
| Manufacturer | SingleLineText |
| Country of origin | SingleLineText |
| Equivalent allowed | SingleLineText |
| Equivalent (status) | SingleSelect |
| Equivalent | LongText |
| Qty | Decimal |
| Unit | SingleLineText |
| Max price (line) | Decimal |
| Package dimensions | LongText |
| Manufacturer URL | URL |
| Max unit price | Decimal |
| Agent price 1 | Decimal |
| Agent comment 1 | LongText |
| Agent price 2 | Decimal |
| Agent comment 2 | LongText |
| Agent price 3 | Decimal |
| Agent comment 3 | LongText |
| Domestic offer 1 | Decimal |
| Domestic link 1 | URL |
| Domestic offer 2 | Decimal |
| Domestic link 2 | URL |
| Domestic offer 3 | Decimal |
| Domestic link 3 | URL |
| International offer 1 | Decimal |
| International link 1 | URL |
| International offer 2 | Decimal |
| International link 2 | URL |
| International offer 3 | Decimal |
| International link 3 | URL |
| Procurement: supplier 1 | SingleLineText |
| Procurement: price 1 | Decimal |
| Procurement: supplier 2 | SingleLineText |
| Procurement: price 2 | Decimal |
| Procurement: supplier 3 | SingleLineText |
| Procurement: price 3 | Decimal |
| Procurement: supplier 4 | SingleLineText |
| Procurement: price 4 | Decimal |
| Procurement: supplier 5 | SingleLineText |
| Procurement: price 5 | Decimal |
| Procurement: supplier 6 | SingleLineText |
| Procurement: price 6 | Decimal |
| Procurement: supplier 7 | SingleLineText |
| Procurement: price 7 | Decimal |
| Procurement: supplier 8 | SingleLineText |
| Procurement: price 8 | Decimal |
| Procurement: supplier 9 | SingleLineText |
| Procurement: price 9 | Decimal |
| Procurement: supplier 10 | SingleLineText |
| Procurement: price 10 | Decimal |
| Procurement: comment | LongText |
| Notes | LongText |

Views created by the sync: **Agent prices** (step 5.1f), **Procurement** (steps 5.1g/5.1h), **Price comparison** (step 5.1j, formula columns from `compare_formula_columns()`).

Value handling:

| Rule | Where |
|---|---|
| Numeric cells: text placeholders → empty value, text moved to Notes | `sanitize_spec_row()` |
| "Max price (line)" prefers "(X for N pcs)" totals | `parse_position_total()` |
| Foreign-currency prices → base currency (`--rate CODE=RATE`) | `convert_foreign_prices()` / `convert_to_base()` |
| Equivalent status → Yes / No / Check | `normalize_equivalent_status()` |
| Procurement values preserved on refill | `read_manual_fields()` / `restore_manual_fields()` |
| Post-write checks | `verify_main_record()` / `verify_spec_table()` |
