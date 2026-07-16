"""markdown-it-py adapter producing MDRack's stable Document IR."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt
from markdown_it.rules_inline.image import image as markdown_image_rule
from markdown_it.rules_inline.state_inline import StateInline
from markdown_it.token import Token

from mdrack.domain.blocks import BlockType, JSONValue, SourceBlock, SourceSpan
from mdrack.domain.documents import Document
from mdrack.domain.identifiers import content_fingerprint, logical_id

_OBSIDIAN_EMBED = re.compile(r"^!\[\[([^\]]+)\]\]$", re.DOTALL)
_HTML_IMAGE = re.compile(r"^<img\b(?P<attributes>[^>]*)/?>$", re.IGNORECASE | re.DOTALL)
_HTML_ATTRIBUTE = re.compile(
    r"\b(?P<name>src|alt)\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_REFERENCE_FINDERS = (
    re.compile(r"!\[\[[^\]]+\]\]", re.DOTALL),
    re.compile(r"<img\b[^>]*?/?>", re.IGNORECASE | re.DOTALL),
)
_CALLOUT = re.compile(r"^>\s*\[!([A-Za-z0-9_-]+)\](?:[+-])?(?:\s+([^\n]+))?", re.MULTILINE)


def _json_value(value: Any) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _frontmatter(content: str) -> tuple[dict[str, JSONValue], int | None]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, None
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        return {}, None
    loaded = yaml.safe_load("\n".join(lines[1:closing]))
    if not isinstance(loaded, Mapping):
        return {}, closing
    return {str(key): _json_value(value) for key, value in loaded.items()}, closing


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


def _markdown_image_attributes(token: Token) -> dict[str, JSONValue] | None:
    reference = token.attrGet("src")
    if not reference:
        return None
    attributes: dict[str, JSONValue] = {
        "syntax": "markdown",
        "alt_text": token.content,
        "reference": reference,
    }
    title = token.attrGet("title")
    if title is not None:
        attributes["title"] = title
    return attributes


def _image_reference_attributes(raw: str) -> dict[str, JSONValue] | None:
    obsidian_embed = _OBSIDIAN_EMBED.fullmatch(raw.strip())
    if obsidian_embed:
        return {
            "syntax": "obsidian",
            "reference": obsidian_embed.group(1),
        }
    html_image = _HTML_IMAGE.fullmatch(raw.strip())
    if html_image:
        values = {
            match.group("name").casefold(): match.group("value")
            for match in _HTML_ATTRIBUTE.finditer(html_image.group("attributes"))
        }
        if values.get("src"):
            attributes: dict[str, JSONValue] = {
                "syntax": "html",
                "reference": values["src"],
            }
            if "alt" in values:
                attributes["alt_text"] = values["alt"]
            return attributes
    return None


class MarkdownItParser:
    """CommonMark/GFM parser adapter with Obsidian block classification."""

    name = "markdown_it"
    version = version("markdown-it-py")

    def __init__(self) -> None:
        self._parser = MarkdownIt("commonmark", {"html": True}).enable("table")
        self._parser.inline.ruler.at("image", _markdown_image_with_source_map)

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
        metadata, frontmatter_end = _frontmatter(content)
        source_lines = content.splitlines()
        body_start = frontmatter_end + 1 if frontmatter_end is not None else 0
        body = "\n".join(source_lines[body_start:])
        offsets = _line_offsets(content)
        tokens = self._parser.parse(body)
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
            elif token.type == "paragraph_open":
                heading_path = tuple(title for _, title in heading_stack if title)
                paragraph_blocks = self._split_reference_paragraph(
                    content=content,
                    offsets=offsets,
                    start=start,
                    end=end,
                    document_id=document_id,
                    heading_path=heading_path,
                )
                if paragraph_blocks is not None:
                    blocks.extend(paragraph_blocks)
                    continue
            elif token.type == "html_block":
                image_attributes = _image_reference_attributes(raw)
                if image_attributes is not None:
                    block_type = BlockType.IMAGE_REFERENCE
                    attributes = image_attributes
                    alt_text = attributes.get("alt_text")
                    plain_text = alt_text if isinstance(alt_text, str) and alt_text else None

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
            blocks=self._with_surrounding_text(blocks),
            source_hash=source_hash,
            parser_name=self.name,
            parser_version=self.version,
        )

    @staticmethod
    def _with_surrounding_text(blocks: list[SourceBlock]) -> tuple[SourceBlock, ...]:
        enriched: list[SourceBlock] = []
        for index, block in enumerate(blocks):
            if block.block_type != BlockType.IMAGE_REFERENCE:
                enriched.append(block)
                continue
            context: list[str] = []
            for neighbor_index in (index - 1, index + 1):
                if 0 <= neighbor_index < len(blocks):
                    neighbor = blocks[neighbor_index]
                    if (
                        neighbor.block_type not in {BlockType.IMAGE_REFERENCE, BlockType.FRONTMATTER}
                        and neighbor.plain_text
                    ):
                        context.append(neighbor.plain_text.strip())
            attributes = dict(block.attributes)
            surrounding = "\n".join(part for part in context if part)
            if surrounding:
                attributes["surrounding_text"] = surrounding
            enriched.append(replace(block, attributes=attributes))
        return tuple(enriched)

    def _split_reference_paragraph(
        self,
        *,
        content: str,
        offsets: list[int],
        start: int,
        end: int,
        document_id: str,
        heading_path: tuple[str, ...],
    ) -> list[SourceBlock] | None:
        paragraph_start = offsets[min(start, len(offsets) - 1)]
        paragraph_end = offsets[min(end, len(offsets) - 1)] if end < len(offsets) else len(content)
        raw_paragraph = content[paragraph_start:paragraph_end].rstrip("\r\n")
        normalized_offsets = _normalized_offset_map(raw_paragraph)
        references: list[tuple[int, int, dict[str, JSONValue]]] = []
        for inline_token in self._parser.parseInline(raw_paragraph):
            for child in inline_token.children or []:
                if child.type != "image":
                    continue
                source_start = child.meta.get("source_start")
                source_end = child.meta.get("source_end")
                attributes = _markdown_image_attributes(child)
                if isinstance(source_start, int) and isinstance(source_end, int) and attributes is not None:
                    references.append(
                        (
                            normalized_offsets[source_start],
                            normalized_offsets[source_end],
                            attributes,
                        )
                    )
        for finder in _REFERENCE_FINDERS:
            for match in finder.finditer(raw_paragraph):
                attributes = _image_reference_attributes(match.group(0))
                if attributes is not None:
                    references.append((match.start(), match.end(), attributes))
        references.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        non_overlapping: list[tuple[int, int, dict[str, JSONValue]]] = []
        cursor = 0
        for source_start, source_end, attributes in references:
            if source_start >= cursor:
                non_overlapping.append((source_start, source_end, attributes))
                cursor = source_end
        if not non_overlapping:
            return None

        result: list[SourceBlock] = []
        cursor = 0
        for source_start, source_end, attributes in non_overlapping:
            if source_start > cursor:
                text_block = self._paragraph_offset_block(
                    content=content,
                    start_offset=paragraph_start + cursor,
                    end_offset=paragraph_start + source_start,
                    document_id=document_id,
                    heading_path=heading_path,
                )
                if text_block is not None:
                    result.append(text_block)
            raw = raw_paragraph[source_start:source_end]
            alt_text = attributes.get("alt_text")
            result.append(
                self._make_block(
                    document_id=document_id,
                    block_type=BlockType.IMAGE_REFERENCE,
                    raw=raw,
                    plain_text=alt_text if isinstance(alt_text, str) and alt_text else None,
                    language=None,
                    heading_level=None,
                    heading_path=heading_path,
                    span=self._span_for_offsets(
                        content,
                        paragraph_start + source_start,
                        paragraph_start + source_end,
                    ),
                    attributes=attributes,
                )
            )
            cursor = source_end
        if cursor < len(raw_paragraph):
            text_block = self._paragraph_offset_block(
                content=content,
                start_offset=paragraph_start + cursor,
                end_offset=paragraph_start + len(raw_paragraph),
                document_id=document_id,
                heading_path=heading_path,
            )
            if text_block is not None:
                result.append(text_block)
        return result

    def _paragraph_offset_block(
        self,
        *,
        content: str,
        start_offset: int,
        end_offset: int,
        document_id: str,
        heading_path: tuple[str, ...],
    ) -> SourceBlock | None:
        raw = content[start_offset:end_offset]
        if raw.strip():
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            content_span = self._span_for_offsets(
                content,
                start_offset + leading,
                end_offset - trailing,
            )
            span = SourceSpan(
                content_span.start_line,
                content_span.end_line,
                start_offset,
                end_offset,
            )
        else:
            span = self._span_for_offsets(content, start_offset, end_offset)
        return self._make_block(
            document_id=document_id,
            block_type=BlockType.PARAGRAPH,
            raw=raw,
            plain_text=raw.strip() or None,
            language=None,
            heading_level=None,
            heading_path=heading_path,
            span=span,
            attributes={},
        )

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
        occurrence = (
            (span.start_offset, span.end_offset)
            if block_type == BlockType.IMAGE_REFERENCE
            else ()
        )
        block_id = logical_id(
            "block",
            document_id,
            block_type.value,
            span.start_line,
            span.end_line,
            *occurrence,
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
