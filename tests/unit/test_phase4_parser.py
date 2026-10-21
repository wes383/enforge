import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from dr_core.providers.errors import (
    DocumentParseError,
    UnsupportedDocumentType,
)
from dr_core.providers.models import ParseStatus
from dr_core.providers.parsing import LocalDocumentParser


def _pdf_with_text(text: str, *, pages: int = 1, title: str | None = None) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_ref}
            )
        }
    )
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(
            f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    if title:
        writer.add_metadata({"/Title": title})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_extracts_pages_and_title():
    content = _pdf_with_text("Hello PDF", pages=2, title="PDF title")
    document = LocalDocumentParser().parse(
        content,
        mime_type="application/pdf",
        filename="paper.pdf",
    )
    assert document.status is ParseStatus.SUCCESS
    assert document.title == "PDF title"
    assert document.page_count == 2
    assert len(document.pages) == 2
    assert document.pages[0].page_number == 1
    assert "Hello PDF" in document.text
    assert document.sections == []
    assert document.tables_status == "not_available"
    assert document.parser_version == "local-1.0"


def test_pdf_without_text_fails_instead_of_fabricating():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(DocumentParseError):
        LocalDocumentParser().parse(
            buffer.getvalue(),
            mime_type="application/pdf",
        )


def test_encrypted_pdf_is_rejected():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(DocumentParseError):
        LocalDocumentParser().parse(buffer.getvalue(), mime_type="application/pdf")


def test_html_extracts_structure_title_and_best_effort_tables():
    html = (
        "<html><head><title>Research Note</title></head><body><main>"
        "<h1>Overview</h1><p>"
        + "Evidence text. " * 50
        + "</p><h2>Methods</h2><p>Method paragraph.</p>"
        "<table><caption>Metrics</caption>"
        "<tr><th>Metric</th><th>Value</th></tr>"
        "<tr><td>Accuracy</td><td>0.9</td></tr></table>"
        "</main></body></html>"
    ).encode()
    document = LocalDocumentParser().parse(html, mime_type="text/html")
    assert document.title == "Research Note"
    assert [section.heading for section in document.sections] == [
        "Overview",
        "Methods",
    ]
    assert document.sections[0].level == 1
    assert document.tables_status == "best_effort"
    assert document.tables[0].headers == ["Metric", "Value"]
    assert document.tables[0].rows == [["Accuracy", "0.9"]]
    assert "Evidence text" in document.text


def test_markdown_extracts_headings_paragraphs_and_tables():
    markdown = (
        "# Research\n\n"
        "First paragraph.\n\n"
        "## Evidence\n\n"
        "Second paragraph.\n\n"
        "| Metric | Value |\n"
        "| --- | --- |\n"
        "| Accuracy | 0.9 |\n"
    ).encode()
    document = LocalDocumentParser().parse(markdown, mime_type="text/markdown")
    assert document.title == "Research"
    assert [section.heading for section in document.sections] == [
        "Research",
        "Evidence",
    ]
    assert document.paragraph_count >= 2
    assert document.tables_status == "best_effort"
    assert document.tables[0].rows == [["Accuracy", "0.9"]]


def test_plain_text_does_not_invent_title_or_sections():
    document = LocalDocumentParser().parse(
        "First paragraph.\n\nSecond paragraph.".encode(),
        mime_type="text/plain",
    )
    assert document.title is None
    assert document.sections == []
    assert document.paragraph_count == 2
    assert document.page_count == 0


def test_parser_rejects_unsupported_mismatched_and_unsafe_documents():
    parser = LocalDocumentParser(max_input_bytes=20)
    with pytest.raises(UnsupportedDocumentType):
        parser.parse(b"data", mime_type="image/png")
    with pytest.raises(DocumentParseError):
        parser.parse(b"not a pdf", mime_type="application/pdf")
    with pytest.raises(DocumentParseError):
        parser.parse(b"%PDF-1.4\n...", mime_type="text/plain")
    with pytest.raises(DocumentParseError):
        parser.parse(b"", mime_type="text/plain")
    with pytest.raises(DocumentParseError):
        parser.parse(b"text\x00inside", mime_type="text/plain")
    with pytest.raises(DocumentParseError):
        parser.parse(b"text\xff", mime_type="text/plain")

    limited = LocalDocumentParser(max_input_bytes=4)
    with pytest.raises(DocumentParseError):
        limited.parse(b"12345", mime_type="text/plain")


def test_parser_truncates_oversized_output_and_marks_partial():
    parser = LocalDocumentParser(max_output_chars=50)
    document = parser.parse(
        ("A" * 200).encode(),
        mime_type="text/plain",
    )
    assert document.status is ParseStatus.PARTIAL
    assert document.truncated is True
    assert len(document.text) == 50
    assert document.sections == []
    assert document.metadata["structure_dropped_after_truncation"] is True
