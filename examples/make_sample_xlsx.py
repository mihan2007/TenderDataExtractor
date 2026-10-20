#!/usr/bin/env python3
"""Builds examples/Items_and_prices.example.xlsx - a small, fictional
"Items & prices" sheet in the exact column layout of CLAUDE.md step 5.1."""
import os
import sys

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from nocodb_client import PROCUREMENT_FIELDS, SPEC_TITLES  # noqa: E402

HEADERS = [t for t in SPEC_TITLES if t not in PROCUREMENT_FIELDS]

ROWS = [
    {"No.": "1", "Item name": "Pressure transmitter 0-10 bar",
     "Technical requirements": "1 Pressure transmitter (PT-200*)\n1.1 Range — 0-10 bar\n1.2 Output — 4-20 mA\nFootnote *: or equivalent",
     "Documentation errors": "✅ all OK", "Brand/model": "Example PT-200", "Manufacturer": "Example Sensors",
     "Country of origin": "Germany", "Equivalent allowed": "Yes", "Equivalent (status)": "Yes",
     "Equivalent": "❌ No 100% equivalent found.", "Qty": 12, "Unit": "pcs", "Max price (line)": "14,400.00",
     "Package dimensions": "20x15x10 cm, 0.6 kg", "Max unit price": 1200,
     "Agent price 1": 980, "Agent comment 1": "Source: shop-a.example (domestic B2B)\nLink: https://shop-a.example/pt-200\nExplanation: per 1 pc, incl. VAT, in stock",
     "Domestic offer 1": 980, "Domestic link 1": "https://shop-a.example/pt-200",
     "Domestic offer 2": 1010, "Domestic link 2": "https://shop-b.example/pt200",
     "Domestic offer 3": 1100, "Domestic link 3": "https://shop-c.example/p/pt-200",
     "International offer 1": "$899.00", "International link 1": "https://us-dist.example/pt-200",
     "Notes": 'International: found 1 of 3 [queries: "PT-200" | "PT200" | "Example PT-200" | "pressure transmitter Example PT-200" | "PT-200 site:ebay.com" | "PT-200 Preis"]'},
    {"No.": "2", "Item name": "Pressure transmitter 0-25 bar",
     "Technical requirements": "2 Pressure transmitter (PT-210)\n2.1 Range — 0-25 bar",
     "Documentation errors": "⚠️ Needs checking: cl. 2.1 range differs from the item list (0-16 bar)",
     "Brand/model": "Example PT-210", "Manufacturer": "Example Sensors", "Country of origin": "Germany",
     "Equivalent allowed": "Not specified in the documents", "Equivalent (status)": "Check",
     "Qty": 4, "Unit": "pcs", "Max price (line)": "Not specified in the documents",
     "Notes": "⚠ Discrepancy: Technical spec. item list — 0-16 bar, price schedule — 0-25 bar"},
]


def build(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Items & prices"
    ws.append(HEADERS)
    for row in ROWS:
        ws.append([row.get(h) for h in HEADERS])
    wb.save(path)
    return path


if __name__ == "__main__":
    out = build(os.path.join(os.path.dirname(os.path.abspath(__file__)), "Items_and_prices.example.xlsx"))
    print(out)
