"""Map HTTP and challenge pages to public error codes."""

import re

from ..errors import BrowsrError

_CHALLENGE = re.compile(
    r"just a moment|attention required|access denied|captcha|are you a robot|"
    r"security check|verify you are human",
    re.IGNORECASE,
)


def raise_for_status(status: int) -> None:
    if status >= 400 and status not in {401, 403, 429, 503}:
        raise BrowsrError("not_found", status=status)


def raise_for_challenge(status: int, title: str, text_len: int) -> None:
    if _CHALLENGE.search(title) or (status in {401, 403, 429, 503} and text_len < 2000):
        raise BrowsrError("blocked", status=status)
