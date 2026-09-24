"""The only module that talks to Google Sheets.

``gspread`` is imported inside the functions so the rest of the package --
and the offline tests -- work without the dependency installed.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

# Appending by key needs nothing from Drive, so only ask for Sheets.
SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)


class SheetError(Exception):
    """Something went wrong on Google's side, phrased for a non-developer."""


class GoogleSheet:
    """Thin wrapper so the CLI can be tested against a fake."""

    def __init__(self, worksheet, title: str = ""):
        self._ws = worksheet
        self.title = title

    def read_values(self) -> list[list[str]]:
        return self._ws.get_all_values()

    def write_header(self, header: Sequence[str]) -> None:
        # Keyword arguments: gspread 5 and 6 order these differently.
        self._ws.update(range_name="A1", values=[list(header)],
                        value_input_option="RAW")

    def other_tabs(self) -> list[tuple[str, int, str]]:
        """(title, row count, content hash) of every *other* tab, read-only."""
        out = []
        for ws in self._ws.spreadsheet.worksheets():
            if ws.id == self._ws.id:
                continue
            values = ws.get_all_values()
            digest = hashlib.sha1(repr(values).encode("utf-8")).hexdigest()[:12]
            out.append((ws.title, len(values), digest))
        return out

    def append(self, rows: Sequence[Sequence[str]]) -> None:
        # RAW, not USER_ENTERED: a cell starting with "=" is text, not a formula.
        self._ws.append_rows([list(r) for r in rows], value_input_option="RAW",
                             insert_data_option="INSERT_ROWS", table_range="A1")


def open_worksheet(spreadsheet_id: str, worksheet_name: str,
                   credentials_info: dict, columns: Sequence[str],
                   create_missing: bool = True) -> GoogleSheet:
    try:
        import gspread
    except ImportError as exc:  # pragma: no cover - only hit outside CI
        raise SheetError(
            "gspread が入っていません。\n"
            "  → pip install -r x_inbox/requirements.txt を実行してください。"
        ) from exc

    client = gspread.service_account_from_dict(credentials_info, scopes=list(SCOPES))
    email = credentials_info.get("client_email", "(不明)")

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
    except gspread.exceptions.APIError as exc:
        raise SheetError(_explain(exc, email, spreadsheet_id)) from exc
    except gspread.exceptions.SpreadsheetNotFound as exc:
        raise SheetError(
            f"スプレッドシートが見つかりません（ID: {spreadsheet_id}）。\n"
            "  → IDの写し間違いか、シートがサービスアカウントに共有されていません。\n"
            f"  → シートの「共有」で {email} を編集者として追加してください。"
        ) from exc

    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        if not create_missing:
            names = ", ".join(w.title for w in spreadsheet.worksheets())
            raise SheetError(
                f"タブ「{worksheet_name}」がありません（あるタブ: {names}）。\n"
                "  → config.json の \"worksheet\" がシートのタブ名と一致しているか確認してください。"
                "タブは自動作成しません。"
            )
        worksheet = spreadsheet.add_worksheet(
            title=worksheet_name, rows=1000, cols=max(len(columns), 26))
    except gspread.exceptions.APIError as exc:
        raise SheetError(_explain(exc, email, spreadsheet_id)) from exc

    return GoogleSheet(worksheet, title=spreadsheet.title)


def _explain(exc, email: str, spreadsheet_id: str) -> str:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 403:
        return (
            "シートへの書き込みを拒否されました（403）。\n"
            f"  → 対象シートの「共有」で {email} を『編集者』として追加してください。\n"
            "  → Google Cloud のプロジェクトで Google Sheets API が有効か確認してください。"
        )
    if status == 404:
        return (
            f"そのIDのスプレッドシートが存在しません（404、ID: {spreadsheet_id}）。\n"
            "  → シートURLの /d/ と /edit の間の文字列だけをIDとして設定してください。"
        )
    if status == 429:
        return (
            "Google API の回数制限に当たりました（429）。\n"
            "  → 数分おいてから、Actions のページで Re-run jobs を押してください。"
        )
    return f"Google Sheets API がエラーを返しました: {exc}"
