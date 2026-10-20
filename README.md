# TenderDataExtractor

[![tests](https://github.com/mihan2007/TenderDataExtractor/actions/workflows/tests.yml/badge.svg)](https://github.com/mihan2007/TenderDataExtractor/actions/workflows/tests.yml)

An AI pipeline that turns a folder of tender documents into structured, verified data: it extracts the key terms of a procurement package, cross-checks the critical fields with a second, independent model, prices every line item on the domestic and international market, estimates the landed cost and margin, and syncs everything into a NocoDB database — plus a Telegram bot that lets a procurement team drop supplier quotes straight into the same tables.

It runs as an expert role ("Tender Analyst") on top of [Claude Code](https://docs.claude.com/en/docs/claude-code/overview): the reasoning-heavy work is described in the system prompt (`CLAUDE.md`), while everything mechanical — building XML, normalizing numbers, unpacking archives, talking to APIs, verifying writes — lives in small, tested Python scripts.

## The problem

A tender package is typically 5–15 documents (notice, instructions to bidders, technical specification, draft contract, annexes, price schedules) in PDF/DOCX/XLSX, often zipped. The data you need is scattered across files and full of cross-references ("see Annex 3"), and the figures and dates are exactly where a mistake is most expensive. Reviewing one tender by hand takes from 30 minutes to several hours — and pricing a 100-line specification takes days.

## What it does

- **Extracts 30+ fields into 7 structured blocks:** goods and delivery, price and payment terms, bid/performance security, key dates, buyer, platform and procedure, domestic-preference / origin rules.
- **Unpacks archives** (`.zip`, `.rar`, `.7z`, `.tar.gz`, nested archives, zip-slip and archive-bomb protection) and **converts OpenDocument** files automatically.
- **Resolves internal references** — when the contract says "as per the specification", it finds that file in the package and uses the real value instead of the bare reference.
- **Builds an items-and-prices table**: verbatim technical requirements per item, documentation errors found in the specification, manufacturer and brand home country, whether an equivalent is allowed (and a 100%-verified equivalent if one exists), package dimensions, the manufacturer's product page.
- **Prices every item in two markets** — three confirmed offers each, domestic and international — with a mandatory query set per item, a script that rejects incomplete or "price on request" shortcuts, and a parallel re-check pass by sub-agents for whatever is still missing.
- **Estimates landed cost and margin** against the tender ceiling with configurable multipliers per source type (domestic B2B, retail, import, supplier quote).
- **Reconciles item lists** across the documents of one package (spec vs. price schedule vs. contract annex) and flags every discrepancy.
- **Cross-checks critical fields with a second model** (DeepSeek): price, dates, quantities, payment and delivery terms are quoted independently and compared; matches get `✅`, unresolved discrepancies `🔺` with both readings.
- **Classifies the procedure type** from its features when the buyer doesn't name it.
- **Saves XML, a PDF report and a summary CSV**, with idempotent upserts and auto-assigned internal numbers.
- **Syncs to NocoDB**: one registry row per tender plus one typed table per tender's specification, with ready-made views ("Agent prices", "Procurement", "Price comparison" with 🏆 for the best price in each group computed by NocoDB formulas). Every write is **re-read and verified**.
- **Generates per-manufacturer RFQ files** (Word) to send to suppliers, and the list of documents the bid must contain.
- **Telegram price bot** for the procurement team: forward a supplier's invoice, quotation or price list (PDF, Word, Excel, photo, scan) into the tender's topic — the bot reads it, matches lines to spec items (🟢 exact / 🔴 partial match), converts currencies at the ECB rate and writes the prices into NocoDB, with one-tap undo.

## How it works

```
Tender documents (folder)
        │
        ▼
  Scan + unpack archives / convert ODF            tools/extract_archive.py, convert_odf.py
        │
        ▼
  Extract Blocks 1–7 (docx / pdf / xlsx skills)   CLAUDE.md, steps 4–5
        │
        ├──► Items & prices table ──► domestic + international market search ──► check_prices.py
        │        │                                         └──► re-check pass (parallel sub-agents)
        │        ├──► equivalents, documentation errors, reconciliation across documents
        │        └──► landed-cost estimate & margin
        │
        └──► Cross-check critical fields with a second model                  deepseek_extract.py
        │
        ▼
  Save XML + PDF + CSV registry                                               save_tender.py
        │
        ▼
  NocoDB: registry row + per-tender spec table + views, verified        nocodb_client.py
        │
        ├──► RFQ files per manufacturer                               make_supplier_files.py
        └──► Telegram price bot ◄── supplier quotes from the team              price_bot/
```

## Tech stack

- **Claude / Claude Code** — orchestration, document reading (`docx` / `pdf` / `xlsx` skills), reasoning, web research, sub-agents
- **Python 3** — helper scripts (stdlib-first; `openpyxl`, `python-docx`)
- **DeepSeek API** — independent cross-check; parsing of supplier documents in the bot
- **NocoDB API** — the tender database (cloud or self-hosted)
- **aiogram 3** — the Telegram bot
- **LibreOffice (headless)** — OpenDocument conversion

## Repository layout

```
CLAUDE.md                 system prompt: role and the step-by-step workflow
config.example.json       per-installation settings (copy to config.json)
docs/
  FIELDS_REFERENCE.md     where every field lives (XML tag, CSV column, NocoDB column) - generated
  PROCUREMENT_TYPES.md    how to classify the procedure type
  PARTICIPATION.md        checking bid content and bidder requirements
  PREPARE_DOCS.md         planning and filling in the bid documents
  SETUP.md                installation and configuration in detail
templates/                COMPANY / CORPORATE_DOCS templates for your company's data
tools/
  save_tender.py          XML / CSV, number normalization, internal numbering
  nocodb_client.py        NocoDB sync: registry, spec tables, views, formulas, verification
  deepseek_extract.py     second-model cross-check of critical fields
  check_prices.py         completeness check of the market-price search
  make_supplier_files.py  per-manufacturer RFQ files (Word)
  extract_archive.py      zip / rar / 7z / tar with zip-slip and archive-bomb protection
  convert_odf.py          OpenDocument -> OOXML via LibreOffice
  env_check.py            environment diagnostics
  config.py               settings loader
  gen_fields_reference.py regenerates docs/FIELDS_REFERENCE.md
price_bot/                Telegram bot for procurement-team prices (see price_bot/README.md)
examples/                 a fictional tender JSON and Items & prices sheet
tests/                    pytest suite (incl. an in-memory fake of the NocoDB API)
```

## Quick start

1. Clone the repository and install the helpers: `pip install -r requirements.txt` (plus `unrar` and LibreOffice for `.rar` / `.odt` files).
2. `cp config.example.json config.json` and set your home market, base currency, VAT rate and NocoDB workspace/base IDs.
3. Put your API keys into `secrets/API_key_NocoDB.txt` and `secrets/API_key_DeepSeek.txt` (the folder is git-ignored).
4. Create the registry table in NocoDB: `python3 tools/nocodb_client.py --init-registry --token-file secrets/API_key_NocoDB.txt`, then put the printed `registry_table_id` into `config.json`.
5. Open the project folder in Claude Code and send the path of a tender folder. On the first run the assistant asks where your tender folders live and runs `tools/env_check.py`.

Details, including the Telegram bot: [docs/SETUP.md](docs/SETUP.md).

## Accuracy and limitations

- Field extraction is LLM-based document analysis, not a formal parser: on heterogeneous real-world packages (scanned PDFs, unusual layouts) expect around 80% of fields right on the first pass. The second-model cross-check targets the fields where an error is costly, but a human review before submitting a bid remains essential.
- Market prices from web search are an estimate for bid/no-bid decisions, not a substitute for real supplier quotations — which is what the RFQ files and the price bot are for.
- The workflow is designed for Claude Code (document skills, web search, sub-agents); the DeepSeek and NocoDB steps need network access to those APIs.
- Nothing here is legal advice; procurement rules differ between jurisdictions and buyers.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest -q tests
```

## License

MIT — see [LICENSE](LICENSE).
