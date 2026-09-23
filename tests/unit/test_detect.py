from pathlib import Path

import pytest

from browsr.errors import BrowsrError
from browsr.fetch import detect

FIXTURES = Path(__file__).parents[1] / "fixtures" / "detect"


@pytest.mark.parametrize("name", ["fastly", "cloudflare"])
def test_challenge_fixtures(name):
    html = (FIXTURES / f"{name}.html").read_text()
    title = html.split("<title>", 1)[1].split("</title>", 1)[0]
    sample = html.split("<body>", 1)[1].split("</body>", 1)[0]
    with pytest.raises(BrowsrError) as error:
        detect.raise_for_challenge(200, title, len(sample), sample)
    assert error.value.code == "blocked"
    assert error.value.detail.startswith("challenge ")


def test_body_only_challenge():
    detect.raise_for_challenge(200, "Example", 1500, "Please complete the captcha")
    with pytest.raises(BrowsrError) as error:
        detect.raise_for_challenge(200, "Example", 70, "Please complete the captcha")
    assert error.value.code == "blocked"
    assert error.value.detail == "challenge body: captcha"


def test_short_normal_fixture_is_not_challenge():
    html = (FIXTURES / "normal.html").read_text()
    title = html.split("<title>", 1)[1].split("</title>", 1)[0]
    sample = html.split("<body>", 1)[1].split("</body>", 1)[0]
    detect.raise_for_challenge(200, title, len(sample), sample)
