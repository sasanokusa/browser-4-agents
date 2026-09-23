"""Turn fetched page data into cacheable, link-neutral article content."""

import json
import re
import time
from importlib.resources import files
from urllib.parse import unquote, urlsplit

from ..config import Config
from ..errors import BrowsrError
from ..models import Page, RawPage
from . import links, markdown, pdf
from .post import post

_ASSETS = files(__package__).joinpath("assets")
_READABILITY_SRC = _ASSETS.joinpath("Readability.js").read_text(encoding="utf-8")
_EXTRACT_BODY = _ASSETS.joinpath("extract.js").read_text(encoding="utf-8")
EXTRACT_FN = "(args) => {\n" + _READABILITY_SRC + "\n" + _EXTRACT_BODY + "\n}"
CONSENT_JS = _ASSETS.joinpath("consent.js").read_text(encoding="utf-8")

_CHARSET = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\s\"']+)", re.I)


def _decode(body: bytes | None, content_type: str) -> str:
    encoding_match = _CHARSET.search(content_type)
    encoding = encoding_match.group(1) if encoding_match else "utf-8"
    try:
        return (body or b"").decode(encoding, errors="replace")
    except LookupError:
        return (body or b"").decode("utf-8", errors="replace")


def _title(raw: RawPage, pdf_title: str) -> str:
    if raw.title and raw.title.strip():
        return raw.title.strip()
    if pdf_title and pdf_title.strip():
        return pdf_title.strip()
    path = urlsplit(raw.final_url or raw.url).path.rstrip("/")
    return unquote(path.rsplit("/", 1)[-1]) or urlsplit(raw.final_url or raw.url).hostname or ""


def _limit(md: str, maximum: int) -> str:
    if len(md) <= maximum:
        return md
    marker = "\n\n(truncated)"
    if maximum <= len(marker):
        return marker[-maximum:] if maximum > 0 else ""
    return md[: maximum - len(marker)].rstrip() + marker


def to_page(raw: RawPage, cfg: Config) -> Page:
    content_type = raw.content_type.split(";", 1)[0].strip().lower()
    pdf_title = ""
    extracted_links: list[str] = []
    if content_type in {"text/html", "application/xhtml+xml"}:
        md = raw.text if raw.text is not None else markdown.convert(raw.html)
        md, extracted_links = links.rewrite(md, raw.final_url or raw.url, cfg.output.max_links)
    elif content_type == "application/pdf":
        md, pdf_title = pdf.to_markdown(raw.body, cfg.extract.max_pdf_pages)
    elif content_type in {"text/plain", "text/markdown", "text/csv"}:
        md = _decode(raw.body, raw.content_type)
    elif content_type == "application/json" or content_type.endswith("+json"):
        try:
            data = json.loads(_decode(raw.body, raw.content_type))
        except json.JSONDecodeError as exc:
            raise BrowsrError("unsupported", detail="Invalid JSON") from exc
        md = "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"
    else:
        raise BrowsrError("unsupported")
    md = _limit(post(md) or "(empty page)", cfg.output.max_markdown_chars)
    return Page(
        url=raw.url,
        final_url=raw.final_url,
        title=_title(raw, pdf_title),
        markdown=md,
        links=extracted_links,
        content_type=content_type,
        created=int(time.time()),
    )


__all__ = ["CONSENT_JS", "EXTRACT_FN", "to_page"]
