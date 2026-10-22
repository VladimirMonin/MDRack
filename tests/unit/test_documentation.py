"""Real CLI checks on explicit disposable documentation, never application imports."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "scripts/check_documentation.py"
INDEX = "docs/proposals/native-media/README.md"
BEGIN = "<!-- native-media-index:start -->"
END = "<!-- native-media-index:end -->"


def put(root: Path, path: str, text: str) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def cli(root: Path, mode: str = "--check") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", str(CHECKER), "--project", str(root), mode],
        cwd=root.parent, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "проект path"
    paths = []
    for i in range(1, 5):
        path = f"docs/proposals/native-media/{i:02d}-note.md"
        paths.append(path)
        put(root, path, f'''---
id: "MDRACK-NATIVE-{i:02d}"
title: "Note {i}"
kind: "proposal"
status: "proposed"
created: "2026-10-20"
updated: "2026-10-21"
areas: ["search"]
summary: "A bounded proposal."
---
# Result
BODY_SENTINEL
''')
    put(root, INDEX, f"# Manual title\nMANUAL_KEEP\n{BEGIN}\nstale\n{END}\nTAIL_KEEP\n")
    put(root, "AGENTS.md", "# Router\n`instructions/local.instructions.md`\n")
    put(root, "instructions/local.instructions.md",
        '---\napplyTo: "**"\nname: "LOCAL.Native"\ndescription: "When editing docs"\n---\n# Rules\n')
    put(root, "docs/documentation-scope.json", json.dumps({
        "authoring": paths, "native": [INDEX, "AGENTS.md", "instructions/local.instructions.md"],
    }))
    return root


def test_update_check_and_noop_preserve_manual_bytes_and_dates(project: Path) -> None:
    originals = {p: p.read_bytes() for p in project.rglob("*") if p.is_file() and p != project / INDEX}
    result = cli(project, "--update-index")
    assert result.returncode == 0, result.stdout + result.stderr
    index = project / INDEX
    body = index.read_text(encoding="utf-8")
    assert "MANUAL_KEEP" in body and "TAIL_KEEP" in body
    assert "BODY_SENTINEL" not in body
    for i in range(1, 5):
        assert f"[Note {i}]({i:02d}-note.md)" in body
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    assert cli(project).returncode == 0
    assert cli(project, "--update-index").returncode == 0
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    assert all(p.read_bytes() == data for p, data in originals.items())


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("replacement", [
    ('status: "proposed"', 'status: "PASS"'),
    ('id: "MDRACK-NATIVE-01"', 'id: "unregistered"'),
    ('title: "Note 1"', 'title: "Note 1"\ntitle: "duplicate"'),
    ('created: "2026-10-20"', 'created: 2026-10-20'),
    ('areas: ["search"]', 'areas: ["search", "{{unknown}}"]'),
    ('summary: "A bounded proposal."', 'summary: &alias "value"\ncopy: *alias'),
    ('summary: "A bounded proposal."', 'summary: !!python/object/apply:builtins.print [unsafe]'),
    ('summary: "A bounded proposal."', 'summary: "safe"\n<<: {x: y}'),
])
def test_invalid_metadata_never_writes(project: Path, replacement: tuple[str, str]) -> None:
    p = project / "docs/proposals/native-media/01-note.md"
    p.write_text(p.read_text(encoding="utf-8").replace(*replacement), encoding="utf-8")
    before = snapshot(project)
    for mode in ("--check", "--update-index"):
        result = cli(project, mode)
        assert result.returncode == 1, result.stdout + result.stderr
        assert snapshot(project) == before


def test_duplicate_identity(project: Path) -> None:
    p = project / "docs/proposals/native-media/02-note.md"
    p.write_text(p.read_text(encoding="utf-8").replace("MDRACK-NATIVE-02", "MDRACK-NATIVE-01"), encoding="utf-8")
    assert cli(project, "--update-index").returncode == 1


@pytest.mark.parametrize("bad", [
    {}, {"authoring": [], "native": [INDEX]},
    {"authoring": ["../escape.md"], "native": [INDEX]},
    {"authoring": ["missing.md"], "native": [INDEX]},
    {"authoring": [INDEX], "native": [INDEX]},
    {"authoring": ["docs"], "native": [INDEX]},
    {"authoring": ["C:/absolute.md"], "native": [INDEX]},
])
def test_scope_errors_exit_two_without_writes(project: Path, bad: dict) -> None:
    put(project, "docs/documentation-scope.json", json.dumps(bad))
    before = snapshot(project)
    assert cli(project, "--update-index").returncode == 2
    assert snapshot(project) == before


@pytest.mark.parametrize("markers", [BEGIN, END, BEGIN + BEGIN + END, END + BEGIN])
def test_bad_markers_refuse_all_writes(project: Path, markers: str) -> None:
    put(project, INDEX, "MANUAL\n" + markers)
    before = snapshot(project)
    assert cli(project, "--update-index").returncode == 1
    assert snapshot(project) == before


def test_stale_check_is_readonly_and_unselected_body_not_read(project: Path) -> None:
    p = project / "docs/proposals/native-media/01-note.md"
    with p.open("a", encoding="utf-8") as f:
        f.write('\n[`outside`](../../../unselected.md#unknown)\n')
    outside = project / "unselected.md"
    outside.write_bytes(b"UNSELECTED_SENTINEL\xff")  # Invalid UTF-8 would fail if parsed.
    before = snapshot(project)
    result = cli(project)
    assert result.returncode == 1
    assert "index.stale" in result.stdout
    assert snapshot(project) == before
    result = cli(project, "--update-index")
    assert result.returncode == 0, result.stdout
    assert "link.anchor_scope" in result.stdout and "not_checked" in result.stdout
    assert "UNSELECTED_SENTINEL" not in (project / INDEX).read_text(encoding="utf-8")


@pytest.mark.parametrize("link", ["[bad](missing.md)", "[bad](02-note.md#absent)", "[bad][undefined]"])
def test_broken_selected_links_block_update(project: Path, link: str) -> None:
    p = project / "docs/proposals/native-media/01-note.md"
    with p.open("a", encoding="utf-8") as f:
        f.write("\n" + link)
    before = snapshot(project)
    assert cli(project, "--update-index").returncode == 1
    assert snapshot(project) == before


def test_native_routes_and_code_literals(project: Path) -> None:
    p = project / "docs/proposals/native-media/01-note.md"
    with p.open("a", encoding="utf-8") as f:
        f.write('\n`[fake](missing.md)`\n```md\n[bad][missing]\n```\n[real](02-note.md#result)\n')
    assert cli(project, "--update-index").returncode == 0
    (project / "instructions/local.instructions.md").unlink()
    result = cli(project)
    assert result.returncode == 2  # Missing explicit corpus input, before route checking.
    scope = json.loads((project / "docs/documentation-scope.json").read_text(encoding="utf-8"))
    scope["native"].remove("instructions/local.instructions.md")
    put(project, "docs/documentation-scope.json", json.dumps(scope))
    result = cli(project)
    assert result.returncode == 1 and "route.missing" in result.stdout


def test_symlink_source_is_input_error(project: Path, tmp_path: Path) -> None:
    target = put(tmp_path, "outside.md", "PRIVATE_SENTINEL")
    p = project / "docs/proposals/native-media/01-note.md"
    p.unlink()
    try:
        p.symlink_to(target)
    except OSError:
        pytest.skip("Native symlink creation unavailable without Windows privilege")
    assert cli(project, "--update-index").returncode == 2


def test_title_is_safe_text_not_metadata_html_or_destination(project: Path) -> None:
    p = project / "docs/proposals/native-media/01-note.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        'title: "Note 1"', 'title: "<img src=x> | [evil](https://bad.invalid)"'), encoding="utf-8")
    assert cli(project, "--update-index").returncode == 0
    text = (project / INDEX).read_text(encoding="utf-8")
    assert "<img" not in text and "&#124;" in text
    assert "&lt;img" in text and "](01-note.md)" in text
    assert cli(project).returncode == 0


@pytest.mark.parametrize("stale_link", ["retired.md", "01-note.md#retired"])
def test_stale_generated_link_is_repairable(project: Path, stale_link: str) -> None:
    index = project / INDEX
    prefix = b"# Manual title\r\nMANUAL_KEEP\r\n[manual](01-note.md#result)\r\n" + BEGIN.encode()
    suffix = END.encode() + b"\r\nTAIL_KEEP\r\n"
    index.write_bytes(prefix + f"\r\n| [Retired title]({stale_link}) | proposal | proposed |\r\n".encode() + suffix)
    before = snapshot(project)
    result = cli(project)
    assert result.returncode == 1, result.stdout + result.stderr
    assert snapshot(project) == before
    result = cli(project, "--update-index")
    assert result.returncode == 0, result.stdout + result.stderr
    data = index.read_bytes()
    assert data.startswith(prefix) and data.endswith(suffix)
    block = data.split(BEGIN.encode(), 1)[1].split(END.encode(), 1)[0].decode()
    assert "Retired title" not in block
    assert block.count("](0") == 4
    for i in range(1, 5):
        assert f"[Note {i}]({i:02d}-note.md)" in block
    assert all((project / p).read_bytes() == state[0] and (project / p).stat().st_mtime_ns == state[1]
               for p, state in before.items() if p != INDEX)
    after = snapshot(project)
    assert cli(project).returncode == 0
    assert cli(project, "--update-index").returncode == 0
    assert snapshot(project) == after


def test_local_link_syntax_in_generated_title_remains_text(project: Path) -> None:
    import html
    import re

    p = project / "docs/proposals/native-media/01-note.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        'title: "Note 1"', 'title: "See [draft](missing.md)"'), encoding="utf-8")
    before = snapshot(project)
    result = cli(project, "--update-index")
    assert result.returncode == 0, result.stdout + result.stderr
    data = (project / INDEX).read_bytes()
    assert data.split(BEGIN.encode())[0] == before[INDEX][0].split(BEGIN.encode())[0]
    assert data.split(END.encode())[1] == before[INDEX][0].split(END.encode())[1]
    # Observe all generated destinations, not merely a substring of the selected link.
    links = re.findall(r"^\| \[(.*)\]\(([^)]+)\) \|", data.decode(), re.M)
    assert [destination for _, destination in links] == [f"{i:02d}-note.md" for i in range(1, 5)]
    assert html.unescape(links[0][0].replace("\\[", "[").replace("\\]", "]")) == "See [draft](missing.md)"
    assert all((project / path).read_bytes() == state[0] and (project / path).stat().st_mtime_ns == state[1]
               for path, state in before.items() if path != INDEX)
    after = snapshot(project)
    result = cli(project)
    assert result.returncode == 0, result.stdout + result.stderr
    result = cli(project, "--update-index")
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(project) == after


@pytest.mark.parametrize("invalid", ["manual-link", "missing-marker", "duplicate-marker", "metadata"])
def test_stale_block_does_not_bypass_manual_or_input_validation(project: Path, invalid: str) -> None:
    index = project / INDEX
    index.write_text(f"MANUAL_KEEP\n{BEGIN}\n| [Retired title](retired.md) | proposal | proposed |\n"
                     f"{END}\nTAIL_KEEP\n", encoding="utf-8")
    if invalid == "manual-link":
        with index.open("a", encoding="utf-8") as f:
            f.write("[bad](missing.md)\n")
    elif invalid == "metadata":
        p = project / "docs/proposals/native-media/01-note.md"
        p.write_text(p.read_text(encoding="utf-8").replace('status: "proposed"', 'status: "PASS"'),
                     encoding="utf-8")
    else:
        text = index.read_text(encoding="utf-8")
        index.write_text(text.replace(BEGIN, "" if invalid == "missing-marker" else BEGIN + BEGIN),
                         encoding="utf-8")
    before = snapshot(project)
    result = cli(project, "--update-index")
    assert result.returncode == 1, result.stdout + result.stderr
    expected = {"manual-link": "link.missing", "metadata": "metadata.status"}.get(invalid, "index.markers")
    assert expected in result.stdout
    assert snapshot(project) == before


def current_manual_index(project: Path, bom: bytes = b"\xef\xbb\xbf") -> Path:
    assert cli(project, "--update-index").returncode == 0
    index = project / INDEX
    data = index.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    index.write_bytes(bom + data + b"\r\n[manual self](#manual-title)\r\n")
    return index


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"], ids=["plain", "bom"])
def test_manual_first_heading_encoding_noop(project: Path, bom: bytes) -> None:
    current_manual_index(project, bom)
    before = snapshot(project)
    for mode in ("--check", "--update-index", "--check", "--update-index"):
        result = cli(project, mode)
        assert result.returncode == 0, result.stdout + result.stderr
        assert snapshot(project) == before


def test_bom_stale_block_preserves_manual_bytes_and_anchor(project: Path) -> None:
    index = current_manual_index(project)
    original = index.read_bytes()
    prefix, rest = original.split(BEGIN.encode(), 1)
    _, suffix = rest.split(END.encode(), 1)
    index.write_bytes(prefix + BEGIN.encode() + b"\r\n| [Retired title](retired.md) | proposal | proposed |\r\n"
                      + END.encode() + suffix)
    before = snapshot(project)
    result = cli(project)
    assert result.returncode == 1, result.stdout + result.stderr
    assert snapshot(project) == before
    result = cli(project, "--update-index")
    assert result.returncode == 0, result.stdout + result.stderr
    assert index.read_bytes() == original  # BOM, exact manual bytes and current four rows restored.
    assert all((project / p).read_bytes() == state[0] and (project / p).stat().st_mtime_ns == state[1]
               for p, state in before.items() if p != INDEX)
    after = snapshot(project)
    for mode in ("--check", "--update-index"):
        result = cli(project, mode)
        assert result.returncode == 0, result.stdout + result.stderr
        assert snapshot(project) == after


@pytest.mark.parametrize("invalid", ["anchor", "link", "metadata", "missing-marker", "duplicate-marker"])
def test_bom_manual_or_input_errors_refuse_without_writes(project: Path, invalid: str) -> None:
    index = current_manual_index(project)
    data = index.read_bytes()
    if invalid == "anchor":
        data = data.replace(b"#manual-title)", b"#missing-heading)")
    elif invalid == "link":
        data += b"[bad](missing.md)\r\n"
    elif invalid == "metadata":
        p = project / "docs/proposals/native-media/01-note.md"
        p.write_bytes(p.read_bytes().replace(b'status: "proposed"', b'status: "PASS"'))
    else:
        data = data.replace(BEGIN.encode(), b"" if invalid == "missing-marker" else BEGIN.encode() * 2)
    index.write_bytes(data)
    before = snapshot(project)
    expected = {"anchor": "link.anchor", "link": "link.missing", "metadata": "metadata.status"}.get(
        invalid, "index.markers")
    for mode in ("--check", "--update-index"):
        result = cli(project, mode)
        assert result.returncode == 1, result.stdout + result.stderr
        assert expected in result.stdout
        assert snapshot(project) == before


def test_usage_requires_exactly_one_mode(project: Path) -> None:
    r = subprocess.run([sys.executable, "-I", "-B", str(CHECKER), "--project", str(project)],
                       capture_output=True, timeout=30)
    assert r.returncode == 2


def test_author_maintenance_route_updates_existing_owner_then_noop(project: Path) -> None:
    assert cli(project, "--update-index").returncode == 0
    owner = project / "docs/proposals/native-media/01-note.md"
    owner.write_text(owner.read_text(encoding="utf-8").replace("Note 1", "Revised contract"), encoding="utf-8")
    assert cli(project).returncode == 1
    assert cli(project, "--update-index").returncode == 0
    assert cli(project).returncode == 0
    before = snapshot(project)
    assert cli(project, "--update-index").returncode == 0
    assert snapshot(project) == before
