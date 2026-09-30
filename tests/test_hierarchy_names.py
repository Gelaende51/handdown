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


def test_object_head_from_descriptive_phrase():
    from handdown.hierarchy.names import object_head

    assert object_head("down arrow in circle") == (["arrow"], ["circle", "down"])
    assert object_head("download arrow with tray") == (["arrow"], ["download", "tray"])
    assert object_head("shield with person icon") == (["shield"], ["person"])  # generic "icon" dropped
    assert object_head("floppy disk")[0] == ["floppy", "disk"]  # a compound WordNet knows stays whole
    assert object_head("octopus cat mascot")[0] == ["mascot"]


def test_directions_are_orientation_not_objects():
    r = name_roles("arrow-down-circle")
    assert r.object_tokens == ["arrow"] and "points-down" in r.varieties
    from handdown.hierarchy.names import object_head

    assert object_head("down arrow")[0] == ["arrow"]
    assert object_head("app icon")[0] == ["app"]
