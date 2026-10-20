#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py - shared, per-installation settings for TenderDataExtractor.

Settings live in `config.json` at the project root (git-ignored; copy
`config.example.json` to start). Every key is optional - anything missing
falls back to DEFAULTS below, so the tools still run on a fresh checkout.

Environment variables override the file for the most commonly changed
values (handy for CI or containers):

    TDE_NOCODB_URL, TDE_NOCODB_WORKSPACE_ID, TDE_NOCODB_BASE_ID,
    TDE_NOCODB_REGISTRY_TABLE_ID, TDE_BASE_CURRENCY

Secrets (API tokens) are NOT stored here - see `secrets/` and README.md.
"""
import copy
import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

DEFAULTS = {
    # NocoDB (cloud or self-hosted). IDs are visible in the NocoDB URL:
    #   https://app.nocodb.com/#/<workspace_id>/<base_id>/<table_id>
    "nocodb": {
        "url": "https://app.nocodb.com",
        "workspace_id": "",
        "base_id": "",
        "registry_table_id": "",
        "registry_table_title": "Tender Registry",
    },
    # Internal tender numbers for your own bookkeeping: T-1, T-2, ...
    "internal_number": {"prefix": "T-", "start": 1},
    # All prices in the specification table are stored in this currency.
    "base_currency": "EUR",
    # Standard VAT / sales-tax rate used for the cost estimate (0.20 = 20%).
    "vat_rate": 0.20,
    # "Domestic" market = where your company buys locally; "international"
    # = everything else, searched in this priority order.
    "home_market": "",
    "international_priority": ["Germany", "China", "United States"],
    # Landed-cost multipliers applied per price source in the cost estimate.
    "cost_coefficients": {
        "domestic_b2b": 1.00,
        "domestic_retail": 1.12,
        "import": 1.28,
        "supplier_quote": 1.00,
    },
    # Number of "Procurement: supplier N / price N" column pairs.
    "procurement_slots": 10,
    "deepseek_model": "deepseek-v4-pro",
}

_ENV_OVERRIDES = {
    "TDE_NOCODB_URL": ("nocodb", "url"),
    "TDE_NOCODB_WORKSPACE_ID": ("nocodb", "workspace_id"),
    "TDE_NOCODB_BASE_ID": ("nocodb", "base_id"),
    "TDE_NOCODB_REGISTRY_TABLE_ID": ("nocodb", "registry_table_id"),
    "TDE_BASE_CURRENCY": ("base_currency",),
}


def _deep_merge(base, extra):
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=None):
    """Returns the merged settings dict (DEFAULTS <- config.json <- env)."""
    cfg = copy.deepcopy(DEFAULTS)
    path = path or os.environ.get("TDE_CONFIG") or CONFIG_PATH
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            _deep_merge(cfg, json.load(f))
    for env, keys in _ENV_OVERRIDES.items():
        value = os.environ.get(env)
        if value:
            target = cfg
            for k in keys[:-1]:
                target = target[k]
            target[keys[-1]] = value
    return cfg


if __name__ == "__main__":
    print(json.dumps(load_config(), ensure_ascii=False, indent=2))
