"""Offline tests of tools/nocodb_client.py - no network calls."""
import nocodb_client as nc
from make_sample_xlsx import build


def test_parse_numbers():
    assert nc.parse_number("1,234.50 EUR") == 1234.5
    assert nc.parse_number("Not specified in the documents") is None
    assert nc.parse_position_total("120.00 per unit (240.00 for 2 pcs)") == 240.0


def test_detect_and_convert_currency():
    assert nc.detect_currency("$45.90") == "USD"
    assert nc.detect_currency("320 RMB") == "CNY"
    value, note = nc.convert_to_base("$100", {"USD": 0.9})
    assert value == 90.0 and "converted from 100.00 USD" in note
    value, note = nc.convert_to_base("$100", {})
    assert value is None and "--rate USD" in note
    assert nc.convert_to_base("1,200.00", {}) == (1200.0, None)  # base currency, no symbol


def test_sanitize_spec_row_moves_placeholders_to_notes():
    row = nc.sanitize_spec_row({nc.COL_ITEM: "x", nc.COL_LINE_MAX: "Not specified in the documents",
                                nc.COL_EQ_ALLOWED: "No — spare parts", "Agent price 1": 10})
    assert row[nc.COL_LINE_MAX] is None
    assert "Max price (line): Not specified" in row[nc.COL_NOTES]
    assert "Agent price 1 has no comment" in row[nc.COL_NOTES]
    assert row[nc.COL_EQ_STATUS] == "No"


def test_equivalent_status():
    assert nc.normalize_equivalent_status("Yes") == "Yes"
    assert nc.normalize_equivalent_status("No — compatibility") == "No"
    assert nc.normalize_equivalent_status("Not specified in the documents") == "Check"
    assert nc.normalize_equivalent_status("") == "Check"


def test_read_xlsx_and_convert(tmp_path):
    rows = nc.read_spec_xlsx(build(str(tmp_path / "s.xlsx")))
    assert [r["No."] for r in rows] == ["1", "2"]
    out = nc.convert_foreign_prices(rows, {"USD": 0.9})
    assert out[0]["International offer 1"] == 809.1
    assert "International offer 1: converted" in out[0]["Notes"]


def test_manual_fields_restored_by_number():
    rows = nc.restore_manual_fields([{"No.": "1"}, {"No.": "2", "Procurement: price 1": 5}],
                                    {"1": {"Procurement: price 1": 7}, "2": {"Procurement: price 1": 9}})
    assert rows[0]["Procurement: price 1"] == 7 and rows[1]["Procurement: price 1"] == 5


def test_spec_columns_unique_and_formulas_reference_existing_columns():
    titles = nc.SPEC_TITLES
    assert len(titles) == len(set(titles))
    names = [nc._db_name(t) for t in titles]
    assert len(names) == len(set(names))
    known = set(titles) | {c[0] for c in nc.compare_formula_columns()}
    import re
    for title, formula, _, _ in nc.compare_formula_columns():
        for ref in re.findall(r"\{([^}]+)\}", formula):
            if not ref.isdigit() and not ref.startswith("3"):
                assert ref in known, (title, ref)
    for f in nc.COMPARE_VIEW_FIELDS:
        assert f in known


def test_registry_row_skips_invalid_urls():
    row = nc.build_registry_row({"purchase_number": "X-1", "tender_url": "Not specified in the documents",
                                 "platform_url": "https://p.example", "unknown_key": 1})
    assert "Tender URL" not in row and row["Platform URL"] == "https://p.example"
    assert "unknown_key" not in row
