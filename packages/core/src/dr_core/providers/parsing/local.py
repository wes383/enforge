from __future__ import annotations

import io
import re
from typing import Any

import trafilatura
from bs4 import BeautifulSoup
from pydantic import ValidationError
from pypdf import PdfReader

from dr_core.providers.errors import (
    DocumentParseError,
    UnsupportedDocumentType,
)
from dr_core.providers.models import (
    ParsedDocument,
    ParsedPage,
    ParsedSection,
    ParsedTable,
    ParseStatus,
    ParserName,
)
from dr_core.providers.text import normalize_extracted_text

_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_MARKDOWN_TYPES = {"text/markdown", "text/x-markdown"}
_TEXT_TYPES = {"text/plain"}
_ALLOWED_TYPES = _HTML_TYPES | _MARKDOWN_TYPES | _TEXT_TYPES | {"application/pdf"}
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_MD_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_MAX_SECTIONS = 500
_MAX_PARAGRAPHS = 5000
_MAX_TABLES = 50
_MAX_TABLE_ROWS = 200
_MAX_TABLE_COLUMNS = 30


class LocalDocumentParser:
    name = ParserName.LOCAL.value
    version = "local-1.0"

    def __init__(
        self,
        *,
        max_input_bytes: int = 5 * 1024 * 1024,
        max_output_chars: int = 2_000_000,
        max_pdf_pages: int = 200,
    ) -> None:
        self.max_input_bytes = max_input_bytes
        self.max_output_chars = max_output_chars
        self.max_pdf_pages = max_pdf_pages

    def parse(
        self,
        content: bytes,
        *,
        mime_type: str,
        filename: str | None = None,
    ) -> ParsedDocument:
        if not content:
            raise DocumentParseError(self.name, "Document is empty")
        if len(content) > self.max_input_bytes:
            raise DocumentParseError(
                self.name,
                "Document exceeds the configured input size limit",
            )
        media_type = (mime_type or "").split(";", 1)[0].strip().lower()
        if media_type not in _ALLOWED_TYPES:
            raise UnsupportedDocumentType(
                self.name,
                f"Unsupported document MIME type: {media_type or 'unknown'}",
            )
        has_pdf_signature = content.startswith(b"%PDF-")
        if media_type == "application/pdf" and not has_pdf_signature:
            raise DocumentParseError(
                self.name,
                "MIME type indicates PDF but the file signature is missing",
            )
        if media_type != "application/pdf" and has_pdf_signature:
            raise DocumentParseError(
                self.name,
                "File signature indicates PDF but the MIME type does not",
            )

        metadata: dict[str, Any] = {
            "filename": filename,
            "source_mime_type": media_type,
            "source_bytes": len(content),
            "parser": self.version,
        }

        if media_type == "application/pdf":
            parsed = self._parse_pdf(content, metadata)
        else:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise DocumentParseError(
                    self.name,
                    "Text documents must be valid UTF-8",
                ) from exc
            if "\x00" in text:
                raise DocumentParseError(
                    self.name,
                    "Text documents must not contain NUL bytes",
                )
            if media_type in _HTML_TYPES:
                parsed = self._parse_html(text, metadata)
            elif media_type in _MARKDOWN_TYPES:
                parsed = self._parse_markdown(text, metadata)
            else:
                parsed = self._parse_plain_text(text, metadata)

        return self._finalize(
            title=parsed.title,
            text=parsed.text,
            sections=parsed.sections,
            pages=parsed.pages,
            tables=parsed.tables,
            page_count=parsed.page_count,
            tables_status=parsed.tables_status,
            truncated=parsed.truncated,
            metadata=parsed.metadata,
        )

    def _parse_pdf(self, content: bytes, metadata: dict[str, Any]) -> _Parsed:
        try:
            reader = PdfReader(io.BytesIO(content), strict=False)
        except Exception as exc:
            raise DocumentParseError(
                self.name,
                "PDF could not be read",
            ) from exc
        if reader.is_encrypted:
            try:
                decrypted = reader.decrypt("")
            except Exception as exc:
                raise DocumentParseError(
                    self.name,
                    "Encrypted PDFs are not supported",
                ) from exc
            if not decrypted:
                raise DocumentParseError(
                    self.name,
                    "Encrypted PDFs are not supported",
                )

        try:
            total_pages = len(reader.pages)
        except Exception as exc:
            raise DocumentParseError(
                self.name,
                "PDF page structure could not be read",
            ) from exc
        if total_pages == 0:
            raise DocumentParseError(self.name, "PDF contains no pages")

        pages: list[ParsedPage] = []
        page_texts: list[str] = []
        page_errors = 0
        processed_pages = min(total_pages, self.max_pdf_pages)
        for index in range(processed_pages):
            try:
                page_text = reader.pages[index].extract_text() or ""
            except Exception:
                page_errors += 1
                page_text = ""
            normalized = normalize_extracted_text(page_text)
            pages.append(ParsedPage(page_number=index + 1, text=normalized))
            if normalized:
                page_texts.append(normalized)

        text = normalize_extracted_text("\n\n".join(page_texts))
        if not text:
            raise DocumentParseError(
                self.name,
                "PDF contains no extractable text; it may be scanned",
            )

        title = None
        try:
            pdf_title = reader.metadata.title if reader.metadata else None
        except Exception:
            pdf_title = None
        if isinstance(pdf_title, str):
            title = pdf_title.strip()[:5000] or None

        metadata = {
            **metadata,
            "pdf_pages_total": total_pages,
            "pdf_pages_processed": processed_pages,
            "pdf_page_errors": page_errors,
        }
        return _Parsed(
            title=title,
            text=text,
            sections=[],
            pages=pages,
            tables=[],
            page_count=total_pages,
            tables_status="not_available",
            truncated=processed_pages < total_pages,
            metadata=metadata,
        )

    def _parse_html(self, text: str, metadata: dict[str, Any]) -> _Parsed:
        soup = BeautifulSoup(text, "html.parser")
        for element in soup.find_all(
            ["script", "style", "noscript", "nav", "footer", "aside", "form"]
        ):
            element.decompose()

        title = None
        if soup.title and soup.title.string:
            title = normalize_extracted_text(soup.title.string)[:5000] or None
        if not title:
            heading = soup.find("h1")
            if heading:
                title = normalize_extracted_text(heading.get_text(" ", strip=True))[
                    :5000
                ] or None

        extracted = None
        try:
            extracted = trafilatura.extract(
                text,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
        except Exception:
            extracted = None
        extracted_text = normalize_extracted_text(extracted or "")

        container = soup.find("main") or soup.find("article") or soup.body or soup
        sections: list[ParsedSection] = []
        current = ParsedSection(heading=None, level=None, paragraphs=[])
        seen_headings: set[str] = set()
        for element in container.find_all(
            ["h1", "h2", "h3", "h4", "h5", "h6", "p"],
            recursive=True,
        ):
            tag = element.name or ""
            element_text = normalize_extracted_text(
                element.get_text(" ", strip=True)
            )
            if not element_text:
                continue
            if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
                if current.heading or current.paragraphs:
                    sections.append(current)
                    if len(sections) >= _MAX_SECTIONS:
                        break
                current = ParsedSection(
                    heading=element_text[:2000],
                    level=int(tag[1]),
                    paragraphs=[],
                )
                seen_headings.add(element_text)
            elif len(current.paragraphs) < _MAX_PARAGRAPHS:
                current.paragraphs.append(element_text)
        if (current.heading or current.paragraphs) and len(sections) < _MAX_SECTIONS:
            sections.append(current)
        sections = [section for section in sections if section.paragraphs or section.heading]

        if not extracted_text:
            fallback_paragraphs = [
                paragraph
                for section in sections
                for paragraph in section.paragraphs
            ]
            if not fallback_paragraphs:
                fallback_paragraphs = [
                    normalize_extracted_text(item)
                    for item in container.get_text("\n", strip=True).split("\n")
                    if normalize_extracted_text(item)
                ]
            extracted_text = normalize_extracted_text(
                "\n\n".join(fallback_paragraphs)
            )
        if not extracted_text:
            raise DocumentParseError(
                self.name,
                "HTML page contains no extractable text",
            )

        tables = self._html_tables(container)
        metadata = {
            **metadata,
            "headings": len(seen_headings),
            "html_extraction": "trafilatura" if extracted else "beautifulsoup",
        }
        return _Parsed(
            title=title,
            text=extracted_text,
            sections=sections,
            pages=[],
            tables=tables,
            page_count=0,
            tables_status="best_effort" if tables else "not_available",
            truncated=False,
            metadata=metadata,
        )

    def _html_tables(self, container: Any) -> list[ParsedTable]:
        tables: list[ParsedTable] = []
        for table in container.find_all("table")[:_MAX_TABLES]:
            rows: list[list[str]] = []
            for row in table.find_all("tr")[:_MAX_TABLE_ROWS]:
                cells = row.find_all(["th", "td"])[:_MAX_TABLE_COLUMNS]
                values = [
                    normalize_extracted_text(cell.get_text(" ", strip=True))[:2000]
                    for cell in cells
                ]
                if any(values):
                    rows.append(values)
            if not rows:
                continue
            caption = table.find("caption")
            title = None
            if caption:
                title = normalize_extracted_text(caption.get_text(" ", strip=True))[
                    :1000
                ] or None
            headers = rows[0] if rows else []
            tables.append(
                ParsedTable(
                    title=title,
                    headers=headers,
                    rows=rows[1:],
                    extraction="best_effort",
                )
            )
        return tables

    def _parse_markdown(self, text: str, metadata: dict[str, Any]) -> _Parsed:
        normalized = normalize_extracted_text(text)
        if not normalized:
            raise DocumentParseError(self.name, "Markdown document is empty")
        matches = list(_HEADING_RE.finditer(normalized))
        sections: list[ParsedSection] = []
        title = None
        if matches:
            for index, match in enumerate(matches[:_MAX_SECTIONS]):
                level = len(match.group(1))
                heading = normalize_extracted_text(match.group(2))[:2000]
                if level == 1 and title is None:
                    title = heading[:5000]
                start = match.end()
                end = (
                    matches[index + 1].start()
                    if index + 1 < len(matches)
                    else len(normalized)
                )
                chunk = normalized[start:end].strip()
                paragraphs = [
                    normalize_extracted_text(part)
                    for part in re.split(r"\n\s*\n", chunk)
                    if normalize_extracted_text(part)
                ]
                sections.append(
                    ParsedSection(
                        heading=heading or None,
                        level=level,
                        paragraphs=paragraphs[:_MAX_PARAGRAPHS],
                    )
                )
        else:
            paragraphs = [
                normalize_extracted_text(part)
                for part in re.split(r"\n\s*\n", normalized)
                if normalize_extracted_text(part)
            ]
            sections = [
                ParsedSection(
                    heading=None,
                    level=None,
                    paragraphs=paragraphs[:_MAX_PARAGRAPHS],
                )
            ]

        tables = self._markdown_tables(normalized)
        metadata = {**metadata, "markdown_headings": len(matches)}
        return _Parsed(
            title=title,
            text=normalized,
            sections=sections,
            pages=[],
            tables=tables,
            page_count=0,
            tables_status="best_effort" if tables else "not_available",
            truncated=False,
            metadata=metadata,
        )

    def _markdown_tables(self, text: str) -> list[ParsedTable]:
        lines = text.split("\n")
        tables: list[ParsedTable] = []
        index = 0
        while index + 1 < len(lines) and len(tables) < _MAX_TABLES:
            if "|" in lines[index] and _MD_SEPARATOR_RE.match(lines[index + 1]):
                headers = _split_markdown_row(lines[index])
                rows: list[list[str]] = []
                cursor = index + 2
                while cursor < len(lines) and "|" in lines[cursor]:
                    rows.append(_split_markdown_row(lines[cursor]))
                    if len(rows) >= _MAX_TABLE_ROWS:
                        break
                    cursor += 1
                if headers:
                    tables.append(
                        ParsedTable(
                            headers=headers[:_MAX_TABLE_COLUMNS],
                            rows=[
                                row[:_MAX_TABLE_COLUMNS]
                                for row in rows
                                if any(row)
                            ],
                            extraction="best_effort",
                        )
                    )
                index = cursor
                continue
            index += 1
        return tables

    def _parse_plain_text(self, text: str, metadata: dict[str, Any]) -> _Parsed:
        normalized = normalize_extracted_text(text)
        if not normalized:
            raise DocumentParseError(self.name, "Text document is empty")
        paragraphs = [
            normalize_extracted_text(part)
            for part in re.split(r"\n\s*\n", normalized)
            if normalize_extracted_text(part)
        ]
        return _Parsed(
            title=None,
            text=normalized,
            sections=[],
            pages=[],
            tables=[],
            page_count=0,
            tables_status="not_available",
            truncated=False,
            metadata={**metadata, "plain_text_paragraphs": len(paragraphs)},
        )

    def _finalize(
        self,
        *,
        title: str | None,
        text: str,
        sections: list[ParsedSection],
        pages: list[ParsedPage],
        tables: list[ParsedTable],
        page_count: int,
        tables_status: str,
        truncated: bool,
        metadata: dict[str, Any],
    ) -> ParsedDocument:
        final_text = normalize_extracted_text(text)
        if not final_text:
            raise DocumentParseError(self.name, "No extractable text was found")
        if len(final_text) > self.max_output_chars:
            final_text = final_text[: self.max_output_chars]
            truncated = True
            sections = []
            pages = []
            tables = []
            tables_status = "not_available"
            metadata = {**metadata, "structure_dropped_after_truncation": True}

        paragraph_count = (
            sum(len(section.paragraphs) for section in sections)
            if sections
            else len(
                [
                    part
                    for part in re.split(r"\n\s*\n", final_text)
                    if part.strip()
                ]
            )
        )
        try:
            return ParsedDocument(
                status=ParseStatus.PARTIAL if truncated else ParseStatus.SUCCESS,
                title=title,
                text=final_text,
                sections=sections,
                pages=pages,
                tables=tables,
                paragraph_count=paragraph_count,
                page_count=page_count,
                character_count=len(final_text),
                tables_status=tables_status,
                parser_name=self.name,
                parser_version=self.version,
                truncated=truncated,
                metadata=metadata,
            )
        except ValidationError as exc:
            raise DocumentParseError(
                self.name,
                "Parsed document failed schema validation",
            ) from exc


class _Parsed:
    __slots__ = (
        "title",
        "text",
        "sections",
        "pages",
        "tables",
        "page_count",
        "tables_status",
        "truncated",
        "metadata",
    )

    def __init__(
        self,
        *,
        title: str | None,
        text: str,
        sections: list[ParsedSection],
        pages: list[ParsedPage],
        tables: list[ParsedTable],
        page_count: int,
        tables_status: str,
        truncated: bool,
        metadata: dict[str, Any],
    ) -> None:
        self.title = title
        self.text = text
        self.sections = sections
        self.pages = pages
        self.tables = tables
        self.page_count = page_count
        self.tables_status = tables_status
        self.truncated = truncated
        self.metadata = metadata


def _split_markdown_row(line: str) -> list[str]:
    candidate = line.strip()
    if candidate.startswith("|"):
        candidate = candidate[1:]
    if candidate.endswith("|"):
        candidate = candidate[:-1]
    return [
        normalize_extracted_text(cell)[:2000]
        for cell in candidate.split("|")
    ]
