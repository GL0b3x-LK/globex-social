"""The client workbook -> calendar JSON: every derived field follows a stated rule."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from app.db import calendar_import, calendar_source


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = calendar_import.SHEET
    ws.append([None, None, None, None])  # the sheet has three blank rows
    ws.append([None, None, None, None])
    ws.append([None, None, None, None])
    ws.append(["Date", "Category", "Key Feature/Theme", "Exact Caption (Optional)"])
    ws.append(
        [datetime(2026, 9, 2), "Holiday", "National Poultry Month kickoff", "🐔 Happy!\n\nBody."]
    )
    ws.append([datetime(2026, 9, 14), "Trade Show", "Live at  Americas  Food Show", None])
    ws.append([datetime(2026, 9, 16), "Trade Show", "Thank you, Americas Food Show", ""])
    ws.append([datetime(2026, 10, 9), "Trade Show", "Meet us at SIAL Paris", None])
    ws.append([datetime(2026, 11, 4), "Milestone", "Globex Founding Day — 33 Years", "33 years!"])
    ws.append([datetime(2026, 12, 21), "Annual", "2027 Trade Show Calendar", None])
    ws.append(["Missing Fish - anything else Len sent?", None, None, None])
    ws.append([None, None, None, None])
    path = tmp_path / "cal.xlsx"
    wb.save(path)
    return path


def test_every_dated_row_becomes_an_anchored_entry_in_sheet_order(workbook: Path) -> None:
    entries, questions = calendar_import.read_workbook(workbook)
    assert [e.seq for e in entries] == [0, 1, 2, 3, 4, 5]
    assert all(e.anchored for e in entries)
    assert [e.planned_date for e in entries][:2] == ["2026-09-02", "2026-09-14"]
    assert entries[1].week == 2  # 12 days after the first entry


def test_categories_and_templates_follow_the_clients_conventions(workbook: Path) -> None:
    entries, _ = calendar_import.read_workbook(workbook)
    by_title = {e.title: e for e in entries}
    assert by_title["National Poultry Month kickoff"].category == "holiday"
    assert by_title["National Poultry Month kickoff"].template == "TS-p3-editorial_4x5"
    # Trade shows: teaser / live / recap each get their own layout.
    assert by_title["Live at Americas Food Show"].template == "TS-p3-editorial_4x5"
    assert by_title["Thank you, Americas Food Show"].template == "TS-p2-cut-navyborder_4x5"
    assert by_title["Meet us at SIAL Paris"].template == "TS-p1-bolddip_4x5"
    assert by_title["Globex Founding Day — 33 Years"].template == "MS-3-anniv-photo_4x5"
    assert by_title["2027 Trade Show Calendar"].category == "special"


def test_a_client_caption_is_kept_verbatim_and_shapes_the_gist(workbook: Path) -> None:
    entries, _ = calendar_import.read_workbook(workbook)
    poultry = entries[0]
    assert poultry.exact_caption == "🐔 Happy!\n\nBody."
    assert '"🐔 Happy!"' in poultry.gist
    # Empty and blank caption cells both mean "the model writes it".
    assert entries[1].exact_caption is None and entries[2].exact_caption is None


def test_double_spaces_in_a_title_are_collapsed(workbook: Path) -> None:
    entries, _ = calendar_import.read_workbook(workbook)
    assert entries[1].title == "Live at Americas Food Show"


def test_undated_rows_are_carried_out_as_client_questions(workbook: Path) -> None:
    _, questions = calendar_import.read_workbook(workbook)
    assert questions == ["Missing Fish - anything else Len sent?"]


def test_the_written_json_loads_as_calendar_entries_with_captions(
    workbook: Path, tmp_path: Path
) -> None:
    entries, questions = calendar_import.read_workbook(workbook)
    out = tmp_path / "calendar.json"
    calendar_import.write_calendar_json(entries, questions, out, source="cal.xlsx")
    doc = json.loads(out.read_text())
    assert doc["_meta"]["client_questions"] == questions
    loaded = calendar_source.load_calendar_file(out)
    assert len(loaded) == 6
    assert loaded[0].exact_caption == "🐔 Happy!\n\nBody."
    assert loaded[1].exact_caption is None
    # Ids are stable per (seq, title), so re-importing never re-drafts a built post.
    assert loaded[0].event_id == calendar_source.load_calendar_file(out)[0].event_id
