"""Convert the cleaned article HTML returned by the browser into Markdown."""

from markdownify import MarkdownConverter


class ArticleConverter(MarkdownConverter):
    def convert_img(self, el, text, parent_tags):
        return el.get("alt", "") or ""

    def convert_a(self, el, text, parent_tags):
        if "_noformat" in parent_tags:
            return text
        href = el.get("href")
        return f"[{text}]({href})" if href and text.strip() else text


def convert(html: str | None) -> str:
    return ArticleConverter(
        heading_style="ATX",
        bullets="-",
        escape_underscores=False,
        escape_asterisks=False,
        strip=["button", "input", "select", "textarea"],
    ).convert(html or "")
