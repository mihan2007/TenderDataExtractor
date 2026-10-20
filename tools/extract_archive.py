#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_archive.py - extracts archives found in a tender documents folder
(TenderDataExtractor project), so the LLM can then scan/read the extracted
files with the docx/pdf/xlsx skills (steps 3-4 in CLAUDE.md).

Handles the mechanical work that used to require the LLM to reason about
archive contents:
  - supports .zip, .tar, .tar.gz/.tgz, .tar.bz2/.tbz2, .gz, .7z, .rar;
  - extracts each archive into a sibling folder "<archive_name>_extracted"
    next to the archive (or a folder you specify with --dest);
  - guards against zip-slip / path traversal (skips any entry that would
    write outside the destination folder);
  - RECURSIVE BY DEFAULT: if an extracted archive itself contains further
    archives (zip-in-zip, rar-in-zip, etc.), those are extracted too,
    all the way down, without needing to be told to do it again. Depth is
    capped (--max-depth, default 5) and extraction aborts with an error if
    it blows past sane file-count/size limits, as protection against
    decompression-bomb style archives. Pass --no-recursive to only extract
    the top-level archive(s).

CLAUDE.md step 3 calls this script automatically for every archive found in
a tender folder and reports the extraction to the user afterwards.

Usage:
    python extract_archive.py --archive "/path/to/file.zip"
    python extract_archive.py --archive "/path/to/a.zip" --archive "/path/to/b.7z"
    python extract_archive.py --scan-dir "/path/to/tender/folder"      # finds & extracts all archives in a folder
    python extract_archive.py --archive "/path/to/file.rar" --password "secret"
    python extract_archive.py --archive "/path/to/file.zip" --no-recursive
    python extract_archive.py --archive "/path/to/file.zip" --max-depth 2

Prints a JSON result to stdout, one entry per top-level archive:
    [
      {"archive": "...", "status": "ok", "dest": "...", "extracted_files": ["...", ...],
       "nested_archives": [{"archive": "...", "status": "ok", "dest": "..."}, ...]},
      {"archive": "...", "status": "error", "error": "..."}
    ]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import unicodedata
import zipfile

ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".tbz2")

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_MAX_DEPTH = 5
DEFAULT_MAX_FILES = 20000
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 ** 3  # 2 GB


class LimitExceeded(RuntimeError):
    """Raised when recursive extraction blows past the decompression-bomb guard."""


def _ensure_pip_module(module_name, pip_name=None):
    """Import module_name, auto-installing pip_name (or module_name) if missing.

    The sandbox environment is not guaranteed to keep pip packages between
    sessions, so scripts that depend on py7zr/rarfile install themselves on
    first use instead of requiring a manual `pip install` step every time.
    """
    try:
        return __import__(module_name)
    except ImportError:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--break-system-packages", "-q",
            pip_name or module_name,
        ])
        return __import__(module_name)


def find_unrar_tool():
    """Locate a working unrar binary.

    Prefers the copy bundled in tools/bin/unrar (committed to the project,
    so it survives across sandbox sessions) over whatever may or may not be
    present in the current sandbox image.

    Each candidate is test-executed (not just checked for the executable
    bit) before being accepted: tools/bin/unrar is a Linux x86-64 binary
    committed to the repo, so on a different OS/CPU (e.g. macOS, including
    Apple Silicon) it exists and is technically "executable" but fails to
    actually run. In that case we fall through to a system-installed unrar
    instead of hard-failing (on macOS: `brew install unrar`).
    """
    candidates = [
        os.path.join(TOOLS_DIR, "bin", "unrar"),
        os.path.join(TOOLS_DIR, "bin", "unrar.exe"),
        shutil.which("unrar"),
        shutil.which("unrar-nonfree"),
        "/tmp/extracted_pkg/usr/bin/unrar-nonfree",
        # Windows: WinRAR installs UnRAR.exe here but does not add itself to PATH
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "WinRAR", "UnRAR.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "WinRAR", "UnRAR.exe"),
    ]
    for c in candidates:
        if not c or not os.path.isfile(c) or not os.access(c, os.X_OK):
            continue
        try:
            subprocess.run([c], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue  # not runnable on this platform/arch (e.g. bundled Linux binary on macOS)
        return c
    return None


def find_archives(scan_dir):
    found = []
    for root, _dirs, files in os.walk(scan_dir):
        for fname in files:
            if fname.lower().endswith(ARCHIVE_EXTENSIONS):
                found.append(os.path.join(root, fname))
    return found


def safe_dest_path(dest_dir, member_name):
    """Resolve a member path inside dest_dir, rejecting path traversal (zip-slip)."""
    target = os.path.normpath(os.path.join(dest_dir, member_name))
    dest_abs = os.path.abspath(dest_dir)
    target_abs = os.path.abspath(target)
    if not (target_abs == dest_abs or target_abs.startswith(dest_abs + os.sep)):
        return None
    return target_abs


# Legacy code pages tried for ZIP entry names without the UTF-8 flag. The ZIP
# spec says cp437, but real-world tools wrote names in the local OEM/ANSI code
# page (Western European, Central European, Cyrillic, Chinese, Japanese).
LEGACY_ZIP_ENCODINGS = ("cp437", "cp850", "cp1252", "cp852", "cp1250", "cp866", "cp1251", "gbk", "shift_jis")


def _name_score(text):
    """Plausibility of a decoded file name: non-ASCII characters should be
    letters of ONE script; box-drawing/symbol soup or mixed scripts score low."""
    non_ascii = [c for c in text if ord(c) > 127]
    if not non_ascii:
        return 0
    bad = sum(1 for c in non_ascii if not c.isalpha())
    scripts = {unicodedata.name(c, "?").split()[0] for c in non_ascii if c.isalpha()}
    score = len(non_ascii) - 3 * bad - max(0, len(scripts) - 1) * len(non_ascii)
    # Latin-script words rarely contain 3+ accented letters in a row - that
    # pattern means non-Latin bytes read through a Latin code page.
    for run in re.findall(r"[^\x00-\x7f]{3,}", text):
        if all(c.isalpha() and unicodedata.name(c, "").startswith("LATIN") for c in run):
            score -= 2 * len(run)
    return score


def _fix_zip_name_encoding(name, flag_bits):
    """Python's zipfile decodes entry names without the UTF-8 flag as cp437.
    Many tools actually wrote them in the local legacy code page, so the
    cp437 decode produces mojibake. Undo it to get the raw bytes back, then
    pick the most plausible decoding (ties keep the spec's cp437)."""
    if flag_bits & 0x800:
        return name  # UTF-8 flag set - name is already correct
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    best, best_score = name, None
    for enc in LEGACY_ZIP_ENCODINGS:
        try:
            candidate = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if not candidate or any(ord(c) < 0x20 for c in candidate):
            continue
        score = _name_score(candidate)
        if best_score is None or score > best_score:
            best, best_score = candidate, score
    return best


def extract_zip(archive_path, dest_dir):
    extracted = []
    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            fixed_name = _fix_zip_name_encoding(info.filename, info.flag_bits)
            target = safe_dest_path(dest_dir, fixed_name)
            if target is None:
                continue  # skip unsafe entry
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                out.write(src.read())
            extracted.append(target)
    return extracted


def extract_tar(archive_path, dest_dir):
    extracted = []
    with tarfile.open(archive_path) as tf:
        for member in tf.getmembers():
            target = safe_dest_path(dest_dir, member.name)
            if target is None:
                continue
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            if not (member.isfile() or member.islnk() or member.issym()):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with open(target, "wb") as out:
                out.write(src.read())
            extracted.append(target)
    return extracted


def extract_gz_single(archive_path, dest_dir):
    import gzip
    base = os.path.basename(archive_path)
    out_name = base[:-3] if base.lower().endswith(".gz") else base + ".out"
    target = os.path.join(dest_dir, out_name)
    with gzip.open(archive_path, "rb") as src, open(target, "wb") as out:
        out.write(src.read())
    return [target]


def extract_7z(archive_path, dest_dir, password=None):
    py7zr = _ensure_pip_module("py7zr")
    extracted = []
    with py7zr.SevenZipFile(archive_path, mode="r", password=password) as zf:
        names = zf.getnames()
        zf.extractall(path=dest_dir)
    for name in names:
        target = safe_dest_path(dest_dir, name)
        if target and os.path.isfile(target):
            extracted.append(target)
    return extracted


def extract_rar(archive_path, dest_dir, password=None):
    rarfile = _ensure_pip_module("rarfile")

    unrar_tool = find_unrar_tool()
    if not unrar_tool:
        raise RuntimeError(
            "No unrar binary found (neither in tools/bin/unrar nor on the system). "
            "Install unrar or put a working unrar binary into tools/bin/unrar."
        )
    rarfile.UNRAR_TOOL = unrar_tool

    extracted = []
    try:
        with rarfile.RarFile(archive_path) as rf:
            for info in rf.infolist():
                target = safe_dest_path(dest_dir, info.filename)
                if target is None or info.is_dir():
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with rf.open(info, pwd=password) as src, open(target, "wb") as out:
                    out.write(src.read())
                extracted.append(target)
    except rarfile.RarCannotExec as e:
        raise RuntimeError(
            "No system unrar/bsdtar found on this machine to extract .rar files. "
            "Install unrar (e.g. apt install unrar / brew install unrar) or extract the archive manually."
        ) from e
    return extracted


def extract_one(archive_path, dest_root, password=None):
    lower = archive_path.lower()
    stem = os.path.basename(archive_path)
    for ext in (".tar.gz", ".tar.bz2"):
        if lower.endswith(ext):
            stem = stem[: -len(ext)]
            break
    else:
        stem = os.path.splitext(stem)[0]

    dest_dir = os.path.join(dest_root, stem + "_extracted")
    os.makedirs(dest_dir, exist_ok=True)

    if lower.endswith(".zip"):
        extracted = extract_zip(archive_path, dest_dir)
    elif lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2")):
        extracted = extract_tar(archive_path, dest_dir)
    elif lower.endswith(".gz"):
        extracted = extract_gz_single(archive_path, dest_dir)
    elif lower.endswith(".7z"):
        extracted = extract_7z(archive_path, dest_dir, password=password)
    elif lower.endswith(".rar"):
        extracted = extract_rar(archive_path, dest_dir, password=password)
    else:
        raise RuntimeError(f"Unsupported archive format: {archive_path}")

    return dest_dir, extracted


def extract_recursive(archive_path, dest_root, password, max_depth, stats):
    """Extract archive_path, then recurse into any archives found inside it.

    `stats` accumulates {'files', 'bytes', 'max_files', 'max_bytes'} across
    the whole run (all top-level archives share one budget) so a chain of
    nested archives can't be used as a decompression bomb.

    Returns (dest_dir, all_extracted_files, nested_results) where
    nested_results is a flat list describing every nested archive that was
    (or failed to be) extracted, for reporting purposes.
    """
    dest_dir, extracted = extract_one(archive_path, dest_root, password=password)

    stats["files"] += len(extracted)
    stats["bytes"] += sum(os.path.getsize(p) for p in extracted if os.path.isfile(p))
    if stats["files"] > stats["max_files"] or stats["bytes"] > stats["max_bytes"]:
        raise LimitExceeded(
            f"Extraction limit exceeded (files: {stats['files']}, bytes: {stats['bytes']}) - "
            "this looks like an archive bomb (abnormally large or deeply nested content); "
            "extraction stopped."
        )

    all_extracted = list(extracted)
    nested_results = []

    if max_depth > 0:
        for nested_archive in [f for f in extracted if f.lower().endswith(ARCHIVE_EXTENSIONS)]:
            try:
                n_dest, n_extracted, n_nested = extract_recursive(
                    nested_archive, os.path.dirname(nested_archive), password, max_depth - 1, stats
                )
                all_extracted.extend(n_extracted)
                nested_results.append({"archive": nested_archive, "status": "ok", "dest": n_dest})
                nested_results.extend(n_nested)
            except LimitExceeded:
                raise
            except Exception as ne:
                nested_results.append({"archive": nested_archive, "status": "error", "error": str(ne)})
    elif any(f.lower().endswith(ARCHIVE_EXTENSIONS) for f in extracted):
        nested_results.append({
            "archive": None,
            "status": "skipped",
            "error": f"Maximum nested-archive depth reached in {archive_path} - some inner archives were not extracted.",
        })

    return dest_dir, all_extracted, nested_results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", action="append", default=[], help="Path to an archive (may be repeated)")
    parser.add_argument("--scan-dir", help="Folder in which to find and extract all archives")
    parser.add_argument("--dest", help="Destination (default: a <name>_extracted folder next to the archive)")
    parser.add_argument("--password", help="Archive password, if required")
    parser.add_argument("--no-recursive", action="store_true", help="Do not extract archives found inside other archives (extraction is recursive by default)")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help=f"Maximum nested-archive depth (default {DEFAULT_MAX_DEPTH})")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help="Limit on the total number of extracted files (archive-bomb protection)")
    parser.add_argument("--max-total-mb", type=int, default=DEFAULT_MAX_TOTAL_BYTES // (1024 * 1024), help="Limit on the total size of extracted data, MB")
    args = parser.parse_args()

    archives = list(args.archive)
    if args.scan_dir:
        archives.extend(find_archives(args.scan_dir))
    archives = sorted(set(archives))

    if not archives:
        print(json.dumps({"error": "No archives found or specified"}, ensure_ascii=False))
        sys.exit(1)

    max_depth = 0 if args.no_recursive else args.max_depth
    stats = {"files": 0, "bytes": 0, "max_files": args.max_files, "max_bytes": args.max_total_mb * 1024 * 1024}

    results = []
    for archive_path in archives:
        dest_root = args.dest or os.path.dirname(archive_path)
        try:
            dest_dir, extracted, nested_results = extract_recursive(
                archive_path, dest_root, args.password, max_depth, stats
            )
            results.append({
                "archive": archive_path,
                "status": "ok",
                "dest": dest_dir,
                "extracted_files": extracted,
                "nested_archives": nested_results,
            })
        except Exception as e:
            results.append({"archive": archive_path, "status": "error", "error": str(e)})

    print(json.dumps(results, ensure_ascii=False, indent=2))
    if any(r["status"] == "error" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
