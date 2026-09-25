"""Deciding what gets written to the sheet -- offline, no Google client.

Everything here runs without network so the tests can cover the part that
matters: which rows are new, which are duplicates already in the sheet, and
what order the values go in. ``sheets.py`` is the only module that talks to
Google.
"""

from __future__ import annotations

import base64
import binascii
import csv
import datetime as dt
import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_CONFIG_PATH = Path("x_inbox/config.json")
PLACEHOLDER_SHEET_ID = "PUT_YOUR_SPREADSHEET_ID_HERE"


class ConfigError(Exception):
    """The config file is missing, malformed, or still has a placeholder."""


class StructureError(Exception):
    """The sheet or the CSV does not have the structure the config expects."""


class CredentialsError(Exception):
    """GOOGLE_SERVICE_ACCOUNT_JSON is missing or is not a service account key."""


@dataclass(frozen=True)
class Config:
    spreadsheet_id: str
    worksheet: str
    key_column: str
    columns: tuple[str, ...]
    inbox_dir: Path
    archive_dir: Path
    normalize_key: bool = False
    strict: bool = False
    required_columns: tuple[str, ...] = ()


def load_config(path: Path | str) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"設定ファイルが見つかりません: {path}\n"
            "  → リポジトリ直下から実行しているか確認してください。"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path} のJSONが壊れています（{exc.lineno}行目付近）。\n"
            "  → カンマの付け忘れや全角記号が混ざっていないか確認してください。"
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} の中身は {{ }} で囲んだ設定でなければいけません。")

    columns = tuple(str(c).strip() for c in raw.get("columns", ()) if str(c).strip())
    if not columns:
        raise ConfigError(f'{path} の "columns" が空です。シートの列名を並べてください。')

    key_column = str(raw.get("key_column", "")).strip()
    for part in _key_parts(key_column):
        if part not in columns:
            raise ConfigError(
                f'{path}: "key_column" に {part!r} とありますが "columns" に同じ名前がありません。'
            )

    required = tuple(str(c).strip() for c in raw.get("required_columns", ()) if str(c).strip())
    for name in required:
        if name not in columns:
            raise ConfigError(
                f'{path}: "required_columns" に {name!r} とありますが "columns" に同じ名前がありません。'
            )

    return Config(
        required_columns=required,
        spreadsheet_id=str(raw.get("spreadsheet_id", "")).strip(),
        worksheet=str(raw.get("worksheet", "")).strip() or "Sheet1",
        key_column=key_column,
        columns=columns,
        inbox_dir=Path(str(raw.get("inbox_dir", "x_inbox"))),
        archive_dir=Path(str(raw.get("archive_dir", "x_inbox/archive"))),
        normalize_key=bool(raw.get("normalize_key", False)),
        strict=bool(raw.get("strict", False)),
    )


def apply_overrides(cfg: Config, env: dict[str, str] | None = None,
                    inbox_dir: str | None = None) -> Config:
    """Environment wins over the config file; --inbox wins over both.

    The spreadsheet id lives in the repo variable ``SPREADSHEET_ID`` for
    people who would rather not commit it.
    """
    env = env or {}
    sheet_id = (env.get("SPREADSHEET_ID") or "").strip() or cfg.spreadsheet_id
    worksheet = (env.get("WORKSHEET") or "").strip() or cfg.worksheet
    cfg = replace(cfg, spreadsheet_id=sheet_id, worksheet=worksheet)
    if inbox_dir:
        cfg = replace(cfg, inbox_dir=Path(inbox_dir))
    return cfg


def require_spreadsheet_id(cfg: Config) -> str:
    if not cfg.spreadsheet_id or cfg.spreadsheet_id == PLACEHOLDER_SHEET_ID:
        raise ConfigError(
            "スプレッドシートIDが未設定です。\n"
            "  → x_inbox/config.json の \"spreadsheet_id\" を書き換えるか、\n"
            "     GitHub の Settings > Secrets and variables > Actions > Variables に\n"
            "     SPREADSHEET_ID を登録してください。\n"
            "  → IDはシートURLの /d/ と /edit の間の文字列です。"
        )
    return cfg.spreadsheet_id


# ---------------------------------------------------------------- inbox files

def find_inbox_files(inbox_dir: Path | str) -> list[Path]:
    """Top-level CSVs only, so archived copies never get re-read."""
    inbox_dir = Path(inbox_dir)
    if not inbox_dir.is_dir():
        return []
    files = [p for p in inbox_dir.glob("*.csv")
             if p.is_file() and not p.name.startswith((".", "_"))]
    return sorted(files)


def _clean(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read one inbox CSV. Blank lines and unnamed columns are dropped."""
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return []
        names = [(n or "").strip() for n in reader.fieldnames]
        records: list[dict[str, str]] = []
        for raw in reader:
            record: dict[str, str] = {}
            for name, field_name in zip(names, reader.fieldnames):
                if not name:
                    continue
                record[name] = _clean(raw.get(field_name))
            if any(record.values()):
                records.append(record)
    return records


# ----------------------------------------------------------------- validation

# Grok sometimes writes this token instead of the real file content.
ARTIFACT_PLACEHOLDER = re.compile(r"SEE[_\s-]*ARTIFACT", re.IGNORECASE)
MAX_REPORTED_PROBLEMS = 20


@dataclass(frozen=True)
class CsvProblem:
    path: Path
    line: int | str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def validate_csv_file(path: Path, required: Sequence[str] = ()) -> list[CsvProblem]:
    """Structural check of one inbox CSV, before anything is read for real.

    A row that is too short, too long, a lone fragment, an artifact placeholder,
    or missing a required cell means the file is broken (typically a newline
    inside a cell that was not quoted). The caller rejects the whole file.
    """
    path = Path(path)
    problems: list[CsvProblem] = []

    def add(line: int, message: str) -> None:
        problems.append(CsvProblem(path, line, message))

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh, strict=True)
        try:
            header = next(reader, None)
        except csv.Error as exc:
            return [CsvProblem(path, reader.line_num, f"CSVとして読めません（{exc}）")]
        if header is None:
            return []
        names = [(n or "").strip() for n in header]
        width = len(names)
        missing = [name for name in required if name not in names]
        if missing:
            add(1, "必須列がヘッダーにありません: " + ", ".join(missing))
            return problems
        required_at = [(name, names.index(name)) for name in required]

        while True:
            start = reader.line_num + 1
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error as exc:
                add(reader.line_num, f"CSVとして読めません（{exc}）。引用符が閉じていない可能性があります")
                break
            end = reader.line_num
            where = start if end == start else f"{start}〜{end}"
            filled = sum(1 for c in row if c.strip())
            if filled == 0:
                continue

            reasons: list[str] = []
            if len(row) != width:
                reasons.append(f"列数が{len(row)}個です（ヘッダーは{width}列）。"
                               "セル内の改行が引用符で囲まれていない可能性があります")
            if width > 2 and filled == 1:
                reasons.append("値が1セルだけの断片行です")
            if any(ARTIFACT_PLACEHOLDER.search(c) for c in row):
                reasons.append("SEE_ARTIFACT_FILE 等のアーティファクト置換文字列が入っています")
            if len(row) == width:
                empty = [name for name, i in required_at if not row[i].strip()]
                if empty:
                    reasons.append("必須項目が空です: " + ", ".join(empty))
            if reasons:
                add(where, "／".join(reasons))
    return problems


# ------------------------------------------------------------------- dedupe

def _fit(values: Sequence[str], width: int) -> list[str]:
    """Pad or trim to the header width.

    Sheets drops trailing empty cells on read, so a row must be fitted the
    same way on both sides or the content hash of an identical row differs.
    """
    out = [_clean(v) for v in values[:width]]
    out.extend([""] * (width - len(out)))
    return out


def _key_parts(key_column: str) -> list[str]:
    """``"a+b"`` means the pair (a, b) is the key; a plain name is a single column."""
    return [p.strip() for p in key_column.split("+") if p.strip()]


def normalize_text(value: str) -> str:
    """Width/case/whitespace-insensitive form used for keys when ``normalize_key`` is on."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def row_key(values: Sequence[str], header: Sequence[str], key_column: str,
            normalize: bool = False) -> str:
    """Identity of a row: the key column(s) when filled, else its content.

    ``key_column`` may join several columns with ``+`` (e.g. ``source_url+ticker``).

    Falling back to a hash of the whole row means a CSV without ids still
    dedupes -- re-running the job never appends the same line twice.
    """
    header = list(header)
    fitted = _fit(values, len(header))
    parts = _key_parts(key_column)
    if parts and all(p in header for p in parts):
        cells = [fitted[header.index(p)] for p in parts]
        if normalize:
            cells = [normalize_text(c) for c in cells]
        if any(cells):
            return cells[0] if len(cells) == 1 else "\x1f".join(cells)
    digest = hashlib.sha1("\x1f".join(fitted).encode("utf-8")).hexdigest()
    return "sha1:" + digest


def existing_keys(values: Sequence[Sequence[str]], header: Sequence[str],
                  key_column: str, normalize: bool = False) -> set[str]:
    """Keys already in the sheet. ``values`` is the sheet including its header."""
    return {row_key(row, header, key_column, normalize) for row in list(values)[1:]
            if any(_clean(v) for v in row)}


def resolve_header(sheet_values: Sequence[Sequence[str]],
                   columns: Sequence[str]) -> tuple[list[str], bool]:
    """Existing sheets keep their own header; empty ones get the config's.

    Returns (header, needs_write). Appending to someone else's sheet means
    writing in *their* column order, not ours.
    """
    if sheet_values:
        first = [_clean(v) for v in sheet_values[0]]
        if any(first):
            while first and not first[-1]:
                first.pop()
            return first, False
    return list(columns), True


def structure_problems(sheet_header: Sequence[str], columns: Sequence[str],
                       csv_columns: Iterable[str], key_column: str,
                       sheet_is_empty: bool = False) -> list[str]:
    """What is wrong with the sheet/CSV shape, phrased for a human. Empty list = fine."""
    problems: list[str] = []
    header, expected = list(sheet_header), list(columns)
    if sheet_is_empty:
        problems.append("シートに列見出し（1行目）がありません。")
    elif header != expected:
        missing = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        detail = []
        if missing:
            detail.append("シートに無い列: " + ", ".join(missing))
        if extra:
            detail.append("config.jsonに無い列: " + ", ".join(extra))
        if not detail:
            detail.append("列の順番が違います")
        problems.append("シートの列見出しがconfig.jsonの columns と一致しません（"
                        + " / ".join(detail) + "）。")
    csv_cols = set(csv_columns)
    if csv_cols:
        unknown = sorted(c for c in csv_cols if c not in header)
        if unknown:
            problems.append("CSVにあるがシートにない列: " + ", ".join(unknown))
        absent = [p for p in _key_parts(key_column) if p not in csv_cols]
        if absent:
            problems.append("重複判定に使う列がCSVにありません: " + ", ".join(absent))
    return problems


@dataclass(frozen=True)
class AppendPlan:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    total_read: int
    duplicates: int
    skipped_empty: int
    unknown_columns: tuple[str, ...]
    missing_columns: tuple[str, ...]

    @property
    def appended(self) -> int:
        return len(self.rows)


def plan_append(records: Iterable[dict[str, str]], header: Sequence[str],
                key_column: str, known_keys: Iterable[str] = (),
                normalize: bool = False) -> AppendPlan:
    """Turn CSV records into sheet rows, dropping anything already there."""
    header = list(header)
    seen = set(known_keys)
    rows: list[tuple[str, ...]] = []
    unknown: set[str] = set()
    used: set[str] = set()
    total = duplicates = empty = 0

    for record in records:
        total += 1
        unknown.update(name for name in record if name not in header)
        used.update(name for name in record if name in header)
        values = [_clean(record.get(name, "")) for name in header]
        if not any(values):
            empty += 1
            continue
        key = row_key(values, header, key_column, normalize)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        rows.append(tuple(values))

    return AppendPlan(
        header=tuple(header),
        rows=tuple(rows),
        total_read=total,
        duplicates=duplicates,
        skipped_empty=empty,
        unknown_columns=tuple(sorted(unknown)),
        missing_columns=tuple(name for name in header if name not in used) if total else (),
    )


# -------------------------------------------------------------- credentials

def load_credentials_info(raw: str | None) -> dict:
    """Accept the service account JSON raw or base64-encoded."""
    text = (raw or "").strip()
    if not text:
        raise CredentialsError(
            "GOOGLE_SERVICE_ACCOUNT_JSON が空です。\n"
            "  → GitHub の Settings > Secrets and variables > Actions > Secrets に\n"
            "     ダウンロードしたJSONキーの中身をそのまま貼り付けて登録してください。"
        )
    info = _parse_json(text)
    if info is None:
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            decoded = ""
        info = _parse_json(decoded) if decoded else None
    if info is None:
        raise CredentialsError(
            "GOOGLE_SERVICE_ACCOUNT_JSON がJSONとして読めません。\n"
            "  → JSONファイルを開いて、{ から } まで全部をコピーし直してください。"
        )
    if not isinstance(info, dict) or "client_email" not in info or "private_key" not in info:
        raise CredentialsError(
            "GOOGLE_SERVICE_ACCOUNT_JSON がサービスアカウントのキーではないようです。\n"
            "  → client_email と private_key を含むJSON（type: service_account）が必要です。"
        )
    return info


def _parse_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------------------ archive

def archive_files(files: Sequence[Path], archive_dir: Path | str,
                  today: dt.date | None = None) -> list[tuple[Path, Path]]:
    """Move processed CSVs into archive_dir/<date>/ so the inbox stays empty."""
    today = today or dt.date.today()
    dest_dir = Path(archive_dir) / today.isoformat()
    moved: list[tuple[Path, Path]] = []
    for src in files:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        n = 1
        while dest.exists():
            dest = dest_dir / f"{src.stem}-{n}{src.suffix}"
            n += 1
        shutil.move(str(src), str(dest))
        moved.append((Path(src), dest))
    return moved
