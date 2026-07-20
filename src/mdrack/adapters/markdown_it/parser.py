"""markdown-it-py adapter producing MDRack's stable Document IR."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt
from markdown_it.rules_inline.html_inline import html_inline as markdown_html_inline_rule
from markdown_it.rules_inline.image import image as markdown_image_rule
from markdown_it.rules_inline.state_inline import StateInline
from markdown_it.token import Token

from mdrack.application.metadata_normalization import (
    MetadataInvalidPolicy,
    MetadataNormalizationError,
    normalize_metadata,
)
from mdrack.domain.blocks import BlockType, JSONValue, SourceBlock, SourceSpan
from mdrack.domain.documents import Document, MetadataDiagnostic
from mdrack.domain.identifiers import content_fingerprint, logical_id

_OBSIDIAN_EMBED = re.compile(r"^!\[\[([^\]]+)\]\]$", re.DOTALL)
_OBSIDIAN_EMBED_FINDER = re.compile(r"!\[\[[^\]]+\]\]", re.DOTALL)
_NUMERIC_OR_DIMENSION_ALIAS = re.compile(r"^\d+(?:\s*[x×]\s*\d+)?$", re.IGNORECASE)
_CALLOUT = re.compile(r"^>\s*\[!([A-Za-z0-9_-]+)\](?:[+-])?(?:\s+([^\n]+))?", re.MULTILINE)
_HTML_RAW_TEXT_TAG = re.compile(
    r"^<(?P<closing>/)?(?P<name>script|style|pre|textarea|title|xmp|iframe|noembed|noframes|plaintext)"
    r"(?=[\s/>])",
    re.IGNORECASE | re.ASCII,
)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate object keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing metadata",
                node.start_mark,
                "duplicate metadata key",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _frontmatter(content: str) -> tuple[object, int | None, tuple[MetadataDiagnostic, ...]]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, None, ()
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        return {}, None, ()
    try:
        loaded = yaml.load("\n".join(lines[1:closing]), Loader=_UniqueKeySafeLoader)
    except (TypeError, yaml.YAMLError):
        return {}, closing, (MetadataDiagnostic("METADATA_PARSE_FAILED"),)
    return ({} if loaded is None else loaded), closing, ()


def _line_offsets(content: str) -> list[int]:
    offsets = [0]
    for line in content.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    if offsets[-1] < len(content):
        offsets.append(len(content))
    return offsets


def _normalized_offset_map(content: str) -> list[int]:
    """Map markdown-it normalized character offsets back to source offsets."""
    offsets = [0]
    source_offset = 0
    while source_offset < len(content):
        source_offset += 2 if content.startswith("\r\n", source_offset) else 1
        offsets.append(source_offset)
    return offsets


def _source_slice(content: str, offsets: list[int], start: int, end: int) -> tuple[str, SourceSpan]:
    start_offset = offsets[min(start, len(offsets) - 1)]
    end_offset = offsets[min(end, len(offsets) - 1)] if end < len(offsets) else len(content)
    raw = content[start_offset:end_offset].rstrip("\r\n")
    end_offset = start_offset + len(raw)
    return raw, SourceSpan(
        start_line=start + 1,
        end_line=max(start + 1, end),
        start_offset=start_offset,
        end_offset=end_offset,
    )


def _inline_text(tokens: list[Token], start_index: int) -> str | None:
    opener = tokens[start_index]
    end_index = start_index + 1
    depth = opener.nesting
    collected: list[str] = []
    while end_index < len(tokens) and depth > 0:
        token = tokens[end_index]
        if token.type == "inline" and token.content.strip():
            collected.append(token.content.strip())
        depth += token.nesting
        end_index += 1
    joined = "\n".join(collected).strip()
    return joined or None


def _heading_text(tokens: list[Token], index: int) -> str:
    if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
        return tokens[index + 1].content.strip()
    return ""


def _markdown_image_with_source_map(state: StateInline, silent: bool) -> bool:
    """Delegate CommonMark image parsing and retain its exact inline source range."""
    start = state.pos
    token_count = len(state.tokens)
    matched = markdown_image_rule(state, silent)
    if matched and not silent and len(state.tokens) > token_count:
        token = state.tokens[-1]
        if token.type == "image":
            token.meta["source_start"] = start
            token.meta["source_end"] = state.pos
    return matched


def _markdown_image_projection(token: Token) -> str:
    return token.content.strip()


def _html_inline_with_source_map(state: StateInline, silent: bool) -> bool:
    """Delegate HTML parsing and retain the token's exact inline source range."""
    start = state.pos
    token_count = len(state.tokens)
    matched = markdown_html_inline_rule(state, silent)
    if matched and not silent and len(state.tokens) > token_count:
        token = state.tokens[-1]
        if token.type == "html_inline":
            token.meta["source_start"] = start
            token.meta["source_end"] = state.pos
    return matched


def _html_image_tags(raw: str) -> list[tuple[int, int, str]]:
    """Return complete ``img`` tags, ignoring ``>`` inside quoted values."""
    tags: list[tuple[int, int, str]] = []
    search_from = 0
    while (start := raw.find("<", search_from)) >= 0:
        if start + 4 > len(raw) or not (
            raw[start + 1] in "iI"
            and raw[start + 2] in "mM"
            and raw[start + 3] in "gG"
        ):
            search_from = start + 1
            continue
        boundary = start + 4
        if boundary >= len(raw) or not (raw[boundary].isspace() or raw[boundary] in "/>"):
            search_from = boundary
            continue

        quote: str | None = None
        cursor = boundary
        while cursor < len(raw):
            char = raw[cursor]
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in {'"', "'"}:
                quote = char
            elif char == ">":
                end = cursor + 1
                tags.append((start, end, raw[start:end]))
                search_from = end
                break
            elif char == "<":
                search_from = boundary
                break
            cursor += 1
        else:
            search_from = boundary
    return tags


def _html_image_alt(tag: str) -> str | None:
    """Parse one complete ``img`` tag and return only its exact ``alt`` value."""
    if not tag.casefold().startswith("<img") or not tag.endswith(">"):
        return None
    attributes = tag[4:-1]

    alt: str | None = None
    cursor = 0
    while cursor < len(attributes):
        while cursor < len(attributes) and attributes[cursor].isspace():
            cursor += 1
        if cursor >= len(attributes):
            break
        if attributes[cursor] == "/" and not attributes[cursor + 1 :].strip():
            break

        name_start = cursor
        while (
            cursor < len(attributes)
            and not attributes[cursor].isspace()
            and attributes[cursor] not in {'"', "'", "=", "<", ">", "`", "/"}
        ):
            cursor += 1
        if cursor == name_start:
            return None
        name = attributes[name_start:cursor].casefold()

        while cursor < len(attributes) and attributes[cursor].isspace():
            cursor += 1
        if cursor >= len(attributes) or attributes[cursor] != "=":
            continue
        cursor += 1
        while cursor < len(attributes) and attributes[cursor].isspace():
            cursor += 1
        if cursor >= len(attributes):
            return None

        if attributes[cursor] in {'"', "'"}:
            quote = attributes[cursor]
            value_start = cursor + 1
            value_end = attributes.find(quote, value_start)
            if value_end < 0:
                return None
            value = attributes[value_start:value_end]
            cursor = value_end + 1
            if cursor < len(attributes) and not attributes[cursor].isspace():
                if attributes[cursor] == "/" and not attributes[cursor + 1 :].strip():
                    cursor = len(attributes)
                else:
                    return None
        else:
            value_start = cursor
            while cursor < len(attributes) and not attributes[cursor].isspace():
                if attributes[cursor] in {'"', "'", "=", "<", ">", "`"}:
                    return None
                cursor += 1
            if cursor == value_start:
                return None
            value = attributes[value_start:cursor]

        if name == "alt" and alt is None:
            alt = value
    return (alt or "").strip()


def _non_markdown_image_projection(raw: str) -> str | None:
    stripped = raw.strip()
    obsidian_embed = _OBSIDIAN_EMBED.fullmatch(stripped)
    if obsidian_embed:
        parts = obsidian_embed.group(1).split("|")
        if len(parts) < 2:
            return ""
        alias = parts[-1].strip()
        if not alias or _NUMERIC_OR_DIMENSION_ALIAS.fullmatch(alias):
            return ""
        return alias
    html_images = _html_image_tags(stripped)
    if len(html_images) == 1 and html_images[0][:2] == (0, len(stripped)):
        return _html_image_alt(html_images[0][2])
    return None


class MarkdownItParser:
    """CommonMark/GFM parser adapter with Obsidian block classification."""

    name = "markdown_it"
    version = version("markdown-it-py")

    def __init__(
        self,
        *,
        metadata_invalid_policy: MetadataInvalidPolicy = "warn_and_continue",
    ) -> None:
        self.metadata_invalid_policy: MetadataInvalidPolicy = metadata_invalid_policy
        self._parser = MarkdownIt(
            "commonmark",
            {"html": True, "store_labels": True},
        ).enable("table")
        self._parser.inline.ruler.at("image", _markdown_image_with_source_map)
        self._parser.inline.ruler.at("html_inline", _html_inline_with_source_map)

    def projection_context(self, raw_markdown: str) -> tuple[dict[str, Any], frozenset[int]]:
        """Parse document references once and return their zero-based source lines."""
        environment: dict[str, Any] = {}
        tokens = self._parser.parse(raw_markdown, environment)
        image_labels = {
            label
            for token in tokens
            for child in (token.children or [])
            if child.type == "image"
            if isinstance((label := child.meta.get("label")), str)
        }
        definition_lines: set[int] = set()
        references = environment.get("references")
        if isinstance(references, Mapping):
            for label, reference in references.items():
                if label not in image_labels:
                    continue
                if not isinstance(reference, Mapping):
                    continue
                source_map = reference.get("map")
                if (
                    isinstance(source_map, list)
                    and len(source_map) == 2
                    and all(isinstance(item, int) for item in source_map)
                ):
                    definition_lines.update(range(source_map[0], source_map[1]))
        return environment, frozenset(definition_lines)

    def project_text(
        self,
        raw_markdown: str,
        *,
        environment: dict[str, Any] | None = None,
    ) -> str:
        """Project image author text without exposing reference metadata."""
        matched, projected, _ = self._project_image_syntax(
            raw_markdown,
            environment=environment,
        )
        return (projected or "") if matched else raw_markdown

    def parse(
        self,
        path: Path,
        *,
        content: str | None = None,
        document_id: str,
        relative_path: str,
    ) -> Document:
        if content is None:
            content = path.read_text(encoding="utf-8")
        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        raw_metadata, frontmatter_end, parse_diagnostics = _frontmatter(content)
        if parse_diagnostics and self.metadata_invalid_policy == "fail_resource":
            raise MetadataNormalizationError(parse_diagnostics)
        normalized_metadata = normalize_metadata(
            raw_metadata,
            policy=self.metadata_invalid_policy,
        )
        metadata = normalized_metadata.source
        metadata_diagnostics = parse_diagnostics + normalized_metadata.diagnostics
        source_lines = content.splitlines()
        body_start = frontmatter_end + 1 if frontmatter_end is not None else 0
        body = "\n".join(source_lines[body_start:])
        offsets = _line_offsets(content)
        environment: dict[str, Any] = {}
        tokens = self._parser.parse(body, environment)
        blocks: list[SourceBlock] = []
        heading_stack: list[tuple[int, str]] = []

        if frontmatter_end is not None:
            raw, span = _source_slice(content, offsets, 0, frontmatter_end + 1)
            blocks.append(
                self._make_block(
                    document_id=document_id,
                    block_type=BlockType.FRONTMATTER,
                    raw=raw,
                    plain_text=None,
                    language=None,
                    heading_level=None,
                    heading_path=(),
                    span=span,
                    attributes={"key_count": len(metadata)},
                )
            )

        for index, token in enumerate(tokens):
            if token.level != 0 or token.map is None:
                continue
            block_type = self._block_type(token)
            if block_type is None:
                continue
            start = body_start + token.map[0]
            end = body_start + token.map[1]
            raw, span = _source_slice(content, offsets, start, end)
            heading_level: int | None = None
            attributes: dict[str, JSONValue] = {}
            language: str | None = None
            plain_text = _inline_text(tokens, index)

            if token.type == "heading_open":
                heading_level = int(token.tag[1:])
                title = _heading_text(tokens, index)
                while heading_stack and heading_stack[-1][0] >= heading_level:
                    heading_stack.pop()
                heading_stack.append((heading_level, title))
                plain_text = title
            elif token.type == "fence":
                language = token.info.strip().split(maxsplit=1)[0] if token.info.strip() else "text"
                plain_text = token.content.rstrip("\n")
                if language.casefold() == "mermaid":
                    block_type = BlockType.MERMAID
            elif token.type in {"bullet_list_open", "ordered_list_open"}:
                attributes["task_list"] = bool(re.search(r"^\s*[-*+]\s+\[[ xX]\]", raw, re.MULTILINE))
                attributes["ordered"] = token.type == "ordered_list_open"
            elif token.type == "blockquote_open":
                match = _CALLOUT.search(raw)
                if match:
                    block_type = BlockType.CALLOUT
                    attributes["callout_kind"] = match.group(1).upper()
            elif token.type == "html_block":
                image_projection = _non_markdown_image_projection(raw)
                if image_projection is not None:
                    block_type = BlockType.PARAGRAPH
                    plain_text = image_projection or None
                    if image_projection:
                        text_start = raw.find(image_projection)
                        attributes["projection_spans"] = [
                            [0, len(image_projection), 0, len(raw), text_start, text_start + len(image_projection)]
                        ]

            if token.type != "html_block" and block_type in {
                BlockType.PARAGRAPH,
                BlockType.LIST,
                BlockType.BLOCKQUOTE,
                BlockType.CALLOUT,
                BlockType.TABLE,
                BlockType.UNKNOWN,
            }:
                has_projection, projected, projection_spans = self._project_image_syntax(
                    raw,
                    environment=environment,
                )
                if has_projection:
                    plain_text = projected
                    attributes["projection_spans"] = projection_spans

            heading_path = tuple(title for _, title in heading_stack if title)
            blocks.append(
                self._make_block(
                    document_id=document_id,
                    block_type=block_type,
                    raw=raw,
                    plain_text=plain_text,
                    language=language,
                    heading_level=heading_level,
                    heading_path=heading_path,
                    span=span,
                    attributes=attributes,
                )
            )

        title_value = metadata.get("title")
        title = title_value if isinstance(title_value, str) else ""
        if not title:
            title = next(
                (
                    block.plain_text or ""
                    for block in blocks
                    if block.block_type == BlockType.HEADING and block.heading_level == 1
                ),
                "",
            )
        return Document(
            document_id=document_id,
            relative_path=relative_path,
            title=title,
            frontmatter=metadata,
            blocks=tuple(blocks),
            source_hash=source_hash,
            parser_name=self.name,
            parser_version=self.version,
            metadata_diagnostics=metadata_diagnostics,
            metadata_fingerprint=normalized_metadata.fingerprint,
            metadata_policy_fingerprint=normalized_metadata.policy_fingerprint,
            metadata_normalizer_version=normalized_metadata.normalizer_version,
        )

    def _project_image_syntax(
        self,
        raw_paragraph: str,
        *,
        environment: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None, list[JSONValue]]:
        normalized_offsets = _normalized_offset_map(raw_paragraph)
        references: list[tuple[int, int, str]] = []

        block_tokens = self._parser.parse(raw_paragraph, {})
        if any(token.type in {"fence", "code_block"} for token in block_tokens):
            return False, None, []
        if any(token.type == "html_block" for token in block_tokens):
            projection = _non_markdown_image_projection(raw_paragraph)
            if projection is None:
                return False, None, []

        raw_text_tag: str | None = None
        for inline_token in self._parser.parseInline(raw_paragraph, environment or {}):
            for child in inline_token.children or []:
                if child.type == "image":
                    source_start = child.meta.get("source_start")
                    source_end = child.meta.get("source_end")
                    if isinstance(source_start, int) and isinstance(source_end, int):
                        references.append(
                            (
                                normalized_offsets[source_start],
                                normalized_offsets[source_end],
                                _markdown_image_projection(child),
                            )
                        )
                    continue

                if child.type != "html_inline":
                    continue
                raw_text_match = _HTML_RAW_TEXT_TAG.match(child.content)
                if raw_text_tag is not None:
                    if (
                        raw_text_match is not None
                        and raw_text_match.group("closing")
                        and raw_text_match.group("name").casefold() == raw_text_tag
                    ):
                        raw_text_tag = None
                    continue
                if raw_text_match is not None and not raw_text_match.group("closing"):
                    if not child.content.rstrip().endswith("/>"):
                        raw_text_tag = raw_text_match.group("name").casefold()
                    continue

                source_start = child.meta.get("source_start")
                source_end = child.meta.get("source_end")
                if not isinstance(source_start, int) or not isinstance(source_end, int):
                    continue
                html_images = _html_image_tags(child.content)
                if len(html_images) != 1 or html_images[0][:2] != (0, len(child.content)):
                    continue
                projection = _html_image_alt(child.content)
                if projection is not None:
                    references.append(
                        (
                            normalized_offsets[source_start],
                            normalized_offsets[source_end],
                            projection,
                        )
                    )
        for match in _OBSIDIAN_EMBED_FINDER.finditer(raw_paragraph):
            projection = _non_markdown_image_projection(match.group(0))
            if projection is not None:
                references.append((match.start(), match.end(), projection))
        references.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        non_overlapping: list[tuple[int, int, str]] = []
        cursor = 0
        for source_start, source_end, projection in references:
            if source_start >= cursor:
                non_overlapping.append((source_start, source_end, projection))
                cursor = source_end
        if not non_overlapping:
            return False, None, []

        result: list[str] = []
        spans: list[tuple[int, int, int, int, int, int]] = []
        projected_offset = 0
        cursor = 0
        for source_start, source_end, projection in non_overlapping:
            if source_start > cursor:
                literal = raw_paragraph[cursor:source_start]
                result.append(literal)
                spans.append(
                    (
                        projected_offset,
                        projected_offset + len(literal),
                        cursor,
                        source_start,
                        cursor,
                        source_start,
                    )
                )
                projected_offset += len(literal)
            if projection:
                projection_source = raw_paragraph.find(projection, source_start, source_end)
                if projection_source < 0:
                    projection_source = source_start
                result.append(projection)
                spans.append(
                    (
                        projected_offset,
                        projected_offset + len(projection),
                        source_start,
                        source_end,
                        projection_source,
                        min(source_end, projection_source + len(projection)),
                    )
                )
                projected_offset += len(projection)
            cursor = source_end
        if cursor < len(raw_paragraph):
            literal = raw_paragraph[cursor:]
            result.append(literal)
            spans.append(
                (
                    projected_offset,
                    projected_offset + len(literal),
                    cursor,
                    len(raw_paragraph),
                    cursor,
                    len(raw_paragraph),
                )
            )
        untrimmed = "".join(result)
        leading = len(untrimmed) - len(untrimmed.lstrip())
        trailing = len(untrimmed) - len(untrimmed.rstrip())
        projected = untrimmed.strip()
        projected_end = len(untrimmed) - trailing
        adjusted: list[JSONValue] = [
            [
                max(projected_start, leading) - leading,
                min(projected_stop, projected_end) - leading,
                raw_start,
                raw_end,
                text_start,
                text_end,
            ]
            for projected_start, projected_stop, raw_start, raw_end, text_start, text_end in spans
            if projected_stop > leading and projected_start < projected_end
        ]
        return True, projected or None, adjusted

    @staticmethod
    def _span_for_offsets(content: str, start_offset: int, end_offset: int) -> SourceSpan:
        start_line = content.count("\n", 0, start_offset) + 1
        end_line = content.count("\n", 0, max(start_offset, end_offset - 1)) + 1
        return SourceSpan(start_line, end_line, start_offset, end_offset)

    @staticmethod
    def _block_type(token: Token) -> BlockType | None:
        return {
            "heading_open": BlockType.HEADING,
            "paragraph_open": BlockType.PARAGRAPH,
            "bullet_list_open": BlockType.LIST,
            "ordered_list_open": BlockType.LIST,
            "blockquote_open": BlockType.BLOCKQUOTE,
            "fence": BlockType.CODE,
            "code_block": BlockType.CODE,
            "table_open": BlockType.TABLE,
            "hr": BlockType.THEMATIC_BREAK,
            "html_block": BlockType.UNKNOWN,
        }.get(token.type)

    @staticmethod
    def _make_block(
        *,
        document_id: str,
        block_type: BlockType,
        raw: str,
        plain_text: str | None,
        language: str | None,
        heading_level: int | None,
        heading_path: tuple[str, ...],
        span: SourceSpan,
        attributes: Mapping[str, JSONValue],
    ) -> SourceBlock:
        block_id = logical_id(
            "block",
            document_id,
            block_type.value,
            span.start_line,
            span.end_line,
            content_fingerprint(raw),
        )
        return SourceBlock(
            block_id=block_id,
            document_id=document_id,
            block_type=block_type,
            raw_markdown=raw,
            plain_text=plain_text,
            language=language,
            heading_level=heading_level,
            heading_path=heading_path,
            source_span=span,
            attributes=attributes,
        )
