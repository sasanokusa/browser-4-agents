from browsr.tokens import estimate


def test_estimate_counts_ascii_quarters_and_every_non_ascii_character():
    assert estimate("12345678") == 2
    assert estimate("日本語") == 3
    assert estimate("é🙂") == 2
    assert estimate("aébc") == 2
