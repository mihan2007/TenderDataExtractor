"""Procurement price bot (Telegram).

Writes supplier prices into a tender's specification table in NocoDB:
tender -> item -> slot 1/2/3 -> supplier -> price -> VAT -> comment -> save
(with a re-read check that the value was stored).

Columns: "Procurement: supplier N", "Procurement: price N", "Procurement: comment"
(step 5.1h in CLAUDE.md).

Features
- Buttons only: pick a tender and an item, type supplier and price.
- Free text (a supplier's message, quotation lines) and files (invoices,
  quotations, price lists - PDF, Word, Excel, CSV, photos, scans) are parsed
  with DeepSeek; the tender must be chosen first.
- The seller and the VAT flag are taken from the document; foreign-currency
  prices are converted at today's ECB rate; an exact match with the item is
  marked 🟢, anything else 🔴 (with the reason) at the start of the supplier name.
- AUTO_SAVE=1: parsed prices are written immediately, with an "Undo" button.
- Several documents in a row / albums: parses don't interfere with each other,
  writes to one table are queued and sent as one batch; the supplier is asked
  once per message.
- Topics: in a Telegram group with topics (forum) the bot creates one topic per
  tender (/setup <password> in the group). With "Threaded Mode" enabled in
  @BotFather the same topics appear in every team member's private chat with
  the bot (PRIVATE_TOPICS=1). Topic names are short: "T-33 HYDAC until 21.10".
  New specifications get a topic automatically (on start and every 30 min).

Settings - environment variables or config.env next to bot.py (see config.env.example).
"""
import asyncio
import html
import json
import logging
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(HERE, "config.env"))  # must run before noco/fx read their settings

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter  # noqa: E402
from aiogram.filters import Command, CommandObject, CommandStart  # noqa: E402
from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.state import State, StatesGroup  # noqa: E402
from aiogram.fsm.strategy import FSMStrategy  # noqa: E402
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton as Btn,  # noqa: E402
                           InlineKeyboardMarkup, Message)

import docs  # noqa: E402
import fx  # noqa: E402
from ai_parse import VAT_EXCL, VAT_INCL, VAT_UNKNOWN, AiError, parse_offer  # noqa: E402
from noco import (COL_AGENT_1, COL_ITEM, COL_MAKER, COL_MODEL, COL_NO, COL_QTY, COL_UNIT,  # noqa: E402
                  COL_UNIT_MAX, COMMENT_COL, DEADLINE_COL, INTERNAL_NUMBER_COL, PRICE_COLS,
                  SPEC_LINK_COLUMN, SUBJECT_COL, SUPPLIER_COLS, TENDER_NUMBER_COL, Noco, NocoError,
                  table_id_from_link)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
BOT_VERSION = "2.5"
PAGE = 8
TENDERS_PAGE = 10

BOT_TOKEN = os.getenv("BOT_TOKEN")
NOCODB_TOKEN = os.getenv("NOCODB_TOKEN")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL") or "deepseek-flash"
OUR_COMPANIES = os.getenv("OUR_COMPANIES") or ""
BASE_CURRENCY = fx.BASE_CURRENCY
PREFIX = os.getenv("INTERNAL_PREFIX") or "T-"
TZ = ZoneInfo(os.getenv("BOT_TIMEZONE") or "UTC")


def _flag(name, default="1"):
    return (os.getenv(name) or default).strip().lower() not in ("0", "no", "false", "off")


AUTO_SAVE = _flag("AUTO_SAVE")            # write parsed prices immediately (0 = ask first)
PRIVATE_TOPICS = _flag("PRIVATE_TOPICS")  # topics per specification in private chats
for _name in ("BOT_TOKEN", "NOCODB_TOKEN", "BOT_PASSWORD", "NOCODB_BASE_ID", "NOCODB_REGISTRY_TABLE_ID"):
    if not os.getenv(_name):
        raise SystemExit(f"{_name} is not set (environment variable or config.env)")

noco = Noco(NOCODB_TOKEN)
dp = Dispatcher(fsm_strategy=FSMStrategy.USER_IN_TOPIC)  # separate state per user in each topic

# ---------- password access ----------
USERS_FILE = os.path.join(HERE, "authorized_users.json")


def load_users():
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


USERS = load_users()
FAILED = {}  # user_id -> [attempts, blocked until]


def save_users():
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in USERS.items()}, f, ensure_ascii=False, indent=1)


def allowed(user) -> bool:
    return user is not None and user.id in USERS


class Auth(StatesGroup):
    password = State()


class Flow(StatesGroup):
    tender = State()
    positions = State()
    supplier = State()
    price = State()
    comment = State()
    ai_supplier = State()
    ai_confirm = State()


# ---------- formatting ----------
def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def money(v) -> str:
    if v in (None, ""):
        return "—"
    try:
        return f"{float(v):,.2f} {BASE_CURRENCY}"
    except (TypeError, ValueError):
        return esc(v)


def qty(v) -> str:
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return esc(v or "—")


def short(s, n=40) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


CURRENCY_SUFFIX_RE = re.compile(r"([a-z]{3}|€|\$|£)$")


def parse_price(text: str):
    """"85924", "85,924.50", "85.924,50", "12.5k", "1 200 000 EUR" -> float."""
    s = text.lower().replace(" ", "").replace(" ", "")
    s = CURRENCY_SUFFIX_RE.sub("", s).lstrip("€$£")
    mult = 1
    if s.endswith("k"):
        mult, s = 1000, s[:-1]
    if not re.fullmatch(r"\d[\d.,]*", s):
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif s.count(",") + s.count(".") == 1:
        whole, frac = re.split(r"[.,]", s)
        s = whole + frac if (len(frac) == 3 and mult == 1) else f"{whole}.{frac}"
    else:
        s = s.replace(",", "").replace(".", "")
    try:
        v = round(float(s) * mult, 2)
    except ValueError:
        return None
    return v if v > 0 else None


def tender_label(rec) -> str:
    name = str(rec.get(SUBJECT_COL) or "").split("\n")[0]
    return f"{rec.get(INTERNAL_NUMBER_COL) or '?'} — {short(name, 34)}"


def has_purchase(row) -> bool:
    return any(row.get(c) not in (None, "") for c in PRICE_COLS)


def position_card(code, row) -> str:
    lines = [f"<b>{esc(code)} · item {esc(row.get(COL_NO))}</b>", esc(short(row.get(COL_ITEM), 300))]
    if row.get(COL_MODEL):
        lines.append(f"Brand/model: {esc(row[COL_MODEL])}")
    if row.get(COL_MAKER):
        lines.append(f"Manufacturer: {esc(row[COL_MAKER])}")
    lines.append(f"Qty: {qty(row.get(COL_QTY))} {esc(row.get(COL_UNIT) or '')}")
    if row.get(COL_UNIT_MAX) is not None:
        lines.append(f"Max unit price: {money(row.get(COL_UNIT_MAX))}")
    if row.get(COL_AGENT_1) is not None:
        lines.append(f"Agent price 1: {money(row.get(COL_AGENT_1))}")
    lines.append("\n<b>Procurement:</b>")
    for i in range(len(SUPPLIER_COLS)):
        sup, pr = row.get(SUPPLIER_COLS[i]), row.get(PRICE_COLS[i])
        lines.append(f"{i + 1}) {esc(sup or '—')} — {money(pr)}" if (sup or pr is not None) else f"{i + 1}) —")
    if row.get(COMMENT_COL):
        lines.append(f"\n<i>Comment:</i>\n{esc(short(row[COMMENT_COL], 700))}")
    return "\n".join(lines)


def card_kb(row, switch=True) -> InlineKeyboardMarkup:
    """switch=False - inside a tender topic: no "Other tender" button (tender = topic)."""
    slots = []
    for i in range(len(SUPPLIER_COLS)):
        sup, pr = row.get(SUPPLIER_COLS[i]), row.get(PRICE_COLS[i])
        label = f"✏️ {i + 1}: {short(sup or '', 14)} {money(pr)}" if (sup or pr is not None) else f"➕ Price {i + 1}"
        slots.append([Btn(text=label, callback_data=f"s:{i + 1}")])
    last = [Btn(text="📋 Item list", callback_data="list")]
    if switch:
        last.append(Btn(text="🔁 Other tender", callback_data="tenders"))
    return InlineKeyboardMarkup(inline_keyboard=slots + [[Btn(text="➡️ Next item", callback_data="next")], last])


CANCEL_KB = InlineKeyboardMarkup(inline_keyboard=[[Btn(text="✖️ Cancel", callback_data="card")]])


# ---------- common steps ----------
async def show_tenders(target: Message, state: FSMContext, edit=False, page=0):
    """All specifications (tenders with a spec table), TENDERS_PAGE per page."""
    await state.set_state(Flow.tender)
    try:
        recs = await noco.recent_tenders(1000)
    except NocoError as e:
        await target.answer(f"⚠️ {esc(e)}")
        return
    pages = max(1, (len(recs) + TENDERS_PAGE - 1) // TENDERS_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = recs[page * TENDERS_PAGE:(page + 1) * TENDERS_PAGE]
    kb = [[Btn(text=tender_label(r), callback_data=f"t:{r['Id']}")] for r in chunk]
    nav = []
    if page > 0:
        nav.append(Btn(text="◀️", callback_data=f"tp:{page - 1}"))
    if pages > 1:
        nav.append(Btn(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(Btn(text="▶️", callback_data=f"tp:{page + 1}"))
    if nav:
        kb.append(nav)
    text = (f"📋 Specifications: {len(recs)} (page {page + 1} of {pages}).\n"
            f"Pick a tender or type its number (e.g. <b>{esc(PREFIX)}32</b>):")
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if edit:
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def open_tender(msg: Message, state: FSMContext, rec):
    table_id = table_id_from_link(rec.get(SPEC_LINK_COLUMN))
    code = rec.get(INTERNAL_NUMBER_COL) or rec.get(TENDER_NUMBER_COL) or "?"
    if not table_id:
        await msg.answer(f"Tender {esc(code)} has no specification table in NocoDB.")
        return
    await state.update_data(table_id=table_id, code=code, page=0, filter="")
    missing = await noco.missing_purchase_columns(table_id)
    if missing:
        await state.set_state(Flow.tender)
        await msg.answer(
            f"The specification <b>{esc(code)}</b> has no \"Procurement\" columns yet ({len(missing)}).\n"
            "Create them? Existing data will not change.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [Btn(text="✅ Create columns", callback_data="mkcols")],
                [Btn(text="🔁 Other tender", callback_data="tenders")]]))
        return
    await after_tender_ready(msg, state)


async def after_tender_ready(msg: Message, state: FSMContext):
    """Tender chosen and columns present: process text/files sent before the
    tender was chosen, otherwise show the items."""
    data = await state.get_data()
    pending_files = data.get("pending_files")
    if pending_files:
        await state.update_data(pending_files=None)
        await msg.answer(f"Tender <b>{esc(data['code'])}</b> selected. Processing the files you sent: {len(pending_files)}…")
        await process_files(msg, state, pending_files)
        return
    pending = data.get("pending_text")
    if pending:
        await state.update_data(pending_text=None)
        await msg.answer(f"Tender <b>{esc(data['code'])}</b> selected. Processing the text you sent…")
        await run_ai(msg, state, pending)
    else:
        await show_positions(msg, state)


async def show_positions(msg: Message, state: FSMContext, edit=False):
    data = await state.get_data()
    await state.set_state(Flow.positions)
    rows = await noco.positions(data["table_id"])
    flt = data.get("filter", "").lower()
    if flt:
        rows = [r for r in rows if flt in f"{r.get(COL_ITEM)} {r.get(COL_MODEL)}".lower()]
    pages = max(1, (len(rows) + PAGE - 1) // PAGE)
    page = min(data.get("page", 0), pages - 1)
    kb = [[Btn(text=("✅ " if has_purchase(r) else "") + f"{r.get(COL_NO)}. {short(r.get(COL_ITEM), 36)}",
               callback_data=f"p:{r['Id']}")] for r in rows[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0:
        nav.append(Btn(text="◀️", callback_data=f"pg:{page - 1}"))
    if pages > 1:
        nav.append(Btn(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(Btn(text="▶️", callback_data=f"pg:{page + 1}"))
    if nav:
        kb.append(nav)
    if not topic_tender(msg):
        kb.append([Btn(text="🔁 Other tender", callback_data="tenders")])
    head = f"<b>{esc(data['code'])}</b>: {len(rows)} items" + (f" (search \"{esc(flt)}\")" if flt else "")
    text = (f"{head}\n✅ — already has a procurement price.\n"
            "Pick an item, type its No., or part of its name/model to search.")
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if edit:
        await msg.edit_text(text, reply_markup=markup)
    else:
        await msg.answer(text, reply_markup=markup)


async def show_card(msg: Message, state: FSMContext, row_id=None, edit=False):
    data = await state.get_data()
    row_id = row_id or data["row_id"]
    await state.update_data(row_id=row_id)
    await state.set_state(Flow.positions)
    row = await noco.position(data["table_id"], row_id)
    text, kb = position_card(data["code"], row), card_kb(row, switch=not topic_tender(msg))
    if edit:
        await msg.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)


_PFX = re.escape(PREFIX.rstrip("-").strip() or "T")
TENDER_RE = re.compile(rf"^\s*({_PFX})\s*-?\s*(\d{{1,6}})\b[\s:;,.\-—]*(.*)$", re.S | re.I)


def tender_code(digits) -> str:
    return f"{PREFIX}{digits}"


def looks_like_offer(text: str) -> bool:
    """Looks like a message with a price: digits plus at least 3 words or a line break."""
    t = text or ""
    return bool(re.search(r"\d", t)) and (len(t.split()) >= 3 or "\n" in t)


# ---------- /version ----------
@dp.message(Command("version"))
async def cmd_version(msg: Message):
    files = sorted(f for f in os.listdir(HERE) if f.endswith(".py"))
    mode = f"connected, topics: {len(GROUP['topics'])}" if GROUP.get("group_id") else "not connected"
    if not PRIVATE_TOPICS:
        priv = "disabled in config (PRIVATE_TOPICS=0)"
    elif await private_topics_on(msg.bot, force=True):
        priv = f"enabled, your topics: {len(PRIV.get(str(msg.from_user.id), {}).get('topics', {}))}"
    else:
        priv = "disabled in @BotFather (Threads Settings → Threaded Mode)"
    await msg.answer(f"🤖 Price bot, version <b>{BOT_VERSION}</b>\n"
                     f"Files: {esc(', '.join(files))}\n"
                     f"DeepSeek: {esc(DEEPSEEK_MODEL) if DEEPSEEK_API_KEY else 'disabled (no key)'}\n"
                     f"Group: {mode}\n"
                     f"Topics in private chat: {priv}")


# ---------- topics: one topic = one specification (group or private chat) ----------
# The topic list comes from NocoDB: every registry record with a "Specification
# link" gets one topic. Everything posted in a topic belongs to that tender.
# Group: a Telegram group with topics (forum), connected with /setup <password>;
# mapping topic -> tender in topics.json; every group member has access.
# Private chat: with "Threaded Mode" enabled in @BotFather the bot creates the
# same topics in each authorized user's private chat; mapping in private_topics.json.
# Store: "topics" {thread: registry Id}, "names" {thread: name given by the bot},
# "keep" [threads created by people - the bot never renames them].
TOPICS_FILE = os.path.join(HERE, "topics.json")
PRIVATE_FILE = os.path.join(HERE, "private_topics.json")
GROUP_CHAT = F.chat.type.in_({"group", "supergroup"})
TOPIC_COLORS = (0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F)  # colors Telegram accepts
TOPICS_OFF_TEXT = ("Topics in the private chat are off. To open the bot chat as a list of topics (one per "
                   "specification): @BotFather → /mybots → this bot → Threads Settings → enable Threaded Mode, "
                   "then /start.\nUntil then the regular menu works: /price.")


def _topic_store(d) -> dict:
    d = d or {}
    return {"topics": {str(k): int(v) for k, v in (d.get("topics") or {}).items()},
            "names": {str(k): str(v) for k, v in (d.get("names") or {}).items()},
            "keep": [str(k) for k in (d.get("keep") or [])]}


def load_group():
    try:
        with open(TOPICS_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    return {"group_id": d.get("group_id"), **_topic_store(d)}


def load_private():
    try:
        with open(PRIVATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    return {str(uid): _topic_store(u) for uid, u in d.items()}


GROUP = load_group()
PRIV = load_private()  # user -> their topics in the private chat
SYNC_LOCK = asyncio.Lock()
PRIV_LOCKS = defaultdict(asyncio.Lock)
BOT_TOPICS = {"on": None, "checked": 0.0}  # is Threaded Mode enabled for the bot


def save_group():
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(GROUP, f, ensure_ascii=False, indent=1)


def save_private():
    with open(PRIVATE_FILE, "w", encoding="utf-8") as f:
        json.dump(PRIV, f, ensure_ascii=False, indent=1)


def priv_store(uid) -> dict:
    return PRIV.setdefault(str(uid), _topic_store({}))


def is_group(msg) -> bool:
    return msg is not None and msg.chat.type in ("group", "supergroup")


def group_ok(chat_id) -> bool:
    return GROUP["group_id"] is not None and chat_id == GROUP["group_id"]


def topic_tender(msg):
    """Registry Id of the topic the message was posted in (group or private), or None."""
    if msg is None or not getattr(msg, "is_topic_message", None) or not getattr(msg, "message_thread_id", None):
        return None
    thread = str(msg.message_thread_id)
    if msg.chat.type == "private":
        return PRIV.get(str(msg.chat.id), {}).get("topics", {}).get(thread)
    if group_ok(msg.chat.id):
        return GROUP["topics"].get(thread)
    return None


# ---------- short topic name: "T-33 HYDAC until 21.10" ----------
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"), 1)}
DMY_TEXT_RE = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(\d{4})", re.I)
MDY_TEXT_RE = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I)
NAME_CACHE = {}  # registry Id -> (time, record key, name)


class SpecMissing(NocoError):
    """The registry has a "Specification link", but the table is gone - no topic."""


def deadline_date(rec):
    """First date in "Submission deadline": "✅ 21.10.2026 12:00 CET", "2026-05-20 12:00",
    "21/10/2026", "21 October 2026", "October 21, 2026"."""
    s = str(rec.get(DEADLINE_COL) or "")
    found = [(m.start(), int(m.group(3)), int(m.group(2)), int(m.group(1)))
             for m in re.finditer(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)", s)]
    found += [(m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3)))
              for m in re.finditer(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", s)]
    found += [(m.start(), int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
              for m in DMY_TEXT_RE.finditer(s)]
    found += [(m.start(), int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
              for m in MDY_TEXT_RE.finditer(s)]
    for _, y, mo, d in sorted(found):
        try:
            return date(y, mo, d)
        except ValueError:
            continue
    return None


def clean_brand(raw) -> str:
    """"Phoenix Contact (equivalent)" -> "Phoenix Contact", "HydroPro, Inc." -> "HydroPro"."""
    b = re.sub(r"\([^)]*\)", " ", str(raw or ""))
    b = re.sub(r"[,\s]+(inc|ltd|llc|gmbh|ag|co|corp|s\.?a|s\.?r\.?l|b\.?v)\.?\s*$", "", b.strip(), flags=re.I)
    return " ".join(b.split()).strip(" ,.;")


def spec_brands(rows, limit=3):
    """Main manufacturers of a specification (by item count) and how many there are.
    Equivalents and empty values are not counted."""
    cnt, shown = Counter(), {}
    for r in rows:
        raw = str(r.get(COL_MAKER) or "").strip()
        if not raw or "equivalent" in raw.lower():
            continue
        b = clean_brand(raw)
        if not b or b.lower().startswith(("not ", "—", "-")):
            continue
        cnt[b.lower()] += 1
        shown.setdefault(b.lower(), b)
    return [shown[k] for k, _ in cnt.most_common(limit)], len(cnt)


def make_topic_name(rec, brands=(), total=0) -> str:
    code = str(rec.get(INTERNAL_NUMBER_COL) or rec.get("Id") or "?").strip()
    d = deadline_date(rec)
    tail = ""
    if d:
        tail = f" until {d:%d.%m}" if d.year == datetime.now(TZ).year else f" until {d:%d.%m.%Y}"
    if brands:
        mid = ", ".join(brands) + (" et al." if total > len(brands) else "")
    else:  # no manufacturers in the specification - start of the subject
        mid = str(rec.get(SUBJECT_COL) or "").split("\n")[0]
    room = min(60, 128 - len(code) - len(tail) - 1)
    return " ".join(f"{code} {short(mid, room) if mid else ''}{tail}".split())[:128]


async def topic_name_for(rec) -> str:
    """Topic name for a registry record and its specification (cached 10 min).
    Raises NocoError if NocoDB does not answer (the caller decides)."""
    key = (rec.get(INTERNAL_NUMBER_COL), rec.get(DEADLINE_COL), rec.get(SUBJECT_COL))
    cached = NAME_CACHE.get(rec["Id"])
    if cached and cached[1] == key and time.time() - cached[0] < 600:
        return cached[2]
    brands, total = [], 0
    table_id = table_id_from_link(rec.get(SPEC_LINK_COLUMN))
    if table_id:
        try:
            brands, total = spec_brands(await noco.positions(table_id))
        except NocoError as e:
            if "ERR_TABLE_NOT_FOUND" in str(e):  # the link exists but the table was deleted
                raise SpecMissing(str(e)) from e
            raise
    name = make_topic_name(rec, brands, total)
    NAME_CACHE[rec["Id"]] = (time.time(), key, name)
    return name


def intro_text(rec, private=False) -> str:
    code = rec.get(INTERNAL_NUMBER_COL) or "?"
    title = str(rec.get(SUBJECT_COL) or "").split("\n")[0]
    deadline = str(rec.get(DEADLINE_COL) or "").replace("✅", "").strip()
    link = rec.get(SPEC_LINK_COLUMN)
    then = "write the prices to NocoDB right away" if AUTO_SAVE else "ask before adding them"
    return (f"📌 <b>{esc(code)} — {esc(short(title, 200))}</b>\n"
            + (f"Submission deadline: {esc(deadline)}\n" if deadline else "")
            + (f'<a href="{esc(link)}">Specification in NocoDB</a>\n' if link else "")
            + f"\nDrop invoices, quotations, price lists (PDF, Word, Excel, photo, scan) or a text with prices "
              f"here — I'll parse them with DeepSeek and {then}.\n"
            + ("Item No. or name — I'll show its card · " if private else "")
            + "/price — item list")


async def tg(call, *args, **kwargs):
    """Telegram call with a retry when Telegram asks to wait (flood control)."""
    for attempt in range(3):
        try:
            return await call(*args, **kwargs)
        except TelegramRetryAfter as e:
            if attempt == 2:
                raise
            await asyncio.sleep(e.retry_after + 1)


async def _sync_chat(bot, chat_id, store, recs, save, private):
    """Topics in one chat (group or private) from the NocoDB specification list:
    create missing ones (oldest first, so new ones end up on top) and rename the
    ones whose name changed. Returns (created, errors)."""
    have = {rec_id: thread for thread, rec_id in store["topics"].items()}
    created, errors = [], []
    for rec in reversed(recs):
        code = str(rec.get(INTERNAL_NUMBER_COL) or rec["Id"])
        thread = have.get(rec["Id"])
        try:
            name = await topic_name_for(rec)
        except SpecMissing:
            continue  # no spec table - no topic
        except NocoError as e:
            logging.warning("Topic name %s: %s", code, e)
            if thread is not None:
                continue  # rename on the next check
            name = make_topic_name(rec)
        if thread is not None:
            if thread in store["keep"] or store["names"].get(thread) == name:
                continue
            try:
                await tg(bot.edit_forum_topic, chat_id, int(thread), name=name)
                logging.info("Topic %s in chat %s renamed: %s", code, chat_id, name)
                await asyncio.sleep(0.5)
            except TelegramForbiddenError as e:
                errors.append(str(e))
                break
            except Exception as e:  # topic deleted or name unchanged - do not retry
                logging.warning("Renaming topic %s in chat %s: %s", code, chat_id, e)
            store["names"][thread] = name
            save()
            continue
        try:
            topic = await tg(bot.create_forum_topic, chat_id, name,
                             icon_color=TOPIC_COLORS[int(rec["Id"]) % len(TOPIC_COLORS)])
        except Exception as e:
            errors.append(str(e))
            break
        thread = str(topic.message_thread_id)
        store["topics"][thread] = rec["Id"]
        store["names"][thread] = name
        save()
        created.append(code)
        try:
            m = await tg(bot.send_message, chat_id, intro_text(rec, private=private),
                         message_thread_id=topic.message_thread_id, disable_web_page_preview=True)
            try:
                await bot.pin_chat_message(chat_id, m.message_id, disable_notification=True)
            except Exception:
                pass
        except Exception as e:
            errors.append(str(e))
        await asyncio.sleep(1.5)  # stay within Telegram limits
    return created, errors


async def sync_topics(bot):
    """Group: topics for all specifications in NocoDB. Returns (created, errors)."""
    if GROUP["group_id"] is None:
        return [], ["group not connected (/setup)"]
    async with SYNC_LOCK:
        recs = await noco.recent_tenders(1000)
        return await _sync_chat(bot, GROUP["group_id"], GROUP, recs, save_group, private=False)


async def private_topics_on(bot, force=False) -> bool:
    """Is Threaded Mode enabled for the bot in private chats (@BotFather)?
    Telegram's answer is cached for 10 minutes."""
    if not PRIVATE_TOPICS:
        return False
    if force or BOT_TOPICS["on"] is None or time.time() - BOT_TOPICS["checked"] > 600:
        try:
            me = await bot.get_me()
            BOT_TOPICS["on"] = bool(getattr(me, "has_topics_enabled", False))
            BOT_TOPICS["checked"] = time.time()
        except Exception as e:
            logging.warning("getMe: %s", e)
    return bool(BOT_TOPICS["on"])


async def sync_private(bot, uid):
    """A user's private chat: topics for all specifications. Returns (created, errors)."""
    async with PRIV_LOCKS[uid]:
        recs = await noco.recent_tenders(1000)
        return await _sync_chat(bot, uid, priv_store(uid), recs, save_private, private=True)


async def sync_all_private(bot):
    if not await private_topics_on(bot, force=True):
        return
    for uid in list(USERS):
        try:
            created, errors = await sync_private(bot, uid)
            if created or errors:
                logging.info("Private topics %s: created %s, errors %s", uid, created, errors)
        except Exception:
            logging.exception("Private topics %s", uid)


def sync_report(created, errors) -> str:
    text = (f"✅ Topics created: {len(created)} ({', '.join(created)})" if created
            else "No new specifications — all topics already exist.")
    if errors:
        text += (f"\n⚠️ {esc(errors[0])}\nMake sure the bot is a group admin "
                 "with the \"Manage topics\" permission.")
    return text


def private_report(uid, created, errors) -> str:
    total = len(priv_store(uid)["topics"])
    text = (f"📂 Topics for NocoDB specifications: <b>{total}</b>"
            + (f", new: {len(created)} ({esc(', '.join(created))})" if created else "") + ".\n"
            "Open a tender's topic and drop invoices, quotations, price lists or a text with prices there. "
            "New specifications appear as topics automatically (checked every 30 minutes, /sync — now).")
    if errors:
        text += (f"\n⚠️ Could not create a topic: {esc(errors[0])}\n"
                 "Check in @BotFather (Threads Settings) that Threaded Mode is enabled, and update Telegram.")
    return text


async def auto_sync(bot):
    """On start and then every 30 minutes: topics for new specifications and
    new names - in the group and in the private chats of all authorized users."""
    await asyncio.sleep(15)
    while True:
        if GROUP["group_id"] is not None:
            try:
                created, errors = await sync_topics(bot)
                if created or errors:
                    logging.info("Group topic sync: created %s, errors %s", created, errors)
            except Exception:
                logging.exception("Group topic sync")
        try:
            await sync_all_private(bot)
        except Exception:
            logging.exception("Private topic sync")
        await asyncio.sleep(1800)


async def show_main(msg: Message, state: FSMContext):
    """Private chat home: in topic mode create missing topics and report
    (the chat opens as a topic list); otherwise the specification list as buttons."""
    if msg.chat.type == "private" and await private_topics_on(msg.bot, force=True):
        uid = msg.chat.id
        if not priv_store(uid)["topics"]:
            await msg.answer("⏳ Creating topics for the NocoDB specifications — this takes about a minute…")
        try:
            created, errors = await sync_private(msg.bot, uid)
        except NocoError as e:
            await msg.answer(f"⚠️ {esc(e)}")
            return
        await msg.answer(private_report(uid, created, errors))
        if priv_store(uid)["topics"]:
            return
        # no topic could be created - fall back to the button list
    await show_tenders(msg, state)


@dp.message.middleware()
async def topic_context_mw(handler, event, data):
    """In a tender topic, puts that tender into the user's state (each user has
    their own state per topic - FSMStrategy.USER_IN_TOPIC) and remembers the
    user's name (actor) for comments written on auto-save."""
    await _remember_actor(event.from_user, data)
    await _inject_topic(event, data)
    return await handler(event, data)


@dp.callback_query.middleware()
async def topic_context_cb_mw(handler, event, data):
    await _remember_actor(event.from_user, data)
    await _inject_topic(event.message, data)
    return await handler(event, data)


async def _remember_actor(user, data):
    state = data.get("state")
    if state is not None and user is not None and not user.is_bot:
        d = await state.get_data()
        if d.get("actor") != user.full_name or d.get("actor_id") != user.id:
            await state.update_data(actor=user.full_name, actor_id=user.id)


async def _inject_topic(msg, data):
    if msg is None or (is_group(msg) and not group_ok(msg.chat.id)):
        return
    rec_id = topic_tender(msg)
    state = data.get("state")
    if not rec_id or state is None:
        return
    d = await state.get_data()
    if d.get("tender_rec") != rec_id or "table_id" not in d:
        rec = await noco.get_tender(rec_id)
        await state.update_data(tender_rec=rec_id, table_id=table_id_from_link(rec.get(SPEC_LINK_COLUMN)),
                                code=rec.get(INTERNAL_NUMBER_COL) or "?", page=0, filter="")


@dp.message(GROUP_CHAT, Command("setup"))
async def cmd_setup(msg: Message, command: CommandObject):
    try:
        await msg.delete()  # don't leave the password in the group
    except Exception:
        pass
    if (command.args or "").strip() != BOT_PASSWORD:
        await msg.answer("Wrong password. To connect the group: /setup <password>")
        return
    if not msg.chat.is_forum:
        await msg.answer("Topics are not enabled in this group: Group settings → Topics → enable. Then /setup <password> again.")
        return
    GROUP["group_id"] = msg.chat.id
    save_group()
    logging.info("Group connected: %s (%s)", msg.chat.title, msg.chat.id)
    await msg.answer("✅ Group connected. Creating topics for the NocoDB specifications — this may take a couple of minutes…")
    created, errors = await sync_topics(msg.bot)
    await msg.answer(sync_report(created, errors))


@dp.message(GROUP_CHAT, lambda m: not group_ok(m.chat.id))
async def foreign_group(msg: Message):
    """Group not connected (/setup) - stay silent."""


@dp.callback_query(lambda c: is_group(c.message) and not group_ok(c.message.chat.id))
async def foreign_group_cb(cb: CallbackQuery):
    await cb.answer()


async def _bind_human_topic(msg: Message, store: dict, save, private: bool):
    """A person created a topic named like "T-35 ..." - bind it to that tender if
    it exists in the registry. The bot never renames such topics."""
    thread = str(msg.message_thread_id or "")
    if not thread or thread in store["topics"]:
        return
    m = TENDER_RE.match(msg.forum_topic_created.name or "")
    rec = await noco.find_tender(tender_code(m.group(2)), PREFIX) if m else None
    if rec and table_id_from_link(rec.get(SPEC_LINK_COLUMN)):
        store["topics"][thread] = rec["Id"]
        store["keep"].append(thread)
        save()
        await msg.answer(intro_text(rec, private=private), disable_web_page_preview=True)
    elif private:
        await msg.answer(f"This topic is not linked to a tender: its name has no NocoDB specification number "
                         f"(e.g. \"{esc(PREFIX)}35 …\"). The bot creates topics for all specifications itself.")


@dp.message(GROUP_CHAT, F.forum_topic_created)
async def topic_created(msg: Message):
    await _bind_human_topic(msg, GROUP, save_group, private=False)


@dp.message(F.chat.type == "private", F.forum_topic_created, F.from_user.func(allowed))
async def private_topic_created(msg: Message):
    await _bind_human_topic(msg, priv_store(msg.chat.id), save_private, private=True)


@dp.message(F.chat.type == "private", F.forum_topic_created | F.forum_topic_edited | F.forum_topic_closed
            | F.forum_topic_reopened | F.pinned_message)
async def private_service(msg: Message):
    """Service messages of private-chat topics (created, renamed, pinned) - ignore."""


@dp.message(GROUP_CHAT, ~F.text & ~F.document & ~F.photo)
async def group_service(msg: Message):
    """Group service messages (pins, joins, etc.) - ignore."""


@dp.message(GROUP_CHAT, Command("sync"))
async def cmd_sync(msg: Message):
    await msg.answer("Checking the NocoDB specifications and creating topics for new ones…")
    created, errors = await sync_topics(msg.bot)
    await msg.answer(sync_report(created, errors))


@dp.message(F.chat.type == "private", Command("sync"), F.from_user.func(allowed))
async def cmd_sync_private(msg: Message):
    if not await private_topics_on(msg.bot, force=True):
        await msg.answer(TOPICS_OFF_TEXT)
        return
    await msg.answer("Checking the NocoDB specifications…")
    created, errors = await sync_private(msg.bot, msg.chat.id)
    await msg.answer(private_report(msg.chat.id, created, errors))


@dp.message(GROUP_CHAT, Command("start", "help"))
async def group_help(msg: Message):
    rec_id = topic_tender(msg)
    if rec_id:
        await msg.answer(intro_text(await noco.get_tender(rec_id)), disable_web_page_preview=True)
    else:
        await msg.answer("Each topic of this group is one NocoDB specification. Open a tender's topic and drop an "
                         "invoice, quotation, price list or a text with prices there — I'll parse it and write it "
                         "to NocoDB.\n/sync — create topics for new specifications (also checked every 30 minutes).")


@dp.message(GROUP_CHAT, lambda m: topic_tender(m) is None)
async def group_no_tender(msg: Message):
    if msg.document or msg.photo or (msg.text and looks_like_offer(msg.text) and not msg.text.startswith("/")):
        await msg.reply(f"This topic is not linked to a tender. Post the file in the right tender's topic "
                        f"(e.g. \"{esc(PREFIX)}32 …\"). Topics for new specifications appear automatically or via /sync.")


async def group_chatter(msg: Message, state: FSMContext) -> bool:
    """Ordinary chat in a tender topic ("ok", "thanks", "they'll send it tomorrow")
    is not sent to DeepSeek. Let through: price-like text, replies to the bot,
    input in a started dialog (supplier, price, comment), item numbers."""
    if msg.text is None or msg.text.startswith("/"):
        return False
    st = await state.get_state()
    if st not in (None, Flow.positions.state, Flow.ai_confirm.state, Flow.tender.state):
        return False
    t = msg.text.strip()
    if looks_like_offer(t):
        return False
    if st == Flow.positions.state and re.search(r"\d", t):
        return False
    r = msg.reply_to_message
    if r is not None and r.from_user is not None and r.from_user.is_bot and not r.forum_topic_created:
        return False
    return True


@dp.message(GROUP_CHAT, group_chatter)
async def group_chatter_skip(msg: Message):
    """Chit-chat in a topic - the bot stays silent."""


# In a group everyone sees the buttons, but only the person who started the input
# can press them: each user has their own state, so without that data a button is "foreign".
OWNER_KEYS = (("ai:", "ai_items"), ("aisup:", "ai_items"), ("s:", "row_id"), ("save", "price"),
              ("clear", "row_id"), ("replace", "row_id"), ("card", "row_id"), ("next", "row_id"),
              ("sup:", "slot"), ("vat:", "price"), ("nocomment", "price"))


async def foreign_button(cb: CallbackQuery, state: FSMContext) -> bool:
    if not is_group(cb.message) or not cb.data:
        return False
    d = await state.get_data()
    for prefix, key in OWNER_KEYS:
        if cb.data.startswith(prefix):
            return not d.get(key)
    return False


@dp.callback_query(foreign_button)
async def foreign_button_cb(cb: CallbackQuery):
    await cb.answer("These buttons belong to whoever sent the file or started the input.", show_alert=True)


@dp.callback_query(lambda c: (is_group(c.message) or topic_tender(c.message)) and c.data
                   and (c.data == "tenders" or c.data.startswith("t:") or c.data.startswith("tp:")))
async def group_no_switch(cb: CallbackQuery):
    await cb.answer("Tender = topic. Open the other tender's topic instead.", show_alert=True)


# ---------- commands ----------
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    if not allowed(msg.from_user):
        await state.set_state(Auth.password)
        await msg.answer("Procurement price bot. Enter the password to get access:")
        return
    rec_id = topic_tender(msg)
    if rec_id:  # /start inside a tender topic - that topic's card
        await msg.answer(intro_text(await noco.get_tender(rec_id), private=True), disable_web_page_preview=True)
        return
    if not await private_topics_on(msg.bot):
        then = "write it to NocoDB right away" if AUTO_SAVE else "ask before adding it"
        await msg.answer(f"Price bot, version {BOT_VERSION}. I add supplier prices to tender specifications (NocoDB).\n"
                         "/price — add a price with buttons · /cancel — cancel\n"
                         + (f"You can also just send a text with prices (e.g. \"{PREFIX}32 AME 25 SD — 815 excl. VAT, "
                            "Acme GmbH\") or a file — invoice, quotation, price list (PDF, Word, Excel, photo) — "
                            f"I'll parse it with DeepSeek and {then}." if DEEPSEEK_API_KEY else ""))
    await show_main(msg, state)


@dp.message(Auth.password)
async def got_password(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    tries, until = FAILED.get(uid, [0, 0])
    if time.time() < until:
        await msg.answer("Too many attempts. Try again in 10 minutes.")
        return
    try:
        await msg.delete()  # don't leave the password in the chat
    except Exception:
        pass
    if (msg.text or "").strip() != BOT_PASSWORD:
        tries += 1
        FAILED[uid] = [0, time.time() + 600] if tries >= 5 else [tries, 0]
        await msg.answer("Wrong password.")
        return
    FAILED.pop(uid, None)
    USERS[uid] = msg.from_user.full_name
    save_users()
    logging.info("Access granted: %s (%s)", msg.from_user.full_name, uid)
    await state.clear()
    await msg.answer("✅ Access granted.")
    await show_main(msg, state)


@dp.message(Command("ping"))
async def cmd_ping(msg: Message):
    t = time.monotonic()
    try:
        await noco.recent_tenders(1)
        await msg.answer(f"✅ NocoDB is reachable ({int((time.monotonic() - t) * 1000)} ms)")
    except NocoError as e:
        await msg.answer(f"❌ {esc(e)}")


@dp.message(F.chat.type == "private", ~F.from_user.func(allowed))
async def not_allowed(msg: Message, state: FSMContext):
    await state.set_state(Auth.password)
    await msg.answer("No access. Enter the password:")


@dp.callback_query(lambda c: not is_group(c.message), ~F.from_user.func(allowed))
async def not_allowed_cb(cb: CallbackQuery):
    await cb.answer("No access — send /start", show_alert=True)


@dp.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("row_id"):
        await msg.answer("Cancelled.")
        await show_card(msg, state)
    else:
        await state.clear()
        await msg.answer("Cancelled. /price — start again.")


@dp.message(Command("price", "tender"))
async def cmd_price(msg: Message, state: FSMContext):
    await state.clear()
    rec_id = topic_tender(msg)
    if rec_id:  # inside a topic the tender is given by the topic
        await open_tender(msg, state, await noco.get_tender(rec_id))
        return
    if is_group(msg):
        return
    arg = (msg.text or "").split(maxsplit=1)
    if len(arg) > 1:
        rec = await noco.find_tender(arg[1], PREFIX)
        if rec:
            await open_tender(msg, state, rec)
            return
        await msg.answer(f"Tender \"{esc(arg[1])}\" not found.")
    await show_tenders(msg, state)


# ---------- tender selection ----------
@dp.callback_query(F.data == "tenders")
async def cb_tenders(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    old = await state.get_data()
    await state.set_data({k: old[k] for k in ("pending_text", "pending_files") if old.get(k)})
    await show_tenders(cb.message, state, edit=True)


@dp.callback_query(F.data.startswith("tp:"))
async def cb_tender_page(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await show_tenders(cb.message, state, edit=True, page=int(cb.data[3:]))


@dp.callback_query(F.data.startswith("t:"))
async def cb_tender(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    rec = await noco.get_tender(int(cb.data[2:]))
    await open_tender(cb.message, state, rec)


@dp.message(Flow.tender, F.text)
async def typed_tender(msg: Message, state: FSMContext):
    if looks_like_offer(msg.text) and not TENDER_RE.match(msg.text):
        await ask_tender_first(msg, state, msg.text)
        return
    await handle_free_text(msg, state, msg.text, tender_required=True)


@dp.callback_query(F.data == "mkcols")
async def cb_mkcols(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Creating columns…")
    data = await state.get_data()
    try:
        await noco.create_purchase_columns(data["table_id"])
    except NocoError as e:
        await cb.message.answer(f"⚠️ Could not create the columns: {esc(e)}")
        return
    logging.info("Procurement columns created in %s (%s)", data["code"], cb.from_user.full_name)
    await cb.message.edit_text(f"✅ \"Procurement\" columns created in {esc(data['code'])}.")
    await after_tender_ready(cb.message, state)


# ---------- item selection ----------
@dp.callback_query(F.data.startswith("pg:"))
async def cb_page(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(page=int(cb.data[3:]))
    await show_positions(cb.message, state, edit=True)


@dp.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@dp.callback_query(F.data == "list")
async def cb_list(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(filter="")
    await show_positions(cb.message, state)


@dp.callback_query(F.data.startswith("p:"))
async def cb_position(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await show_card(cb.message, state, int(cb.data[2:]))


@dp.message(Flow.positions, F.text)
async def typed_position(msg: Message, state: FSMContext):
    await find_position(msg, state, msg.text.strip())


async def find_position(msg: Message, state: FSMContext, text: str):
    """Item No. -> card; part of a name/model -> search; text with a price -> DeepSeek."""
    data = await state.get_data()
    if "table_id" not in data:
        await show_tenders(msg, state)
        return
    rows = await noco.positions(data["table_id"])
    exact = [r for r in rows if str(r.get(COL_NO) or "").strip() == text]
    if exact:
        await show_card(msg, state, exact[0]["Id"])
        return
    if TENDER_RE.match(text) or (looks_like_offer(text) and DEEPSEEK_API_KEY):
        await handle_free_text(msg, state, text, force_ai=True)
        return
    found = [r for r in rows if text.lower() in f"{r.get(COL_ITEM)} {r.get(COL_MODEL)}".lower()]
    if len(found) == 1:
        await show_card(msg, state, found[0]["Id"])
        return
    if not found and DEEPSEEK_API_KEY and len(text.split()) >= 2:
        await handle_free_text(msg, state, text, force_ai=True)
        return
    await state.update_data(filter=text if found else "", page=0)
    if not found:
        await msg.answer(f"Nothing found for \"{esc(text)}\".")
    await show_positions(msg, state)


@dp.callback_query(F.data == "next")
async def cb_next(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    rows = await noco.positions(data["table_id"])
    ids = [r["Id"] for r in rows]
    i = ids.index(data["row_id"]) if data.get("row_id") in ids else -1
    if i + 1 >= len(ids):
        await cb.message.answer("That was the last item.")
        await show_positions(cb.message, state)
        return
    await show_card(cb.message, state, ids[i + 1])


@dp.callback_query(F.data == "card")
async def cb_card(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Cancelled")
    await show_card(cb.message, state, edit=True)


# ---------- manual price entry ----------
@dp.callback_query(F.data.startswith("s:"))
async def cb_slot(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    slot = int(cb.data[2:])
    data = await state.get_data()
    row = await noco.position(data["table_id"], data["row_id"])
    await state.update_data(slot=slot)
    if row.get(SUPPLIER_COLS[slot - 1]) or row.get(PRICE_COLS[slot - 1]) is not None:
        await cb.message.answer(
            f"Slot {slot}: {esc(row.get(SUPPLIER_COLS[slot - 1]) or '—')} — {money(row.get(PRICE_COLS[slot - 1]))}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [Btn(text="✏️ Replace", callback_data="replace"), Btn(text="🗑 Clear", callback_data="clear")],
                [Btn(text="↩️ Back", callback_data="card")]]))
        return
    await ask_supplier(cb.message, state)


@dp.callback_query(F.data == "replace")
async def cb_replace(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await ask_supplier(cb.message, state)


def known_suppliers(rows):
    return list(dict.fromkeys(strip_mark(r.get(c)) for r in rows for c in SUPPLIER_COLS if r.get(c)))[:6]


async def ask_supplier(msg: Message, state: FSMContext):
    data = await state.get_data()
    known = known_suppliers(await noco.positions(data["table_id"]))
    await state.update_data(known=known)
    kb = [[Btn(text=short(s, 40), callback_data=f"sup:{i}")] for i, s in enumerate(known)]
    kb.append([Btn(text="✖️ Cancel", callback_data="card")])
    await state.set_state(Flow.supplier)
    await msg.answer(f"Slot {data['slot']}. Type the <b>supplier</b>"
                     + (" or pick one already used in this tender:" if known else ":"),
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(Flow.supplier, F.data.startswith("sup:"))
async def cb_known_supplier(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    await set_supplier(cb.message, state, data["known"][int(cb.data[4:])])


@dp.message(Flow.supplier, F.text)
async def typed_supplier(msg: Message, state: FSMContext):
    name = " ".join(msg.text.split())
    if len(name) < 2:
        await msg.answer("That name is too short. Type the supplier:")
        return
    await set_supplier(msg, state, name[:200])


async def set_supplier(msg: Message, state: FSMContext, name: str):
    await state.update_data(supplier=name)
    await state.set_state(Flow.price)
    await msg.answer(f"Supplier: <b>{esc(name)}</b>\nNow the <b>price per 1 unit, {BASE_CURRENCY}</b> "
                     "(e.g. 85924 or 85,924.50):", reply_markup=CANCEL_KB)


@dp.message(Flow.price, F.text)
async def typed_price(msg: Message, state: FSMContext):
    price = parse_price(msg.text)
    if price is None:
        await msg.answer("I didn't get the price. Type a number, e.g. 85924 or 85,924.50:", reply_markup=CANCEL_KB)
        return
    await state.update_data(price=price)
    await msg.answer(f"Price: <b>{money(price)}</b> per unit. This price is…",
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                         [Btn(text=VAT_INCL, callback_data=f"vat:{VAT_INCL}"),
                          Btn(text=VAT_EXCL, callback_data=f"vat:{VAT_EXCL}")],
                         [Btn(text="don't know", callback_data=f"vat:{VAT_UNKNOWN}")],
                         [Btn(text="✖️ Cancel", callback_data="card")]]))


@dp.callback_query(F.data.startswith("vat:"))
async def cb_vat(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(vat=cb.data[4:])
    await state.set_state(Flow.comment)
    await cb.message.answer("Comment (lead time, availability, quotation no.)? Type it or skip:",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [Btn(text="Skip", callback_data="nocomment")],
                                [Btn(text="✖️ Cancel", callback_data="card")]]))


@dp.callback_query(Flow.comment, F.data == "nocomment")
async def cb_nocomment(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(comment="")
    await confirm(cb.message, state, cb.from_user)


@dp.message(Flow.comment, F.text)
async def typed_comment(msg: Message, state: FSMContext):
    await state.update_data(comment=" ".join(msg.text.split())[:500])
    await confirm(msg, state, msg.from_user)


async def confirm(msg: Message, state: FSMContext, user=None):
    if AUTO_SAVE:  # no question - save right away
        await manual_save(msg, state, user or msg.from_user, edit=False)
        return
    d = await state.get_data()
    rows = await noco.positions(d["table_id"])
    row = next((r for r in rows if r["Id"] == d["row_id"]), {})
    await state.set_state(Flow.positions)
    await msg.answer(
        f"<b>Please check:</b>\n{esc(d['code'])} · item {esc(row.get(COL_NO))} {esc(short(row.get(COL_ITEM), 60))}\n"
        f"Slot {d['slot']}: <b>{esc(d['supplier'])}</b> — <b>{money(d['price'])}</b> per unit ({esc(d['vat'])})"
        + (f"\nComment: {esc(d['comment'])}" if d.get("comment") else ""),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [Btn(text="✅ Save", callback_data="save")],
            [Btn(text="✖️ Cancel", callback_data="card")]]))


def comment_line(user, text) -> str:
    name = user if isinstance(user, str) else (user.full_name if user else "?")
    return f"{datetime.now(TZ):%Y-%m-%d %H:%M} {name}: {text}"


@dp.callback_query(F.data == "save")
async def cb_save(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "price" not in d or "supplier" not in d:
        await cb.answer("Nothing to save", show_alert=True)
        return
    await cb.answer("Saving…")
    await manual_save(cb.message, state, cb.from_user, edit=True)


async def manual_save(msg: Message, state: FSMContext, user, edit: bool):
    """Saves a price entered with buttons (shared by "Save" and AUTO_SAVE)."""
    d = await state.get_data()
    old = await noco.position(d["table_id"], d["row_id"])
    note = f"price {d['slot']}: {d['supplier']} — {money(d['price'])} per unit, {d['vat']}"
    if d.get("comment"):
        note += f"; {d['comment']}"
    comment = "\n".join(x for x in [str(old.get(COMMENT_COL) or "").strip(), comment_line(user, note)] if x)
    fields = {SUPPLIER_COLS[d["slot"] - 1]: d["supplier"], PRICE_COLS[d["slot"] - 1]: d["price"], COMMENT_COL: comment}
    try:
        async with TABLE_LOCKS[d["table_id"]]:
            _, bad = await noco.update_position(d["table_id"], d["row_id"], fields)
    except NocoError as e:
        await msg.answer(f"❌ Not saved: {esc(e)}\nPlease try again.")
        return
    for k in ("price", "supplier", "vat", "comment"):
        d.pop(k, None)
    undo_tok = store_undo(d.get("actor_id"), d["table_id"], d["code"], {d["row_id"]: {k: old.get(k) for k in fields}})
    await state.set_data(d)
    logging.info("%s: %s item %s %s", user.full_name, d["code"], d["row_id"], note)
    text = (f"✅ Saved to NocoDB: slot {d['slot']} — {money(fields[PRICE_COLS[d['slot'] - 1]])}" if not bad else
            "⚠️ Saved, but the check found differences:\n" + esc("\n".join(bad)))
    kb = InlineKeyboardMarkup(inline_keyboard=[[Btn(text="↩️ Undo", callback_data=f"undo:{undo_tok}")]])
    if edit:
        await msg.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)
    await show_card(msg, state)


@dp.callback_query(F.data == "clear")
async def cb_clear(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    d = await state.get_data()
    old = await noco.position(d["table_id"], d["row_id"])
    s = d["slot"]
    note = f"price {s} cleared (was: {old.get(SUPPLIER_COLS[s - 1]) or '—'} — {money(old.get(PRICE_COLS[s - 1]))})"
    comment = "\n".join(x for x in [str(old.get(COMMENT_COL) or "").strip(), comment_line(cb.from_user, note)] if x)
    try:
        await noco.update_position(d["table_id"], d["row_id"],
                                   {SUPPLIER_COLS[s - 1]: None, PRICE_COLS[s - 1]: None, COMMENT_COL: comment})
    except NocoError as e:
        await cb.message.answer(f"❌ {esc(e)}")
        return
    await cb.message.edit_text(f"🗑 Slot {s} cleared.")
    await show_card(cb.message, state)


# ---------- free text -> DeepSeek ----------
async def ask_tender_first(msg: Message, state: FSMContext, text: str):
    await state.update_data(pending_text=text[:6000])
    await msg.answer("Please pick a tender first — nothing is sent without one.\n"
                     "I've kept your text and will process it right after you choose.")
    await show_tenders(msg, state)


async def handle_free_text(msg: Message, state: FSMContext, text: str, tender_required=False, force_ai=False):
    """Free text. "T-32 ..." at the start selects the tender (and parses the rest).
    Without a selected tender the text is not sent to DeepSeek - ask to pick one.
    In a topic the tender comes from the topic; in a private-chat topic, text
    without a price is an item number or name (force_ai=True - already checked)."""
    text = (text or "").strip()
    if is_group(msg) or topic_tender(msg):
        if "table_id" not in await state.get_data():
            await msg.reply("This topic is not linked to a tender.")
            return
        if not is_group(msg) and not force_ai and not looks_like_offer(text):
            await find_position(msg, state, text)
            return
        await run_ai(msg, state, text)
        return
    m = TENDER_RE.match(text)
    if m:
        rec = await noco.find_tender(tender_code(m.group(2)), PREFIX)
        if rec:
            rest = m.group(3).strip()
            if rest:
                await state.update_data(pending_text=rest[:6000])
            await open_tender(msg, state, rec)
            return
        await msg.answer(f"Tender {esc(tender_code(m.group(2)))} was not found in the registry.")
        if not looks_like_offer(m.group(3)):
            return
    if tender_required and not looks_like_offer(text):
        rec = await noco.find_tender(text, PREFIX)  # "32", "T 32", official tender number
        if rec:
            await open_tender(msg, state, rec)
            return
    data = await state.get_data()
    if tender_required or "table_id" not in data:
        if looks_like_offer(text):
            await ask_tender_first(msg, state, text)
        else:
            await msg.answer(f"Tender \"{esc(text)}\" not found. Type the internal number, e.g. {esc(PREFIX)}32, "
                             "or pick one from the list.")
            await show_tenders(msg, state)
        return
    await run_ai(msg, state, text)


MARK_FULL, MARK_PART = "🟢", "🔴"


def strip_mark(name) -> str:
    """Supplier name without the leading 🟢/🔴 mark."""
    return re.sub(r"^\s*[🟢🔴]\s*", "", str(name or "")).strip()


def assign_slot(row, supplier):
    """Same supplier already present -> its slot (update the price); else the first empty slot; else None."""
    sup = strip_mark(supplier).lower()
    for i in range(len(SUPPLIER_COLS)):
        if sup and strip_mark(row.get(SUPPLIER_COLS[i])).lower() == sup:
            return i + 1
    for i in range(len(SUPPLIER_COLS)):
        if not row.get(SUPPLIER_COLS[i]) and row.get(PRICE_COLS[i]) is None:
            return i + 1
    return None


def item_problem(it, row):
    if not it.get("row_id"):
        return "item not found in the specification" + (f" ({it['reason']})" if it.get("reason") else "")
    if not it.get("price"):
        if it.get("price_orig"):
            return (f"price {it['price_orig']} {it.get('currency')}: {it.get('fx_error') or 'could not convert'}"
                    " — enter it manually")
        return "no price found"
    if not it.get("supplier"):
        return "no supplier"
    if row is not None and assign_slot(row, it["supplier"]) is None:
        return f"all {len(SUPPLIER_COLS)} slots are taken — clear one in the item card"
    return None


async def convert_prices(items):
    """price_orig in the document currency -> price in the base currency at
    today's ECB rate; without a rate the price stays empty and is flagged ⚠️."""
    for it in items:
        cur = fx.norm_currency(it.get("currency"))
        it["currency"] = cur
        orig = it.get("price_orig")
        it["price"], it["fx_note"], it["fx_error"] = None, "", ""
        if not orig:
            continue
        if cur == BASE_CURRENCY:
            it["price"] = orig
            continue
        try:
            r = await fx.rates()
        except fx.NoRates as e:
            it["fx_error"] = str(e)
            continue
        if not r.get(cur):
            it["fx_error"] = f"no ECB rate for {cur}"
            continue
        it["price"] = round(orig * r[cur], 2)
        it["fx_note"] = f"{orig:,.2f} {cur} × {r[cur]:.4f} (ECB rate of {r.get('_date') or 'today'})"


# ---------- DeepSeek parse -> save (write queue, batch writes, NocoDB rate limit) ----------
# Parses live in memory under a token, not in the dialog state: several files
# sent in a row are processed independently and don't overwrite each other.
# Writes to one specification are strictly sequential (TABLE_LOCKS), one batch
# request per document.
BATCHES = {}                    # token -> parse of one file/text
AWAIT_SUP = defaultdict(list)   # (chat, topic, user) -> ask ids waiting for a supplier
UNDOS = {}                      # token -> data for "Undo"
TABLE_LOCKS = defaultdict(asyncio.Lock)


def _prune(d: dict, keep=300):
    while len(d) > keep:
        d.pop(next(iter(d)))


def user_key(msg: Message, uid) -> tuple:
    return (msg.chat.id, msg.message_thread_id if msg.is_topic_message else None, uid)


async def run_ai(msg: Message, state: FSMContext, text: str, images=None, source: str = "",
                 defer_supplier: bool = False):
    data = await state.get_data()
    if not DEEPSEEK_API_KEY:
        await msg.answer("DeepSeek parsing is not configured (no DEEPSEEK_API_KEY).\n"
                         "You can add a price with buttons: pick an item.")
        return
    what = source or "text"
    wait = await msg.answer(f"⏳ Sending {esc(what)} to DeepSeek (tender {esc(data['code'])})…"
                            + (" Images can take up to a minute." if images else ""))
    rows = await noco.positions(data["table_id"])
    try:
        res = await parse_offer(DEEPSEEK_API_KEY, DEEPSEEK_MODEL, text, rows, data["code"], images=images,
                                source=source, our_companies=OUR_COMPANIES, default_currency=BASE_CURRENCY)
    except AiError as e:
        await wait.edit_text(f"⚠️ {esc(e)}\nYou can add the price manually: pick an item.")
        return
    by_no = {str(r.get(COL_NO) or "").strip(): r for r in rows}
    items = res["items"]
    await convert_prices(items)
    for it in items:
        row = by_no.get(it["position_no"] or "")
        it["row_id"] = row["Id"] if row else None
        it["pos_name"] = short((row or {}).get(COL_MODEL) or (row or {}).get(COL_ITEM), 40)
    logging.info("%s: DeepSeek parsed %s for %s: %d offers", msg.chat.id, what, data["code"], len(items))
    if not items:
        await wait.edit_text(f"🤖 DeepSeek found no prices for this tender in {esc(what)}."
                             + (f"\n{esc(res['note'])}" if res.get("note") else "") + "\nNothing was saved.")
        return
    meta = {"supplier": res.get("supplier"), "tax_id": res.get("supplier_tax_id")}
    if not AUTO_SAVE:  # ask "Add?" first
        await state.update_data(ai_items=items, ai_note=res.get("note") or "", ai_meta=meta, ai_source=source)
        if any(it["row_id"] and it["price"] and not it["supplier"] for it in items):
            await ask_ai_supplier(wait, state)
        else:
            await show_ai_confirm(wait, state, edit=True, init=True)
        return
    token = uuid.uuid4().hex[:10]
    BATCHES[token] = {"owner": data.get("actor_id"), "actor": data.get("actor") or "?", "table_id": data["table_id"],
                      "code": data["code"], "items": items, "meta": meta, "note": res.get("note") or "",
                      "source": source, "what": what}
    _prune(BATCHES)
    if any(it["row_id"] and it["price"] and not it["supplier"] for it in items):
        if defer_supplier:  # the question is asked once for all files of the message
            await wait.edit_text(f"🤖 {esc(what)}: parsed, but no supplier given.")
            return token
        key = (msg.chat.id, msg.message_thread_id if msg.is_topic_message else None, data.get("actor_id"))
        await enqueue_supplier_question(msg, state, key, [token], edit_msg=wait)
        return None
    await save_batch(wait, token, edit=True)
    return None


ASKS = {}  # "who is the supplier" question id -> {"tokens": [...], "owner": ..., "known": [...]}


async def enqueue_supplier_question(msg: Message, state: FSMContext, key, tokens, edit_msg=None):
    """One supplier question per group of parses (e.g. all files of one message).
    One user's questions are asked one at a time."""
    aid = uuid.uuid4().hex[:10]
    ASKS[aid] = {"tokens": list(tokens), "owner": key[2], "known": []}
    _prune(ASKS)
    AWAIT_SUP[key].append(aid)
    await state.set_state(Flow.ai_supplier)
    if len(AWAIT_SUP[key]) == 1:
        await ask_batch_supplier(edit_msg or msg, aid, edit=edit_msg is not None)
    elif edit_msg is not None:
        await edit_msg.edit_text("🤖 Parsed, no supplier given — I'll ask after the previous question.")
    else:
        await msg.answer("🤖 More files without a supplier — I'll ask after the previous question.")


async def ask_batch_supplier(msg: Message, aid: str, edit=False):
    ask = ASKS.get(aid)
    batches = [BATCHES[t] for t in (ask or {}).get("tokens", []) if t in BATCHES]
    if not batches:
        return
    known = known_suppliers(await noco.positions(batches[0]["table_id"]))
    ask["known"] = known
    kb = [[Btn(text=short(s, 40), callback_data=f"bsup:{aid}:{i}")] for i, s in enumerate(known)]
    kb.append([Btn(text="❌ Don't save", callback_data=f"bno:{aid}")])
    if len(batches) == 1:
        where = f"{esc(batches[0]['what'])} has no supplier."
    else:
        names = ", ".join(esc(b["what"]) for b in batches[:8])
        where = f"{len(batches)} files have no supplier: {names}. Your answer applies to all of them."
    text = f"🤖 {where} Who sent the prices? Type the name" + (" or pick one:" if known else ":")
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if edit:
        await msg.edit_text(text, reply_markup=markup)
    else:
        await msg.answer(text, reply_markup=markup)


async def _supplier_given(msg: Message, state: FSMContext, key, aid, name):
    if aid in AWAIT_SUP.get(key, []):
        AWAIT_SUP[key].remove(aid)
    ask = ASKS.pop(aid, None) or {"tokens": []}
    for token in ask["tokens"]:
        b = BATCHES.get(token)
        if b and name:
            for it in b["items"]:
                if not it.get("supplier"):
                    it["supplier"] = name
            await save_batch(msg, token)
        elif b:
            BATCHES.pop(token, None)
            await msg.answer(f"❌ {esc(b['what'])}: nothing saved.")
    if AWAIT_SUP.get(key):
        await ask_batch_supplier(msg, AWAIT_SUP[key][0])
    else:
        AWAIT_SUP.pop(key, None)
        await state.set_state(Flow.positions)


@dp.callback_query(F.data.startswith("bsup:") | F.data.startswith("bno:"))
async def cb_batch_supplier(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    aid = parts[1]
    ask = ASKS.get(aid)
    if not ask:
        await cb.answer("Already handled", show_alert=True)
        return
    if ask["owner"] and ask["owner"] != cb.from_user.id:
        await cb.answer("These buttons belong to whoever sent the file.", show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    name = ask["known"][int(parts[2])] if parts[0] == "bsup" else None
    await _supplier_given(cb.message, state, user_key(cb.message, cb.from_user.id), aid, name)


async def save_items(table_id: str, code: str, items, actor: str, source: str):
    """Writes offers into the specification: under the table lock, on fresh data,
    in one batch request. Returns (report lines, undo {row_id: previous fields}, skipped)."""
    report, skipped = [], []
    undo, updates, lines_by_row = {}, {}, {}
    async with TABLE_LOCKS[table_id]:
        rows = {r["Id"]: dict(r) for r in await noco.positions(table_id, fresh=True)}
        for it in items:
            row = rows.get(it.get("row_id"))
            problem = item_problem(it, row)
            if problem:
                skipped.append(f"⚠️ \"{esc(it['item_text'] or '—')}\" — {esc(problem)}")
                continue
            slot = assign_slot(row, it["supplier"])
            circle = MARK_FULL if it.get("match") == "full" else MARK_PART
            sup_clean = strip_mark(it["supplier"])
            note = f"price {slot}: {sup_clean} — {circle} {money(it['price'])} per unit, {it['vat']}"
            if it.get("fx_note"):
                note += f" ({it['fx_note']})"
            note += ("; exact match with the specification" if circle == MARK_FULL
                     else f"; not an exact match: {it.get('match_note') or 'check'}")
            if it.get("comment"):
                note += f"; {it['comment']}"
            note += f" (from {source or 'a message'}, parsed by DeepSeek)"
            if it.get("confidence") != "high":
                note += " ❔ uncertain match — check"
            comment = "\n".join(x for x in [str(row.get(COMMENT_COL) or "").strip(), comment_line(actor, note)] if x)
            fields = {SUPPLIER_COLS[slot - 1]: f"{circle} {sup_clean}", PRICE_COLS[slot - 1]: it["price"],
                      COMMENT_COL: comment}
            u = undo.setdefault(row["Id"], {})
            for k in fields:
                u.setdefault(k, row.get(k))
            row.update(fields)  # the next line of the same document sees the slot as taken
            updates.setdefault(row["Id"], {}).update(fields)
            line = (f"✅ item {esc(it['position_no'])} · {esc(it.get('pos_name') or '')} → price {slot}: "
                    f"{circle} <b>{money(it['price'])}</b>, {esc(it['vat'])}")
            if it.get("fx_note"):
                line += f"\n    💱 {esc(it['fx_note'])}"
            if circle == MARK_PART:
                line += f"\n    {MARK_PART} {esc(it.get('match_note') or 'not an exact match')}"
            if it.get("confidence") != "high":
                line += f"\n    ❔ please check: {esc(it.get('reason') or 'uncertain match')}"
            lines_by_row.setdefault(row["Id"], []).append(len(report))
            report.append(line)
            logging.info("%s: %s item %s %s", actor, code, it["position_no"], note)
        if updates:
            try:
                bad = await noco.update_positions(table_id, updates)
            except NocoError as e:
                return [f"❌ Not saved: {esc(e)}\nPlease send the file again in a minute."], {}, skipped
            for rid, problems in bad.items():
                for idx in lines_by_row.get(rid, []):
                    report[idx] += f"\n    ⚠️ differences found by the check: {esc('; '.join(problems))}"
    return report, undo, skipped


def store_undo(owner, table_id, code, undo: dict):
    if not undo:
        return None
    token = uuid.uuid4().hex[:10]
    UNDOS[token] = {"owner": owner, "table_id": table_id, "code": code, "rows": undo}
    _prune(UNDOS)
    return token


def after_save_kb(undo_token) -> InlineKeyboardMarkup:
    kb = [[Btn(text="↩️ Undo", callback_data=f"undo:{undo_token}")]] if undo_token else []
    kb.append([Btn(text="📋 Item list", callback_data="list")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _doc_header(what, code, meta, items):
    sup_doc = meta.get("supplier") or next((strip_mark(i.get("supplier")) for i in items if i.get("supplier")), None)
    vats = sorted({i.get("vat") for i in items if i.get("vat")})
    return (f"Supplier: <b>{esc(sup_doc or 'not stated in the document')}</b>"
            + (f" (tax ID {esc(meta['tax_id'])})" if meta.get("tax_id") else "")
            + (f" · prices {esc(', '.join(vats))}" if vats else ""))


async def save_batch(msg: Message, token: str, edit=False):
    """Writes a parse immediately (AUTO_SAVE) and shows the result."""
    b = BATCHES.pop(token, None)
    if not b:
        return
    report, undo, skipped = await save_items(b["table_id"], b["code"], b["items"], b["actor"], b["source"])
    head = [f"🤖 <b>{esc(b['what'][:1].upper() + b['what'][1:])}: saved to NocoDB</b> · tender {esc(b['code'])}",
            _doc_header(b["what"], b["code"], b["meta"], b["items"]),
            f"{MARK_FULL} — exact match with the specification, {MARK_PART} — not exact\n"]
    text = "\n".join(head + (report or ["Nothing to save."]) + (["\n<b>Not saved:</b>"] + skipped if skipped else []))
    if b.get("note"):
        text += f"\n\n<i>{esc(b['note'])}</i>"
    kb = after_save_kb(store_undo(b["owner"], b["table_id"], b["code"], undo))
    if edit:
        await msg.edit_text(text[:4000], reply_markup=kb)
    else:
        await msg.answer(text[:4000], reply_markup=kb)


@dp.callback_query(F.data.startswith("undo:"))
async def cb_undo(cb: CallbackQuery):
    """Undo: restore the previous supplier, price and comment values."""
    u = UNDOS.get(cb.data[5:])
    if not u:
        await cb.answer("Nothing to undo (already undone or the bot was restarted)", show_alert=True)
        return
    if u["owner"] and u["owner"] != cb.from_user.id:
        await cb.answer("Only the person who sent the data can undo it.", show_alert=True)
        return
    await cb.answer("Undoing…")
    UNDOS.pop(cb.data[5:], None)
    try:
        async with TABLE_LOCKS[u["table_id"]]:
            await noco.update_positions(u["table_id"], u["rows"])
    except NocoError as e:
        UNDOS[cb.data[5:]] = u
        await cb.message.answer(f"❌ Undo failed: {esc(e)}. Press it again in a minute.")
        return
    logging.info("%s: undo in %s (%d rows)", cb.from_user.full_name, u["code"], len(u["rows"]))
    await cb.message.edit_reply_markup(reply_markup=after_save_kb(None))
    await cb.message.answer(f"↩️ Undone: previous values restored ({len(u['rows'])} items)")


# ---------- "Add?" confirmation mode (AUTO_SAVE=0) ----------
async def ask_ai_supplier(msg: Message, state: FSMContext):
    data = await state.get_data()
    known = known_suppliers(await noco.positions(data["table_id"]))
    await state.update_data(known=known)
    await state.set_state(Flow.ai_supplier)
    kb = [[Btn(text=short(s, 40), callback_data=f"aisup:{i}")] for i, s in enumerate(known)]
    kb.append([Btn(text="❌ Don't add", callback_data="ai:no")])
    await msg.edit_text("🤖 The text has no supplier. Who sent the price? Type the name"
                        + (" or pick one:" if known else ":"),
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


async def set_ai_supplier(msg: Message, state: FSMContext, name: str):
    data = await state.get_data()
    for it in data.get("ai_items") or []:
        if not it.get("supplier"):
            it["supplier"] = name
    await state.update_data(ai_items=data.get("ai_items"))
    await show_ai_confirm(msg, state, init=True)


@dp.callback_query(Flow.ai_supplier, F.data.startswith("aisup:"))
async def cb_ai_supplier(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    await set_ai_supplier(cb.message, state, data["known"][int(cb.data[6:])])


@dp.message(Flow.ai_supplier, F.text)
async def typed_ai_supplier(msg: Message, state: FSMContext):
    name = " ".join(msg.text.split())[:200]
    if len(name) < 2:
        await msg.answer("Type the supplier's name:")
        return
    key = user_key(msg, msg.from_user.id)
    if AWAIT_SUP.get(key):  # answer to the supplier question for the earliest file
        await _supplier_given(msg, state, key, AWAIT_SUP[key][0], name)
        return
    await set_ai_supplier(msg, state, name)


async def show_ai_confirm(msg: Message, state: FSMContext, edit=False, init=False):
    data = await state.get_data()
    items = data.get("ai_items") or []
    rows = {r["Id"]: r for r in await noco.positions(data["table_id"])}
    what = data.get("ai_source") or "text"
    lines = [f"🤖 <b>DeepSeek parsed {esc(what)}</b> · tender {esc(data['code'])}",
             _doc_header(what, data["code"], data.get("ai_meta") or {}, items),
             f"{MARK_FULL} — item matches the specification exactly, {MARK_PART} — not exactly\n"]
    toggles = []
    for n, it in enumerate(items, 1):
        row = rows.get(it.get("row_id"))
        problem = item_problem(it, row)
        it["writable"] = problem is None
        if init:
            it["selected"] = it["writable"] and it["confidence"] == "high"
        it["selected"] = it.get("selected", False) and it["writable"]
        if problem:
            lines.append(f"⚠️ <b>{n})</b> \"{esc(it['item_text'] or '—')}\" — {esc(problem)}")
            continue
        slot = assign_slot(row, it["supplier"])
        replace = bool(row.get(SUPPLIER_COLS[slot - 1]))
        mark = "☑️" if it["selected"] else "⬜"
        circle = MARK_FULL if it.get("match") == "full" else MARK_PART
        lines.append(f"{mark} <b>{n})</b> item {esc(it['position_no'])} · {esc(it['pos_name'])}\n"
                     f"    {esc(strip_mark(it['supplier']))} — {circle} <b>{money(it['price'])}</b> per unit, {esc(it['vat'])}"
                     f" → price {slot}" + (" (update)" if replace else ""))
        if it.get("fx_note"):
            lines.append(f"    💱 {esc(it['fx_note'])}")
        if circle == MARK_PART:
            lines.append(f"    {MARK_PART} {esc(it.get('match_note') or 'not an exact match — please check')}")
        if it.get("comment"):
            lines.append(f"    <i>{esc(it['comment'])}</i>")
        if it["confidence"] != "high":
            lines.append(f"    ❔ not sure: {esc(it.get('reason') or 'please check the match')}")
        toggles.append(Btn(text=f"{mark} {n}", callback_data=f"ai:t:{n - 1}"))
    if data.get("ai_note"):
        lines.append(f"\n<i>{esc(data['ai_note'])}</i>")
    chosen = sum(1 for it in items if it.get("selected"))
    lines.append("\n<b>Add to NocoDB?</b>" + (" Tick the lines you want with the numbered buttons." if toggles else ""))
    await state.update_data(ai_items=items)
    await state.set_state(Flow.ai_confirm)
    kb = [toggles[i:i + 5] for i in range(0, len(toggles), 5)]
    if chosen:
        kb.append([Btn(text=f"✅ Yes, add ({chosen})", callback_data="ai:yes")])
    kb.append([Btn(text="❌ No, don't add", callback_data="ai:no")])
    text = "\n".join(lines)[:4000]
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if edit:
        await msg.edit_text(text, reply_markup=markup)
    else:
        await msg.answer(text, reply_markup=markup)


@dp.callback_query(F.data.startswith("ai:t:"))
async def cb_ai_toggle(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    items = data.get("ai_items")
    if not items:
        await cb.answer("Already handled", show_alert=True)
        return
    i = int(cb.data[5:])
    if 0 <= i < len(items) and items[i].get("writable"):
        items[i]["selected"] = not items[i].get("selected")
    await state.update_data(ai_items=items)
    await cb.answer()
    await show_ai_confirm(cb.message, state, edit=True)


@dp.callback_query(F.data == "ai:no")
async def cb_ai_no(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Not added")
    await state.update_data(ai_items=None)
    await state.set_state(Flow.positions)
    await cb.message.edit_text("❌ Nothing was added.\nSend another text or pick an item: /price")


@dp.callback_query(F.data == "ai:yes")
async def cb_ai_yes(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    items = [it for it in (data.get("ai_items") or []) if it.get("selected") and it.get("writable")]
    if not items:
        await cb.answer("Nothing to add", show_alert=True)
        return
    await cb.answer("Saving…")
    await state.update_data(ai_items=None)  # guard against a double tap
    await cb.message.edit_reply_markup(reply_markup=None)
    report, undo, skipped = await save_items(data["table_id"], data["code"], items, cb.from_user.full_name,
                                             data.get("ai_source") or "")
    await state.set_state(Flow.positions)
    await cb.message.answer("<b>Saved to NocoDB:</b>\n" + "\n".join(report + skipped),
                            reply_markup=after_save_kb(store_undo(cb.from_user.id, data["table_id"], data["code"], undo)))


# ---------- files: invoices, quotations, price lists ----------
MAX_FILE = 20 * 1024 * 1024  # Telegram Bot API download limit
ALBUMS = {}  # (chat, topic, user, media_group_id) -> files of one message (album)
ALBUM_WAIT = 1.5  # Telegram delivers album files as separate consecutive messages


@dp.message(F.document | F.photo)
async def got_file(msg: Message, state: FSMContext):
    if msg.photo:
        ph = msg.photo[-1]
        file = {"file_id": ph.file_id, "name": "photo.jpg", "size": ph.file_size or 0}
    else:
        d = msg.document
        file = {"file_id": d.file_id, "name": d.file_name or "file", "size": d.file_size or 0}
    caption = (msg.caption or "").strip()
    if not msg.media_group_id:
        await handle_files(msg, state, [file], caption)
        return
    # files of one message are collected and processed as one batch
    key = (msg.chat.id, msg.message_thread_id if msg.is_topic_message else None,
           msg.from_user.id if msg.from_user else 0, msg.media_group_id)
    album = ALBUMS.setdefault(key, {"files": [], "caption": "", "msg": msg, "state": state, "task": None})
    if msg.photo:  # so the album's photos can be told apart in the replies
        file["name"] = f"photo {len(album['files']) + 1}.jpg"
    album["files"].append(file)
    if caption:
        album["caption"] = caption
    if album["task"]:
        album["task"].cancel()
    album["task"] = asyncio.create_task(_flush_album(key))


async def _flush_album(key):
    try:
        await asyncio.sleep(ALBUM_WAIT)
    except asyncio.CancelledError:
        return
    album = ALBUMS.pop(key, None)
    if not album:
        return
    try:
        await handle_files(album["msg"], album["state"], album["files"], album["caption"])
    except Exception as e:
        logging.exception("Album processing")
        try:
            await album["msg"].answer(f"⚠️ Error while processing the files: {esc(e)}")
        except Exception:
            pass


def _files_list(files) -> str:
    names = [f"\"{esc(f['name'])}\"" for f in files]
    return ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")


async def handle_files(msg: Message, state: FSMContext, files: list, caption: str):
    """Files of one message (single or album): a tender is required, asked once."""
    big = [f for f in files if f["size"] > MAX_FILE]
    if big:
        await msg.answer(f"Files over 20 MB can't be downloaded by bots (Telegram limit): {_files_list(big)}.")
        files = [f for f in files if f["size"] <= MAX_FILE]
    if not files:
        return
    if is_group(msg) or topic_tender(msg):  # inside a topic the tender comes from the topic
        if "table_id" not in await state.get_data():
            await msg.reply("This topic is not linked to a tender.")
            return
        for f in files:
            f["caption"] = caption
        await process_files(msg, state, files)
        return
    m = TENDER_RE.match(caption) if caption else None
    if m:
        rec = await noco.find_tender(tender_code(m.group(2)), PREFIX)
        if rec:
            for f in files:
                f["caption"] = m.group(3).strip()
            await state.update_data(pending_files=files)
            await open_tender(msg, state, rec)
            return
        await msg.answer(f"Tender {esc(tender_code(m.group(2)))} was not found in the registry.")
    for f in files:
        f["caption"] = caption
    data = await state.get_data()
    if "table_id" not in data or await state.get_state() == Flow.tender.state:
        already = data.get("pending_files") or []
        await state.update_data(pending_files=already + files)
        if already and await state.get_state() == Flow.tender.state:
            await msg.answer(f"Added {len(files)} more file(s) — {len(already) + len(files)} in total. "
                             "I'll process them all right after you pick the tender.")
            return
        await msg.answer(f"Please pick a tender first — files are not sent without one.\n"
                         f"Kept {len(files)} file(s): {_files_list(files)}. I'll process them right after you choose.")
        await show_tenders(msg, state)
        return
    await process_files(msg, state, files)


async def process_files(msg: Message, state: FSMContext, files: list):
    """Parse files in parallel and save. If some files have no supplier, ask
    ONCE for all of them."""
    if not DEEPSEEK_API_KEY:
        await msg.answer("DeepSeek parsing of files is not configured (no DEEPSEEK_API_KEY).")
        return
    tokens = await asyncio.gather(*(process_file(msg, state, f) for f in files), return_exceptions=True)
    waiting = []
    for f, t in zip(files, tokens):
        if isinstance(t, Exception):
            logging.error("File %s: %s", f["name"], t)
            await msg.answer(f"⚠️ File \"{esc(f['name'])}\": {esc(t)}")
        elif t:
            waiting.append(t)
    if waiting:
        data = await state.get_data()
        await enqueue_supplier_question(msg, state, (msg.chat.id, msg.message_thread_id if msg.is_topic_message else None,
                                                     data.get("actor_id")), waiting)


async def process_file(msg: Message, state: FSMContext, file: dict):
    """Read and parse one file. Returns the parse token if the file has no
    supplier (process_files asks once for all files), otherwise None."""
    wait = await msg.answer(f"📄 Reading \"{esc(file['name'])}\"…")
    try:
        buf = await msg.bot.download(file["file_id"])
        doc = docs.extract(file["name"], buf.read())
    except docs.DocError as e:
        await wait.edit_text(f"⚠️ File \"{esc(file['name'])}\": {esc(e)}")
        return None
    except Exception as e:
        await wait.edit_text(f"⚠️ Could not download \"{esc(file['name'])}\": {esc(e)}")
        return None
    info = doc["kind"] + (f", {doc['note']}" if doc["note"] else "")
    await wait.edit_text(f"📄 \"{esc(file['name'])}\" read ({esc(info)}).")
    text = doc["text"]
    if file.get("caption"):
        text = f"Team member's comment on the file: {file['caption']}\n\n{text}"
    logging.info("%s: file %s (%s, %d chars, %d images)", msg.chat.id, file["name"], doc["kind"],
                 len(doc["text"]), len(doc["images"]))
    return await run_ai(msg, state, text, images=doc["images"], source=f"file \"{file['name']}\"",
                        defer_supplier=True)


@dp.message(Flow.ai_confirm, F.text)
async def typed_during_confirm(msg: Message, state: FSMContext):
    await handle_free_text(msg, state, msg.text)


@dp.message(F.text & ~F.text.startswith("/"))
async def free_text(msg: Message, state: FSMContext):
    await handle_free_text(msg, state, msg.text)


@dp.message()
async def fallback(msg: Message, state: FSMContext):
    await msg.answer("/price — add a price · /cancel — cancel\n"
                     "Or just send a supplier's text with prices, or a file: invoice, quotation, price list.")


@dp.errors()
async def on_error(event):
    logging.exception("Error: %s", event.exception)
    upd = event.update
    target = upd.message or (upd.callback_query.message if upd.callback_query else None)
    if target:
        try:
            if isinstance(event.exception, NocoError) and "429" in str(event.exception):
                await target.answer("⚠️ NocoDB is overloaded with requests right now. Wait a minute and send the file again.")
            else:
                await target.answer(f"⚠️ Error: {esc(event.exception)}\n/price — start again")
        except Exception:
            pass
    return True


async def main():
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    await bot.set_my_commands([BotCommand(command="price", description="Add a price"),
                               BotCommand(command="cancel", description="Cancel"),
                               BotCommand(command="ping", description="Check NocoDB"),
                               BotCommand(command="sync", description="Refresh topics from NocoDB specifications"),
                               BotCommand(command="help", description="How to use"),
                               BotCommand(command="version", description="Running bot version")])
    me = await bot.get_me()
    logging.info("Price bot %s @%s started, authorized users: %d, DeepSeek: %s", BOT_VERSION, me.username,
                 len(USERS), DEEPSEEK_MODEL if DEEPSEEK_API_KEY else "disabled")
    topics_on = bool(getattr(me, "has_topics_enabled", False))
    BOT_TOPICS.update(on=topics_on, checked=time.time())
    if not PRIVATE_TOPICS:
        logging.info("Private-chat topics disabled in config (PRIVATE_TOPICS=0)")
    elif topics_on:
        logging.info("Private-chat topics enabled, users with topics: %d", len(PRIV))
    else:
        logging.warning("Private-chat topics: Threaded Mode is off for the bot (@BotFather → Threads Settings) - "
                        "using the regular button menu")
    if GROUP["group_id"]:
        logging.info("Group with topics: %s, topics: %d", GROUP["group_id"], len(GROUP["topics"]))
    asyncio.create_task(auto_sync(bot))
    try:
        await dp.start_polling(bot)
    finally:
        await noco.close()


if __name__ == "__main__":
    asyncio.run(main())
