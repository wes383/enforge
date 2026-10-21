from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

_DOI_PREFIX_RE = re.compile(
    r"^(?:https?://(?:www\.)?(?:dx\.)?doi\.org/|doi:\s*)",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref_src",
    "ref_url",
}
_MAX_URL_LENGTH = 4096


class InvalidDOI(ValueError):
    pass


class InvalidURL(ValueError):
    pass


def normalize_doi(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidDOI("DOI must be a string")
    candidate = unquote(value.strip())
    candidate = _DOI_PREFIX_RE.sub("", candidate)
    if not candidate or len(candidate) > 255:
        raise InvalidDOI("DOI must be between 1 and 255 characters")
    if any(character.isspace() for character in candidate):
        raise InvalidDOI("DOI must not contain whitespace")
    if not _DOI_RE.fullmatch(candidate):
        raise InvalidDOI("DOI must start with a valid '10.xxxx/' prefix")
    return candidate.lower()


def normalize_url(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidURL("URL must be a string")
    candidate = value.strip()
    if not candidate or len(candidate) > _MAX_URL_LENGTH:
        raise InvalidURL("URL must be between 1 and 4096 characters")
    if any(character.isspace() or ord(character) < 32 for character in candidate):
        raise InvalidURL("URL must not contain whitespace or control characters")

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise InvalidURL("URL contains an invalid host or port") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise InvalidURL("Only http and https URLs are supported")
    if not parsed.netloc or not hostname:
        raise InvalidURL("URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidURL("URL credentials are not allowed")

    try:
        ascii_host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise InvalidURL("URL hostname is not a valid international domain") from exc

    if port is not None and port not in {
        80,
        443,
    }:
        authority = f"{ascii_host}:{port}"
    else:
        authority = ascii_host
    if ":" in ascii_host:
        authority = f"[{ascii_host}]" + (f":{port}" if port not in {None, 80, 443} else "")

    path = re.sub(r"/{2,}", "/", parsed.path)
    path = path.rstrip("/") or "/"

    query_pairs = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PREFIXES)
        and key.lower() not in _TRACKING_PARAMS
    ]
    query_pairs.sort()
    query = urlencode(query_pairs, doseq=True)

    normalized = urlunsplit((scheme, authority, path, query, ""))
    if len(normalized) > _MAX_URL_LENGTH:
        raise InvalidURL("Normalized URL is too long")
    return normalized


def url_fingerprint(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def claim_fingerprint(claim_text: str) -> str:
    return text_sha256(normalized_text(claim_text))


def query_fingerprint(query_text: str) -> str:
    return text_sha256(normalized_text(query_text))


def question_fingerprint(question: str) -> str:
    return text_sha256(normalized_text(question))


def evidence_fingerprint(
    source_version_id: str,
    quoted_text: str,
    research_question_id: str | None = None,
) -> str:
    payload = f"{source_version_id}:{normalized_text(quoted_text)}"
    if research_question_id is not None:
        payload = f"{payload}:{research_question_id}"
    return text_sha256(payload)
