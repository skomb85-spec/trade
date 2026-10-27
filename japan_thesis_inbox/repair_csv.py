"""Safely repair Grok's unquoted multiline post_text in Japan Thesis CSVs.

Only the unambiguous post_text case is repaired. Anything else is left for
x_inbox's strict validator to reject, without losing or moving source data.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
CONFIG = json.loads((DIRECTORY / "config.json").read_text(encoding="utf-8"))
COLUMNS = CONFIG["columns"]
REQUIRED = CONFIG["required_columns"]
DATE_TIME = r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?"
RECORD_START = re.compile(
    rf"(?m)(?=^{DATE_TIME},[^,\n]+,[^,\n]+,[^,\n]*,[^,\n]*,{DATE_TIME},)"
)
PREFIX = re.compile(
    rf"^({DATE_TIME},[^,\n]+,[^,\n]+,[^,\n]*,[^,\n]*,{DATE_TIME},)([\s\S]+)$"
)
SOURCE_TAIL = re.compile(
    r"([\s\S]*)(,https://(?:x\.com|twitter\.com)/[^,\n]+,[^,\n]*,[^,\n]*,[^,\n]*)\Z"
)


def parse(text: str) -> list[list[str]] | None:
    try:
        return list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        return None


def valid(rows: list[list[str]] | None) -> bool:
    if not rows or rows[0] != COLUMNS:
        return False
    required = [COLUMNS.index(col) for col in REQUIRED]
    for row in rows[1:]:
        if (len(row) != len(COLUMNS) or not any(v.strip() for v in row)
                or any(not row[i].strip() for i in required)
                or any(re.search(r"SEE[_\s-]*ARTIFACT", v, re.I) for v in row)):
            return False
    return True


def repair(text: str) -> tuple[str, int] | None:
    """Return fixed CSV and repaired-record count, or None if not provably safe."""
    if valid(parse(text)):
        return text, 0
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    header, sep, body = normalized.partition("\n")
    if not sep or parse(header) != [COLUMNS]:
        return None
    parts = RECORD_START.split(body)
    if parts[0].strip():
        return None
    chunks = [p.rstrip("\n") for p in parts[1:] if p.strip()]
    if not chunks:
        return None
    fixed: list[list[str]] = [COLUMNS]
    repairs = 0
    for chunk in chunks:
        parsed = parse(chunk)
        if parsed and len(parsed) == 1 and len(parsed[0]) == len(COLUMNS):
            fixed.append(parsed[0])
            continue
        match = PREFIX.fullmatch(chunk)
        if not match:
            return None
        prefix, remainder = match.groups()
        tail = SOURCE_TAIL.fullmatch(remainder)
        if not tail:
            return None
        post_text, suffix = tail.groups()
        # Do not guess how to fix a field that already begins with a quote.
        if not post_text or post_text.startswith('"') or "\n" not in post_text:
            return None
        candidate = prefix + '"' + post_text.replace('"', '""') + '"' + suffix
        row = parse(candidate)
        if not row or len(row) != 1 or len(row[0]) != len(COLUMNS):
            return None
        if row[0][6] != post_text or not row[0][7].startswith(("https://x.com/", "https://twitter.com/")):
            return None
        fixed.append(row[0])
        repairs += 1
    if not valid(fixed) or repairs == 0:
        return None
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(fixed)
    result = output.getvalue()
    if not valid(parse(result)) or len(parse(result) or []) != len(fixed):
        return None
    return result, repairs


def main() -> None:
    for path in sorted(DIRECTORY.glob("*.csv")):
        original = path.read_text(encoding="utf-8-sig")
        fixed = repair(original)
        if fixed is None:
            print(f"unrepaired (strict validation will reject if invalid): {path}")
            continue
        content, repairs = fixed
        if not repairs:
            continue
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent,
                                             prefix="._repair-", suffix=".tmp", delete=False) as fh:
                temp_path = fh.name
                fh.write(content)
            os.replace(temp_path, path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        print(f"repaired: {path} ({repairs} multiline post_text fields)")


if __name__ == "__main__":
    main()
