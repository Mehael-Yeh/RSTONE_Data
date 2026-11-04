"""Synthetic complete-name regressions; baselines use real collect/prepare/signing."""
import json
from pathlib import Path

import pytest

from conftest import commit_all, run_git
from rstone_data import quality_gate as q
from rstone_data.build import deletion_approval, inventory
from rstone_data.protocol import DataError
from rstone_data.sources import collect, validate_markdown
from test_identity_migration_security import artifacts
from test_identity_migrations import SCHEMA, approval_for
from test_quality_parent_review import RecordingSigner


def product(alias=None):
    field = '' if alias is None else '实验牌号: '+json.dumps(alias, ensure_ascii=False)+'\n'
    return ('---\ntags: [油性, UV]\n固含: 75\n'+field+'---\n# synthetic complete identity\n').encode()


@pytest.fixture
def policy(monkeypatch, tmp_path):
    path = tmp_path/'installed-identity-policy.json'
    path.write_text(json.dumps({'schema':SCHEMA,'approvals':[]}))
    monkeypatch.setattr(q, 'default_identity_migrations', lambda: path)
    return path


def baseline(sources, store, signing, products):
    rstone, tds = sources
    for name, raw in products.items():
        validate_markdown('产品列表/'+name+'.md', raw)
        (rstone/'产品列表'/(name+'.md')).write_bytes(raw)
    commit_all(rstone)
    before = collect(rstone, tds)
    for name, raw in products.items():
        assert before.files['产品列表/'+name+'.md'] == raw
    result = store.prepare(before, signing[0], public_scope_approved=True)
    assert result['qualityGate']['summary']['ok']
    store.promote()
    assert store._snapshot(store.read()).files == before.files
    return before


def change(sources, remove, add):
    rstone, tds = sources
    for name in remove:
        (rstone/'产品列表'/(name+'.md')).unlink()
        # Stage the exact removal before adding a differently-cased name.
        # Case-insensitive working trees can otherwise retain the old index name.
        run_git(rstone, 'rm', '--cached', '--', '产品列表/'+name+'.md')
    for name, raw in add.items():
        validate_markdown('产品列表/'+name+'.md', raw)
        (rstone/'产品列表'/(name+'.md')).write_bytes(raw)
    commit_all(rstone)
    after = collect(rstone, tds)
    assert {'产品列表/'+name+'.md' for name in add} <= after.files.keys()
    assert not {'产品列表/'+name+'.md' for name in set(remove)-set(add)} & after.files.keys()
    return after


def deny(store, signing, after):
    previous = artifacts(store)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match='quality gate'):
        store.prepare(after, signer, public_scope_approved=True,
                      approval=deletion_approval(store.read(),inventory(after.files)))
    assert signer.calls == 0
    assert store.pending() is None
    assert artifacts(store) == previous


@pytest.mark.parametrize('source,target,claim', [
    ('RD9402.1','RS9502',False),
    ('RD9402v02','RS9502',True),
    ('RD9402_A','RS9502',False),
    ('RD9402(A)','RS9502',True),
    ('RD9402+A','RS9502',False),
    ('RD9402 A','RS9502',True),
    ('rD9402.1','RS9502',False),
    ('RD 9402','RS9502',True),
    ('RDword9402','RS9502',False),
    ('RD9402@A','RS9502',True),
    ('RD9402甲','RS9502',False),
    ('RD9402-5','RS9502_A',False),
    ('RD9402-5','RS9502(A)',False),
    ('RD9402-5','RS9502+A',False),
    ('RD9402-5','RS9502 A',False),
    ('RD9402-5','rS9502.1',False),
    ('RD9402-5','RS 9502',False),
    ('RD9402-5','RS9502@A',False),
])
def test_every_ingestible_rd_rs_spelling_requires_review(
        sources, store, signing, policy, source, target, claim):
    baseline(sources, store, signing, {source:product()})
    after = change(sources,[source],{target:product(source if claim else None)})
    deny(store,signing,after)


@pytest.mark.parametrize('suffix', ['.1','v02','_A','(A)','+A',' A','@A','甲'])
def test_exact_approval_preserves_every_supported_suffix_byte(
        sources, store, signing, policy, suffix):
    source, target = 'RD9402'+suffix, 'RS9402'+suffix
    before = baseline(sources,store,signing,{source:product()})
    after = change(sources,[source],{target:product(source)})
    token = approval_for(before,after,'产品列表/'+source+'.md','产品列表/'+target+'.md')
    policy.write_text(json.dumps({'schema':SCHEMA,'approvals':[token]}))
    signer = RecordingSigner(signing[0])
    result = store.prepare(after,signer,public_scope_approved=True,
                           approval=deletion_approval(store.read(),inventory(after.files)))
    assert result['qualityGate']['summary']['ok'] and signer.calls == 1
    assert store.prepare(None,signer,public_scope_approved=True)['resumed']
    assert signer.calls == 1
    assert (store.public/store.package(result['sequence'])).is_file()
    assert store.promote(keep_pending=True)['status'] == 'published'
    assert store.complete(lambda pointer:store.read(pointer))['status'] == 'published'
    assert store._snapshot(store.read()).files == after.files


@pytest.mark.parametrize('source,target', [
    ('RD9402.1','RD9402'),
    ('RS9402.1','RS9402'),
    ('RD9402v02','RD9402'),
    ('RS9402v02','RS9402v0'),
    ('RD9402_A','RD9402'),
    ('RS9402(A)','RS9402'),
    ('RD9402+A','RD9402'),
    ('RS9402 A','RS9402'),
    ('RD9402-6B','RD9402-6'),
    ('RS9402-6B-V02','RS9402-6B'),
    ('RD 9402.1','RD 9402'),
    ('RSword9402.1','RSword9402'),
])
def test_actual_same_prefix_suffix_loss_is_rejected(
        sources,store,signing,policy,source,target):
    baseline(sources,store,signing,{source:product()})
    after=change(sources,[source],{target:product()})
    deny(store,signing,after)


@pytest.mark.parametrize('first,second', [
    ('RD9402.1','RD9402.2'),
    ('RS9402v01','RS9402v02'),
    ('RD9402_A','RD9402_B'),
    ('RS9402(A)','RS9402(B)'),
    ('RD9402+A','RD9402+B'),
    ('RS9402 A','RS9402 B'),
])
def test_complete_sibling_loss_cannot_merge_into_retained_member(
        sources,store,signing,policy,first,second):
    baseline(sources,store,signing,{first:product(),second:product()})
    after=change(sources,[second],{})
    deny(store,signing,after)


@pytest.mark.parametrize('source,target', [
    ('RD9402.1','RD9402.2'),
    ('RS9402v02','RS9402V03'),
    ('RD9402_A','RD9402+A'),
    ('RS9402(A)','RS9402 A'),
    ('RD9402-6B','RD9402-8B'),
])
def test_same_number_suffix_replacement_is_not_an_unrelated_new_identity(
        sources,store,signing,policy,source,target):
    baseline(sources,store,signing,{source:product()})
    after=change(sources,[source],{target:product()})
    deny(store,signing,after)


@pytest.mark.parametrize('prefix',['RD','RS'])
@pytest.mark.parametrize('suffix',['-4','.1','v02','_A','(A)','+A',' A'])
def test_additive_base_is_not_suffix_loss_when_original_identity_remains(
        sources,store,signing,policy,prefix,suffix):
    source=prefix+'9402'+suffix
    baseline(sources,store,signing,{source:product()})
    after=change(sources,[],{prefix+'9402':product()})
    result=store.prepare(after,signing[0],public_scope_approved=True)
    assert result['qualityGate']['summary']['ok']
    store.promote()
    assert store._snapshot(store.read()).files == after.files


def test_additive_base_is_not_suffix_loss_when_original_has_exact_migration(
        sources,store,signing,policy):
    source,target='RD9402-4','RS9502'
    before=baseline(sources,store,signing,{source:product()})
    after=change(sources,[source],{target:product(source),'RD9402':product()})
    token=approval_for(before,after,'产品列表/'+source+'.md','产品列表/'+target+'.md')
    policy.write_text(json.dumps({'schema':SCHEMA,'approvals':[token]}))
    result=store.prepare(after,signing[0],public_scope_approved=True,
                         approval=deletion_approval(store.read(),inventory(after.files)))
    assert result['qualityGate']['summary']['ok']
    store.promote()
    assert store._snapshot(store.read()).files == after.files


@pytest.mark.parametrize('source,target', [
    ('RD9402.1','RS9402.2'),
    ('RD9402v02','RS9402V02'),
    ('RD9402-6B','RS9402.6B'),
    ('RD9402_A','RS9402(A)'),
    ('RD9402(A)','RS9402+A'),
    ('RD9402 A','RS9402A'),
    ('rD9402.1','RS9502'),
    ('RD 9402','RS9502'),
    ('RDword9402','RS9502'),
    ('RD9402.1','RS 9502'),
    ('RD9402.1','rs9502'),
    ('RD','RS9502'),
])
def test_receipt_cannot_override_suffix_bytes_or_unsupported_grant_spelling(
        sources,store,signing,policy,source,target):
    before=baseline(sources,store,signing,{source:product()})
    after=change(sources,[source],{target:product(source)})
    policy.write_text(json.dumps({'schema':SCHEMA,'approvals':[
        approval_for(before,after,'产品列表/'+source+'.md','产品列表/'+target+'.md')]}))
    deny(store,signing,after)


@pytest.mark.parametrize('prefix',['RD','RS'])
def test_numeric_base_comparison_is_not_a_bare_prefix_match(
        sources,store,signing,policy,prefix):
    baseline(sources,store,signing,{prefix+'9402':product()})
    after=change(sources,[prefix+'9402'],{prefix+'94':product()})
    result=store.prepare(after,signing[0],public_scope_approved=True,
                         approval=deletion_approval(store.read(),inventory(after.files)))
    assert result['qualityGate']['summary']['ok']


def test_additive_base_does_not_cover_a_second_unapproved_or_merged_sibling(
        sources,store,signing,policy):
    before=baseline(sources,store,signing,{'RD9402-4':product(),'RD9402-5':product()})
    after=change(sources,['RD9402-4','RD9402-5'],{'RS9502':product('RD9402-4'),'RD9402':product()})
    policy.write_text(json.dumps({'schema':SCHEMA,'approvals':[
        approval_for(before,after,'产品列表/RD9402-4.md','产品列表/RS9502.md')]}))
    deny(store,signing,after)


def test_case_rewrite_is_not_covered_by_adding_a_base(
        sources,store,signing,policy):
    baseline(sources,store,signing,{'RS9402v02':product()})
    after=change(sources,['RS9402v02'],{'RS9402V02':product(),'RS9402':product()})
    deny(store,signing,after)


def test_demo_unnumbered_formal_copy_cannot_hide_unapproved_rd_deletion(
        sources,store,signing,policy):
    before=baseline(sources,store,signing,{'RD-DEMO2':product()})
    # Copying a retained RS is not the documented one-to-one deleted-RS move.
    after=change(sources,['RD-DEMO2'],{'RS-COPY':before.files['产品列表/RS100.md']})
    deny(store,signing,after)


@pytest.mark.parametrize('target',['Rß9502','Rﬆ9502'])
def test_detection_uses_the_exact_ingress_uppercase_prefix_boundary(
        sources,store,signing,policy,target):
    # Ingress uses name.upper().startswith, including Unicode expansions.
    baseline(sources,store,signing,{'RD9402-5':product()})
    after=change(sources,['RD9402-5'],{target:product()})
    deny(store,signing,after)


@pytest.mark.parametrize('prefix',['Rß','Rﬆ'])
def test_every_ingress_prefix_keeps_its_numbered_suffix_independent(
        sources,store,signing,policy,prefix):
    baseline(sources,store,signing,{prefix+'9402.1':product()})
    after=change(sources,[prefix+'9402.1'],{prefix+'9402':product()})
    deny(store,signing,after)
