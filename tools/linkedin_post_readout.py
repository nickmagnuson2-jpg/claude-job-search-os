"""Build or refresh a LinkedIn post readout page from a LinkedIn post analytics export.

Method and runbook: framework/linkedin-post-readout-runbook.md
Page template:      framework/templates/linkedin-post-readout.html
Vercel gate:        framework/templates/linkedin-post-readout-middleware.js

Usage:
    python3 -I tools/linkedin_post_readout.py data <export.xlsx>
    python3 -I tools/linkedin_post_readout.py refresh <export.xlsx> <page.html>
    python3 -I tools/linkedin_post_readout.py build <config.json> <export.xlsx> <page.html> [--template T] [--vercel-dir D]

`data` prints the parsed export as JSON. `refresh` replaces only the DATA block of an
existing page. `build` writes a page from the template with this post's CONFIG and DATA,
and with --vercel-dir also writes the deployable folder (index.html, middleware.js,
package.json). The export is parsed with the standard library, because openpyxl is not
installed; `downloaded_at` is the export file's modification time.
"""
import datetime as dt
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, "framework", "templates", "linkedin-post-readout.html")
MIDDLEWARE = os.path.join(REPO, "framework", "templates", "linkedin-post-readout-middleware.js")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

METRICS = {
    "Impressions": "impressions",
    "Members reached": "members_reached",
    "Profile viewers from this post": "profile_viewers",
    "Followers gained from this post": "followers_gained",
    "Social engagements": "social_engagements",
    "Reactions": "reactions",
    "Comments": "comments",
    "Reposts": "reposts",
    "Saves": "saves",
    "Sends on LinkedIn": "sends",
    "Link engagements": "link_engagements",
    "Premium custom button engagements": "premium_button_engagements",
}
REQUIRED_CATEGORIES = ("Job title", "Location", "Seniority", "Company", "Industry", "Company size")
TITLE_MARKER = "<title>/*CONFIG:page_title*/</title>"


class ReadoutError(Exception):
    pass


def read_rows(xlsx_path):
    """Return the first sheet as a list of rows, each a list of cell strings in column order."""
    with zipfile.ZipFile(xlsx_path) as z:
        names = z.namelist()
        strings = []
        if "xl/sharedStrings.xml" in names:
            strings = [
                "".join(t.text or "" for t in si.iter(NS + "t"))
                for si in ET.fromstring(z.read("xl/sharedStrings.xml"))
            ]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = {}
    for c in sheet.iter(NS + "c"):
        v = c.find(NS + "v")
        inline = c.find(NS + "is")
        if c.get("t") == "s" and v is not None:
            val = strings[int(v.text)]
        elif v is not None:
            val = v.text or ""
        elif inline is not None:
            val = "".join(t.text or "" for t in inline.iter(NS + "t"))
        else:
            val = ""
        m = re.fullmatch(r"([A-Z]+)(\d+)", c.get("r") or "")
        if not m:
            raise ReadoutError(f"Cell without a valid reference: {c.get('r')!r}")
        col = 0
        for ch in m.group(1):
            col = col * 26 + ord(ch) - 64
        rows.setdefault(int(m.group(2)), {})[col] = val.strip()
    # place each cell at its column, so a missing cell is "" rather than shifting the row left;
    # trailing empty cells carry nothing and are dropped
    out = []
    for _, cells in sorted(rows.items()):
        row = [cells.get(c, "") for c in range(1, max(cells) + 1)]
        while row and not row[-1]:
            row.pop()
        out.append(row)
    return out


def to_int(s):
    return int(float(s.replace(",", "").strip()))


def parse_export(xlsx_path):
    rows = read_rows(xlsx_path)
    pairs = {r[0]: r[1] for r in rows if len(r) == 2}
    missing = [k for k in list(METRICS) + ["Post Date", "Post Publish Time", "Post URL"] if not pairs.get(k, "").strip()]
    if missing:
        raise ReadoutError(f"Export is missing or has empty fields: {missing}")
    month, day, year = pairs["Post Date"].split("/")
    post = {
        "date": f"{year}-{int(month):02d}-{int(day):02d}",
        "time": pairs["Post Publish Time"],
        "url": pairs["Post URL"],
        "downloaded_at": dt.datetime.fromtimestamp(os.path.getmtime(xlsx_path)).strftime("%Y-%m-%d %H:%M"),
    }
    metrics = {key: to_int(pairs[label]) for label, key in METRICS.items()}
    if metrics["profile_viewers"] > metrics["members_reached"]:
        raise ReadoutError("Export has more profile viewers than members reached; the reach view cannot draw that")

    demographics, in_demo = [], False
    for r in rows:
        if r == ["Category", "Value", "%"]:
            in_demo = True
            continue
        if in_demo:
            if len(r) != 3 or not all(r):
                raise ReadoutError(f"Export has a demographic row without three non-empty cells: {r}")
            label = r[2].replace("< ", "<")
            share = None if "<" in label else int(label.rstrip("%")) / 100
            demographics.append([r[0], r[1], share, label])
    absent = [c for c in REQUIRED_CATEGORIES if not any(r[0] == c for r in demographics)]
    if absent:
        raise ReadoutError(f"Export demographics are missing categories: {absent}")
    return {"post": post, "metrics": metrics, "demographics": demographics}


def replace_block(html, name, body):
    """Replace the text between /*NAME:START*/ and /*NAME:END*/; exactly one pair must exist."""
    block = f"/*{name}:START*/\n{body}\n/*{name}:END*/"
    new, n = re.subn(rf"/\*{name}:START\*/.*?/\*{name}:END\*/", lambda _: block, html, flags=re.S)
    if n != 1:
        raise ReadoutError(f"Expected exactly one {name} marker pair, found {n}")
    return new


def script_json(value):
    """JSON safe to place inside an HTML <script>: no '<' at all, so no spelling of </script> can close it."""
    return json.dumps(value, ensure_ascii=False, indent=1).replace("<", "\\u003c")


def data_block(data):
    return "const DATA = " + script_json(data) + ";"


def template_config(template_html):
    """The template's own CONFIG block is the single definition of the required keys."""
    m = re.search(r"/\*CONFIG:START\*/\s*const CONFIG = (.*?);\s*/\*CONFIG:END\*/", template_html, re.S)
    if not m:
        raise ReadoutError("Template has no CONFIG block")
    return json.loads(m.group(1))


def config_problems(expected, actual, where="config"):
    """Keys missing from `actual`, or values of a different JSON type, at every level of `expected`, list items included."""
    if type(actual) is not type(expected):
        return [f"{where} should be {type(expected).__name__}, not {type(actual).__name__}"]
    if isinstance(expected, list):
        # every item must look like the template's first item
        return [p for i, item in enumerate(actual) for p in config_problems(expected[0], item, f"{where}[{i}]")] if expected else []
    if not isinstance(expected, dict):
        return []
    problems = []
    for key in expected:
        if key not in actual:
            problems.append(f"{where}.{key} is missing")
        else:
            problems += config_problems(expected[key], actual[key], f"{where}.{key}")
    return problems


def load_config(path, template_html):
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    problems = config_problems(template_config(template_html), config)
    if problems:
        raise ReadoutError("Config does not match the template's CONFIG block: " + "; ".join(problems))
    return config


def render_page(template_html, config, data):
    if template_html.count(TITLE_MARKER) != 1:
        raise ReadoutError(f"Template must contain exactly one {TITLE_MARKER}")
    html = replace_block(template_html, "CONFIG", "const CONFIG = " + script_json(config) + ";")
    html = replace_block(html, "DATA", data_block(data))
    title = config["page_title"].replace("&", "&amp;").replace("<", "&lt;")
    return html.replace(TITLE_MARKER, f"<title>{title}</title>")


def wrap_for_vercel(page_html):
    """The artifact format has no <head>; move its leading <title> and <style> into one."""
    head, sep, rest = page_html.partition("</style>")
    if not sep:
        raise ReadoutError("Page has no </style>; cannot build the Vercel wrapper")
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        f"{head}</style>\n</head>\n<body>\n{rest.strip()}\n</body>\n</html>\n"
    )


def write_vercel_dir(out_dir, page_html, config, middleware_template):
    os.makedirs(out_dir, exist_ok=True)
    salt = os.path.basename(os.path.abspath(out_dir))
    # the name is the cookie salt in a JavaScript string and the npm package name
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", salt):
        raise ReadoutError(f"Vercel folder name must be lowercase letters, digits and hyphens: {salt!r}")
    # the headline lands in HTML inside a JavaScript template literal: escape both
    headline = (config["headline"].replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                .replace("&", "&amp;").replace("<", "&lt;"))
    middleware = middleware_template.replace("__HEADLINE__", headline).replace("__COOKIE_SALT__", salt)
    if re.search(r"__[A-Z_]+__", middleware):
        raise ReadoutError("Middleware template still has an unfilled placeholder")
    files = {
        "index.html": wrap_for_vercel(page_html),
        "middleware.js": middleware,
        "package.json": json.dumps({"name": salt, "private": True}) + "\n",
    }
    for name, text in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(text)
    return sorted(files)


def summary(data, path):
    m = data["metrics"]
    return (f"{path}: {m['impressions']} impressions, {m['members_reached']} reached, "
            f"{len(data['demographics'])} demographic rows, data as of {data['post']['downloaded_at']}")


def main(argv):
    flags = {}
    args = []
    it = iter(argv)
    for a in it:
        if a in ("--template", "--vercel-dir"):
            flags[a] = next(it, None)
        else:
            args.append(a)
    if not args:
        print(__doc__)
        return 2
    cmd, rest = args[0], args[1:]
    try:
        if cmd == "data" and len(rest) == 1:
            print(json.dumps(parse_export(rest[0]), ensure_ascii=False, indent=1))
        elif cmd == "refresh" and len(rest) == 2:
            data = parse_export(rest[0])
            with open(rest[1], encoding="utf-8") as f:
                html = replace_block(f.read(), "DATA", data_block(data))
            with open(rest[1], "w", encoding="utf-8") as f:
                f.write(html)
            print("Updated " + summary(data, rest[1]))
        elif cmd == "build" and len(rest) == 3:
            with open(flags.get("--template") or TEMPLATE, encoding="utf-8") as f:
                template_html = f.read()
            config = load_config(rest[0], template_html)
            data = parse_export(rest[1])
            page = render_page(template_html, config, data)
            with open(rest[2], "w", encoding="utf-8") as f:
                f.write(page)
            print("Built " + summary(data, rest[2]))
            if flags.get("--vercel-dir"):
                with open(MIDDLEWARE, encoding="utf-8") as f:
                    written = write_vercel_dir(flags["--vercel-dir"], page, config, f.read())
                print(f"Wrote {', '.join(written)} to {flags['--vercel-dir']}")
        else:
            print(__doc__)
            return 2
    except (ReadoutError, OSError, KeyError, ValueError, IndexError, zipfile.BadZipFile, ET.ParseError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
