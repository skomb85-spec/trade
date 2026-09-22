"""Append the CSVs sitting in the inbox to an existing Google Sheet.

    PYTHONPATH=src python -m x_inbox --dry-run     # 何が書かれるか見るだけ
    PYTHONPATH=src python -m x_inbox --check       # シートに繋がるかだけ試す
    PYTHONPATH=src python -m x_inbox               # 実際に追記する

Reads every ``x_inbox/*.csv``, drops rows the sheet already has, appends the
rest under the sheet's own header, then moves the processed files into
``x_inbox/archive/<date>/``. Running it twice never duplicates a row.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from . import core
from .core import ConfigError, CredentialsError
from .sheets import SheetError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="x_inbox",
        description="Append inbox CSVs to an existing Google Sheet, without duplicates.",
    )
    p.add_argument("--config", default=str(core.DEFAULT_CONFIG_PATH),
                   help=f"config file (default: {core.DEFAULT_CONFIG_PATH})")
    p.add_argument("--inbox", default=None,
                   help="read CSVs from this directory instead of the configured one")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be appended; touches neither Google nor the files")
    p.add_argument("--check", action="store_true",
                   help="only test the connection and print what the sheet looks like")
    p.add_argument("--no-archive", action="store_true",
                   help="leave processed CSVs in the inbox instead of moving them")
    p.add_argument("--limit", type=int, default=None,
                   help="append at most N rows (for a first careful run)")
    return p


def main(argv: list[str] | None = None, *, open_target=None,
         env: dict[str, str] | None = None, today: dt.date | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ if env is None else env)
    opener = open_target or _open_google_sheet

    try:
        cfg = core.apply_overrides(core.load_config(args.config), env, args.inbox)
        if args.check:
            return _check(cfg, env, opener)
        return _run(cfg, args, env, opener, today)
    except (ConfigError, CredentialsError, SheetError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1


def _open_google_sheet(cfg: core.Config, credentials_info: dict):
    from .sheets import open_worksheet
    return open_worksheet(cfg.spreadsheet_id, cfg.worksheet, credentials_info, cfg.columns)


def _connect(cfg: core.Config, env: dict[str, str], opener):
    core.require_spreadsheet_id(cfg)
    info = core.load_credentials_info(env.get("GOOGLE_SERVICE_ACCOUNT_JSON"))
    print(f"service account: {info.get('client_email')}")
    print(f"spreadsheet:     {cfg.spreadsheet_id} / シート「{cfg.worksheet}」")
    return opener(cfg, info)


def _check(cfg: core.Config, env: dict[str, str], opener) -> int:
    target = _connect(cfg, env, opener)
    values = target.read_values()
    header, needs_header = core.resolve_header(values, cfg.columns)
    data_rows = max(len(values) - 1, 0)
    print(f"connected: 「{getattr(target, 'title', '')}」 / 既存データ {data_rows} 行")
    print("header:    " + (", ".join(header) if header else "(なし)"))
    if needs_header:
        print("note:      シートが空なので、最初の追記時に config.json の列名を1行目に書きます。")
    print("\nOK: 接続できました。あとは x_inbox/ にCSVを置いてpushするだけです。")
    return 0


def _run(cfg: core.Config, args, env: dict[str, str], opener,
         today: dt.date | None) -> int:
    files = core.find_inbox_files(cfg.inbox_dir)
    if not files:
        print(f"{cfg.inbox_dir}/ に処理するCSVがありません。何もしません。")
        _report(env, appended=0, duplicates=0, archived=False)
        return 0

    records: list[dict[str, str]] = []
    for path in files:
        rows = core.read_csv_rows(path)
        print(f"read: {path} ({len(rows)} 行)")
        records.extend(rows)

    target = None
    if args.dry_run:
        header, needs_header = list(cfg.columns), True
        known: set[str] = set()
        print("\n--dry-run: Googleには接続しません。重複判定はシートを見ずに行います。")
    else:
        target = _connect(cfg, env, opener)
        values = target.read_values()
        header, needs_header = core.resolve_header(values, cfg.columns)
        known = core.existing_keys(values, header, cfg.key_column)
        print(f"sheet:     既存 {len(known)} 行を重複チェックに使います。")

    plan = core.plan_append(records, header, cfg.key_column, known)
    rows = list(plan.rows)
    if args.limit is not None and len(rows) > args.limit:
        print(f"note: --limit {args.limit} により {len(rows) - args.limit} 行を今回は見送ります。")
        rows = rows[:args.limit]

    _warn(plan, cfg)
    print(f"\n読み込み {plan.total_read} 行 / 新規 {len(rows)} 行 / "
          f"重複スキップ {plan.duplicates} 行 / 空行スキップ {plan.skipped_empty} 行")

    if args.dry_run:
        _preview(header, rows)
        print("\n(--dry-run のため書き込みもファイル移動もしていません)")
        _report(env, appended=0, duplicates=plan.duplicates, archived=False)
        return 0

    if rows:
        if needs_header:
            print(f"シートが空なので1行目に列名を書きます: {', '.join(header)}")
            target.write_header(header)
        target.append(rows)
        print(f"appended: シート「{cfg.worksheet}」に {len(rows)} 行を追記しました。")
    else:
        print("追記するものはありませんでした（すべて既にシートにあります）。")

    archived = False
    if not args.no_archive:
        moved = core.archive_files(files, cfg.archive_dir, today)
        archived = bool(moved)
        for src, dest in moved:
            print(f"archived: {src} -> {dest}")

    _report(env, appended=len(rows), duplicates=plan.duplicates, archived=archived)
    return 0


def _warn(plan: core.AppendPlan, cfg: core.Config) -> None:
    if plan.unknown_columns:
        print("\nwarning: CSVにあるがシートにない列は捨てられます: "
              + ", ".join(plan.unknown_columns))
        print("  → シートの1行目に同じ名前の列を足すと拾えるようになります。")
    if plan.missing_columns:
        print("warning: シートにあるがCSVになく、空欄で埋める列: "
              + ", ".join(plan.missing_columns))
    if cfg.key_column and cfg.key_column in plan.unknown_columns:
        print(f"warning: 重複判定用の列 {cfg.key_column!r} がCSVにありません。"
              "行の中身が完全に同じものだけを重複として扱います。")


def _preview(header: list[str], rows: list[tuple[str, ...]], limit: int = 5) -> None:
    if not rows:
        return
    print("\n追記される内容（先頭 " + str(min(limit, len(rows))) + " 行）:")
    print("  " + " | ".join(header))
    for row in rows[:limit]:
        cells = [c if len(c) <= 30 else c[:29] + "…" for c in row]
        print("  " + " | ".join(cells))
    if len(rows) > limit:
        print(f"  ... ほか {len(rows) - limit} 行")


def _report(env: dict[str, str], *, appended: int, duplicates: int,
            archived: bool) -> None:
    """Hand the numbers to GitHub Actions when running there."""
    out = env.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"appended={appended}\n")
            fh.write(f"duplicates={duplicates}\n")
            fh.write(f"archived={'true' if archived else 'false'}\n")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Xデータ追記\n\n- 追記した行: **{appended}**\n"
                     f"- 重複でスキップ: {duplicates}\n")
