"""Tests for tools/linkedin_post_readout.py"""
import importlib.util
import json
import re
import zipfile
from xml.sax.saxutils import escape
from pathlib import Path

import pytest

from conftest import REPO_ROOT

spec = importlib.util.spec_from_file_location("linkedin_post_readout", REPO_ROOT / "tools" / "linkedin_post_readout.py")
lpr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lpr)

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

# A synthetic export in LinkedIn's layout: label/value rows, then a Category/Value/% table.
ROWS = [
    ["Post URL", "https://www.linkedin.com/posts/example-post"],
    ["Post Date", "1/5/2026"],
    ["Post Publish Time", "8:00 AM"],
    ["Discovery"],
    ["Impressions", "2,000"],
    ["Members reached", "1200"],
    ["Profile viewers from this post", "50"],
    ["Followers gained from this post", "2"],
    ["Social engagements", "30"],
    ["Reactions", "20"],
    ["Comments", "5"],
    ["Reposts", "3"],
    ["Saves", "2"],
    ["Sends on LinkedIn", "0"],
    ["Link engagements", "0"],
    ["Premium custom button engagements", "0"],
    ["Post viewer demographics"],
    ["Category", "Value", "%"],
    ["Seniority", "Director", "15%"],
    ["Seniority", "Training", "< 1%"],
    ["Company size", "51-200 employees", "12%"],
    ["Job title", "Chief of Staff", "2%"],
    ["Location", "Example Metro Area", "10%"],
    ["Company", "Example Corp", "2%"],
    ["Industry", "Software Development", "20%"],
]


def write_export(path: Path, rows=ROWS):
    """Write rows with every string in sharedStrings (deduplicated), as LinkedIn does."""
    strings, index = [], {}
    cells_xml = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, val in enumerate(row):
            if val not in index:
                index[val] = len(strings)
                strings.append(val)
            cells.append(f'<c r="{"ABC"[c]}{r}" t="s"><v>{index[val]}</v></c>')
        cells_xml.append(f'<row r="{r}">{"".join(cells)}</row>')
    sst = f'<sst xmlns="{NS}">' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>"
    sheet = f'<worksheet xmlns="{NS}"><sheetData>{"".join(cells_xml)}</sheetData></worksheet>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


TEMPLATE_HTML = (REPO_ROOT / "framework" / "templates" / "linkedin-post-readout.html").read_text(encoding="utf-8")


def config():
    return lpr.template_config(TEMPLATE_HTML)


def test_parse_export_reads_metrics_post_and_demographics(tmp_path):
    data = lpr.parse_export(write_export(tmp_path / "e.xlsx"))
    assert data["metrics"]["impressions"] == 2000
    assert data["metrics"]["members_reached"] == 1200
    assert data["metrics"]["profile_viewers"] == 50
    assert data["post"]["date"] == "2026-01-05"
    assert data["post"]["time"] == "8:00 AM"
    assert data["post"]["url"] == "https://www.linkedin.com/posts/example-post"
    assert ["Seniority", "Director", 0.15, "15%"] in data["demographics"]
    assert ["Company size", "51-200 employees", 0.12, "12%"] in data["demographics"]


def test_under_one_percent_has_no_share(tmp_path):
    data = lpr.parse_export(write_export(tmp_path / "e.xlsx"))
    assert ["Seniority", "Training", None, "<1%"] in data["demographics"]


def test_missing_metric_fails(tmp_path):
    rows = [r for r in ROWS if r[0] != "Members reached"]
    with pytest.raises(lpr.ReadoutError, match="Members reached"):
        lpr.parse_export(write_export(tmp_path / "e.xlsx", rows))


def test_missing_demographics_fails(tmp_path):
    rows = [r for r in ROWS if len(r) != 3]
    with pytest.raises(lpr.ReadoutError, match="demographics"):
        lpr.parse_export(write_export(tmp_path / "e.xlsx", rows))


def test_replace_block_requires_exactly_one_marker_pair():
    html = "a /*DATA:START*/old/*DATA:END*/ b"
    assert "new" in lpr.replace_block(html, "DATA", "new")
    assert "old" not in lpr.replace_block(html, "DATA", "new")
    with pytest.raises(lpr.ReadoutError):
        lpr.replace_block("no markers", "DATA", "x")
    with pytest.raises(lpr.ReadoutError):
        lpr.replace_block(html + html, "DATA", "x")


def test_render_page_fills_config_data_and_title(tmp_path):
    template = (REPO_ROOT / "framework" / "templates" / "linkedin-post-readout.html").read_text(encoding="utf-8")
    cfg = config()
    cfg["page_title"] = "Readout <Test>"
    cfg["org"] = "Zeta Org"
    data = lpr.parse_export(write_export(tmp_path / "e.xlsx"))
    page = lpr.render_page(template, cfg, data)
    assert "<title>Readout &lt;Test></title>" in page
    assert '"org": "Zeta Org"' in page
    assert '"members_reached": 1200' in page
    assert page.count("/*CONFIG:START*/") == 1 and page.count("/*DATA:START*/") == 1


def test_load_config_rejects_missing_keys(tmp_path):
    cfg = config()
    del cfg["buyers"]
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(lpr.ReadoutError, match="buyers"):
        lpr.load_config(p, TEMPLATE_HTML)


def test_build_writes_page_and_vercel_dir(tmp_path):
    cfg_path = tmp_path / "c.json"
    cfg = config()
    cfg["headline"] = "Who saw it?"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    export = write_export(tmp_path / "e.xlsx")
    page = tmp_path / "page.html"
    out = tmp_path / "site"
    assert lpr.main(["build", str(cfg_path), str(export), str(page), "--vercel-dir", str(out)]) == 0
    index = (out / "index.html").read_text(encoding="utf-8")
    assert index.startswith("<!doctype html>")
    assert index.index("<title>") < index.index("</head>") < index.index("<body>")
    assert 'name="robots" content="noindex, nofollow"' in index
    mw = (out / "middleware.js").read_text(encoding="utf-8")
    assert "Who saw it?" in mw
    assert "'site:' + password" in mw
    assert "__HEADLINE__" not in mw and "__COOKIE_SALT__" not in mw
    assert json.loads((out / "package.json").read_text())["name"] == "site"


def test_refresh_replaces_only_data(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<p>keep</p>\n/*DATA:START*/\nconst DATA = {};\n/*DATA:END*/\n", encoding="utf-8")
    assert lpr.main(["refresh", str(write_export(tmp_path / "e.xlsx")), str(page)]) == 0
    text = page.read_text(encoding="utf-8")
    assert "<p>keep</p>" in text and '"impressions": 2000' in text


def write_raw_sheet(path: Path, cells_by_row, shared=None):
    """Write a sheet with explicit cell XML; `shared` adds a sharedStrings part when given."""
    rows = "".join(f'<row r="{r}">{cells}</row>' for r, cells in cells_by_row)
    with zipfile.ZipFile(path, "w") as z:
        if shared is not None:
            z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}">' + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{NS}"><sheetData>{rows}</sheetData></worksheet>')
    return path


def test_read_rows_handles_number_inline_and_empty_cells(tmp_path):
    path = write_raw_sheet(tmp_path / "e.xlsx", [
        (1, f'<c r="A1" t="s"><v>0</v></c><c r="B1"><v>2800</v></c>'),
        (2, f'<c r="A2" t="inlineStr"><is><t>Reach</t></is></c><c r="B2"/>'),
    ], shared=["Impressions"])
    assert lpr.read_rows(path) == [["Impressions", "2800"], ["Reach"]]


def test_read_rows_without_shared_strings_part(tmp_path):
    path = write_raw_sheet(tmp_path / "e.xlsx", [(1, '<c r="A1" t="inlineStr"><is><t>Only</t></is></c>')])
    assert lpr.read_rows(path) == [["Only"]]


def test_wrap_for_vercel_requires_style_block():
    with pytest.raises(lpr.ReadoutError):
        lpr.wrap_for_vercel("<title>x</title><p>no styles</p>")


def test_write_vercel_dir_rejects_unfilled_placeholder(tmp_path):
    with pytest.raises(lpr.ReadoutError, match="placeholder"):
        lpr.write_vercel_dir(tmp_path / "site", "<title>t</title><style></style><p>x</p>", config(), "x __UNKNOWN__ y")


def test_build_without_vercel_dir_writes_only_the_page(tmp_path, capsys):
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps(config()), encoding="utf-8")
    page = tmp_path / "page.html"
    assert lpr.main(["build", str(cfg_path), str(write_export(tmp_path / "e.xlsx")), str(page)]) == 0
    assert '"impressions": 2000' in page.read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.json", "e.xlsx", "page.html"]
    out = capsys.readouterr().out
    assert "Built" in out and "2000 impressions" in out and "Wrote" not in out


def test_build_with_vercel_dir_reports_files(tmp_path, capsys):
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps(config()), encoding="utf-8")
    lpr.main(["build", str(cfg_path), str(write_export(tmp_path / "e.xlsx")), str(tmp_path / "p.html"), "--vercel-dir", str(tmp_path / "site")])
    assert "Wrote index.html, middleware.js, package.json" in capsys.readouterr().out


def test_data_command_prints_json(tmp_path, capsys):
    assert lpr.main(["data", str(write_export(tmp_path / "e.xlsx"))]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["reactions"] == 20


def test_refresh_reports_update(tmp_path, capsys):
    page = tmp_path / "page.html"
    page.write_text("/*DATA:START*/\n/*DATA:END*/", encoding="utf-8")
    lpr.main(["refresh", str(write_export(tmp_path / "e.xlsx")), str(page)])
    assert "Updated" in capsys.readouterr().out


def test_usage_and_errors_return_nonzero(tmp_path, capsys):
    assert lpr.main([]) == 2
    assert "Usage" in capsys.readouterr().out
    assert lpr.main(["data"]) == 2
    assert "Usage" in capsys.readouterr().out
    assert lpr.main(["build", "a", "b"]) == 2
    rows = [r for r in ROWS if r[0] != "Reactions"]
    assert lpr.main(["data", str(write_export(tmp_path / "e.xlsx", rows))]) == 1
    assert "Error:" in capsys.readouterr().err


def test_cli_entry_point_runs_and_exits_with_status(tmp_path):
    import subprocess, sys
    script = str(REPO_ROOT / "tools" / "linkedin_post_readout.py")
    ok = subprocess.run([sys.executable, "-I", script, "data", str(write_export(tmp_path / "e.xlsx"))], capture_output=True, text=True)
    assert ok.returncode == 0 and json.loads(ok.stdout)["metrics"]["impressions"] == 2000
    bad = subprocess.run([sys.executable, "-I", script], capture_output=True, text=True)
    assert bad.returncode == 2


def test_empty_metric_value_fails(tmp_path):
    rows = [["Members reached", ""] if r[0] == "Members reached" else r for r in ROWS]
    with pytest.raises(lpr.ReadoutError, match="Members reached"):
        lpr.parse_export(write_export(tmp_path / "e.xlsx", rows))


def test_missing_demographic_category_fails(tmp_path):
    rows = [r for r in ROWS if r[0] != "Industry"]
    with pytest.raises(lpr.ReadoutError, match="Industry"):
        lpr.parse_export(write_export(tmp_path / "e.xlsx", rows))


def test_script_close_tag_in_config_cannot_end_the_script(tmp_path):
    cfg = config()
    cfg["intro"] = "Ends here </script><script>alert(1)</script>"
    page = lpr.render_page(TEMPLATE_HTML, cfg, lpr.parse_export(write_export(tmp_path / "e.xlsx")))
    assert "</script><script>alert(1)" not in page
    assert page.count("</script>") == TEMPLATE_HTML.count("</script>")


def test_template_needs_exactly_one_title_marker(tmp_path):
    data = lpr.parse_export(write_export(tmp_path / "e.xlsx"))
    with pytest.raises(lpr.ReadoutError, match="title"):
        lpr.render_page(TEMPLATE_HTML.replace(lpr.TITLE_MARKER, "<title>x</title>"), config(), data)
    with pytest.raises(lpr.ReadoutError, match="title"):
        lpr.render_page(TEMPLATE_HTML + lpr.TITLE_MARKER, config(), data)


def test_template_without_config_block_fails():
    with pytest.raises(lpr.ReadoutError, match="CONFIG"):
        lpr.template_config("<p>no config</p>")


def _middleware_for(tmp_path, headline):
    cfg = config()
    cfg["headline"] = headline
    mw_template = (REPO_ROOT / "framework" / "templates" / "linkedin-post-readout-middleware.js").read_text(encoding="utf-8")
    lpr.write_vercel_dir(tmp_path / "site", "<title>t</title><style></style><p>x</p>", cfg, mw_template)
    return tmp_path / "site" / "middleware.js"


NODE_DRIVER = r"""
const mod = await import(process.argv[2]);
const mw = mod.default;
process.env.READOUT_PASSWORD = "secret-pw";
const url = "https://example.test/";
const form = (pw) => new Request(url, {method: "POST", body: new URLSearchParams({password: pw})});
const out = {};
let r = await mw(new Request(url));
out.first = [r.status, (await r.text()).includes('type="password"'), r.headers.get("set-cookie")];
r = await mw(form("wrong"));
out.wrong = [r.status, r.headers.get("set-cookie")];
r = await mw(form("secret-pw"));
const cookie = r.headers.get("set-cookie");
out.right = [r.status, cookie];
const value = cookie.split(";")[0];
out.withCookie = (await mw(new Request(url, {headers: {cookie: value}}))) === undefined;
out.badCookie = (await mw(new Request(url, {headers: {cookie: "readout_auth=abc123"}}))).status;
r = await mw(new Request(url));
out.title = (await r.text()).match(/<title>(.*)<\/title>/)[1];
console.log(JSON.stringify(out));
"""


def _run_node(tmp_path, mw_path):
    import shutil, subprocess
    if not shutil.which("node"):
        pytest.skip("node not installed")
    mjs = tmp_path / "middleware.mjs"
    mjs.write_text(mw_path.read_text(encoding="utf-8"), encoding="utf-8")
    driver = tmp_path / "driver.mjs"
    driver.write_text(NODE_DRIVER, encoding="utf-8")
    res = subprocess.run(["node", str(driver), mjs.as_uri()], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_generated_middleware_gates_on_password(tmp_path):
    out = _run_node(tmp_path, _middleware_for(tmp_path, "Who saw it?"))
    assert out["first"] == [401, True, None]
    assert out["wrong"] == [401, None]
    status, cookie = out["right"]
    assert status == 303
    assert "secret-pw" not in cookie
    for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Max-Age=2592000"):
        assert flag in cookie
    assert out["withCookie"] is True
    assert out["badCookie"] == 401
    assert out["title"] == "Who saw it?"


def test_headline_with_js_syntax_stays_literal(tmp_path):
    out = _run_node(tmp_path, _middleware_for(tmp_path, "Odd `tick` ${1+1} \\ <b>"))
    assert out["title"] == "Odd `tick` ${1+1} \\ &lt;b>"


def test_script_json_has_no_angle_bracket_in_any_case(tmp_path):
    cfg = config()
    cfg["intro"] = "Ends </SCRIPT><script>x</Script > here"
    page = lpr.render_page(TEMPLATE_HTML, cfg, lpr.parse_export(write_export(tmp_path / "e.xlsx")))
    assert page.lower().count("</script") == TEMPLATE_HTML.lower().count("</script")
    assert "<" not in lpr.script_json({"a": "<b>"})
    assert json.loads(lpr.script_json({"a": "</SCRIPT>"})) == {"a": "</SCRIPT>"}


@pytest.mark.parametrize("field", ["Post Date", "Post Publish Time", "Post URL"])
def test_empty_post_field_fails(tmp_path, field):
    rows = [[field, ""] if r[0] == field else r for r in ROWS]
    with pytest.raises(lpr.ReadoutError, match=field):
        lpr.parse_export(write_export(tmp_path / "e.xlsx", rows))


@pytest.mark.parametrize("row", [["Industry", "", "20%"], ["", "Software Development", "20%"], ["Industry", "Software Development", ""]])
def test_demographic_row_with_empty_cell_fails(tmp_path, row):
    rows = [row if r == ["Industry", "Software Development", "20%"] else r for r in ROWS]
    with pytest.raises(lpr.ReadoutError, match="three non-empty cells"):
        lpr.parse_export(write_export(tmp_path / "e.xlsx", rows))


@pytest.mark.parametrize("name", ["client's-site", "Site", "my site", "-site", "a\\b"])
def test_vercel_folder_name_must_be_safe(tmp_path, name):
    with pytest.raises(lpr.ReadoutError, match="folder name"):
        lpr.write_vercel_dir(tmp_path / name, "<title>t</title><style></style><p>x</p>", config(), "__COOKIE_SALT__")


def test_read_rows_orders_cells_by_column_letter(tmp_path):
    path = write_raw_sheet(tmp_path / "e.xlsx", [
        (1, '<c r="B1"><v>2800</v></c><c r="A1" t="inlineStr"><is><t>Impressions</t></is></c>'),
        (2, '<c r="AA2"><v>3</v></c><c r="B2"><v>2</v></c><c r="A2"><v>1</v></c>'),
    ])
    assert lpr.read_rows(path) == [["Impressions", "2800"], ["1", "2"] + [""] * 24 + ["3"]]


def test_cell_without_reference_fails(tmp_path):
    path = write_raw_sheet(tmp_path / "e.xlsx", [(1, '<c><v>1</v></c>')])
    with pytest.raises(lpr.ReadoutError, match="reference"):
        lpr.read_rows(path)


@pytest.mark.parametrize("sheet", ["<worksheet", f'<worksheet xmlns="{NS}"><sheetData><row r="1"><c r="A1" t="s"><v>5</v></c></row></sheetData></worksheet>'])
def test_broken_sheet_is_an_error_not_a_traceback(tmp_path, capsys, sheet):
    path = tmp_path / "e.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}"><si><t>x</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    assert lpr.main(["data", str(path)]) == 1
    assert "Error:" in capsys.readouterr().err


def test_read_rows_keeps_a_missing_cell_in_its_column(tmp_path):
    path = write_raw_sheet(tmp_path / "e.xlsx", [
        (1, '<c r="A1"><v>1</v></c><c r="C1"><v>3</v></c>'),
        (2, '<c r="B2"><v>2</v></c>'),
    ])
    assert lpr.read_rows(path) == [["1", "", "3"], ["", "2"]]


def test_read_rows_drops_trailing_empty_cells(tmp_path):
    path = write_raw_sheet(tmp_path / "e.xlsx", [
        (1, '<c r="A1" t="inlineStr"><is><t>Impressions</t></is></c><c r="B1"><v>2000</v></c><c r="C1"/><c r="D1"><v></v></c>'),
        (2, '<c r="A2"/>'),
    ])
    assert lpr.read_rows(path) == [["Impressions", "2000"], []]


def test_more_profile_viewers_than_members_reached_fails(tmp_path):
    rows = [["Profile viewers from this post", "1201"] if r[0] == "Profile viewers from this post" else r for r in ROWS]
    with pytest.raises(lpr.ReadoutError, match="profile viewers"):
        lpr.parse_export(write_export(tmp_path / "e.xlsx", rows))
    rows = [["Profile viewers from this post", "1200"] if r[0] == "Profile viewers from this post" else r for r in ROWS]
    assert lpr.parse_export(write_export(tmp_path / "f.xlsx", rows))["metrics"]["profile_viewers"] == 1200


def _with_raw_demographic_row(path, cells_xml):
    """The fixture export with Industry's row replaced by hand-written cell XML (inline strings)."""
    write_export(path)
    with zipfile.ZipFile(path) as z:
        sst, sheet = z.read("xl/sharedStrings.xml").decode(), z.read("xl/worksheets/sheet1.xml").decode()
    r = next(i for i, row in enumerate(ROWS, start=1) if row[0] == "Industry")
    sheet = re.sub(rf'<row r="{r}">.*?</row>', f'<row r="{r}">{cells_xml.format(r=r)}</row>', sheet)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


@pytest.mark.parametrize("cells", [
    '<c r="A{r}" t="inlineStr"><is><t>Industry</t></is></c><c r="C{r}" t="inlineStr"><is><t>20%</t></is></c>',
    '<c r="B{r}" t="inlineStr"><is><t>Industry</t></is></c><c r="C{r}" t="inlineStr"><is><t>Software Development</t></is></c><c r="D{r}" t="inlineStr"><is><t>20%</t></is></c>',
])
def test_demographic_row_with_a_missing_cell_fails(tmp_path, cells):
    with pytest.raises(lpr.ReadoutError, match="three non-empty cells"):
        lpr.parse_export(_with_raw_demographic_row(tmp_path / "e.xlsx", cells))


@pytest.mark.parametrize("mutate, message", [
    (lambda c: c["buyers"].pop("seniority"), r"config\.buyers\.seniority is missing"),
    (lambda c: c["buyer_names"].pop("job_title"), r"config\.buyer_names\.job_title is missing"),
    (lambda c: c.update(excerpt="one paragraph"), r"config\.excerpt should be list, not str"),
    (lambda c: c["buyers"].update(company_size="51-200 employees"), r"config\.buyers\.company_size should be list"),
])
def test_load_config_checks_nested_keys_and_types(tmp_path, mutate, message):
    cfg = config()
    mutate(cfg)
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(lpr.ReadoutError, match=message):
        lpr.load_config(p, TEMPLATE_HTML)


def test_load_config_accepts_the_template_config_and_extra_keys(tmp_path):
    cfg = config()
    cfg["extra"] = 1
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    assert lpr.load_config(p, TEMPLATE_HTML) == cfg
