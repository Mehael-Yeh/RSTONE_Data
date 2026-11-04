import base64
import json
from pathlib import Path
import pytest
from rstone_data import recipe_codec as c

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((Path(__file__).parent/'fixtures/recipe_protocol/golden_v2.json').read_text())['cases']

@pytest.mark.parametrize('case',CASES,ids=lambda x:x['name'])
def test_frozen_v2(case):
    raw = base64.b64decode(case['rawBase64'])
    if not case['accept']:
        with pytest.raises(ValueError): c.parse(raw)
        return
    assert c.parse(raw) == case['model']
    assert c.emit(case['model']) == raw
    expected = ''.join(s['displayText'] if s['kind']=='link' else c.entity_text(s['text'])
                       for s in case['expectedFirstCellSegments'])
    lines = c.body(case['model']).splitlines()
    assert lines[lines.index('| --- | --- | --- | --- | --- |')+1].startswith('| ' + expected + ' |')


def test_workflow_default_client_floor_supports_v2():
    from rstone_data.client import version_tuple
    config = json.loads((ROOT/'config/release.json').read_text())
    assert version_tuple(config['minAppVersion']) >= (2,0,5)
    workflow = (ROOT/'.github/workflows/publish-data.yml').read_text()
    assert 'publisher/scripts/data.py prepare' in workflow
