"""End-to-end sync against an in-memory fake NocoDB (no network)."""
import json

import pytest

import nocodb_client as nc
from fake_nocodb import FakeNocoDB
from make_sample_xlsx import build


@pytest.fixture
def fake(monkeypatch):
    f = FakeNocoDB(nc)
    monkeypatch.setattr(nc, "_request", f.request)
    monkeypatch.setattr(nc.time, "sleep", lambda s: None)
    monkeypatch.setattr(nc, "BASE_ID", "pbase")
    monkeypatch.setattr(nc, "REGISTRY_TABLE_ID", "")
    res = nc.init_registry("tok")
    monkeypatch.setattr(nc, "REGISTRY_TABLE_ID", res["registry_table_id"])
    return f


def _data():
    data = json.load(open("examples/sample_tender_data.json", encoding="utf-8"))
    data["internal_number"] = "T-7"
    return data


def test_init_registry_is_idempotent(fake):
    again = nc.init_registry("tok")
    assert again["created_columns"] == []


def test_full_upsert_with_spec(fake, tmp_path):
    xlsx = build(str(tmp_path / "s.xlsx"))
    res = nc.upsert("tok", _data(), spec_xlsx_path=xlsx, rates={"USD": 0.9})
    assert res["action"] == "created"
    assert res["verification"]["ok"], res["verification"]
    assert res["spec_verification"]["ok"], res["spec_verification"]
    spec = next(t for t in fake.tables.values() if t["title"] == "T-7")
    assert [v["title"] for v in spec["views"]][1:] == ["Agent prices", "Procurement", "Price comparison"]
    assert spec["rows"][0]["International offer 1"] == 809.1
    reg = fake.tables[nc.REGISTRY_TABLE_ID]["rows"][0]
    assert reg["Source files"] == "Notice.pdf\nTechnical specification.docx\nDraft contract.docx"
    assert reg["Specification link"].endswith(spec["id"] + "/" + spec["views"][0]["id"])

    # a procurement price entered by a person survives a re-sync, and the record is updated, not duplicated
    spec["rows"][0]["Procurement: supplier 1"] = "Acme GmbH"
    spec["rows"][0]["Procurement: price 1"] = 950.0
    res2 = nc.upsert("tok", _data(), spec_xlsx_path=xlsx, rates={"USD": 0.9})
    assert res2["action"] == "updated" and res2["spec_verification"]["ok"]
    assert len(fake.tables[nc.REGISTRY_TABLE_ID]["rows"]) == 1
    assert spec["rows"][0]["Procurement: price 1"] == 950.0


def test_views_show_expected_fields(fake, tmp_path):
    nc.upsert("tok", _data(), spec_xlsx_path=build(str(tmp_path / "s.xlsx")))
    table_id = nc.find_spec_table_id("tok", "T-7")
    structure = nc.ensure_spec_structure("tok", "T-7")
    assert structure["ok"], structure
    cmp_res = nc.ensure_compare_table("tok", table_id)
    assert cmp_res["ok"] and all(f["action"] == "unchanged" for f in cmp_res["formulas"])


def test_docs_list_and_spec_only(fake, tmp_path):
    xlsx = build(str(tmp_path / "s.xlsx"))
    rec_id = nc.upsert("tok", _data())["record_id"]
    assert nc.set_application_documents("tok", rec_id, "1. Bid form\n2. Price schedule")["ok"]
    res = nc.sync_spec_only("tok", "ACME-2026-0417", xlsx)
    assert res["spec_id"] == "T-7" and res["spec_verification"]["ok"]
