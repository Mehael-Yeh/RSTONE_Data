"""Real build/store and witness paths, synthetic business identifiers only."""
import copy
import pytest
from rstone_data import recipe_codec as c, catalog_revisions_v2 as v, quality_gate as q
from rstone_data.protocol import DataError
from rstone_data.build import make_manifest
from test_structured_publication import model, snapshot, PATH
from test_protocol_migration_policy import proof, legacy
from test_quality_parent_review import RecordingSigner
from test_identity_migration_security import artifacts


def snap2():
    m = model(); m['schema'] = 'rstone-recipe/2'
    return snapshot(m)


@pytest.mark.parametrize('minimum', ['1.0.0','2.0.2','2.0.4'])
def test_build_rejects_low_minimum(minimum):
    with pytest.raises(DataError, match='2.0.5'):
        make_manifest(snap2(),1,'2026.01.01.001','2026-01-01T00:00:00Z','test-key',minimum)


def test_build_v2_sets_manifest_schema():
    manifest = make_manifest(snap2(),1,'2026.01.01.001','2026-01-01T00:00:00Z','test-key','2.0.5')
    assert manifest['schemaVersion'] == 2


@pytest.mark.parametrize('minimum', ['2.0.2','2.0.4'])
def test_store_rejects_before_signing(store, signing, minimum):
    signer = RecordingSigner(signing[0]); before = artifacts(store)
    with pytest.raises(DataError, match='2.0.5'):
        store.prepare(snap2(),signer,public_scope_approved=True,min_app_version=minimum)
    assert signer.calls == 0 and artifacts(store) == before


def test_store_v2_success(store,signing):
    store.prepare(snap2(),signing[0],public_scope_approved=True,min_app_version='2.0.5')
    assert store.read(store.pending()['pointer'])['minAppVersion'] == '2.0.5'


@pytest.mark.parametrize('operation',['resume','promote','complete','package','rollback'])
def test_signed_v2_with_low_minimum_rejected_all_exits(store,signing,operation):
    from rstone_data.protocol import canonical_json, sha256
    from rstone_data.release import pointer_for
    from rstone_data.storage import atomic_write
    snap = snap2()
    manifest = make_manifest(snap,1,'2026.01.01.001','2026-01-01T00:00:00Z',signing[0].key_id,'2.0.5')
    manifest['minAppVersion']='2.0.2'
    raw=canonical_json(manifest); pointer=pointer_for(manifest,raw)
    for data in snap.files.values(): atomic_write(store.public/'objects'/sha256(data),data)
    atomic_write(store.public/pointer['manifestPath'],raw)
    atomic_write(store.public/pointer['signaturePath'],signing[0].sign(raw))
    if operation in {'resume','promote','complete'}:
        atomic_write(store.state/'pending.json',canonical_json({'pointer':pointer,'previousLatestSha256':None,'summary':{'sequence':1}}))
    else: atomic_write(store.public/'latest.json',canonical_json(pointer))
    before=artifacts(store); signer=RecordingSigner(signing[0])
    with pytest.raises(DataError,match='2.0.5'):
        if operation=='resume': store.prepare(None,signer,public_scope_approved=True)
        elif operation=='promote': store.promote()
        elif operation=='complete': store.complete(lambda _:pytest.fail('verifier reached'))
        elif operation=='package': store.package(1)
        else: store.rollback(1,signer,public_scope_approved=True)
    assert before == artifacts(store) and signer.calls == 0


def corpora(mixed=False):
    a = snap2()
    if mixed:
        m = model(); m['recipeId'] += '-OTHER'; m['title'] += '-OTHER'; m['classification'] = 'example'
        a.files['产品配方/'+m['title']+'.md'] = c.emit(m)
    b = legacy(a)
    before,after = q.snapshot_corpus(b),q.snapshot_corpus(a)
    before.manifest_sha256 = 'a'*64
    return b,a,before,after


@pytest.mark.parametrize('mixed',[False,True])
def test_transaction_uses_highest_actual_version(mixed):
    _,_,b,a = corpora(mixed)
    payload = v.transaction_payload(b,a,[],None)
    assert (payload['protocol'],payload['renderer']) == ('rstone-recipe/2','rstone-recipe-markdown/2')


def test_witness_accepts_explicit_v2_pair():
    b,a,_,_ = corpora(); w=proof(b,a,'a'*64)
    w.update(protocol='rstone-recipe/2',renderer='rstone-recipe-markdown/2')
    assert v.validate_witness(w) == w


@pytest.mark.parametrize('pair',[('rstone-recipe/1','rstone-recipe-markdown/2'),
    ('rstone-recipe/2','rstone-recipe-markdown/1'),('rstone-recipe/3','rstone-recipe-markdown/3')])
def test_witness_rejects_cross_unknown_pairs(pair):
    b,a,_,_ = corpora(); w=proof(b,a,'a'*64); w.update(protocol=pair[0],renderer=pair[1])
    with pytest.raises(DataError): v.validate_witness(w)


def test_witness_metadata_must_match_actual_documents():
    b,a,bc,ac = corpora(); w=proof(b,a,'a'*64)
    assert v._matching_witness(bc,ac,[('b'*64,w)],{'c'*64}) is None
    w.update(protocol='rstone-recipe/2',renderer='rstone-recipe-markdown/2')
    assert v._matching_witness(bc,ac,[('b'*64,w)],{'c'*64}) == 'b'*64
