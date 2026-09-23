"""Map HTTP and challenge pages to public error codes."""

import re

from ..errors import BrowsrError

TITLE_RE = re.compile(
    r"just a moment|attention required|access denied|captcha|are you a robot|"
    r"security check|verify you are human|client challenge|checking your browser|"
    r"ddos protection|bot verification|please verify",
    re.IGNORECASE,
)
BODY_RE = re.compile(
    r"captcha|enter the characters|verify you are (?:a )?human|checking your browser|"
    r"enable javascript and cookies|unusual traffic|are you a robot|access denied",
    re.IGNORECASE,
)


def raise_for_status(status: int) -> None:
    if status >= 400 and status not in {401, 403, 429, 503}:
        raise BrowsrError("not_found", status=status)


def raise_for_challenge(status: int, title: str, text_len: int, sample: str = "") -> None:
    if match := TITLE_RE.search(title):
        raise BrowsrError("blocked", status=status, detail=f"challenge title: {match.group(0)}")
    if text_len < 1500 and (match := BODY_RE.search(sample)):
        raise BrowsrError("blocked", status=status, detail=f"challenge body: {match.group(0)}")
    if status in {401, 403, 429, 503} and text_len < 2000:
        raise BrowsrError("blocked", status=status, detail=f"challenge HTTP {status}: short body")
