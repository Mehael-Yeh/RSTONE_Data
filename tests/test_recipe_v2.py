"""Synthetic v2 contract boundaries; v1 fixtures remain untouched."""
import copy
import pytest
from rstone_data import recipe_codec as c
from test_structured_publication import model, OWNER


def test_v2_embedded_adjacent_links_and_v1_unchanged():
    m = model(); m['schema'] = 'rstone-recipe/2'
    m['rows'][0][0] = '前[[' + OWNER + ']][[' + OWNER + ']]后'
    raw = c.emit(m)
    assert c.parse(raw) == m
    assert ('&#x524d;[[产品列表/' + OWNER + ']][[产品列表/' + OWNER + ']]&#x540e;').encode() in raw
    old = copy.deepcopy(m); old['schema'] = c.SCHEMA
    assert '[[' not in c.body(old)
    assert c.renderer_for(m) == 'rstone-recipe-markdown/2'
    with pytest.raises(ValueError): c.parse(raw.replace(b'[[', b'&#x5b;&#x5b;', 1))


@pytest.mark.parametrize('text', ['[[UNKNOWN]]', '[[{p}|alias]]', '[[/{p}]]', '[[https://{p}]]',
    '[[[{p}]]]', '[[x[[{p}]]', '\\[[{p}]]', '[[{p}\n]]', '[[{p}\t]]',
    '[[<b>{p}</b>]]', '[[]]', '[[' + 'A'*129 + ']]', '[[{p}', '[[{p.lower}]]'])
def test_v2_unsafe_candidates_are_fully_encoded(text):
    text = text.replace('{p}', OWNER).replace('{p.lower}', OWNER.lower())
    m = model(); m['schema'] = 'rstone-recipe/2'; m['rows'][0][0] = text
    assert c.entity_text(text) in c.body(c.parse(c.emit(m)))


def test_v2_other_fields_never_upgrade_and_unknown_schema_rejected():
    m = model(); m['schema'] = 'rstone-recipe/2'
    token = '[[' + OWNER + ']]'
    m['rows'][0][4] = token; m['preTable'] = [token]
    m['content'] = [{'kind': 'note', 'text': token}]
    assert c.body(c.parse(c.emit(m))).count('[[产品列表/' + OWNER + ']]') == 1
    m['schema'] = 'rstone-recipe/3'
    with pytest.raises(ValueError): c.emit(m)
