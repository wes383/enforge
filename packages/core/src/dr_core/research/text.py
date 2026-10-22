from __future__ import annotations

import re
from collections.abc import Iterable

_ASCII_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]+")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s*|\n+")
_SENTENCE_SPAN = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")
_MARKDOWN_NOISE = re.compile(r"[`*_>#\[\]()]")
_CITATION_MARKER = re.compile(r"\[S(\d+)\]")
_LOW_VALUE_SENTENCE = re.compile(
    r"关键词\s*[:：]"
    r"|中图分类号|文献标识码|文章编号|论文编号|收稿日期|修回日期|录用日期"
    r"|基金项目|作者简介|通信作者|责任编辑|引用本文|本文链接|下载\s*pdf"
    r"|版权所有|免责声明|隐私政策|网站地图|上一篇|下一篇|相关阅读|扫码关注"
    r"|\bcopyright\b|\ball rights reserved\b"
    r"|\bkeywords?\s*[:：]|\bcorresponding author\b"
    r"|\bdoi\b|\bissn\b|\bisbn\b"
    r"|\breceived\s*[:：]|\baccepted\s*[:：]|\bpublished\s*[:：]"
    r"|\bdownloaded from\b|\bjournal homepage\b|\bsubscribe\b|\bsign in\b"
    r"|^(?:首页|登录|注册|关于我们|联系我们|网站首页)\s*$",
    re.IGNORECASE,
)
_LOW_VALUE_PREFIX = re.compile(
    r"^(?:share|share this|read more|related articles|table of contents|"
    r"share this article|follow us|advertisement)\b",
    re.IGNORECASE,
)


def is_low_value_sentence(text: str) -> bool:
    cleaned = " ".join(text.split())
    if not cleaned:
        return True
    return bool(
        _LOW_VALUE_SENTENCE.search(cleaned)
        or _LOW_VALUE_PREFIX.search(cleaned)
    )
_STOPWORDS = frozenset(
    {
        "and",
        "are",
        "about",
        "also",
        "been",
        "but",
        "can",
        "for",
        "from",
        "has",
        "have",
        "into",
        "its",
        "may",
        "more",
        "not",
        "that",
        "the",
        "their",
        "there",
        "these",
        "they",
        "this",
        "was",
        "were",
        "what",
        "when",
        "which",
        "with",
        "would",
        "的",
        "了",
        "是",
        "在",
        "和",
        "与",
        "及",
        "中",
        "对",
        "为",
        "有",
        "什么",
        "如何",
        "哪些",
        "以及",
        "是否",
        "相关",
        "进行",
        "可以",
        "这个",
        "这些",
    }
)


def tokenize(text: str) -> set[str]:
    lowered = text.casefold()
    tokens = {
        word
        for word in _ASCII_WORD.findall(lowered)
        if len(word) > 2 and word not in _STOPWORDS
    }
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            if run not in _STOPWORDS:
                tokens.add(run)
        else:
            tokens.update(
                run[index : index + 2]
                for index in range(len(run) - 1)
                if run[index : index + 2] not in _STOPWORDS
            )
    return tokens


def split_sentences(text: str) -> list[str]:
    candidate = _MARKDOWN_NOISE.sub(" ", text)
    sentences = []
    for part in _SENTENCE_SPLIT.split(candidate):
        sentence = " ".join(part.split())
        if 20 <= len(sentence) <= 2000 and not is_low_value_sentence(sentence):
            sentences.append(sentence)
    return sentences


def sentence_spans(text: str) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    for match in _SENTENCE_SPAN.finditer(text):
        raw = match.group()
        stripped = raw.strip()
        if not stripped:
            continue
        cleaned = " ".join(stripped.split())
        if not 20 <= len(cleaned) <= 2000:
            continue
        if is_low_value_sentence(cleaned):
            continue
        start = match.start() + (len(raw) - len(raw.lstrip()))
        spans.append((cleaned, start, start + len(stripped)))
    return spans


def best_sentence_span(
    text: str,
    query: str,
    *,
    limit: int = 1,
    minimum_score: int = 1,
) -> list[tuple[str, int, int, int]]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    scored: list[tuple[str, int, int, int]] = []
    for sentence, start, end in sentence_spans(text):
        score = len(query_tokens & tokenize(sentence))
        if score >= minimum_score:
            scored.append((sentence, score, start, end))
    scored.sort(key=lambda item: (-item[1], len(item[0]), item[2]))
    return scored[: max(1, limit)]


def context_window(
    text: str,
    start: int,
    end: int,
    *,
    window: int = 240,
) -> tuple[str, str]:
    before = text[max(0, start - window) : start].strip()
    after = text[end : end + window].strip()
    return before or "", after or ""


def split_paragraphs(text: str) -> list[str]:
    blocks = re.split(r"\n\s*\n", text)
    paragraphs = []
    for block in blocks:
        cleaned = " ".join(
            line.strip()
            for line in block.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        if len(cleaned) >= 20:
            paragraphs.append(cleaned)
    return paragraphs


def best_sentences(
    text: str,
    query: str,
    *,
    limit: int = 2,
    minimum_score: int = 1,
) -> list[tuple[str, int]]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    scored = []
    for sentence in split_sentences(text):
        score = len(query_tokens & tokenize(sentence))
        if score >= minimum_score:
            scored.append((sentence, score))
    scored.sort(key=lambda item: (-item[1], len(item[0])))
    return scored[: max(1, limit)]


def best_paragraph(
    text: str,
    query: str,
) -> tuple[str | None, int]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return None, 0
    best: tuple[str | None, int] = (None, 0)
    for paragraph in split_paragraphs(text):
        score = len(query_tokens & tokenize(paragraph))
        if score > best[1]:
            best = (paragraph, score)
    return best


def citation_markers(text: str) -> list[int]:
    return sorted({int(match) for match in _CITATION_MARKER.findall(text)})


def format_reference(
    *,
    authors: Iterable[str],
    title: str,
    year: int | None,
    venue: str | None,
    publisher: str | None,
    doi: str | None,
    url: str | None,
    style: str,
) -> str:
    author_list = [name.strip() for name in authors if name.strip()]
    if style == "ieee":
        author_text = ", ".join(author_list)
        parts = [f"{author_text}," if author_text else "", f"“{title},”" if title else ""]
        if venue:
            parts.append(f"{venue},")
        if year:
            parts.append(str(year))
        tail = f"doi: {doi}" if doi else url
        if tail:
            parts.append(f"doi: {doi}." if doi else f"{url}.")
        return " ".join(part for part in parts if part).strip()
    if style == "mla":
        author_text = author_list[0] if author_list else ""
        if author_text:
            author_text += "."
        parts = [author_text, f"“{title}.”" if title else ""]
        if venue:
            parts.append(f"{venue},")
        if year:
            parts.append(str(year) + ".")
        tail = f"doi: {doi}." if doi else (f"{url}." if url else "")
        if tail:
            parts.append(tail)
        return " ".join(part for part in parts if part).strip()
    if style == "chicago":
        author_text = author_list[0] if author_list else ""
        if len(author_list) > 1:
            author_text += " et al."
        if author_text:
            author_text += "."
        parts = [author_text, f"“{title}.”" if title else ""]
        if venue:
            parts.append(f"{venue}.")
        if year:
            parts.append(str(year) + ".")
        tail = f"doi: {doi}." if doi else (f"{url}." if url else "")
        if tail:
            parts.append(tail)
        return " ".join(part for part in parts if part).strip()

    if not author_list:
        author_text = ""
    elif len(author_list) == 1:
        author_text = f"{author_list[0]}."
    elif len(author_list) == 2:
        author_text = f"{author_list[0]}, & {author_list[1]}."
    else:
        author_text = f"{author_list[0]}, et al."
    parts = [author_text, f"({year})." if year else "", f"{title}."]
    if venue:
        parts.append(f"{venue}.")
    if publisher:
        parts.append(f"{publisher}.")
    tail = f"https://doi.org/{doi}" if doi else url
    if tail:
        parts.append(tail)
    return " ".join(part for part in parts if part).strip()
