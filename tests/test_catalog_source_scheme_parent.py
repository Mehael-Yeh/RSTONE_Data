"""Synthetic source-scheme labels are editorial, registered product suffixes are not."""
import zipfile
import pytest
from rstone_data import quality_gate as q
from test_catalog_recipe_names import GRADE, recipe, snapshot

@pytest.mark.parametrize('tail',['-参考配方-方案RS902-1','-方案RS777-A'])
def test_source_scheme_label_can_be_removed_when_full_identity_is_preserved(store,signing,tail):
    old=snapshot(GRADE+tail)
    new=snapshot()
    assert q.validate_snapshot(new,old)['summary']['ok']
    store.prepare(old,signing[0],public_scope_approved=True)
    store.promote()
    result=store.prepare(new,signing[0],public_scope_approved=True)
    with zipfile.ZipFile(store.public/store.package(result['sequence'])) as z:
        assert z.read('files/产品配方/'+GRADE+'.md')==recipe()

@pytest.mark.parametrize('tail',['-参考配方-方案RS902-1','-方案RS777-A'])
def test_registered_full_product_suffix_never_becomes_an_editorial_tail(tail):
    full=GRADE+tail
    old=snapshot(full,raw=recipe(full),products=[GRADE,full])
    new=snapshot(products=[GRADE,full])
    findings=q.check_identity(q.snapshot_corpus(new),q.snapshot_corpus(old))
    assert any(f['category']=='identity-suffix-dropped' for f in findings)

@pytest.mark.parametrize('tail',['-方案CUSTOM','-方案rs902-1','-方案RS','-方案RS902_1'])
def test_unrecognized_scheme_spelling_is_not_automatically_exempt(tail):
    old=snapshot(GRADE+tail)
    findings=q.check_identity(q.snapshot_corpus(snapshot()),q.snapshot_corpus(old))
    assert any(f['category']=='identity-suffix-dropped' for f in findings)
