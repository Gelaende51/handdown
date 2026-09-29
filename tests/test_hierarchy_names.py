import pytest

from handdown.hierarchy.names import name_roles


@pytest.fixture(scope="module", autouse=True)
def _wn():
    from handdown.concepts import wordnet

    try:
        wordnet()
    except LookupError:
        pytest.skip("WordNet data not downloaded")


def test_meaning_only():
    r = name_roles("download")
    assert r.object_tokens == [] and r.meaning_tokens == ["download"]


def test_object_only():
    r = name_roles("floppy-disk")
    assert r.object_tokens == ["floppy", "disk"] and r.meaning_tokens == []


def test_object_and_meaning():
    r = name_roles("cloud-download-outline")
    assert r.object_tokens == ["cloud"] and r.meaning_tokens == ["download"]


def test_view_and_variety():
    r = name_roles("coffee-cup-hot-side")
    assert r.view == "side"
    assert r.varieties == ["steam"]
    assert r.object_tokens == ["coffee", "cup"]


def test_unknown_tokens_do_not_crash():
    r = name_roles("ic-24")
    assert r.view == "unknown" and r.varieties == []


def test_short_words_are_ignored():
    r = name_roles("document-as-pdf")
    assert "as" not in r.object_tokens + r.meaning_tokens
