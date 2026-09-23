from __future__ import annotations


class BrowsrError(Exception):
    def __init__(self, code: str, *, status: int | None = None, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail


ALT_CODES = {"timeout", "blocked", "not_found", "unsupported", "disallowed"}
HINTS: dict[str, str] = {
    "bad_input": "Pass a URL, a result number, or search words.",
    "unknown_id": "Number not found. search again or pass a URL.",
    "timeout": "Page too slow. Try another result.",
    "blocked": "Site blocked access. Try another result.",
    "not_found": "Page not found (HTTP {status}). Try another result.",
    "unsupported": "Cannot read this file type. Try another result.",
    "forbidden_target": "This address is not allowed.",
    "disallowed": "Site disallows robots. Try another result.",
    "search_unavailable": "Search is temporarily unavailable. Try again later or open a known URL.",
}


def hint(code: str, status: int | None = None, alt_id: int | None = None) -> str:
    template = HINTS.get(code, "Request failed.")
    message = template.format(status=status if status is not None else "unknown")
    if code in ALT_CODES and alt_id is not None:
        message = message.removesuffix(".") + f", e.g. open({alt_id})."
    return message
