"""Closed adjudicated use labels; no product suffix inference or aliases."""
import pytest
from rstone_data import recipe_codec as codec
from rstone_data.protocol import DataError
from test_structured_publication import snapshot,model,PATH,OWNER
from test_quality_parent_review import RecordingSigner
from test_identity_migration_security import artifacts


@pytest.mark.parametrize("tail",["-处理剂","-白","-红","-涂料","-蓝","-面漆"])
def test_adjudicated_use_labels_accept_exact_registered_owner(store,signing,tail):
    m=model();m["title"]=OWNER+tail;s=snapshot();s.files.pop(PATH);s.files["产品配方/"+m["title"]+".md"]=codec.emit(m)
    result=store.prepare(s,signing[0],public_scope_approved=True)
    store.package(result["sequence"]);store.promote()


def test_unknown_auxiliary_amount_is_preserved_without_granting_owner_credit(store,signing):
    m=model();m["rows"][1][1]="若干";s=snapshot(m)
    result=store.prepare(s,signing[0],public_scope_approved=True);store.package(result["sequence"]);store.promote()
    assert codec.parse(store._snapshot(store.read()).files[PATH])["rows"][1][1]=="若干"


@pytest.mark.parametrize("amount",["适量（NCO/OH=0-1）","适量（NCO/OH=1.15-1）"])
def test_unknown_ratio_constraints_do_not_accept_zero_or_reversed_range(store,signing,amount):
    m=model();m["rows"][1][1]=amount;s=snapshot(m);signer=RecordingSigner(signing[0]);before=artifacts(store)
    with pytest.raises(DataError):store.prepare(s,signer,public_scope_approved=True)
    assert before==artifacts(store) and signer.calls==0
