"""Offline tests: temp CSVs and a fake sheet, no network."""

from __future__ import annotations

import base64
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x_inbox import cli, core  # noqa: E402

COLUMNS = ("collected_at", "id", "author", "text", "url")
SERVICE_ACCOUNT = {
    "type": "service_account",
    "client_email": "writer@example.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
}


class FakeSheet:
    """Stands in for the worksheet: remembers what it was told to write."""

    title = "テスト用シート"

    def __init__(self, values: list[list[str]] | None = None):
        self.values = [list(r) for r in (values or [])]
        self.header_writes: list[list[str]] = []

    def read_values(self) -> list[list[str]]:
        return [list(r) for r in self.values]

    def write_header(self, header) -> None:
        self.header_writes.append(list(header))
        if self.values:
            self.values[0] = list(header)
        else:
            self.values.append(list(header))

    def append(self, rows) -> None:
        self.values.extend([list(r) for r in rows])


def write_config(tmp_path: Path, **overrides) -> Path:
    payload = {
        "spreadsheet_id": "sheet-id-123",
        "worksheet": "inbox",
        "key_column": "id",
        "columns": list(COLUMNS),
        "inbox_dir": str(tmp_path / "inbox"),
        "archive_dir": str(tmp_path / "inbox" / "archive"),
    }
    payload.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "inbox").mkdir(exist_ok=True)
    return path


def write_csv(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "inbox" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def run(config: Path, sheet: FakeSheet, *args, env=None) -> int:
    environment = {"GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(SERVICE_ACCOUNT)}
    environment.update(env or {})
    return cli.main(
        ["--config", str(config), *args],
        open_target=lambda cfg, info: sheet,
        env=environment,
        today=dt.date(2026, 9, 22),
    )


# ------------------------------------------------------------------ reading

def test_read_csv_rows_handles_bom_blank_lines_and_padding(tmp_path):
    path = write_csv(tmp_path, "a.csv",
                     "﻿id, author ,text\n1, @me ,  hello  \n\n\n2,@you,hi\n")
    rows = core.read_csv_rows(path)
    assert rows == [
        {"id": "1", "author": "@me", "text": "hello"},
        {"id": "2", "author": "@you", "text": "hi"},
    ]


def test_read_csv_rows_keeps_newlines_inside_a_cell(tmp_path):
    path = write_csv(tmp_path, "a.csv", 'id,text\n1,"line one\r\nline two"\n')
    assert core.read_csv_rows(path)[0]["text"] == "line one\nline two"


def test_find_inbox_files_skips_archive_and_hidden(tmp_path):
    write_config(tmp_path)
    write_csv(tmp_path, "b.csv", "id\n1\n")
    write_csv(tmp_path, "a.csv", "id\n2\n")
    write_csv(tmp_path, "_notes.csv", "id\n3\n")
    (tmp_path / "inbox" / "archive" / "2026-09-01").mkdir(parents=True)
    (tmp_path / "inbox" / "archive" / "2026-09-01" / "old.csv").write_text("id\n9\n")
    names = [p.name for p in core.find_inbox_files(tmp_path / "inbox")]
    assert names == ["a.csv", "b.csv"]


# ------------------------------------------------------------------- header

def test_existing_header_wins_over_config_columns():
    values = [["url", "id", "text", "", ""], ["u1", "1", "hi"]]
    header, needs_write = core.resolve_header(values, COLUMNS)
    assert header == ["url", "id", "text"]
    assert needs_write is False


def test_empty_sheet_takes_the_configured_columns():
    header, needs_write = core.resolve_header([], COLUMNS)
    assert header == list(COLUMNS)
    assert needs_write is True


# ------------------------------------------------------------------ dedupe

def test_rows_are_written_in_the_sheets_column_order():
    plan = core.plan_append(
        [{"id": "1", "text": "hi", "author": "@me"}],
        header=["text", "id"], key_column="id")
    assert plan.rows == (("hi", "1"),)
    assert plan.unknown_columns == ("author",)


def test_duplicates_against_the_sheet_are_dropped():
    sheet = [["id", "text"], ["1", "already there"]]
    keys = core.existing_keys(sheet, ["id", "text"], "id")
    plan = core.plan_append([{"id": "1", "text": "again"}, {"id": "2", "text": "new"}],
                            header=["id", "text"], key_column="id", known_keys=keys)
    assert plan.rows == (("2", "new"),)
    assert plan.duplicates == 1


def test_duplicates_within_one_batch_are_dropped():
    plan = core.plan_append([{"id": "1"}, {"id": "1"}], header=["id"], key_column="id")
    assert plan.appended == 1
    assert plan.duplicates == 1


def test_rows_without_a_key_dedupe_on_their_contents():
    sheet = [["id", "text"], ["", "same line"]]
    keys = core.existing_keys(sheet, ["id", "text"], "id")
    plan = core.plan_append([{"text": "same line"}, {"text": "different"}],
                            header=["id", "text"], key_column="id", known_keys=keys)
    assert plan.rows == (("", "different"),)
    assert plan.duplicates == 1


def test_row_key_ignores_trailing_empty_cells():
    header = ["id", "text", "note"]
    assert (core.row_key(["", "hi"], header, "id")
            == core.row_key(["", "hi", ""], header, "id"))


def test_blank_records_are_not_appended():
    plan = core.plan_append([{"author": "@me"}], header=["id", "text"], key_column="id")
    assert plan.rows == ()
    assert plan.skipped_empty == 1


# ------------------------------------------------------------- credentials

def test_credentials_accept_raw_json_and_base64():
    raw = json.dumps(SERVICE_ACCOUNT)
    encoded = base64.b64encode(raw.encode()).decode()
    assert core.load_credentials_info(raw)["client_email"].startswith("writer@")
    assert core.load_credentials_info(encoded) == SERVICE_ACCOUNT


@pytest.mark.parametrize("value", ["", "   ", "not json", json.dumps({"a": 1})])
def test_bad_credentials_explain_themselves(value):
    with pytest.raises(core.CredentialsError):
        core.load_credentials_info(value)


# ----------------------------------------------------------------- archive

def test_archive_moves_files_into_a_dated_folder(tmp_path):
    write_config(tmp_path)
    first = write_csv(tmp_path, "a.csv", "id\n1\n")
    moved = core.archive_files([first], tmp_path / "archive", dt.date(2026, 9, 22))
    assert not first.exists()
    assert moved[0][1] == tmp_path / "archive" / "2026-09-22" / "a.csv"


def test_archive_does_not_overwrite_a_same_named_file(tmp_path):
    write_config(tmp_path)
    core.archive_files([write_csv(tmp_path, "a.csv", "id\n1\n")],
                       tmp_path / "archive", dt.date(2026, 9, 22))
    core.archive_files([write_csv(tmp_path, "a.csv", "id\n2\n")],
                       tmp_path / "archive", dt.date(2026, 9, 22))
    names = sorted(p.name for p in (tmp_path / "archive" / "2026-09-22").iterdir())
    assert names == ["a-1.csv", "a.csv"]


# --------------------------------------------------------------- end to end

def test_append_writes_the_header_then_the_rows(tmp_path):
    config = write_config(tmp_path)
    write_csv(tmp_path, "day1.csv",
              "collected_at,id,author,text,url\n2026-09-22,1,@me,hi,https://x.com/1\n")
    sheet = FakeSheet()
    assert run(config, sheet) == 0
    assert sheet.header_writes == [list(COLUMNS)]
    assert sheet.values == [list(COLUMNS),
                            ["2026-09-22", "1", "@me", "hi", "https://x.com/1"]]


def test_running_twice_does_not_duplicate(tmp_path):
    config = write_config(tmp_path)
    body = "id,text\n1,hi\n"
    write_csv(tmp_path, "day1.csv", body)
    sheet = FakeSheet([list(COLUMNS)])
    run(config, sheet)
    write_csv(tmp_path, "day1.csv", body)          # same rows pushed again
    run(config, sheet)
    assert len(sheet.values) == 2                  # header + one row
    assert sheet.header_writes == []               # header was already there


def test_processed_files_are_archived(tmp_path):
    config = write_config(tmp_path)
    write_csv(tmp_path, "day1.csv", "id,text\n1,hi\n")
    run(config, FakeSheet([list(COLUMNS)]))
    assert core.find_inbox_files(tmp_path / "inbox") == []
    assert (tmp_path / "inbox" / "archive" / "2026-09-22" / "day1.csv").exists()


def test_no_archive_leaves_the_inbox_alone(tmp_path):
    config = write_config(tmp_path)
    write_csv(tmp_path, "day1.csv", "id,text\n1,hi\n")
    run(config, FakeSheet([list(COLUMNS)]), "--no-archive")
    assert [p.name for p in core.find_inbox_files(tmp_path / "inbox")] == ["day1.csv"]


def test_dry_run_touches_neither_the_sheet_nor_the_files(tmp_path):
    config = write_config(tmp_path)
    write_csv(tmp_path, "day1.csv", "id,text\n1,hi\n")
    sheet = FakeSheet([list(COLUMNS)])
    assert run(config, sheet, "--dry-run") == 0
    assert sheet.values == [list(COLUMNS)]
    assert [p.name for p in core.find_inbox_files(tmp_path / "inbox")] == ["day1.csv"]


def test_limit_caps_how_many_rows_go_in(tmp_path):
    config = write_config(tmp_path)
    write_csv(tmp_path, "day1.csv", "id,text\n1,a\n2,b\n3,c\n")
    sheet = FakeSheet([list(COLUMNS)])
    run(config, sheet, "--limit", "2")
    assert len(sheet.values) == 3


def test_rows_land_under_the_sheets_own_column_order(tmp_path):
    config = write_config(tmp_path)
    write_csv(tmp_path, "day1.csv", "id,text,url\n1,hi,https://x.com/1\n")
    sheet = FakeSheet([["url", "text", "id"]])
    run(config, sheet)
    assert sheet.values[1] == ["https://x.com/1", "hi", "1"]


def test_missing_spreadsheet_id_fails_with_advice(tmp_path, capsys):
    config = write_config(tmp_path, spreadsheet_id=core.PLACEHOLDER_SHEET_ID)
    write_csv(tmp_path, "day1.csv", "id,text\n1,hi\n")
    assert run(config, FakeSheet()) == 1
    assert "SPREADSHEET_ID" in capsys.readouterr().err


def test_spreadsheet_id_from_the_environment_overrides_the_file(tmp_path):
    config = write_config(tmp_path, spreadsheet_id=core.PLACEHOLDER_SHEET_ID)
    write_csv(tmp_path, "day1.csv", "id,text\n1,hi\n")
    seen = {}

    def opener(cfg, info):
        seen["id"] = cfg.spreadsheet_id
        return FakeSheet([list(COLUMNS)])

    code = cli.main(["--config", str(config)], open_target=opener,
                    env={"GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(SERVICE_ACCOUNT),
                         "SPREADSHEET_ID": "from-env"}, today=dt.date(2026, 9, 22))
    assert code == 0
    assert seen["id"] == "from-env"


def test_an_empty_inbox_is_not_an_error(tmp_path, capsys):
    config = write_config(tmp_path)
    assert run(config, FakeSheet()) == 0
    assert "処理するCSVがありません" in capsys.readouterr().out


def test_actions_outputs_are_written(tmp_path):
    config = write_config(tmp_path)
    write_csv(tmp_path, "day1.csv", "id,text\n1,hi\n")
    out = tmp_path / "gh_output"
    run(config, FakeSheet([list(COLUMNS)]), env={"GITHUB_OUTPUT": str(out)})
    written = out.read_text(encoding="utf-8")
    assert "appended=1" in written
    assert "archived=true" in written


def test_check_mode_reports_without_writing(tmp_path, capsys):
    config = write_config(tmp_path)
    write_csv(tmp_path, "day1.csv", "id,text\n1,hi\n")
    sheet = FakeSheet([list(COLUMNS), ["2026-09-01", "0", "@x", "old", "u"]])
    assert run(config, sheet, "--check") == 0
    assert sheet.values[1:] == [["2026-09-01", "0", "@x", "old", "u"]]
    assert "既存データ 1 行" in capsys.readouterr().out


# ------------------------------------------------- Google errors, in Japanese

class FakeAPIError(Exception):
    def __init__(self, status: int | None):
        super().__init__("boom")
        self.response = type("Resp", (), {"status_code": status})()


def test_permission_error_names_the_account_to_share_with():
    from x_inbox.sheets import _explain
    message = _explain(FakeAPIError(403), "writer@example.iam.gserviceaccount.com", "sid")
    assert "writer@example.iam.gserviceaccount.com" in message
    assert "編集者" in message


def test_missing_sheet_error_points_at_the_id():
    from x_inbox.sheets import _explain
    assert "404" in _explain(FakeAPIError(404), "writer@example.com", "sid")


def test_unknown_errors_are_passed_through():
    from x_inbox.sheets import _explain
    assert "boom" in _explain(FakeAPIError(None), "writer@example.com", "sid")
