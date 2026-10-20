# Setup

## What this is, technically

Not a standalone program you "run" — it is three parts working together:

1. **`CLAUDE.md`** — the system prompt for Claude: the step-by-step tender-analysis workflow.
2. **Python scripts in `tools/`** — the mechanical part (unpacking archives, converting ODF, saving XML/CSV, syncing NocoDB), which Claude calls through the shell while following the workflow.
3. **The project folder opened in a Claude session** (Claude Code, or Claude with access to the folder) — without it nobody reads `CLAUDE.md` and nothing calls the scripts.

## 1. Software

| Component | Needed for | Install |
|---|---|---|
| Python 3.9+ | everything | python.org / `brew install python3` / your package manager |
| `openpyxl`, `python-docx` | items table, RFQ files | `pip install -r requirements.txt` |
| unrar | `.rar` archives only | `brew install unrar` / `sudo apt install unrar` / `choco install unrar` |
| LibreOffice | `.odt/.ods/.odp` only | `brew install --cask libreoffice` / `sudo apt install libreoffice` / libreoffice.org |

`rarfile` and `py7zr` are installed automatically the first time an archive needs them. A local unrar binary can also be put into `tools/bin/unrar` (git-ignored).

Check everything at once — the script prints the fix command for your OS:

```
python3 tools/env_check.py
```

## 2. Configuration — `config.json`

```
cp config.example.json config.json
```

| Key | Meaning |
|---|---|
| `nocodb.url` | `https://app.nocodb.com` or your self-hosted URL |
| `nocodb.workspace_id`, `nocodb.base_id` | from the NocoDB URL: `…/<workspace_id>/<base_id>/<table_id>` |
| `nocodb.registry_table_id` | filled after `--init-registry` (below) |
| `internal_number.prefix` / `start` | your internal tender numbers, e.g. `T-` and `1` → `T-1`, `T-2`, … |
| `base_currency` | ISO code all prices are stored in |
| `vat_rate` | standard VAT / sales-tax rate for the cost estimate (`0.20` = 20%) |
| `home_market` | the country of your "domestic" market |
| `international_priority` | countries searched first for international prices |
| `cost_coefficients` | landed-cost multipliers per source type |
| `procurement_slots` | number of supplier/price column pairs for the procurement team |
| `deepseek_model` | model used for the cross-check |

Environment variables `TDE_NOCODB_URL`, `TDE_NOCODB_WORKSPACE_ID`, `TDE_NOCODB_BASE_ID`, `TDE_NOCODB_REGISTRY_TABLE_ID`, `TDE_BASE_CURRENCY` override the file; `TDE_CONFIG` points to another config file.

## 3. Secrets

Create `secrets/` (git-ignored) with:

- `secrets/API_key_NocoDB.txt` — a NocoDB API token (Team & Settings → Tokens);
- `secrets/API_key_DeepSeek.txt` — a DeepSeek API key (platform.deepseek.com).

Never commit them. `.gitignore` also excludes `config.json`, `COMPANY.md`, `CORPORATE_DOCS.md`, `CorporateDocs/`, `all_tenders.csv` and every `AIExtract/` folder, because they contain your company data or buyers' contact details.

## 4. NocoDB

Create a base in NocoDB, put its IDs into `config.json`, then:

```
python3 tools/nocodb_client.py --init-registry --token-file secrets/API_key_NocoDB.txt
```

This creates the "Tender Registry" table with all columns (or adds the missing ones to an existing table — it is safe to re-run) and prints `registry_table_id` for `config.json`. Specification tables (one per tender) and their views are created automatically on the first sync of each tender.

Optional, in the NocoDB UI: conditional colouring for "Payment term (short)" — `is like 🟢%` → green, `is like 🔴%` → red (paid plans).

## 5. Your company data

```
cp templates/COMPANY.example.md COMPANY.md
cp templates/CORPORATE_DOCS.example.md CORPORATE_DOCS.md
mkdir CorporateDocs
```

Used when preparing bids (`docs/PARTICIPATION.md`, `docs/PREPARE_DOCS.md`). All three are git-ignored.

## 6. First run

Open the project folder in Claude Code and send the path of a tender folder (or its name, once the base folder is known). On the first run on a machine the assistant:

1. asks where the tender folders are stored and saves the answer to `tools/tender_base_folder.txt` (git-ignored — every machine has its own);
2. runs `tools/env_check.py` and shows the fix for anything missing (without blocking the rest);
3. creates `config.json` from your answers if it does not exist yet.

On later sessions, as long as the saved path exists, this step is skipped silently. Ask "re-check the environment" to repeat it.

## 7. Moving to another computer

Copy the project folder (with `secrets/` and `config.json` if the same accounts are used) — paths inside the project are relative, so the location on disk doesn't matter. On the new machine the setup check runs once and asks for the local tender folder. Install unrar / LibreOffice there if needed; the DeepSeek and NocoDB steps don't depend on the operating system.

## 8. Telegram price bot (optional)

See [price_bot/README.md](../price_bot/README.md).

## 9. Quick verification

- `python -m pytest -q tests` — the test suite (works offline).
- Process a small test folder through steps 1–5 and check that `.docx/.pdf/.xlsx` are read.
- Extract a test `.zip` and, if available, a `.rar`.
- Run one full tender and check the record and the specification table in NocoDB — the sync reports `verification.ok`.
