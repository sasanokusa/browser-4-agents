from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = {"fbclid", "gclid", "yclid", "mc_cid", "mc_eid", "ref_src"}


def normalize(url: str) -> str:
    """Return a stable URL key without fragments and common tracking parameters."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").encode("idna").decode("ascii").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host
    if parts.username is not None:
        auth = parts.username
        if parts.password is not None:
            auth += f":{parts.password}"
        netloc = f"{auth}@{netloc}"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc += f":{port}"
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING:
            continue
        query.append((key, value))
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(query, doseq=True), ""))
