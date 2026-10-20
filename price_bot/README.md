# Price bot (Telegram)

A Telegram bot for the procurement team: it writes the prices suppliers send into the tender's specification table in NocoDB — the "Procurement: supplier N / price N / comment" columns (step 5.1h of `CLAUDE.md`). Those prices then appear in the "Procurement" and "Price comparison" views next to the market prices the agent found.

## What it can do

- **Buttons:** tender → item → slot → supplier → price → VAT → comment → saved (and re-read to verify).
- **Free text and files:** paste a supplier's message or forward an invoice, quotation or price list (PDF, Word, Excel, CSV, photo, scan). DeepSeek reads it, takes the seller and the VAT flag from the document, matches every line to a spec item and marks it 🟢 (exact match: same part number, variant and pack size) or 🔴 (anything else, with the reason).
- **Currencies:** prices in another currency are converted to the base currency at the day's ECB reference rate; the conversion is written into the comment.
- **Auto-save with undo** (`AUTO_SAVE=1`), or a confirmation screen with per-line checkboxes (`AUTO_SAVE=0`).
- **Many files at once:** albums and series of files are parsed in parallel; writes to one table are queued and sent as one batch; NocoDB's rate limit is respected with retries.
- **Topics — one per tender:** in a Telegram group with topics (forum), or right inside each user's private chat with the bot (Bot API "Threaded Mode"). Topics are created automatically for every specification in NocoDB, named like `T-33 HYDAC until 21.10` and renamed when the deadline or manufacturers change. Anything posted in a topic belongs to that tender.
- **Access by password**, with a lock-out after repeated wrong attempts.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token. For topics in private chats: @BotFather → your bot → Threads Settings → enable **Threaded Mode**.
2. `pip install -r requirements.txt`
3. `cp config.env.example config.env` and fill it in (or set the same names as environment variables). The NocoDB base and registry table are the ones from the project's `config.json`; `INTERNAL_PREFIX` must match `internal_number.prefix`.
4. `python bot.py`

The bot keeps its state in `authorized_users.json`, `topics.json` and `private_topics.json` next to `bot.py` (all git-ignored).

### Group mode

Create a group, enable Topics in its settings, add the bot as an admin with "Manage topics", then send `/setup <password>` in the group (the message with the password is deleted).

## Commands

| Command | |
|---|---|
| `/start` | log in / main menu (creates the private topics) |
| `/price` | add a price with buttons (`/price T-32` opens that tender) |
| `/cancel` | cancel the current input |
| `/sync` | create topics for new specifications now (otherwise every 30 minutes) |
| `/ping` | check that NocoDB is reachable |
| `/version` | running version and mode |

## Notes

- The bot fills the first three procurement slots; the specification table may have more (`procurement_slots` in `config.json`), which can be filled in NocoDB directly.
- The DeepSeek model only *parses*; it never decides what is written. Without `DEEPSEEK_API_KEY` the bot works in buttons-only mode.
- The base currency must be one the ECB publishes (EUR, USD, GBP, CHF, JPY, PLN, SEK, ...).
