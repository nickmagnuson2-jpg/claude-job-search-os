"""outreach_read.py - the one reader for data/outreach-log.md table rows."""

COLUMNS = ("date", "skill", "channel", "recipient", "company", "subject", "status")


def parse_rows(content: str) -> list[dict]:
    """Every outreach-log row as a record, in file order.

    Columns: Date | Skill | Channel | Recipient | Company | Subject | Status.
    A row needs all seven cells. The header row, and a separator row (first cell
    only '-', ':' and spaces), are skipped.
    """
    rows = []
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 7 or cols[0] == "Date" or set(cols[0]) <= set("-: "):
            continue
        rows.append(dict(zip(COLUMNS, cols[:7])))
    return rows
