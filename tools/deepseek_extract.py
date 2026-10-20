#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deepseek_extract.py - independent second-model cross-check (step 5.2 in CLAUDE.md).

Sends an excerpt of the tender documents to the DeepSeek API and asks it to
QUOTE the values of a fixed list of critical fields exactly as written in the
source - not reformatted, not summarized.

Why raw quotes: Claude's own extraction already applies the formatting rules
of CLAUDE.md. If the second model reformatted too, two equivalent answers
worded differently ("30 calendar days" vs "30 days") would look like a
mismatch. Claude compares DeepSeek's raw quotes with what it read itself and
only then decides match / mismatch and writes the final value with a ✅ or 🔺
marker.

This script does not compare anything, write to NocoDB or touch any files
(except --out). It makes one API call and prints the JSON answer.

Usage:
    python deepseek_extract.py --text excerpt.txt \
        --api-key-file secrets/API_key_DeepSeek.txt [--model deepseek-v4-pro] [--out result.json]

excerpt.txt: plain text / markdown of the parts that matter for the critical
fields - the notice / tender data sheet, the technical specification tables
and the price & payment clauses of the draft contract, each preceded by
"=====FILE: <document name>=====". Leave out long irrelevant annexes
(evaluation methodology, forms) to save tokens.

Output: {"model": ..., "usage": {...}, "fields": {field: "raw quote" | "not found in the provided text"}}
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config  # noqa: E402

API_URL = "https://api.deepseek.com/chat/completions"
NOT_FOUND = "not found in the provided text"

# Critical fields: price, all Block 4 dates, quantities, payment terms,
# delivery term. Buyer registration details are deliberately excluded.
CRITICAL_FIELDS = {
    "start_price": "Starting / maximum contract (or lot) price, with currency, exactly as written (do not recalculate or reformat the number).",
    "publication_date": "Date the tender notice / documents were officially published, as written.",
    "submission_deadline": "Date and time when bid submission closes, as written (including the time zone, if stated).",
    "auction_date": "Date of the auction / re-bidding / reverse auction / other price-reduction stage (or an explicit statement that there is none), as written.",
    "results_date": "Date of evaluation completion / award decision, as written.",
    "contract_date": "Contract signing deadline or rule (a date, or a rule such as 'not earlier than X and not later than Y days after...'), as written.",
    "quantity": "List and quantities of the items being procured (name, brand/part number, quantity, unit) - exactly as in the bill of quantities / item list.",
    "payment_terms": "Contract payment terms: whether there is an advance payment and its size, payment deadlines and the event they are counted from - exactly as written (do not shorten or paraphrase).",
    "delivery_term": "Delivery period and/or place of delivery of goods / performance of works or services, exactly as written.",
}

SYSTEM_PROMPT = (
    "You are performing a technical text-extraction task on procurement (tender) documents. "
    "Do NOT formalize, normalize or paraphrase. Quote the matching fragment of the text as close to "
    "the original as possible, exactly as it is written in the document. If a field is covered by "
    "several fragments (e.g. advance payment and final payment), include all of them, each as written. "
    f"If a field is not present in the provided text, write exactly: '{NOT_FOUND}' - never invent a "
    "value or fill it in from general knowledge about such tenders. "
    "Answer STRICTLY with a JSON object (no markdown, no text outside the JSON) whose keys are exactly "
    "the field names listed below. Keep the quotes in the original language of the document."
)


def build_user_prompt(text):
    fields_desc = "\n".join(f'- "{k}": {v}' for k, v in CRITICAL_FIELDS.items())
    return (
        "Below is the text of the tender documentation (it may contain several documents, separated by "
        "'=====FILE: ...=====' headers). Extract verbatim fragments for the following fields:\n\n"
        f"{fields_desc}\n\n"
        "Documentation text:\n\n"
        "-----BEGIN DOCUMENT TEXT-----\n"
        f"{text}\n"
        "-----END DOCUMENT TEXT-----"
    )


def main():
    parser = argparse.ArgumentParser(description="Cross-check critical tender fields with DeepSeek")
    parser.add_argument("--text", required=True, help="Combined plain-text / markdown excerpt of the documents")
    parser.add_argument("--api-key-file", required=True, help="File containing the DeepSeek API key")
    parser.add_argument("--model", default=load_config().get("deepseek_model") or "deepseek-v4-pro",
                        help="DeepSeek model name (default from config.json)")
    parser.add_argument("--out", help="Also save the JSON result to this file")
    args = parser.parse_args()

    with open(args.text, "r", encoding="utf-8") as f:
        text = f.read()
    with open(args.api_key_file, "r", encoding="utf-8") as f:
        api_key = f.read().strip()

    body = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(text)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(json.dumps({"error": f"HTTP {e.code}", "body": e.read().decode("utf-8", errors="replace")}),
              file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(json.dumps({"error": "connection_failed", "detail": str(e)}), file=sys.stderr)
        sys.exit(1)

    response = json.loads(raw)
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        print(json.dumps({"error": "unexpected_response_shape", "raw": response}), file=sys.stderr)
        sys.exit(1)
    try:
        extracted = json.loads(content)
    except json.JSONDecodeError:
        # Surface non-JSON output instead of failing silently.
        extracted = {"_raw_non_json_content": content}

    output = json.dumps({"model": args.model, "usage": response.get("usage", {}), "fields": extracted},
                        ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)


if __name__ == "__main__":
    main()
