import hashlib
import io
import uuid
from datetime import UTC, datetime

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select

from dr_core.db.enums import (
    ParserStatus,
    SourceOrigin,
    SourceType,
    SourceVersionKind,
)
from dr_core.db.models import SourceVersion
from dr_core.db.repositories import (
    SourceRepository,
    SourceVersionRepository,
)
from dr_core.maintenance import reconcile_files
from dr_core.providers.models import (
    AcademicResult,
    ContentProviderName,
    FetchStatus,
    SearchResult,
    WebFetchResult,
)
from dr_core.providers.parsing import LocalDocumentParser
from dr_core.schemas import SourceCreate, SourceVersionCreate
from dr_core.services import (
    ingest_academic_result,
    ingest_web_search_result,
    mark_parse_started,
    persist_parse_failure,
    persist_parsed_document,
    persist_web_fetch,
)
from dr_core.storage import LocalFileStorage

pytestmark = pytest.mark.integration


def _pdf_with_text(text: str) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_ref}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_search_snippet_and_fetched_full_text_are_distinct(
    session,
    database,
    project,
    tmp_path,
):
    session.commit()
    search_result = SearchResult(
        provider="mock",
        title="Fetched web source",
        url="https://example.com/source",
        snippet="A search snippet with traceable context.",
        published_at=datetime(2024, 3, 1).date(),
        rank=1,
    )
    ingested = ingest_web_search_result(
        database,
        project.id,
        search_result,
        query="traceable evidence",
    )
    assert ingested.version_id is not None
    assert ingested.content_kind is SourceVersionKind.SEARCH_SNIPPET
    assert ingested.parser_status is ParserStatus.SKIPPED

    repeated = ingest_web_search_result(
        database,
        project.id,
        search_result,
        query="traceable evidence",
    )
    assert repeated.source_id == ingested.source_id
    assert repeated.version_id == ingested.version_id

    storage = LocalFileStorage(tmp_path / "files")
    fetched = WebFetchResult(
        provider=ContentProviderName.MOCK,
        status=FetchStatus.SUCCESS,
        url="https://example.com/source",
        final_url="https://example.com/source-final",
        title="Fetched web source",
        text="Normalized full text. " * 20,
        fetched_at=datetime.now(tz=UTC),
        http_status=200,
    )
    persisted = persist_web_fetch(
        database,
        project.id,
        ingested.source_id,
        fetched,
        storage=storage,
    )
    assert persisted.version_id is not None
    assert persisted.content_kind is SourceVersionKind.FULL_TEXT
    assert persisted.storage_key is not None
    assert storage.exists(persisted.storage_key)

    with database.session_scope() as current:
        version = SourceVersionRepository(current).get(
            persisted.version_id,
            project_id=project.id,
        )
        assert version.content_kind is SourceVersionKind.FULL_TEXT
        assert version.fetched_url == "https://example.com/source-final"
        assert version.content_hash == fetched.content_hash
        assert version.version_metadata["status"] == "success"
        assert version.version_metadata["completeness"] == "full_text"
        source = SourceRepository(current).get(
            ingested.source_id,
            project_id=project.id,
        )
        assert source.source_metadata["last_fetch"]["status"] == "success"

        report = reconcile_files(current, storage)
        missing_keys = [item.storage_key for item in report.missing_files]
        assert persisted.storage_key not in missing_keys
        assert persisted.storage_key not in report.orphan_keys

    paywalled = WebFetchResult(
        provider=ContentProviderName.MOCK,
        status=FetchStatus.PAYWALLED,
        url="https://example.com/source",
        fetched_at=datetime.now(tz=UTC),
        http_status=403,
        error_code="paywall",
        error_message="Subscription required",
    )
    paywalled_result = persist_web_fetch(
        database,
        project.id,
        ingested.source_id,
        paywalled,
        storage=storage,
    )
    assert paywalled_result.version_id is None
    with database.session_scope() as current:
        source = SourceRepository(current).get(
            ingested.source_id,
            project_id=project.id,
        )
        assert source.source_metadata["last_fetch"]["status"] == "paywalled"
        version_count = current.scalar(
            select(func.count())
            .select_from(SourceVersion)
            .where(SourceVersion.source_id == ingested.source_id)
        )
    assert version_count == 2


def test_academic_result_ingestion_deduplicates_doi(session, database, project):
    session.commit()
    result = AcademicResult(
        provider="mock",
        title="Academic source",
        authors=["Ada Lovelace"],
        doi="10.1000/ACADEMIC",
        url="https://example.com/academic",
        published_year=2023,
        venue="Test Journal",
        abstract="An academic abstract.",
        citation_count=9,
        work_type="journal-article",
        provider_id="mock-paper",
    )
    first = ingest_academic_result(database, project.id, result)
    second = ingest_academic_result(database, project.id, result)
    assert first.source_id == second.source_id
    assert first.version_id == second.version_id
    assert first.parser_status is ParserStatus.SKIPPED

    with database.session_scope() as current:
        source = SourceRepository(current).get(
            first.source_id,
            project_id=project.id,
        )
        assert source.doi == "10.1000/academic"
        assert source.source_type is SourceType.JOURNAL_ARTICLE
        assert source.source_origin is SourceOrigin.ACADEMIC_SEARCH
        version = SourceVersionRepository(current).get(
            first.version_id,
            project_id=project.id,
        )
        assert version.version_metadata["completeness"] == "abstract"
        assert version.version_metadata["snippet"] == "An academic abstract."


def test_parse_status_persistence_and_failure_guard(session, database, project, tmp_path):
    session.commit()
    storage = LocalFileStorage(tmp_path / "files")
    pdf = _pdf_with_text("Parse persistence")
    storage_key = f"projects/{project.id}/sources/{uuid.uuid4().hex}/original.pdf"
    storage.save(storage_key, pdf)
    digest = hashlib.sha256(pdf).hexdigest()

    with database.session_scope() as current:
        source = SourceRepository(current).create(
            project.id,
            SourceCreate(
                title="Parsed local document",
                source_type=SourceType.USER_DOCUMENT,
                source_origin=SourceOrigin.USER_UPLOAD,
            ),
        )
        version = SourceVersionRepository(current).create(
            project.id,
            source.id,
            SourceVersionCreate(
                content_kind=SourceVersionKind.UPLOADED_DOCUMENT,
                content_hash=digest,
                storage_key=storage_key,
                mime_type="application/pdf",
                size_bytes=len(pdf),
                metadata={"original_filename": "paper.pdf"},
            ),
        )
        source_id, version_id = source.id, version.id

    started = mark_parse_started(
        database,
        project.id,
        version_id,
        parser_version="local-1.0",
    )
    assert started.parser_status is ParserStatus.PARSING

    parsed = LocalDocumentParser().parse(
        pdf,
        mime_type="application/pdf",
        filename="paper.pdf",
    )
    persisted = persist_parsed_document(database, project.id, version_id, parsed)
    assert persisted.parser_status is ParserStatus.PARSED

    with database.session_scope() as current:
        version = SourceVersionRepository(current).get(
            version_id,
            project_id=project.id,
        )
        assert version.parser_status is ParserStatus.PARSED
        assert version.parser_version == "local-1.0"
        assert version.parse_error is None
        assert version.version_metadata["parse"]["page_count"] == 1
        assert version.version_metadata["parse"]["character_count"] > 0

    guarded = persist_parse_failure(
        database,
        project.id,
        version_id,
        parser_version="local-1.0",
        error="This must not overwrite a parsed version",
    )
    assert guarded.parser_status is ParserStatus.PARSED

    corrupt = b"%PDF-1.4\ncorrupt"
    corrupt_key = f"projects/{project.id}/sources/{source_id}/original-corrupt.pdf"
    storage.save(corrupt_key, corrupt)
    with database.session_scope() as current:
        failed_version = SourceVersionRepository(current).create(
            project.id,
            source_id,
            SourceVersionCreate(
                content_kind=SourceVersionKind.UPLOADED_DOCUMENT,
                content_hash=hashlib.sha256(corrupt).hexdigest(),
                storage_key=corrupt_key,
                mime_type="application/pdf",
                size_bytes=len(corrupt),
            ),
        )
        failed_version_id = failed_version.id
    failure = persist_parse_failure(
        database,
        project.id,
        failed_version_id,
        parser_version="local-1.0",
        error="PDF could not be read",
        error_code="parse_failed",
    )
    assert failure.parser_status is ParserStatus.FAILED
    with database.session_scope() as current:
        failed_version = SourceVersionRepository(current).get(
            failed_version_id,
            project_id=project.id,
        )
        assert failed_version.parser_status is ParserStatus.FAILED
        assert failed_version.parse_error == "PDF could not be read"
