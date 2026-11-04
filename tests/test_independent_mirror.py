"""Exercise actual mirror publication, ephemeral keys and loopback only."""
import pytest
from rstone_data.protocol import canonical_json,sha256,DataError
from rstone_data.build import make_manifest
from rstone_data.release import pointer_for
from rstone_data.storage import atomic_write
from rstone_data.mirrors import deploy_mirror
from test_recipe_v2_release import snap2

@pytest.mark.parametrize('outer',[1,2])
def test_mirror_rejects_v2_with_low_floor(store,signing,serve,tmp_path,outer):
    s=snap2();m=make_manifest(s,1,'2026.01.01.001','2026-01-01T00:00:00Z',signing[0].key_id,'2.0.5')
    m.update(schemaVersion=outer,minAppVersion='2.0.4')
    raw=canonical_json(m);p=pointer_for(m,raw)
    for data in s.files.values():atomic_write(store.public/'objects'/sha256(data),data)
    atomic_write(store.public/p['manifestPath'],raw);atomic_write(store.public/p['signaturePath'],signing[0].sign(raw));atomic_write(store.public/'latest.json',canonical_json(p))
    target=tmp_path/'mirror';target.mkdir()
    with serve(target) as url:
        with pytest.raises(DataError):deploy_mirror(store.root,target,url,signing[1],allow_local_http=True)
    assert not (target/'latest.json').exists()
