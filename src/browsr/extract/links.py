"""Replace article links with stable placeholders for session rendering."""

import re
from urllib.parse import urljoin, urlsplit

from ..urlnorm import normalize

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(?:[^\n]*)$")
_TITLE = re.compile(r'\s+"(?:[^"\\]|\\.)*"$')


def _link_at(line: str, start: int) -> tuple[int, str, str] | None:
    """Return end, label, destination for a same-line inline Markdown link."""
    if line[start] != "[":
        return None
    depth = 1
    pos = start + 1
    while pos < len(line) and depth:
        if line[pos] == "\\":
            pos += 2
            continue
        if line[pos] == "[":
            depth += 1
        elif line[pos] == "]":
            depth -= 1
        pos += 1
    if depth or pos >= len(line) or line[pos] != "(":
        return None
    label = line[start + 1 : pos - 1]
    dest_start = pos + 1
    pos = dest_start
    depth = 1
    while pos < len(line) and depth:
        if line[pos] == "\\":
            pos += 2
            continue
        if line[pos] == "(":
            depth += 1
        elif line[pos] == ")":
            depth -= 1
        pos += 1
    if depth:
        return None
    destination = line[dest_start : pos - 1].strip()
    destination = _TITLE.sub("", destination).strip()
    if destination.startswith("<") and destination.endswith(">"):
        destination = destination[1:-1]
    return pos, label, destination


def rewrite(md: str, base_url: str, max_links: int) -> tuple[str, list[str]]:
    links: list[str] = []
    seen: dict[str, int] = {}
    output: list[str] = []
    fence_char = ""
    fence_len = 0
    for line in md.splitlines(keepends=True):
        fence = _FENCE.match(line.rstrip("\r\n"))
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_len = marker[0], len(marker)
            elif (
                marker[0] == fence_char
                and len(marker) >= fence_len
                and not line.strip().strip(marker[0])
            ):
                fence_char, fence_len = "", 0
            output.append(line)
            continue
        if fence_char:
            output.append(line)
            continue
        result: list[str] = []
        pos = 0
        while pos < len(line):
            if line[pos] == "`":
                run = len(line[pos:]) - len(line[pos:].lstrip("`"))
                closing = line.find("`" * run, pos + run)
                if closing != -1:
                    result.append(line[pos : closing + run])
                    pos = closing + run
                    continue
            image = line.startswith("![", pos) and (pos == 0 or line[pos - 1] != "\\")
            bracket = pos + 1 if image else pos
            if line[bracket : bracket + 1] == "[" and (
                bracket == 0 or line[bracket - 1] != "\\" or image
            ):
                found = _link_at(line, bracket)
                if found:
                    end, label, href = found
                    if image:
                        pos = end
                        continue
                    label = label.strip()[:200]
                    if not label:
                        pos = end
                        continue
                    try:
                        absolute = urljoin(base_url, href)
                        parsed = urlsplit(absolute)
                        valid = parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
                    except ValueError:
                        valid = False
                    if not valid:
                        result.append(label)
                    else:
                        key = normalize(absolute)
                        number = seen.get(key)
                        if number is None and len(links) < max_links:
                            number = len(links)
                            seen[key] = number
                            links.append(absolute)
                        result.append(f"[{label}](@L{number})" if number is not None else label)
                    pos = end
                    continue
            result.append(line[pos])
            pos += 1
        output.append("".join(result))
    return "".join(output), links
