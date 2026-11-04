"""Pre-release /2 revision002 changes display only, never source tokens."""
from rstone_data import recipe_codec as c
from test_structured_publication import model, OWNER


def test_v2_body_qualifies_only_valid_ingredient_tokens():
    m = model()
    m['schema'] = c.SCHEMA_V2
    token = '[[' + OWNER + ']]'
    m['rows'][0][0] = token
    before = c.header(m)
    assert ('[[产品列表/' + OWNER + ']]') in c.body(m)
    assert m['rows'][0][0] == token
    assert c.header(m) == before
    assert c.parse(c.emit(m)) == m


def test_qualified_input_and_associated_paths_are_not_tokens():
    m = model()
    m['schema'] = c.SCHEMA_V2
    for target in ['产品列表/' + OWNER, '../' + OWNER, 'other/' + OWNER]:
        m['associatedProducts'] = [OWNER, target]
        m['rows'][0][0] = '[[' + target + ']]'
        assert c.entity_text(m['rows'][0][0]) in c.body(m)
        assert '[[' not in c.body(m)
        assert c.parse(c.emit(m)) == m
