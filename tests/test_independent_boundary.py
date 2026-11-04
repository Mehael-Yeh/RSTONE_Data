"""Independent synthetic signed entry probes; never production trust."""
import pytest
from rstone_data.protocol import canonical_json, sha256, DataError
from rstone_data.build import make_manifest
from rstone_data.release import pointer_for
from rstone_data.storage import atomic_write
from rstone_data import recipe_codec as c, catalog_revisions_v2 as v
from test_recipe_v2_release import snap2, corpora
from test_protocol_migration_policy import proof
from test_identity_migration_security import artifacts
from test_quality_parent_review import RecordingSigner

@pytest.mark.parametrize('minimum',['1.0.0','2.0.2','2.0.4'])
@pytest.mark.parametrize('op',['read','resume','promote','complete','package','rollback'])
def test_independent_signed_low_min(store,signing,minimum,op):
    snap=snap2()
    m=make_manifest(snap,1,'2026.01.01.001','2026-01-01T00:00:00Z',signing[0].key_id,'2.0.5')
    m['minAppVersion']=minimum
    raw=canonical_json(m); p=pointer_for(m,raw)
    for data in snap.files.values(): atomic_write(store.public/'objects'/sha256(data),data)
    atomic_write(store.public/p['manifestPath'],raw)
    atomic_write(store.public/p['signaturePath'],signing[0].sign(raw))
    if op in ('resume','promote','complete'):
        atomic_write(store.state/'pending.json',canonical_json({'pointer':p,'previousLatestSha256':None,'summary':{'sequence':1}}))
    else: atomic_write(store.public/'latest.json',canonical_json(p))
    before=artifacts(store); signer=RecordingSigner(signing[0])
    with pytest.raises(DataError,match='2.0.5'):
        if op=='read':store.read(p)
        elif op=='resume':store.prepare(None,signer,public_scope_approved=True)
        elif op=='promote':store.promote()
        elif op=='complete':store.complete(lambda _:pytest.fail('unexpected verifier'))
        elif op=='package':store.package(1)
        else:store.rollback(1,signer,public_scope_approved=True)
    assert signer.calls==0 and artifacts(store)==before

@pytest.mark.parametrize('mixed',[False,True])
def test_independent_witness_actual_version(mixed):
    b,a,bc,ac=corpora(mixed)
    w=proof(b,a,'a'*64)
    assert v._matching_witness(bc,ac,[('b'*64,w)],{'c'*64}) is None
    for p,r in [('rstone-recipe/2','rstone-recipe-markdown/1'),('rstone-recipe/1','rstone-recipe-markdown/2')]:
        w.update(protocol=p,renderer=r)
        with pytest.raises(DataError):v.validate_witness(w)
    w.update(protocol=c.SCHEMA_V2,renderer=c.RENDERER_V2)
    assert v._matching_witness(bc,ac,[('b'*64,w)],{'c'*64})=='b'*64
