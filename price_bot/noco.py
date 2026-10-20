"""NocoDB access for the price bot: tender registry, specification tables and
the "Procurement: ..." columns (same names as tools/nocodb_client.py)."""
import asyncio
import json
import logging
import os
import re

import aiohttp

NOCODB_URL = (os.getenv("NOCODB_URL") or "https://app.nocodb.com").rstrip("/")
API_URL = f"{NOCODB_URL}/api/v2"
TABLES_URL = f"{API_URL}/tables"
META_URL = f"{API_URL}/meta"
BASE_ID = os.getenv("NOCODB_BASE_ID") or ""
REGISTRY_TABLE_ID = os.getenv("NOCODB_REGISTRY_TABLE_ID") or ""

# Registry columns
INTERNAL_NUMBER_COL = "Internal number"
TENDER_NUMBER_COL = "Tender number"
SUBJECT_COL = "Subject of procurement"
DEADLINE_COL = "Submission deadline"
SPEC_LINK_COLUMN = "Specification link"

# Specification columns
COL_NO = "No."
COL_ITEM = "Item name"
COL_MODEL = "Brand/model"
COL_MAKER = "Manufacturer"
COL_QTY = "Qty"
COL_UNIT = "Unit"
COL_UNIT_MAX = "Max unit price"
COL_AGENT_1 = "Agent price 1"

# The bot fills the first BOT_SLOTS procurement slots (the table may have more).
BOT_SLOTS = 3
SUPPLIER_COLS = [f"Procurement: supplier {i}" for i in range(1, BOT_SLOTS + 1)]
PRICE_COLS = [f"Procurement: price {i}" for i in range(1, BOT_SLOTS + 1)]
COMMENT_COL = "Procurement: comment"
DECIMAL_META = {"dtxp": 15, "dtxs": 2, "meta": {"precision": 2}}
PURCHASE_COLUMN_DEFS = []
for _i in range(1, BOT_SLOTS + 1):
    PURCHASE_COLUMN_DEFS.append({"title": SUPPLIER_COLS[_i - 1], "uidt": "SingleLineText",
                                 "column_name": f"procurement_supplier_{_i}"})
    PURCHASE_COLUMN_DEFS.append({"title": PRICE_COLS[_i - 1], "uidt": "Decimal",
                                 "column_name": f"procurement_price_{_i}", **DECIMAL_META})
PURCHASE_COLUMN_DEFS.append({"title": COMMENT_COL, "uidt": "LongText", "column_name": "procurement_comment"})


class NocoError(Exception):
    pass


def table_id_from_link(url):
    if not BASE_ID:
        return None
    m = re.search(re.escape(BASE_ID) + r"/(m[a-z0-9]+)", str(url or ""))
    return m.group(1) if m else None


class Noco:
    MIN_INTERVAL = 0.25  # at most ~4 requests/s in total (NocoDB allows about 5/s)

    def __init__(self, token: str):
        self._headers = {"xc-token": token, "Content-Type": "application/json"}
        self._session = None
        self._positions_cache = {}  # table_id -> (time, rows)
        self._tenders_cache = None
        self._throttle_lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self):
        """Shared request queue: at least MIN_INTERVAL between any two requests."""
        async with self._throttle_lock:
            loop = asyncio.get_running_loop()
            wait = self._last_request + self.MIN_INTERVAL - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = loop.time()

    async def close(self):
        if self._session:
            await self._session.close()

    async def _req(self, method, url, params=None, body=None):
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40), trust_env=True)
        last = None
        for attempt in range(10):
            await self._throttle()
            try:
                async with self._session.request(method, url, headers=self._headers,
                                                 params=params, json=body) as r:
                    raw = await r.text()
                    if r.status == 429:  # rate limited - wait and retry
                        retry_after = r.headers.get("Retry-After", "")
                        delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else min(30, 2 ** attempt)
                        last = None
                        logging.warning("NocoDB 429 (too many requests), retrying in %.0f s", delay)
                        await asyncio.sleep(delay)
                        continue
                    if r.status >= 400:
                        raise NocoError(f"NocoDB HTTP {r.status}: {raw[:300]}")
                    return json.loads(raw) if raw else {}
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = e
                await asyncio.sleep(2 * (attempt + 1))
        raise NocoError(f"NocoDB is unavailable: {last or 'too many requests (429)'}")

    # ---------- tender registry ----------
    async def recent_tenders(self, limit=10):
        """Registry tenders that have a specification table, newest first
        (paged - NocoDB returns at most 100 rows per request; cached 60 s)."""
        loop = asyncio.get_running_loop()
        cached = self._tenders_cache
        if cached and loop.time() - cached[0] < 60 and len(cached[1]) >= min(limit, cached[2]):
            return cached[1][:limit]
        out, offset = [], 0
        fields = ",".join(["Id", INTERNAL_NUMBER_COL, SUBJECT_COL, DEADLINE_COL, SPEC_LINK_COLUMN])
        while len(out) < limit:
            params = {"fields": fields, "sort": "-Id", "limit": str(min(100, limit - len(out))),
                      "offset": str(offset), "where": f"({SPEC_LINK_COLUMN},isnot,null)"}
            body = await self._req("GET", f"{TABLES_URL}/{REGISTRY_TABLE_ID}/records", params=params)
            batch = body.get("list", [])
            out += [r for r in batch if table_id_from_link(r.get(SPEC_LINK_COLUMN))]
            if body.get("pageInfo", {}).get("isLastPage", True) or not batch:
                total = len(out)
                break
            offset += len(batch)
        else:
            total = limit + 1
        self._tenders_cache = (loop.time(), out, total)
        return out[:limit]

    async def get_tender(self, record_id):
        return await self._req("GET", f"{TABLES_URL}/{REGISTRY_TABLE_ID}/records/{record_id}")

    async def find_tender(self, code: str, prefix: str = ""):
        """By internal number ("T-12", "t12", "12") or by official tender number."""
        code = code.strip()
        variants = [code, code.upper()]
        digits = re.sub(r"\D", "", code)
        if prefix and digits and (code.isdigit() or code.upper().startswith(prefix.rstrip("-").upper())):
            variants.append(f"{prefix}{digits}")
        for v in dict.fromkeys(variants):
            for col in (INTERNAL_NUMBER_COL, TENDER_NUMBER_COL):
                params = {"where": f"({col},eq,{v})", "limit": "1"}
                body = await self._req("GET", f"{TABLES_URL}/{REGISTRY_TABLE_ID}/records", params=params)
                if body.get("list"):
                    return body["list"][0]
        return None

    # ---------- specification table ----------
    async def column_titles(self, table_id):
        body = await self._req("GET", f"{META_URL}/tables/{table_id}")
        return {c["title"] for c in body.get("columns", [])}

    async def missing_purchase_columns(self, table_id):
        titles = await self.column_titles(table_id)
        return [d["title"] for d in PURCHASE_COLUMN_DEFS if d["title"] not in titles]

    async def create_purchase_columns(self, table_id):
        titles = await self.column_titles(table_id)
        for d in PURCHASE_COLUMN_DEFS:
            if d["title"] not in titles:
                await self._req("POST", f"{META_URL}/tables/{table_id}/columns", body=d)
                await asyncio.sleep(0.4)
        self._positions_cache.pop(table_id, None)

    async def positions(self, table_id, fresh=False):
        loop = asyncio.get_running_loop()
        cached = self._positions_cache.get(table_id)
        if cached and not fresh and loop.time() - cached[0] < 60:
            return cached[1]
        rows, offset = [], 0
        while True:
            # NocoDB caps a page at 100 rows, so advance by the rows actually received.
            params = {"limit": "100", "offset": str(offset), "sort": "Id"}
            body = await self._req("GET", f"{TABLES_URL}/{table_id}/records", params=params)
            batch = body.get("list", [])
            rows += batch
            if body.get("pageInfo", {}).get("isLastPage", True) or not batch:
                break
            offset += len(batch)
        self._positions_cache[table_id] = (loop.time(), rows)
        return rows

    async def position(self, table_id, row_id):
        return await self._req("GET", f"{TABLES_URL}/{table_id}/records/{row_id}")

    @staticmethod
    def _diff(fields: dict, row: dict):
        bad = []
        for k, v in fields.items():
            got = row.get(k)
            if isinstance(v, float) or isinstance(got, (int, float)):
                ok = (v is None and got is None) or (v is not None and got is not None
                                                    and abs(float(got) - float(v)) < 0.005)
            else:
                ok = str(got or "").strip() == str(v or "").strip()
            if not ok:
                bad.append(f"{k}: expected {v!r}, NocoDB has {got!r}")
        return bad

    async def update_positions(self, table_id, updates: dict):
        """Batch write {row_id: {field: value}} (up to 100 rows per request) and
        one verification re-read. Returns {row_id: [differences]}."""
        items = [{"Id": rid, **f} for rid, f in updates.items()]
        for i in range(0, len(items), 100):
            await self._req("PATCH", f"{TABLES_URL}/{table_id}/records", body=items[i:i + 100])
        self._positions_cache.pop(table_id, None)
        rows = {r["Id"]: r for r in await self.positions(table_id, fresh=True)}
        bad = {}
        for rid, f in updates.items():
            problems = self._diff(f, rows.get(rid, {}))
            if problems:
                bad[rid] = problems
        return bad

    async def update_position(self, table_id, row_id, fields: dict):
        """PATCH one row, re-read it and check what was stored."""
        await self._req("PATCH", f"{TABLES_URL}/{table_id}/records", body=[{"Id": row_id, **fields}])
        self._positions_cache.pop(table_id, None)
        row = await self.position(table_id, row_id)
        return row, self._diff(fields, row)
