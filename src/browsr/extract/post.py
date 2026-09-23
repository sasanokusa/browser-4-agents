"""Small, predictable cleanups after format conversion."""

import re

_ZERO_WIDTH = str.maketrans("", "", "\u200b\u200c\u200d\ufeff")


def post(md: str) -> str:
    md = md.translate(_ZERO_WIDTH).replace("\u00a0", " ")
    md = re.sub(r"[ \t]+$", "", md, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", md).strip()
