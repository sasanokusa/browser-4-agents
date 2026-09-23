"""Split extracted Markdown into deterministic, bounded response parts."""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from .config import OutputConfig
from .models import Page
from .tokens import estimate

_HEADING = re.compile(r"^#{1,6}\s")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SENTENCE = re.compile(r"(?<=[。．！？!?])|(?<=\.)(?=\s)")
_PLACEHOLDER = re.compile(r"\[[^\n]*?\]\(@L\d+\)")


def _sentences(text: str) -> list[str]:
    protected = [(match.start(), match.end()) for match in _PLACEHOLDER.finditer(text)]
    starts = [0]
    for match in _SENTENCE.finditer(text):
        pos = match.start()
        if not any(start < pos < end for start, end in protected):
            starts.append(pos)
    starts.append(len(text))
    return [
        text[starts[i] : starts[i + 1]] for i in range(len(starts) - 1) if starts[i] < starts[i + 1]
    ]


@dataclass(frozen=True, slots=True)
class _Block:
    kind: str
    text: str


def _blocks(markdown: str) -> list[_Block]:
    lines = markdown.splitlines()
    blocks: list[_Block] = []
    pos = 0
    while pos < len(lines):
        line = lines[pos]
        if not line.strip():
            pos += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            start = pos
            pos += 1
            while pos < len(lines):
                closing = _FENCE.match(lines[pos])
                if (
                    closing
                    and closing.group(1)[0] == marker[0]
                    and len(closing.group(1)) >= len(marker)
                    and not lines[pos].strip().strip(marker[0])
                ):
                    pos += 1
                    break
                pos += 1
            blocks.append(_Block("code", "\n".join(lines[start:pos])))
            continue
        if line.startswith("|"):
            start = pos
            while pos < len(lines) and lines[pos].startswith("|"):
                pos += 1
            blocks.append(_Block("table", "\n".join(lines[start:pos])))
            continue
        if _HEADING.match(line):
            blocks.append(_Block("heading", line))
            pos += 1
            continue
        start = pos
        while (
            pos < len(lines)
            and lines[pos].strip()
            and not _FENCE.match(lines[pos])
            and not lines[pos].startswith("|")
            and not _HEADING.match(lines[pos])
        ):
            pos += 1
        blocks.append(_Block("para", "\n".join(lines[start:pos])))
    return blocks


class Chunker:
    def __init__(self, output: OutputConfig, cost_fn: Callable[[str], int] | None = None):
        self.output = output
        self.budget = max(1, output.max_tokens - output.envelope_reserve)
        self.cost_fn = cost_fn or estimate
        self._cache: OrderedDict[tuple[str, int, int], tuple[str, ...]] = OrderedDict()

    def _fits(self, text: str) -> bool:
        return self.cost_fn(text) <= self.budget

    @staticmethod
    def _join(parts: list[str]) -> str:
        return "\n\n".join(part for part in parts if part)

    def _hard_split(
        self, text: str, prefix: str = "", suffix: str = "", context: str = ""
    ) -> list[str]:
        """Split an exceptional overlong line without losing any characters."""
        parts: list[str] = []
        remaining = text
        while remaining:
            low, high = 1, len(remaining)
            best = 0
            while low <= high:
                mid = (low + high) // 2
                wrapped = prefix + remaining[:mid] + suffix
                if self._fits(self._join([context, wrapped])):
                    best = mid
                    low = mid + 1
                else:
                    high = mid - 1
            if best:
                for match in _PLACEHOLDER.finditer(remaining):
                    if match.start() < best < match.end():
                        best = match.start()
                        if best == 0 and not (prefix or suffix or context):
                            # This link is too large to fit intact; keep its label.
                            remaining = (
                                match.group()[1 : match.group().rfind("](@L")]
                                + remaining[match.end() :]
                            )
                        break
            if best == 0:
                # The wrapper alone exceeds the budget. Fall back to plain text.
                if prefix or suffix or context:
                    prefix = suffix = context = ""
                    continue
                oversized_link = _PLACEHOLDER.match(remaining)
                if oversized_link:
                    remaining = (
                        oversized_link.group()[1 : oversized_link.group().rfind("](@L")]
                        + remaining[oversized_link.end() :]
                    )
                    continue
                best = 1
            parts.append(prefix + remaining[:best] + suffix)
            remaining = remaining[best:]
            context = ""
        return parts

    def _split_para(self, text: str, first_prefix: str = "") -> list[str]:
        sentences = _sentences(text)
        parts: list[str] = []
        current = ""
        for sentence in sentences:
            candidate = current + sentence
            prefix = first_prefix if not parts else ""
            if self._fits(self._join([prefix, candidate])):
                current = candidate
                continue
            if current:
                parts.append(current)
                current = ""
                prefix = ""
            if self._fits(self._join([prefix, sentence])):
                current = sentence
                continue
            for fragment in self._hard_split(sentence, context=prefix):
                if current:
                    parts.append(current)
                current = fragment
                prefix = ""
        if current:
            parts.append(current)
        return parts

    def _split_code(self, text: str, first_prefix: str = "") -> list[str]:
        lines = text.splitlines()
        opening = lines[0]
        marker = _FENCE.match(opening)
        if not marker:
            return self._split_para(text)
        closing = marker.group(1)[0] * len(marker.group(1))
        body = lines[1:-1] if lines[-1].strip() == closing else lines[1:]
        if not body:
            return [text] if self._fits(text) else self._hard_split(text)
        parts: list[str] = []
        current: list[str] = []
        for line in body:
            candidate = "\n".join([opening, *current, line, closing])
            if self._fits(self._join([first_prefix if not parts else "", candidate])):
                current.append(line)
                continue
            if current:
                parts.append("\n".join([opening, *current, closing]))
                current = []
            if self._fits(
                self._join([first_prefix if not parts else "", "\n".join([opening, line, closing])])
            ):
                current = [line]
            else:
                parts.extend(
                    self._hard_split(
                        line,
                        opening + "\n",
                        "\n" + closing,
                        first_prefix if not parts else "",
                    )
                )
        if current:
            parts.append("\n".join([opening, *current, closing]))
        return parts

    def _split_table(self, text: str, first_prefix: str = "") -> list[str]:
        lines = text.splitlines()
        header = lines[:2]
        rows = lines[2:]
        if not rows:
            return [text] if self._fits(text) else self._hard_split(text)
        parts: list[str] = []
        current: list[str] = []
        for row in rows:
            candidate = "\n".join([*header, *current, row])
            if self._fits(self._join([first_prefix if not parts else "", candidate])):
                current.append(row)
                continue
            if current:
                parts.append("\n".join([*header, *current]))
                current = []
            if self._fits(
                self._join([first_prefix if not parts else "", "\n".join([*header, row])])
            ):
                current = [row]
            else:
                # A single row is too wide to preserve intact within the budget.
                parts.extend(
                    self._hard_split(
                        row,
                        "\n".join(header) + "\n",
                        context=first_prefix if not parts else "",
                    )
                )
        if current:
            parts.append("\n".join([*header, *current]))
        return parts

    def _split_large(self, block: _Block, first_prefix: str = "") -> list[str]:
        if block.kind == "code":
            parts = self._split_code(block.text, first_prefix)
        elif block.kind == "table":
            parts = self._split_table(block.text, first_prefix)
        else:
            parts = self._split_para(block.text, first_prefix)
        return parts

    def split(self, markdown: str) -> list[str]:
        if not markdown.strip():
            return [""]
        chunks: list[str] = []
        current: list[_Block] = []

        def flush() -> None:
            nonlocal current
            if not current:
                return
            if len(current) > 1 and current[-1].kind == "heading":
                keep = current.pop()
                chunks.append(self._join([b.text for b in current]))
                current = [keep]
            else:
                chunks.append(self._join([b.text for b in current]))
                current = []

        for block in _blocks(markdown):
            candidate = self._join([*(b.text for b in current), block.text])
            heading_break = (
                block.kind == "heading"
                and current
                and self.cost_fn(self._join([b.text for b in current])) >= self.budget * 0.5
            )
            if not heading_break and self._fits(candidate):
                current.append(block)
                continue
            if current and current[-1].kind != "heading":
                flush()
            elif len(current) > 1:
                flush()
            candidate = self._join([*(b.text for b in current), block.text])
            if self._fits(candidate):
                current.append(block)
                continue
            prefix = self._join([b.text for b in current])
            parts = self._split_large(block, prefix)
            if not parts:
                continue
            if prefix and self._fits(self._join([prefix, parts[0]])):
                chunks.append(self._join([prefix, parts.pop(0)]))
                current = []
            elif prefix:
                # If a structure cannot share a part with a heading, keep the heading
                # attached to the following content using a smaller first split.
                if block.kind == "para":
                    parts = self._split_para(block.text, prefix)
                    if parts and self._fits(self._join([prefix, parts[0]])):
                        chunks.append(self._join([prefix, parts.pop(0)]))
                        current = []
                if current:
                    flush()
            chunks.extend(parts[:-1])
            if parts:
                current = [_Block(block.kind, parts[-1])]
        flush()
        return [chunk for chunk in chunks if chunk] or [""]

    def split_cached(self, page: Page) -> list[str]:
        key = (page.final_url, page.created, self.output.max_tokens)
        if key in self._cache:
            self._cache.move_to_end(key)
            return list(self._cache[key])
        chunks = self.split(page.markdown)
        self._cache[key] = tuple(chunks)
        if len(self._cache) > 64:
            self._cache.popitem(last=False)
        return chunks
