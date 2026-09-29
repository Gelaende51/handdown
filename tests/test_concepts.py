import pytest

from handdown.concepts import clean_tokens, negation, resolve, split_name


def test_split_name():
    assert split_name("ic_fluent_delete_24_regular") == ["ic", "fluent", "delete", "24", "regular"]
    assert split_name("TrashCanOutline") == ["trash", "can", "outline"]
    assert split_name("arrow-left.circle") == ["arrow", "left", "circle"]


def test_clean_tokens_drops_style_size_and_container():
    assert clean_tokens(["trash", "can", "outline"]) == ["trash", "can"]
    assert clean_tokens(["delete", "24", "regular"]) == ["delete"]
    assert clean_tokens(["arrow", "left", "circle", "fill"]) == ["arrow", "left"]
    assert clean_tokens(["circle"]) == ["circle"]  # a container alone is the content
    assert clean_tokens(["number", "24"]) == ["number", "24"]  # only trailing sizes after style go
    assert clean_tokens(["user", "plus", "bold"]) == ["user", "plus"]


def test_negation():
    assert negation(["bell", "off"]) == "slash"
    assert negation(["bell"]) == "none"


@pytest.fixture(scope="module")
def wn():
    from handdown.concepts import wordnet

    try:
        return wordnet()
    except LookupError:
        pytest.skip("WordNet data not downloaded")


def test_resolve_synset_merges_synonyms(wn):
    a = resolve(["trash", "can"], wn)
    b = resolve(["ashcan"], wn)
    assert a.id == b.id == "wn:ashcan.n.01"
    assert a.referent_type == "object"


def test_resolve_unknown_phrase_is_term_with_parent(wn):
    c = resolve(["arrow", "left"], wn)
    assert c.id == "term:arrow left"
    assert c.parent_id == "wn:arrow.n.01"


def test_resolve_verb(wn):
    assert resolve(["delete"], wn).referent_type == "action"
