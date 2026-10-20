"""A tiny in-memory stand-in for the parts of the NocoDB v2 API that
tools/nocodb_client.py uses - enough to run a full sync offline."""
import itertools
import re
import urllib.parse


class FakeNocoDB:
    def __init__(self, nc):
        self.nc = nc
        self.ids = itertools.count(1)
        self.tables = {}   # id -> {"title", "table_name", "columns": [..], "rows": [..], "views": [..]}
        self.next_row = itertools.count(1)

    # --- helpers --------------------------------------------------------------
    def _new_id(self, prefix):
        return f"{prefix}{next(self.ids):05d}"

    def add_table(self, title, columns):
        tid = self._new_id("m")
        cols = [{"id": self._new_id("c"), "title": c["title"], "uidt": c["uidt"],
                 "colOptions": {"formula_raw": c.get("formula_raw")} if c.get("formula_raw") else {}}
                for c in columns]
        view = {"id": self._new_id("v"), "title": title, "columns": []}
        t = {"id": tid, "title": title, "table_name": f"tbl_{tid}", "columns": cols, "rows": [], "views": [view]}
        for i, c in enumerate(cols):
            view["columns"].append({"id": self._new_id("vc"), "fk_column_id": c["id"], "order": i + 1, "show": True})
        self.tables[tid] = t
        return t

    def _add_column(self, t, body):
        col = {"id": self._new_id("c"), "title": body["title"], "uidt": body["uidt"],
               "colOptions": {"formula_raw": body.get("formula_raw")} if body.get("formula_raw") else {}}
        t["columns"].append(col)
        for v in t["views"]:
            v["columns"].append({"id": self._new_id("vc"), "fk_column_id": col["id"],
                                 "order": len(v["columns"]) + 1, "show": True})
        return col

    def _find_col(self, cid):
        for t in self.tables.values():
            for c in t["columns"]:
                if c["id"] == cid:
                    return t, c
        return None, None

    def _view(self, vid):
        for t in self.tables.values():
            for v in t["views"]:
                if v["id"] == vid:
                    return t, v
        return None, None

    def _meta(self, t):
        return {"id": t["id"], "title": t["title"], "table_name": t["table_name"], "columns": t["columns"]}

    # --- request router -----------------------------------------------------------
    def request(self, method, url, token, body=None, retries=0):
        nc = self.nc
        parsed = urllib.parse.urlparse(url)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        meta = urllib.parse.urlparse(nc.META_URL).path
        tables = urllib.parse.urlparse(nc.TABLES_URL).path
        m = re.fullmatch(rf"{meta}/bases/[^/]+/tables", path)
        if m:
            if method == "GET":
                return 200, {"list": [self._meta(t) for t in self.tables.values()]}
            return 200, self._meta(self.add_table(body["title"], body["columns"]))
        m = re.fullmatch(rf"{meta}/tables/([^/]+)", path)
        if m:
            t = self.tables[m.group(1)]
            if method == "PATCH":
                t["title"] = body["title"]
            return 200, self._meta(t)
        m = re.fullmatch(rf"{meta}/tables/([^/]+)/columns", path)
        if m:
            self._add_column(self.tables[m.group(1)], body)
            return 200, self._meta(self.tables[m.group(1)])
        m = re.fullmatch(rf"{meta}/columns/([^/]+)", path)
        if m:
            t, c = self._find_col(m.group(1))
            if method == "DELETE":
                t["columns"].remove(c)
            else:
                c.update({"title": body.get("title", c["title"]),
                          "colOptions": {"formula_raw": body.get("formula_raw")}})
            return 200, {}
        m = re.fullmatch(rf"{meta}/tables/([^/]+)/views", path)
        if m:
            return 200, {"list": [{"id": v["id"], "title": v["title"]} for v in self.tables[m.group(1)]["views"]]}
        m = re.fullmatch(rf"{meta}/tables/([^/]+)/grids", path)
        if m:
            t = self.tables[m.group(1)]
            v = {"id": self._new_id("v"), "title": body["title"],
                 "columns": [{"id": self._new_id("vc"), "fk_column_id": c["id"], "order": i + 1, "show": True}
                             for i, c in enumerate(t["columns"])]}
            t["views"].append(v)
            return 200, {"id": v["id"], "title": v["title"]}
        m = re.fullmatch(rf"{meta}/views/([^/]+)/columns(?:/([^/]+))?", path)
        if m:
            _, v = self._view(m.group(1))
            if m.group(2):
                vc = next(x for x in v["columns"] if x["id"] == m.group(2))
                vc.update(body)
                return 200, vc
            return 200, {"list": [dict(x) for x in v["columns"]]}
        m = re.fullmatch(rf"{tables}/([^/]+)/records(?:/(\d+))?", path)
        if m:
            t = self.tables[m.group(1)]
            if m.group(2):
                row = next((r for r in t["rows"] if r["Id"] == int(m.group(2))), None)
                return (200, dict(row)) if row else (404, {"error": "not found"})
            if method == "GET":
                rows = t["rows"]
                where = (query.get("where") or [None])[0]
                if where:
                    col, _, val = re.fullmatch(r"\((.+?),(eq),(.*)\)", where).groups()
                    rows = [r for r in rows if str(r.get(col)) == val]
                off, lim = int((query.get("offset") or [0])[0]), int((query.get("limit") or [100])[0])
                page = rows[off:off + min(lim, 100)]
                return 200, {"list": [dict(r) for r in page], "pageInfo": {"isLastPage": off + len(page) >= len(rows)}}
            titles = {c["title"] for c in t["columns"]}
            items = body if isinstance(body, list) else [body]
            if method == "POST":
                out = []
                for it in items:
                    row = {"Id": next(self.next_row), **{k: v for k, v in it.items() if k in titles}}
                    t["rows"].append(row)
                    out.append({"Id": row["Id"]})
                return 200, out if isinstance(body, list) else out[0]
            if method == "PATCH":
                for it in items:
                    row = next(r for r in t["rows"] if r["Id"] == it["Id"])
                    row.update({k: v for k, v in it.items() if k in titles})
                return 200, {}
            if method == "DELETE":
                ids = {it["Id"] for it in items}
                t["rows"] = [r for r in t["rows"] if r["Id"] not in ids]
                return 200, {}
        raise AssertionError(f"unexpected request {method} {url}")
