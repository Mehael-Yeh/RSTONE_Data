"""Full five-column/ordered-facts and stable identity transaction regressions."""
import copy
import pytest
from rstone_data import quality_gate as q, recipe_codec as codec, catalog_revisions_v2 as v2
from rstone_data.protocol import DataError
from rstone_data.build import inventory, deletion_approval
from test_structured_publication import model,snapshot,PATH,OWNER
from test_protocol_migration_policy import config
from test_identity_migration_security import artifacts
from test_quality_parent_review import RecordingSigner


def pair():
    m=model(); m["title"]=OWNER+"-底-参考配方-方案02"
    before=snapshot(); before.files.pop(PATH); before.files["产品配方/"+m["title"]+".md"]=codec.emit(m)
    m["title"]=OWNER
    after=snapshot(); after.files.pop(PATH); after.files["产品配方/"+OWNER+".md"]=codec.emit(m)
    return before,after


@pytest.mark.parametrize("change", ["same", "column0", "column1", "column2", "column3", "column4",
    "last-row", "empty-tail", "columns", "preTable", "note", "content-order", "branch", "step", "step-order", "classification", "id"])
def test_typed_business_rename_requires_every_original_fact(store,signing,monkeypatch,tmp_path,change):
    before,after=pair(); config(monkeypatch,tmp_path)
    store.prepare(before,signing[0],public_scope_approved=True);store.promote()
    p="产品配方/"+OWNER+".md"; m=codec.parse(after.files[p])
    if change.startswith("column") and change[-1].isdigit(): m["rows"][0][int(change[-1])]="41" if change=="column1" else "changed"
    elif change=="last-row": m["rows"].pop()
    elif change=="empty-tail": m["rows"][1][4]=""
    elif change=="columns": m["columns"]=codec.COLUMNS[1][:]
    elif change=="preTable": m["preTable"]=[]
    elif change=="note": m["content"][0]["text"]="changed"
    elif change=="content-order": m["content"].reverse()
    elif change=="branch": m["content"].pop(1)
    elif change=="step": m["content"][1]["steps"].pop()
    elif change=="step-order": m["content"][1]["steps"].reverse()
    elif change=="classification": m["classification"]="reference"
    elif change=="id": m["recipeId"]="NEW-FORGED-ID"
    after.files[p]=codec.emit(m)
    b=q.snapshot_corpus(store._snapshot(store.read()));a=q.snapshot_corpus(after)
    findings=q.check_identity(a,b)
    # Give even an ineligible candidate an exact nominal v2 digest: structural
    # eligibility remains independent of knowing the hash.
    from rstone_data.protocol import canonical_json,sha256
    grant=sha256(canonical_json(v2.transaction_payload(b,a,findings,None)))
    config(monkeypatch,tmp_path,approvals=[grant])
    signer=RecordingSigner(signing[0]);frozen=artifacts(store)
    if change=="same":
        result=store.prepare(after,signer,public_scope_approved=True,
            approval=deletion_approval(store.read(),inventory(after.files)))
        assert result["qualityGate"]["catalogRevision"]["used"]
        store.package(result["sequence"]);store.promote()
    else:
        with pytest.raises(DataError):store.prepare(after,signer,public_scope_approved=True,
            approval=deletion_approval(store.read(),inventory(after.files)))
        assert artifacts(store)==frozen and signer.calls==0


@pytest.mark.parametrize("case", ["union-lost", "order-lost", "union-preserved"])
def test_duplicate_typed_origins_preserve_all_associations_in_order(store,signing,monkeypatch,tmp_path,case):
    before,after=pair();alias="RD990009-Q5"
    for snap in (before,after):snap.files["产品列表/"+alias+".md"]=snap.files["产品列表/"+OWNER+".md"]
    selected=codec.parse(next(raw for path,raw in before.files.items() if path.startswith("产品配方/")))
    alt=copy.deepcopy(selected);alt["title"]=OWNER+"-底-参考配方-方案03";alt["recipeId"]="SYNTH-SECOND-ORIGIN"
    alt["associatedProducts"]=[OWNER,alias]
    before.files["产品配方/"+alt["title"]+".md"]=codec.emit(alt)
    p="产品配方/"+OWNER+".md";m=codec.parse(after.files[p])
    if case=="order-lost":m["associatedProducts"]=[alias,OWNER]
    elif case=="union-preserved":m["associatedProducts"]=[OWNER,alias]
    after.files[p]=codec.emit(m)
    config(monkeypatch,tmp_path);store.prepare(before,signing[0],public_scope_approved=True);store.promote()
    b=q.snapshot_corpus(store._snapshot(store.read()));a=q.snapshot_corpus(after);findings=q.check_identity(a,b)
    from rstone_data.protocol import canonical_json,sha256
    grant=sha256(canonical_json(v2.transaction_payload(b,a,findings,None)))
    config(monkeypatch,tmp_path,approvals=[grant]);frozen=artifacts(store);signer=RecordingSigner(signing[0])
    if case=="union-preserved":
        result=store.prepare(after,signer,public_scope_approved=True,approval=deletion_approval(store.read(),inventory(after.files)))
        store.package(result["sequence"]);store.promote()
    else:
        with pytest.raises(DataError):store.prepare(after,signer,public_scope_approved=True,approval=deletion_approval(store.read(),inventory(after.files)))
        assert artifacts(store)==frozen and signer.calls==0


@pytest.mark.parametrize("change",["new-id-same-path","owner-reassignment"])
def test_stable_recipe_id_cannot_be_regenerated_or_reassigned(store,signing,change):
    before=snapshot();store.prepare(before,signing[0],public_scope_approved=True);store.promote()
    after=copy.deepcopy(before);m=model()
    if change=="new-id-same-path": m["recipeId"]="NEW-ID"
    else:
        other="RS990002-Q5";after.files["产品列表/"+other+".md"]=after.files["产品列表/"+OWNER+".md"]
        m["ownerProduct"]=other;m["associatedProducts"]=[other];m["rows"][0][0]="[["+other+"]]"
        m["title"]=other+"-底";after.files.pop(PATH)
    after.files["产品配方/"+m["title"]+".md"]=codec.emit(m)
    signer=RecordingSigner(signing[0]);frozen=artifacts(store)
    with pytest.raises(DataError, match="quality gate"):store.prepare(after,signer,public_scope_approved=True,
        approval=deletion_approval(store.read(),inventory(after.files)))
    assert artifacts(store)==frozen and signer.calls==0
