"""Frozen byte/shape reference for rstone-recipe/1; NOT an authorizer.

Public production integrations must additionally enforce catalogue identity,
origin/manifest routing, exact migration approvals and all existing gates.
"""
import json
import re

SCHEMA = 'rstone-recipe/1'
RENDERER = 'rstone-recipe-markdown/1'
KEYS = frozenset(['schema', 'recipeId', 'title', 'classification', 'ownerProduct',
                  'associatedProducts', 'columns', 'rows', 'preTable', 'content'])
COLUMNS = [['原料编号', '投入数(g)', '百分比(%)', '供应商', '备注'],
           ['原料编号', '投入数', '百分比(%)', '供应商', '备注']]
MAX_FILE = 16 * 1024 * 1024
MAX_HEADER = 512 * 1024
MAX_DEPTH = 16
MAX_ITEMS = 1000
MAX_UTF16 = 4096


def require(ok, category='invalid-structured-recipe'):
    if not ok:
        raise ValueError(category)


def valid_string(value):
    return (type(value) is str
            and all((ord(c) >= 32 or c in '\t\n') and not 0xD800 <= ord(c) <= 0xDFFF
                    for c in value)
            and len(value.encode('utf-16-be')) // 2 <= MAX_UTF16)


def strings(value, nonempty=False):
    return (type(value) is list and len(value) <= MAX_ITEMS
            and (not nonempty or bool(value)) and all(valid_string(x) for x in value))


def validate(model):
    require(type(model) is dict and set(model) == KEYS)
    for key in ['schema', 'recipeId', 'title', 'classification', 'ownerProduct']:
        require(valid_string(model[key]) and bool(model[key]))
    require(model['schema'] == SCHEMA)
    require(re.fullmatch(r'[A-Za-z0-9._-]{1,128}', model['recipeId']) is not None)
    require(model['classification'] in ['main', 'example', 'reference', 'unverified'])
    require(type(model['columns']) is list and model['columns'] in COLUMNS)
    refs = model['associatedProducts']
    require(strings(refs, nonempty=True) and all(refs))
    require(len(set(refs)) == len(refs) and model['ownerProduct'] in refs)
    require(type(model['rows']) is list and 0 < len(model['rows']) <= MAX_ITEMS)
    for row in model['rows']:
        require(type(row) is list and len(row) == 5 and all(valid_string(c) for c in row))
        require(bool(row[0]) and bool(row[1]))
    require(strings(model['preTable']))
    content = model['content']
    require(type(content) is list and len(content) <= MAX_ITEMS)
    for block in content:
        require(type(block) is dict)
        if block.get('kind') == 'note':
            require(set(block) == {'kind', 'text'} and valid_string(block['text']))
        elif block.get('kind') == 'process':
            require(set(block) == {'kind', 'label', 'steps'}
                    and valid_string(block['label']) and bool(block['label'])
                    and strings(block['steps'], nonempty=True))
        else:
            require(False)
    return model


def entity_text(value):
    return ''.join('&#x%x;' % ord(c) for c in value)


def body(model):
    lines = ['# ' + entity_text(model['title'])]
    for paragraph in model['preTable']:
        lines += ['', entity_text(paragraph)]
    lines += ['', '## 配方',
              '| ' + ' | '.join(entity_text(x) for x in model['columns']) + ' |',
              '| --- | --- | --- | --- | --- |']
    lines += ['| ' + ' | '.join(entity_text(x) for x in row) + ' |' for row in model['rows']]
    for block in model['content']:
        if block['kind'] == 'note':
            lines += ['', entity_text(block['text'])]
        else:
            lines += ['', '## ' + entity_text(block['label'])]
            lines += ['- ' + entity_text(step) for step in block['steps']]
    return '\n'.join(lines) + '\n'


def header(model):
    return json.dumps(model, ensure_ascii=True, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def emit(model):
    validate(model)
    encoded_header = header(model).encode('ascii')
    require(len(encoded_header) <= MAX_HEADER, 'structured-header-limit')
    raw = b'---\n' + encoded_header + b'\n---\n' + body(model).encode('utf-8')
    require(len(raw) <= MAX_FILE, 'structured-file-limit')
    return raw


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate-structured-key')
        result[key] = value
    return result


def parse(raw):
    require(type(raw) is bytes and len(raw) <= MAX_FILE, 'structured-file-limit')
    require(raw.startswith(b'---\n'), 'structured-envelope-required')
    end = raw.find(b'\n---\n', 4)
    require(end >= 4 and end - 4 <= MAX_HEADER, 'structured-header-limit')
    encoded_header = raw[4:end]
    require(b'\n' not in encoded_header and b'\r' not in encoded_header,
            'structured-header-not-single-line')
    try:
        encoded_header.decode('ascii')
        model = json.loads(encoded_header.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: require(False, 'invalid-structured-constant'))
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError('invalid-structured-json') from exc
    stack = [(model, 0)]
    while stack:
        value, depth = stack.pop()
        require(depth <= MAX_DEPTH, 'structured-depth-limit')
        if type(value) is dict:
            stack.extend((v, depth + 1) for pair in value.items() for v in pair)
        elif type(value) is list:
            stack.extend((v, depth + 1) for v in value)
    validate(model)
    require(emit(model) == raw, 'noncanonical-envelope-or-display-mismatch')
    return model
