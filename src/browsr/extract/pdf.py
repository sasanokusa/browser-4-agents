"""Text extraction for PDFs."""

from concurrent.futures import ThreadPoolExecutor

import pymupdf

from ..errors import BrowsrError

# All PyMuPDF API calls made by this package run on this one thread.  The caller
# may be any asyncio.to_thread worker, but PDF documents never cross threads.
_PDF_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browsr-pdf")


def _extract_on_worker(body: bytes, max_pages: int) -> tuple[str, str]:
    try:
        with pymupdf.open(stream=body, filetype="pdf") as doc:
            extracted = [
                doc[index].get_text("text").strip() for index in range(min(len(doc), max_pages))
            ]
            title = (doc.metadata or {}).get("title") or ""
    except (ValueError, RuntimeError) as exc:
        raise BrowsrError("unsupported", detail="Invalid PDF") from exc
    if sum(len(text) for text in extracted) < 50:
        raise BrowsrError("unsupported", detail="PDF has no extractable text")
    markdown = "\n\n".join(
        f"### Page {index}\n\n{text}" for index, text in enumerate(extracted, start=1)
    )
    return markdown, title


def to_markdown(body: bytes | None, max_pages: int = 300) -> tuple[str, str]:
    if not body:
        raise BrowsrError("unsupported")
    return _PDF_WORKER.submit(_extract_on_worker, body, max_pages).result()
