#!/usr/bin/env python3
"""Bounded documentation validation and one navigation block updater.

YAML/path/Markdown helpers adapted from Project Foundation Kit 0.2.0,
add0238e31b85588ef9c9c4af8e973f02170b40e (MIT).
Full copyright/permission notices: docs/provenance/project-foundation-kit.md.
No application imports, network, recursive link-body reads or architecture inference.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import yaml

INDEX = "docs/proposals/native-media/README.md"
BEGIN = "<!-- native-media-index:start -->"
END = "<!-- native-media-index:end -->"
STATUSES = {"draft", "proposed", "accepted", "superseded", "archived"}
KINDS = {"philosophy", "proposal", "plan"}
TOKEN = re.compile(r"\{\{[^{}\n]+\}\}")

class InputError(ValueError):
    """Invalid boundary, unreadable input or invalid corpus, never an empty success."""


class UniqueSafeLoader(yaml.SafeLoader):
    """Safe YAML without aliases/anchors; repeated mappings never use last-key wins."""

    def compose_node(self, parent, index):
        event = self.peek_event()
        if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None):
            raise ValueError("YAML anchors and aliases are not part of this schema")
        return super().compose_node(parent, index)


def unique_mapping(loader, node, deep=False):
    if any(key.tag == "tag:yaml.org,2002:merge" for key, _ in node.value):
        raise ValueError("YAML merge keys are not part of this schema")
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise ValueError(f"Duplicate YAML key: {key!r}")
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as error:
            raise ValueError("YAML mapping key must be a scalar") from error
    return result


UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def safe_yaml(text):
    return yaml.load(text, Loader=UniqueSafeLoader)


def bounded(root, relative):
    """Resolve without permitting absolute/traversal/symlink access outside scope."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise InputError("Use a nonempty root-relative forward-slash path")
    path = Path(relative)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", relative) or ".." in path.parts:
        raise InputError("Absolute paths and parent traversal are not corpus entries")
    target = root / path
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise InputError("Symlinks are not selected inputs")
    if not target.resolve().is_relative_to(root):
        raise InputError("Path escapes selected root")
    return target


def read_text(path):
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise InputError("Selected text must exist and be at most 1 MiB")
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise InputError("Selected text is unreadable UTF-8") from error


def frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("Initial YAML frontmatter is required")
    end = next((i for i, line in enumerate(lines[1:], 1) if line == "---"), None)
    if end is None:
        raise ValueError("Unclosed frontmatter")
    value = safe_yaml("\n".join(lines[1:end]))
    if not isinstance(value, dict):
        raise ValueError("Frontmatter must be a mapping")
    return value, "\n".join(lines[end + 1:])


def unfenced(text):
    """Remove common fenced blocks before role-specific inline processing."""
    result, fence = [], None
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if match:
            mark = match.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is None:
            result.append(line)
    return "\n".join(result)


def code_spans(text):
    """Pair complete, equally long backtick runs; unmatched runs stay literal.

    This bounded subset supports multiline spans, not all CommonMark constructs.
    Return offsets so link labels keep their original reference-key identity.
    """
    runs = list(re.finditer(r"`+", text))
    following, next_equal = {}, {}
    for index in range(len(runs) - 1, -1, -1):
        size = len(runs[index][0])
        if size in next_equal:
            following[index] = next_equal[size]
        next_equal[size] = index
    spans, index = [], 0
    while index < len(runs):
        end = following.get(index)
        if end is None:
            index += 1
        else:
            spans.append((runs[index].start(), runs[end].end(), len(runs[index][0])))
            index = end + 1
    return spans


def mask_code(text, spans):
    chars = list(text)
    for start, end, _ in spans:
        chars[start:end] = ["\n" if c == "\n" else " " for c in text[start:end]]
    return "".join(chars)


def visible(text):
    """Literal-free view for syntax that cannot contain inline code (IDs/wiki)."""
    text = unfenced(text)
    return mask_code(text, code_spans(text))


def local_links(text):
    text = unfenced(text)
    syntax = mask_code(text, code_spans(text))
    definitions = {}
    for match in re.finditer(r"(?m)^[ \t]{0,3}\[([^\]\n]+)\]:\s*(?:<([^>]+)>|(\S+))", text):
        opener = match.start() + match[0].index("[")
        if syntax[opener] == "[" and not match[1].startswith("^"):
            definitions[match[1].casefold()] = match[2] or match[3]
    inline = r"!?\[[^\]\n]+\]\(\s*(?:<([^>\n]+)>|([^\s()]+))(?:\s+\"[^\"]*\")?\s*\)"
    targets = [m[1] or m[2] for m in re.finditer(inline, text) if syntax[m.start()] != " "]
    missing = []
    for match in re.finditer(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]", text):
        if syntax[match.start()] == " ":
            continue
        key = (match[2] or match[1]).casefold()
        if key in definitions:
            targets.append(definitions[key])
        else:
            missing.append(key)
    return targets, missing


def anchors(text):
    text = unfenced(text)
    syntax = mask_code(text, code_spans(text))
    result = set(re.findall(r"\bid=[\"']([^\"']+)[\"']", syntax))
    used = {}
    for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", text):
        if syntax[match.start()] != "#":
            continue
        heading = match[1]
        for start, end, size in reversed(code_spans(heading)):
            heading = heading[:start] + heading[start + size:end - size] + heading[end:]
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = used.get(slug, 0)
        used[slug] = count + 1
        result.add(slug + (f"-{count}" if count else ""))
    return result


class Checker:
    def __init__(self, root):
        self.root = root
        self.findings, self.docs, self.bodies = [], {}, {}
        self.external = 0

    def add(self, path, code, message, severity="error"):
        self.findings.append(dict(path=path, code=code, message=message, severity=severity))

    def load(self):
        scope = json.loads(read_text(bounded(self.root, "docs/documentation-scope.json")),
                           object_pairs_hook=json_mapping)
        if not isinstance(scope, dict) or set(scope) != {"authoring", "native"}:
            raise InputError("Scope requires authoring and native lists only")
        selected = {}
        for group, paths in scope.items():
            if not isinstance(paths, list) or not paths:
                raise InputError("Both scope lists must be nonempty")
            for path in paths:
                file = bounded(self.root, path)
                if path in selected or file.relative_to(self.root).as_posix() != path:
                    raise InputError("Duplicate, conflicting or noncanonical scope path")
                if file.suffix != ".md" or path.startswith("skills/"):
                    raise InputError("Select explicit Markdown documentation only, not skills")
                selected[path] = group
        if selected.get(INDEX) != "native":
            raise InputError("Fixed navigation target must be selected native")
        identities = set()
        for path, group in sorted(selected.items()):
            text = read_text(bounded(self.root, path))
            try:
                if group == "authoring":
                    data, body = frontmatter(text)
                    self.metadata(path, data)
                    identity = data.get("id")
                    if isinstance(identity, str):
                        if identity in identities:
                            self.add(path, "id.duplicate", "Duplicate document ID")
                        identities.add(identity)
                    self.docs[path] = data
                elif path.endswith(".instructions.md"):
                    data, body = frontmatter(text)
                    for key in ("applyTo", "name", "description"):
                        if not isinstance(data.get(key), str) or not data[key].strip():
                            self.add(path, "instruction.field", f"Native instruction requires {key}")
                else:
                    body = text
                self.bodies[path] = body
            except (ValueError, yaml.YAMLError) as error:
                self.add(path, "yaml.invalid", str(error))
        if len(self.docs) != 4:
            self.add("docs/documentation-scope.json", "bundle.count", "Select exactly four authoring documents")
        for path, body in self.bodies.items():
            if Path(path).name == "AGENTS.md":
                for route in re.findall(r"`(instructions/[^`\n]+\.instructions\.md)`", unfenced(body)):
                    if not bounded(self.root, route).is_file():
                        self.add(path, "route.missing", f"Missing instruction route: {route}")

    def metadata(self, path, data):
        for key in ("id", "title", "kind", "status", "created", "updated", "summary"):
            value = data.get(key)
            if type(value) is not str or not value.strip() or TOKEN.search(value):
                self.add(path, "metadata.type", f"Required nonempty string: {key}")
        identity = data.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"MDRACK-NATIVE-0[1-4]", identity):
            self.add(path, "metadata.id", "Expected canonical MDRACK-NATIVE-01 through 04")
        areas = data.get("areas")
        if not isinstance(areas, list) or not areas or not all(
            type(v) is str and v.strip() and not TOKEN.search(v) for v in areas
        ):
            self.add(path, "metadata.type", "areas requires nonempty resolved strings")
        for key, allowed in (("kind", KINDS), ("status", STATUSES)):
            if not isinstance(data.get(key), str) or data[key] not in allowed:
                self.add(path, "metadata." + key, "Unknown vocabulary value")
        for key in ("created", "updated"):
            try:
                value = data.get(key)
                if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except (ValueError, TypeError):
                self.add(path, "metadata.date", f"{key} requires a quoted ISO date")

    def links(self, path, body):
        targets, missing = local_links(body)
        for key in missing:
            self.add(path, "link.reference", f"Missing reference: {key}")
        for value in targets:
            parts = urlsplit(value)
            if parts.scheme in {"http", "https", "mailto", "tel", "data"}:
                self.external += 1
                continue
            relative = unquote(parts.path)
            if parts.scheme or parts.netloc or relative.startswith(("/", "\\")) or "\\" in relative:
                self.add(path, "link.boundary", "Link must stay within root")
                continue
            candidate = (self.root / path).parent if relative else self.root / path
            unsafe = False
            for part in Path(relative).parts:
                candidate = candidate.parent if part == ".." else candidate / part
                if not candidate.is_relative_to(self.root) or candidate.is_symlink():
                    unsafe = True
                    break
            if unsafe:
                self.add(path, "link.boundary", "Link escapes root or traverses symlink")
                continue
            name = candidate.relative_to(self.root).as_posix()
            if not candidate.exists():
                self.add(path, "link.missing", f"Missing target: {name}")
            elif parts.fragment:
                if name not in self.bodies:
                    self.add(path, "link.anchor_scope", f"Unselected target body: {name}", "not_checked")
                elif unquote(parts.fragment) not in anchors(self.bodies[name]):
                    self.add(path, "link.anchor", f"Missing selected anchor: {name}#{parts.fragment}")
        if "[[" in visible(body):
            self.add(path, "link.wiki", "Wikilinks outside supported subset", "not_checked")

    def index(self, update):
        target = bounded(self.root, INDEX)
        original = target.read_bytes()
        text = original.decode("utf-8")
        if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) >= text.index(END):
            self.add(INDEX, "index.markers", "Require exactly one ordered marker pair")
            return
        rows = ["| Document | Kind | Status |", "| --- | --- | --- |"]
        for path, data in sorted(self.docs.items()):
            # Paths come only from the selected corpus, never metadata or a URL.
            if not path.startswith("docs/proposals/native-media/"):
                self.add(path, "index.path", "Authoring belongs to the canonical bundle")
                continue
            link = quote(path.removeprefix("docs/proposals/native-media/"), safe="/-._")
            rows.append(f"| [{cell(data.get('title', ''))}]({link}) | "
                        f"{cell(data.get('kind', ''))} | {cell(data.get('status', ''))} |")
        start = text.index(BEGIN) + len(BEGIN)
        end = text.index(END)
        newline = "\r\n" if b"\r\n" in original else "\n"
        block = newline + newline.join(rows) + newline
        replacement = (text[:start] + block + text[end:]).encode("utf-8")
        # Validate the complete prospective document, not obsolete content being replaced.
        # Manual README content and all other selected bodies remain checked normally.
        if update:
            self.bodies[INDEX] = replacement.decode("utf-8-sig")
        for path, body in self.bodies.items():
            self.links(path, body)
        if replacement == original:
            return
        if not update:
            self.add(INDEX, "index.stale", "Run --update-index after author-maintained changes")
        elif not any(f["severity"] == "error" for f in self.findings):
            target.write_bytes(replacement)


def cell(value):
    text = html.escape(str(value), quote=True)
    # Entities keep brackets literal inside the link label for both Markdown and
    # our bounded link parser, without interpreting title text as nested links.
    text = text.replace("[", "&#91;").replace("]", "&#93;")
    for char in ("\\", "`", "*", "_"):
        text = text.replace(char, "\\" + char)
    return text.replace("|", "&#124;").replace("\r", " ").replace("\n", " ")


def json_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("Duplicate JSON scope key")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--update-index", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.project.is_symlink():
            raise InputError("Project root must not be a symlink")
        checker = Checker(args.project.resolve())
        checker.load()
        checker.index(args.update_index)
        report = dict(exit_code=int(any(f["severity"] == "error" for f in checker.findings)),
                      checked=len(checker.bodies), findings=checker.findings,
                      external_links_not_checked=checker.external,
                      limitations=["Explicit corpus only; unselected bodies never read; external URLs not requested",
                                   "Simple inline/reference links, ATX headings and HTML IDs only; not full GFM",
                                   "Mermaid rendering not_checked: no renderer is invoked",
                                   "Structure/navigation are not architecture truth or independent acceptance"])
    except (OSError, ValueError, yaml.YAMLError) as error:
        report = dict(exit_code=2, findings=[dict(code="input.invalid", message=str(error))])
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

