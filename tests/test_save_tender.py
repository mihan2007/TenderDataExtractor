import csv
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

import save_tender as st


@pytest.mark.parametrize("raw, expected", [
    ("EUR 1,500,000.00", "1500000.00"),
    ("1 500 000,00 €", "1500000.00"),
    ("1.500.000,00", "1500000.00"),
    ("1'500'000.50 CHF", "1500000.50"),
    ("1500000", "1500000.00"),
    ("12,5", "12.50"),
    ("approx. 3.500", "3500.00"),
    ("0.500", "0.50"),
    (st.NOT_SPECIFIED, st.NOT_SPECIFIED),
    ("", st.NOT_SPECIFIED),
    ("no number here", "no number here"),
])
def test_normalize_number(raw, expected):
    assert st.normalize_number(raw) == expected


def test_normalize_number_keeps_markers():
    assert st.normalize_number("✅ EUR 18,450.00") == "✅ 18450.00"
    assert st.normalize_number("🔺 18 450,00 (discrepancy: DeepSeek read 18,540)").startswith("🔺 18450.00 (")


def test_format_source_files():
    assert st.format_source_files("a.pdf + b.docx") == "a.pdf\nb.docx"
    assert st.format_source_files("a.pdf") == "a.pdf"


def test_internal_number_sequence(tmp_path):
    state = tmp_path / "state.txt"
    assert st.resolve_internal_number("", str(state), "T-", 1) == "T-1"
    assert st.resolve_internal_number("", str(state), "T-", 1) == "T-2"
    assert st.resolve_internal_number("T-40", str(state), "T-", 1) == "T-40"   # user value, bumps counter
    assert st.resolve_internal_number("", str(state), "T-", 1) == "T-41"


def test_build_xml_roundtrip_multiline():
    fields = {"product_name": "Cables\n\nItems:\n1) A\n2) B", "internal_number": "T-1"}
    xml = st.build_xml(fields, "a.pdf", "2026-10-20")
    root = ET.fromstring(xml)
    assert root.find("block1_product/name").text == fields["product_name"]
    assert root.find("block5_buyer/name").text == st.NOT_SPECIFIED


def test_cli_end_to_end_upsert(tmp_path):
    data = json.load(open("examples/sample_tender_data.json", encoding="utf-8"))
    data_path = tmp_path / "data.json"
    data_path.write_text(json.dumps(data), encoding="utf-8")
    csv_path = tmp_path / "all_tenders.csv"
    cmd = [sys.executable, "tools/save_tender.py", "--json", str(data_path), "--extract-dir",
           str(tmp_path / "AIExtract"), "--csv-path", str(csv_path), "--source-file", data["source_file"]]
    first = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    second = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    assert first["csv_row_action"] == "appended" and second["csv_row_action"] == "updated"
    assert first["internal_number"] == second["internal_number"] == "T-1"
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    assert len(rows) == 1 and rows[0]["start_price"] == "✅ 18450.00"
    assert list(rows[0]) == st.CSV_COLUMNS


def test_zip_name_decoding():
    import extract_archive as ea
    for original, enc in (("Prüfbericht.pdf", "cp437"), ("Прайс-лист.xlsx", "cp866"), ("Smlouva_příloha.pdf", "cp852")):
        mojibake = original.encode(enc).decode("cp437")
        assert ea._fix_zip_name_encoding(mojibake, 0) == original, (original, enc)
    assert ea._fix_zip_name_encoding("plain.txt", 0) == "plain.txt"
