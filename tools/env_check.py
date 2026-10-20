#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
env_check.py - one-time environment diagnostic (step 0 in CLAUDE.md).

Checks - on Windows, macOS and Linux alike - whether the local tools this
project relies on (unrar, LibreOffice, a few Python packages) are present
AND actually runnable, and prints one JSON report with a status and an
OS-specific fix command per check. Also reports the project version and
whether config.json / the API key files exist.

    python3 tools/env_check.py
"""
import importlib
import json
import os
import platform
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


def _os_name():
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(platform.system(), "other")


def read_project_version():
    """VERSION at the project root; never raises."""
    try:
        with open(os.path.join(PROJECT_ROOT, "VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "unknown"


def check_python():
    ok = sys.version_info >= (3, 9)
    return {"check": "python3", "status": "ok" if ok else "missing", "detail": sys.version.split()[0],
            "fix": None if ok else "Python 3.9+ is required: https://www.python.org/downloads/"}


def check_unrar():
    try:
        from extract_archive import find_unrar_tool
    except Exception as e:
        return {"check": "unrar", "status": "unknown", "detail": f"could not check: {e}", "fix": None}
    tool = find_unrar_tool()
    if tool:
        return {"check": "unrar", "status": "ok", "detail": tool, "fix": None}
    fixes = {"macos": "brew install unrar", "linux": "sudo apt install unrar",
             "windows": "choco install unrar  (or download unrar.exe from rarlab.com into tools/bin/)"}
    return {"check": "unrar", "status": "missing",
            "detail": "no working unrar found - .rar archives cannot be extracted (.zip/.7z/.tar are unaffected)",
            "fix": fixes.get(_os_name(), "install unrar with your OS package manager")}


def check_soffice():
    try:
        from convert_odf import find_soffice
    except Exception as e:
        return {"check": "libreoffice", "status": "unknown", "detail": f"could not check: {e}", "fix": None}
    tool = find_soffice()
    if tool:
        return {"check": "libreoffice", "status": "ok", "detail": tool, "fix": None}
    fixes = {"macos": "brew install --cask libreoffice", "linux": "sudo apt install libreoffice",
             "windows": "download and install from libreoffice.org/download"}
    return {"check": "libreoffice", "status": "missing",
            "detail": "LibreOffice (soffice) not found - .odt/.ods/.odp conversion will not work (nothing else is affected)",
            "fix": fixes.get(_os_name(), "install LibreOffice for your OS")}


def check_module(module_name, pip_name=None, note=""):
    try:
        importlib.import_module(module_name)
        return {"check": module_name, "status": "ok", "detail": note, "fix": None}
    except ImportError:
        return {"check": module_name, "status": "missing", "detail": note,
                "fix": f"{sys.executable} -m pip install {pip_name or module_name}"}


def check_file(label, rel_path, fix):
    exists = os.path.isfile(os.path.join(PROJECT_ROOT, rel_path))
    return {"check": label, "status": "ok" if exists else "missing", "detail": rel_path,
            "fix": None if exists else fix}


OPTIONAL = ("rarfile", "py7zr")


def main():
    checks = [
        check_python(),
        check_unrar(),
        check_soffice(),
        check_module("openpyxl", note="reads/writes Items_and_prices.xlsx"),
        check_module("docx", pip_name="python-docx", note="supplier request files (tools/make_supplier_files.py)"),
        check_module("rarfile", note="installed automatically on the first .rar extraction - can be ignored"),
        check_module("py7zr", note="installed automatically on the first .7z extraction - can be ignored"),
        check_file("config.json", "config.json", "copy config.example.json to config.json and fill it in"),
        check_file("NocoDB token", os.path.join("secrets", "API_key_NocoDB.txt"),
                   "put your NocoDB API token into secrets/API_key_NocoDB.txt"),
        check_file("DeepSeek key", os.path.join("secrets", "API_key_DeepSeek.txt"),
                   "put your DeepSeek API key into secrets/API_key_DeepSeek.txt"),
    ]
    print(json.dumps({
        "project_version": read_project_version(),
        "os": platform.system(),
        "python_version": sys.version.split()[0],
        "checks": checks,
        "all_ok": all(c["status"] == "ok" for c in checks if c["check"] not in OPTIONAL),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
