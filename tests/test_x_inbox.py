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


def test_composite_key_treats_same_url_with_another_ticker_as_new(tmp_path):
    columns = ["ticker", "source_url", "text"]
    config = write_config(tmp_path, key_column="source_url+ticker", columns=columns)
    sheet = FakeSheet([list(columns)])
    write_csv(tmp_path, "a.csv", "ticker,source_url,text\n7203,https://x.com/1,a\n")
    run(config, sheet)
    write_csv(tmp_path, "b.csv",
              "ticker,source_url,text\n7203,https://x.com/1,edited\n"
              "6758,https://x.com/1,b\n")
    run(config, sheet)
    assert [r[0] for r in sheet.values[1:]] == ["7203", "6758"]


def test_composite_key_must_name_existing_columns(tmp_path):
    config = write_config(tmp_path, key_column="source_url+nope")
    with pytest.raises(core.ConfigError):
        core.load_config(config)


def test_blank_ticker_still_dedupes_on_url(tmp_path):
    columns = ["ticker", "source_url", "text"]
    config = write_config(tmp_path, key_column="source_url+ticker", columns=columns)
    sheet = FakeSheet([list(columns)])
    for name in ("a.csv", "b.csv"):
        write_csv(tmp_path, name, "ticker,source_url,text\n,https://x.com/9,hi\n")
        run(config, sheet)
    assert len(sheet.values) == 2


def test_normalized_key_ignores_width_case_and_spacing(tmp_path):
    columns = ["signal_id", "元ネタ", "派生ワード", "発見日時"]
    config = write_config(tmp_path, key_column="元ネタ+派生ワード", columns=columns,
                          normalize_key=True)
    sheet = FakeSheet([list(columns)])
    write_csv(tmp_path, "a.csv", "signal_id,元ネタ,派生ワード,発見日時\n"
                                 "S1,ChatGPT  Agent,ずらし構文,2026-09-24\n")
    run(config, sheet)
    write_csv(tmp_path, "b.csv", "signal_id,元ネタ,派生ワード,発見日時\n"
                                 "S2,ＣｈａｔＧＰＴ agent,ずらし構文 ,2026-09-25\n"
                                 "S3,ChatGPT agent,別ワード,2026-09-25\n")
    run(config, sheet)
    assert [r[0] for r in sheet.values[1:]] == ["S1", "S3"]


def strict_config(tmp_path, **extra):
    return write_config(tmp_path, strict=True, **extra)


def test_strict_stops_on_unknown_csv_column_and_keeps_the_file(tmp_path, capsys):
    config = strict_config(tmp_path)
    sheet = FakeSheet([list(COLUMNS)])
    write_csv(tmp_path, "a.csv", "id,text,surprise\n1,hi,x\n")
    assert run(config, sheet) == 1
    assert len(sheet.values) == 1
    assert (tmp_path / "inbox" / "a.csv").exists()
    assert "surprise" in capsys.readouterr().err


def test_strict_stops_when_the_sheet_header_differs_from_config(tmp_path):
    config = strict_config(tmp_path)
    sheet = FakeSheet([["id", "text", "extra"]])
    write_csv(tmp_path, "a.csv", "id,text\n1,hi\n")
    assert run(config, sheet) == 1
    assert len(sheet.values) == 1
    assert (tmp_path / "inbox" / "a.csv").exists()


def test_strict_stops_on_an_empty_sheet_instead_of_writing_a_header(tmp_path):
    config = strict_config(tmp_path)
    sheet = FakeSheet()
    write_csv(tmp_path, "a.csv", "id,text\n1,hi\n")
    assert run(config, sheet) == 1
    assert sheet.values == [] and sheet.header_writes == []


def test_strict_stops_when_the_key_column_is_missing_from_the_csv(tmp_path):
    config = strict_config(tmp_path)
    write_csv(tmp_path, "a.csv", "text\nhi\n")
    assert run(config, FakeSheet([list(COLUMNS)])) == 1


def test_strict_run_that_matches_appends_and_archives(tmp_path):
    config = strict_config(tmp_path)
    sheet = FakeSheet([list(COLUMNS)])
    write_csv(tmp_path, "a.csv", "id,text\n1,hi\n")
    assert run(config, sheet) == 0
    assert len(sheet.values) == 2
    assert not (tmp_path / "inbox" / "a.csv").exists()


def test_check_prints_a_fingerprint_of_the_other_tabs(tmp_path, capsys):
    config = strict_config(tmp_path)
    sheet = FakeSheet([list(COLUMNS)])
    sheet.other_tabs = lambda: [("README", 7, "abc123")]
    assert run(config, sheet, "--check") == 0
    assert "other tab: README rows=7 sha1=abc123" in capsys.readouterr().out


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


def _fake_gspread(monkeypatch, tabs):
    import types
    mod = types.ModuleType("gspread")
    exc = types.SimpleNamespace(
        APIError=type("APIError", (Exception,), {}),
        SpreadsheetNotFound=type("SpreadsheetNotFound", (Exception,), {}),
        WorksheetNotFound=type("WorksheetNotFound", (Exception,), {}),
    )
    mod.exceptions = exc
    created = []

    class Spreadsheet:
        title = "S"

        def worksheet(self, name):
            if name not in tabs:
                raise exc.WorksheetNotFound()
            return object()

        def worksheets(self):
            return [types.SimpleNamespace(title=t) for t in tabs]

        def add_worksheet(self, **kw):
            created.append(kw["title"])
            return object()

    mod.service_account_from_dict = lambda info, scopes: types.SimpleNamespace(
        open_by_key=lambda key: Spreadsheet())
    monkeypatch.setitem(sys.modules, "gspread", mod)
    return created


def test_missing_tab_is_not_created_when_strict(monkeypatch):
    from x_inbox import sheets
    created = _fake_gspread(monkeypatch, ["README", "TREND_SIGNAL"])
    with pytest.raises(sheets.SheetError) as err:
        sheets.open_worksheet("id", "typo", SERVICE_ACCOUNT, ["a"], create_missing=False)
    assert "README" in str(err.value) and created == []


def test_missing_tab_is_created_by_default(monkeypatch):
    from x_inbox import sheets
    created = _fake_gspread(monkeypatch, ["README"])
    sheets.open_worksheet("id", "new", SERVICE_ACCOUNT, ["a"])
    assert created == ["new"]


# --------------------------------------------------------- CSV row validation

REPO = Path(__file__).resolve().parents[1]
REQUIRED = {
    "x_inbox": ["投稿URL", "投稿者名", "アカウント", "投稿本文"],
    "japan_thesis_inbox": ["collected_at", "handle", "display_name", "post_created_at",
                           "post_text", "source_url", "topic", "stance", "summary"],
    "trend_signal_inbox": ["signal_id", "発見日時", "source", "元ネタ", "派生ワード",
                           "事実/推測", "status"],
}
V_COLUMNS = ["id", "author", "text", "url"]


def vcfg(tmp_path, **extra):
    return write_config(tmp_path, strict=True, columns=V_COLUMNS, key_column="url",
                        required_columns=["id", "author", "text", "url"], **extra)


def csv_text(*rows):
    import csv as _csv
    import io
    buf = io.StringIO()
    _csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def validate(tmp_path, body, required=("id", "text", "url")):
    return core.validate_csv_file(write_csv(tmp_path, "v.csv", body), required)


def test_quoted_multiline_cell_is_one_valid_record(tmp_path):
    body = csv_text(V_COLUMNS, ["1", "@a", "line1\nline2\n\nline4", "https://x.com/1"])
    assert validate(tmp_path, body) == []
    sheet = FakeSheet([V_COLUMNS])
    write_csv(tmp_path, "a.csv", body)
    assert run(vcfg(tmp_path), sheet) == 0
    assert sheet.values[1][2] == "line1\nline2\n\nline4"
    assert len(sheet.values) == 2


def test_unquoted_newline_splits_the_row_and_fails_with_line_numbers(tmp_path):
    body = "id,author,text,url\n1,@a,first line\nsecond line,https://x.com/1\n"
    problems = validate(tmp_path, body)
    assert [p.line for p in problems] == [2, 3]
    assert all("列数" in p.message for p in problems)
    assert "v.csv:2:" in str(problems[0])


def test_split_csv_is_rejected_whole_and_not_archived(tmp_path, capsys):
    config = vcfg(tmp_path)
    sheet = FakeSheet([V_COLUMNS])
    good = "id,author,text,url\n9,@z,ok,https://x.com/9\n"
    write_csv(tmp_path, "bad.csv", "id,author,text,url\n1,@a,first\nsecond,https://x.com/1\n")
    assert run(config, sheet) == 1
    assert sheet.values == [V_COLUMNS]                       # nothing appended
    assert (tmp_path / "inbox" / "bad.csv").exists()          # not archived
    assert not (tmp_path / "inbox" / "archive").exists()
    err = capsys.readouterr().err
    assert "bad.csv" in err and "bad.csv:2:" in err

    write_csv(tmp_path, "good.csv", good)                     # a valid file next to it still goes through
    assert run(config, sheet) == 1
    assert [r[0] for r in sheet.values[1:]] == ["9"]
    assert (tmp_path / "inbox" / "bad.csv").exists()
    assert not (tmp_path / "inbox" / "good.csv").exists()


def test_extra_columns_fail(tmp_path):
    problems = validate(tmp_path, "id,author,text,url\n1,@a,hi,https://x.com/1,oops\n")
    assert len(problems) == 1 and "5個" in problems[0].message


def test_missing_columns_fail(tmp_path):
    problems = validate(tmp_path, "id,author,text,url\n1,@a,hi\n")
    assert len(problems) == 1 and "3個" in problems[0].message


@pytest.mark.parametrize("row", [["1", "@a", "hi", ""], ["", "@a", "hi", "https://x.com/1"],
                                 ["1", "@a", "   ", "https://x.com/1"]])
def test_blank_required_field_fails(tmp_path, row):
    problems = validate(tmp_path, csv_text(V_COLUMNS, row))
    assert len(problems) == 1 and "必須項目が空" in problems[0].message


def test_missing_required_header_fails(tmp_path):
    problems = validate(tmp_path, "id,author,text\n1,@a,hi\n")
    assert len(problems) == 1 and problems[0].line == 1 and "url" in problems[0].message


def test_lone_fragment_row_fails(tmp_path):
    problems = validate(tmp_path, "id,author,text,url\n1,@a,hi,https://x.com/1\nこれかな？\n",
                        required=())
    assert [p.line for p in problems] == [3]
    assert "断片" in problems[0].message


@pytest.mark.parametrize("cell", ["SEE_ARTIFACT_FILE", "see_artifact_file", "SEE ARTIFACT FILE"])
def test_artifact_placeholder_fails(tmp_path, cell):
    body = csv_text(V_COLUMNS, ["1", "@a", cell, "https://x.com/1"])
    problems = validate(tmp_path, body)
    assert len(problems) == 1 and "アーティファクト" in problems[0].message


def test_placeholder_only_row_fails_even_without_required_columns(tmp_path):
    problems = validate(tmp_path, "id,author,text,url\nSEE_ARTIFACT_FILE\n", required=())
    assert len(problems) == 1 and "アーティファクト" in problems[0].message


def test_unterminated_quote_fails(tmp_path):
    problems = validate(tmp_path, 'id,author,text,url\n1,@a,"never closed,https://x.com/1\n')
    assert problems and "CSVとして読めません" in problems[0].message


def test_blank_lines_and_bom_are_fine(tmp_path):
    body = "﻿id,author,text,url\n\n1,@a,hi,https://x.com/1\n\n"
    assert validate(tmp_path, body) == []


def test_quoted_comma_is_fine_but_unquoted_comma_fails(tmp_path):
    ok = csv_text(V_COLUMNS, ["1", "@a", "( ´,_ゝ`)", "https://x.com/1"])
    assert validate(tmp_path, ok) == []
    problems = validate(tmp_path, "id,author,text,url\n1,@a,( ´,_ゝ`),https://x.com/1\n")
    assert len(problems) == 1 and "5個" in problems[0].message


def test_dry_run_also_rejects_and_touches_nothing(tmp_path):
    config = vcfg(tmp_path)
    path = write_csv(tmp_path, "bad.csv", "id,author,text,url\nSEE_ARTIFACT_FILE\n")
    assert run(config, FakeSheet([V_COLUMNS]), "--dry-run") == 1
    assert path.exists()


def test_valid_csv_still_appends_dedupes_and_archives(tmp_path):
    config = vcfg(tmp_path)
    sheet = FakeSheet([V_COLUMNS])
    body = csv_text(V_COLUMNS, ["1", "@a", "hi", "https://x.com/1"],
                    ["2", "@b", "yo", "https://x.com/2"])
    write_csv(tmp_path, "a.csv", body)
    assert run(config, sheet) == 0
    assert len(sheet.values) == 3
    assert not (tmp_path / "inbox" / "a.csv").exists()
    assert (tmp_path / "inbox" / "archive" / "2026-09-22" / "a.csv").exists()

    write_csv(tmp_path, "b.csv", body)                        # same rows again
    assert run(config, sheet) == 0
    assert len(sheet.values) == 3                             # not duplicated
    assert (tmp_path / "inbox" / "archive" / "2026-09-22" / "b.csv").exists()


def test_required_columns_must_be_real_columns(tmp_path):
    config = write_config(tmp_path, required_columns=["nope"])
    with pytest.raises(core.ConfigError):
        core.load_config(config)


def test_config_without_required_columns_only_checks_structure(tmp_path):
    config = write_config(tmp_path, strict=True)
    sheet = FakeSheet([list(COLUMNS)])
    write_csv(tmp_path, "a.csv", "id,text\n,hi\n")            # blank id is allowed here
    assert run(config, sheet) == 0
    assert len(sheet.values) == 2


# The three real pipelines: their configs carry the agreed required lists, and the
# cases the pipelines must keep accepting (blank ticker, blank 使用日時/成果) pass.

@pytest.mark.parametrize("pipeline", sorted(REQUIRED))
def test_real_config_declares_the_required_columns(pipeline):
    cfg = core.load_config(REPO / pipeline / "config.json")
    assert list(cfg.required_columns) == REQUIRED[pipeline]
    assert cfg.strict


def real_row(pipeline, **blank):
    cfg = core.load_config(REPO / pipeline / "config.json")
    row = [("" if name in blank else f"{name}-値") for name in cfg.columns]
    return list(cfg.columns), row, cfg


def test_japan_thesis_blank_ticker_passes_but_blank_source_url_fails(tmp_path):
    header, row, cfg = real_row("japan_thesis_inbox", ticker=True)
    path = write_csv(tmp_path, "j.csv", csv_text(header, row))
    assert core.validate_csv_file(path, cfg.required_columns) == []
    header, row, cfg = real_row("japan_thesis_inbox", source_url=True)
    path = write_csv(tmp_path, "j2.csv", csv_text(header, row))
    problems = core.validate_csv_file(path, cfg.required_columns)
    assert len(problems) == 1 and "source_url" in problems[0].message


def test_japan_thesis_blank_company_follows_the_existing_rule(tmp_path):
    header, row, cfg = real_row("japan_thesis_inbox", company=True)
    path = write_csv(tmp_path, "j.csv", csv_text(header, row))
    assert core.validate_csv_file(path, cfg.required_columns) == []


def test_trend_signal_blank_usage_and_result_pass_and_unknown_source_is_allowed(tmp_path):
    header, row, cfg = real_row("trend_signal_inbox", **{"使用日時": True, "成果": True})
    row[header.index("元ネタ")] = "不明"
    path = write_csv(tmp_path, "t.csv", csv_text(header, row))
    assert core.validate_csv_file(path, cfg.required_columns) == []
    header, row, cfg = real_row("trend_signal_inbox", **{"元ネタ": True})
    path = write_csv(tmp_path, "t2.csv", csv_text(header, row))
    assert len(core.validate_csv_file(path, cfg.required_columns)) == 1


def test_x_research_blank_url_fails(tmp_path):
    header, row, cfg = real_row("x_inbox", **{"投稿URL": True})
    path = write_csv(tmp_path, "x.csv", csv_text(header, row))
    problems = core.validate_csv_file(path, cfg.required_columns)
    assert len(problems) == 1 and "投稿URL" in problems[0].message


def test_committed_example_csv_is_valid():
    cfg = core.load_config(REPO / "x_inbox" / "config.json")
    assert core.validate_csv_file(REPO / "x_inbox" / "examples" / "sample.csv",
                                  cfg.required_columns) == []
