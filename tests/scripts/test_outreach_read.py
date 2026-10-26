"""outreach_read: the one reader of data/outreach-log.md table rows (2026-10-24).

outreach_metrics, outreach_pending, scorer_eval, conversations_metric and company_dim
read the log through it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import outreach_read as orr  # noqa: E402

HEAD = "| Date | Skill | Channel | Recipient | Company | Subject | Status |\n|---|---|---|---|---|---|---|\n"
ROW = "| 2026-02-01 | cold | email | Ann Example | Acme | Hi there | Sent |\n"


def test_reads_every_seven_cell_row_into_named_fields():
    assert orr.parse_rows(HEAD + ROW) == [{
        "date": "2026-02-01", "skill": "cold", "channel": "email", "recipient": "Ann Example",
        "company": "Acme", "subject": "Hi there", "status": "Sent"}]


def test_skips_header_separators_short_rows_and_prose():
    content = (HEAD + "| :--- | :--- | --- | --- | --- | --- | --- |\n" + ROW
               + "| 2026-02-02 | Sent |\nProse with a | b | c | d | e | f | g pipe\n"
               + " " + ROW)
    assert [r["date"] for r in orr.parse_rows(content)] == ["2026-02-01"]


def test_an_eighth_cell_does_not_shift_the_status():
    assert orr.parse_rows(HEAD + ROW.rstrip("\n") + " extra |\n")[0]["status"] == "Sent"


def test_alignment_separator_is_not_a_row():
    """BEHAVIOR CHANGE 2026-10-24: outreach_metrics.parse_rows, which this replaces,
    returned '| :--- | ... |' as a row dated ':---'."""
    assert orr.parse_rows("| Date | Skill | Channel | Recipient | Company | Subject | Status |\n"
                          "| :--- | :--- | --- | --- | --- | --- | --- |\n") == []
