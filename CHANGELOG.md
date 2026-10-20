# Changelog

## 1.0.0 — 2026-10-20

First release of the international (English) edition.

- Workflow prompt (`CLAUDE.md`) and all documentation in English, jurisdiction-neutral: generic procedure types (open tender, RFQ, RFP, reverse auction, framework agreement, ...), a generic "domestic preference and origin restrictions" block, buyer registration IDs instead of country-specific identifiers.
- All installation-specific values moved to `config.json` (NocoDB IDs, internal-number prefix, base currency, VAT rate, home market, international search priority, landed-cost multipliers).
- Market price search split into **domestic** and **international** markets (three confirmed offers each), validated by `tools/check_prices.py`.
- Multi-currency: foreign prices converted to the base currency with `--rate CODE=RATE`; the Telegram bot uses ECB reference rates.
- `tools/nocodb_client.py --init-registry` creates the registry table in a fresh NocoDB base.
- `docs/FIELDS_REFERENCE.md` is generated from the code (`tools/gen_fields_reference.py`).
- Test suite (pytest) including an end-to-end sync against an in-memory fake NocoDB API; GitHub Actions CI.
