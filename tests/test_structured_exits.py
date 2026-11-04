"""Real signed store attacks at every publication exit, no gate replacement."""
import copy
import pytest
from rstone_data import recipe_codec as codec
from rstone_data.build import inventory, deletion_approval
from rstone_data.protocol import DataError
from test_structured_publication import snapshot, model, PATH, OWNER
from test_release_quality import install_legacy
from test_identity_migration_security import artifacts
from test_quality_parent_review import RecordingSigner

OPS=["prepare", "resume", "promote", "complete", "package", "rollback"]


@pytest.mark.parametrize("operation", OPS)
@pytest.mark.parametrize("defect", ["legacy", "body", "header", "zero-owner", "unknown-reference", "non-main-collision"])
def test_every_actual_exit_rejects_bad_recipe_without_new_artifacts(store, signing, operation, defect):
    snap=snapshot(); m=model()
    if defect=="legacy": snap.files[PATH]=b"| resin | 40 |\n\n| water | 10 |\n"
    elif defect=="body": snap.files[PATH]+=b"\nextra display\n"
    elif defect=="header": snap.files[PATH]=snap.files[PATH].replace(b'"main"',b'"main","approved":true',1)
    elif defect=="zero-owner": m["rows"][0][1]="0"; snap.files[PATH]=codec.emit(m)
    elif defect=="unknown-reference": m["associatedProducts"].append("RS990099-Q5"); snap.files[PATH]=codec.emit(m)
    else:
        m["classification"]="example"; m["title"]=OWNER
        snap.files.pop(PATH); snap.files["产品配方/"+OWNER+".md"]=codec.emit(m)
    if operation!="prepare": install_legacy(store,signing[0],snap,pending=operation in {"resume","promote","complete"},schema_version=2)
    before=artifacts(store); signer=RecordingSigner(signing[0])
    with pytest.raises(DataError):
        if operation=="prepare": store.prepare(snap,signer,public_scope_approved=True)
        elif operation=="resume": store.prepare(None,signer,public_scope_approved=True)
        elif operation=="promote": store.promote(lambda _:pytest.fail("verifier reached"))
        elif operation=="complete": store.complete(lambda _:pytest.fail("verifier reached"))
        elif operation=="package": store.package(1)
        else: store.rollback(1,signer,public_scope_approved=True)
    assert before==artifacts(store) and signer.calls==0


@pytest.mark.parametrize("operation", ["resume", "promote", "complete", "package"])
def test_signed_pending_deletion_still_needs_exact_independent_grant(store,signing,operation):
    before=snapshot()
    for index in range(5): before.files["产品TDS/RS990001-Q5-"+str(index)+".TDS.md"]=str(index).encode()
    install_legacy(store,signing[0],before,schema_version=2)
    after=snapshot(); install_legacy(store,signing[0],after,sequence=2,pending=True,schema_version=2)
    frozen=artifacts(store); signer=RecordingSigner(signing[0])
    with pytest.raises(DataError,match="deletion"):
        if operation=="resume": store.prepare(None,signer,public_scope_approved=True)
        elif operation=="promote": store.promote()
        elif operation=="complete": store.complete(lambda _:pytest.fail("verifier reached"))
        else: store.package(2)
    assert artifacts(store)==frozen and signer.calls==0


def test_exact_restore_does_not_self_approve_a_bulk_deletion(store,signing):
    old=snapshot(); old.files[PATH]=b"legacy complete facts\n"
    install_legacy(store,signing[0],old)
    newer=snapshot()
    for i in range(5): newer.files["产品TDS/RS990001-Q5-"+str(i)+".TDS.md"]=str(i).encode()
    install_legacy(store,signing[0],newer,sequence=2,schema_version=2)
    frozen=artifacts(store); signer=RecordingSigner(signing[0])
    with pytest.raises(DataError,match="deletion"): store.rollback(1,signer,public_scope_approved=True)
    assert artifacts(store)==frozen and signer.calls==0
    grant=deletion_approval(store.read(),inventory(old.files))
    result=store.rollback(1,signer,public_scope_approved=True,approval=grant)
    store.package(result["sequence"]); store.promote()
    assert store.read()["schemaVersion"]==1
