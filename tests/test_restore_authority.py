"""A signed pending release is not a way around revoked conversion authority."""
import json
import pytest
from rstone_data import catalog_revisions_v2 as v2,quality_gate as q
from rstone_data.protocol import DataError,sha256,canonical_json,validate_manifest
from rstone_data.build import make_manifest,deletion_approval,inventory
from test_structured_publication import snapshot,PATH
from test_protocol_migration_policy import config,proof,legacy
from test_quality_parent_review import RecordingSigner
from test_release_quality import install_legacy
from test_identity_migration_security import artifacts


@pytest.mark.parametrize("operation",["resume","promote","complete","package","rollback"])
@pytest.mark.parametrize("revoke",["proof","grant"])
def test_revoked_new_permission_cannot_escape_via_recovery(store,signing,monkeypatch,tmp_path,operation,revoke):
    after=snapshot();before=legacy(after);install_legacy(store,signing[0],before)
    baseline=sha256((store.public/"manifests/000001.json").read_bytes());w=proof(before,after,baseline)
    config(monkeypatch,tmp_path,w);b=q.snapshot_corpus(store._snapshot(store.read()));a=q.snapshot_corpus(after)
    proposed=v2.proposal(b,a,v2.migration_findings(b,a)+q.check_identity(a,b));assert proposed
    config(monkeypatch,tmp_path,w,[proposed["fingerprint"]]);store.prepare(after,signing[0],public_scope_approved=True)
    config(monkeypatch,tmp_path,None if revoke=="proof" else w,[] if revoke=="grant" else [proposed["fingerprint"]])
    frozen=artifacts(store);signer=RecordingSigner(signing[0])
    with pytest.raises(DataError,match="quality gate"):
        if operation=="resume":store.prepare(None,signer,public_scope_approved=True)
        elif operation=="promote":store.promote(lambda _:pytest.fail("verifier reached"))
        elif operation=="complete":store.complete(lambda _:pytest.fail("verifier reached"))
        elif operation=="package":store.package(2)
        else:
            # Remove only the pending control marker, not its signed payload.
            # The rollback path must itself revalidate the original authority.
            (store.state/"pending.json").unlink();frozen=artifacts(store)
            store.rollback(2,signer,public_scope_approved=True)
    assert frozen==artifacts(store) and signer.calls==0


@pytest.mark.parametrize("target",[True,1,2,3,0,-1,"1",1.0])
def test_restore_marker_is_a_strict_prior_sequence(target):
    manifest=make_manifest(snapshot(),2,"2026.01.01.001","2026-01-01T00:00:00Z","test-ephemeral")
    manifest["rollbackOf"]=target
    if type(target) is int and target==1:assert validate_manifest(manifest)==manifest
    else:
        with pytest.raises(DataError):validate_manifest(manifest)


def test_direct_restore_context_rejects_forged_provenance(store,signing):
    import copy
    old=snapshot();old.files[PATH]=b"historical complete legacy facts\n";install_legacy(store,signing[0],old)
    install_legacy(store,signing[0],snapshot(),sequence=2,schema_version=2)
    tampered=copy.deepcopy(old);tampered.commits["Mehael-Yeh/Rstone"]="d"*40
    frozen=artifacts(store);signer=RecordingSigner(signing[0])
    with pytest.raises(DataError):store.prepare(tampered,signer,public_scope_approved=True,rollback_of=1)
    assert artifacts(store)==frozen and signer.calls==0


def test_direct_restore_preserves_original_client_requirement(store,signing):
    old=snapshot();old.files[PATH]=b"historical complete legacy facts\n";install_legacy(store,signing[0],old)
    install_legacy(store,signing[0],snapshot(),sequence=2,schema_version=2)
    store.prepare(old,signing[0],public_scope_approved=True,rollback_of=1,min_app_version="0.0.0")
    assert store.read(store.pending()["pointer"])["minAppVersion"]=="1.0.0"
    store.package(3);store.promote()


@pytest.mark.parametrize("operation",["resume","promote","complete","package"])
def test_signed_restore_metadata_cannot_weaken_original_client_requirement(store,signing,operation):
    from rstone_data.protocol import decode_json
    from rstone_data.release import pointer_for
    old=snapshot();old.files[PATH]=b"historical complete legacy facts\n";install_legacy(store,signing[0],old)
    install_legacy(store,signing[0],snapshot(),sequence=2,schema_version=2)
    store.rollback(1,signing[0],public_scope_approved=True)
    pending=store.pending();path=store.public/pending["pointer"]["manifestPath"]
    manifest=decode_json(path.read_bytes());manifest["minAppVersion"]="0.0.0";raw=canonical_json(manifest)
    path.write_bytes(raw);(store.public/pending["pointer"]["signaturePath"]).write_bytes(signing[0].sign(raw))
    pending["pointer"]=pointer_for(manifest,raw);(store.state/"pending.json").write_bytes(canonical_json(pending))
    frozen=artifacts(store);signer=RecordingSigner(signing[0])
    with pytest.raises(DataError,match="restore"):
        if operation=="resume":store.prepare(None,signer,public_scope_approved=True)
        elif operation=="promote":store.promote(lambda _:pytest.fail("verifier reached"))
        elif operation=="complete":store.complete(lambda _:pytest.fail("verifier reached"))
        else:store.package(3)
    assert artifacts(store)==frozen and signer.calls==0


def test_cli_exact_restore_accepts_only_explicit_independent_deletion_token(store,signing,tmp_path,monkeypatch):
    import base64
    from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
    from rstone_data.cli import main
    old=snapshot();old.files[PATH]=b"historical complete legacy facts\n";install_legacy(store,signing[0],old)
    newer=snapshot()
    for i in range(4):newer.files["产品TDS/RS990001-Q5-"+str(i)+".TDS.md"]=str(i).encode()
    install_legacy(store,signing[0],newer,sequence=2,schema_version=2)
    keys=tmp_path/"keys.json";keys.write_bytes(canonical_json({"keys":signing[1]}))
    seed=signing[0].private.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption())
    monkeypatch.setenv("DATA_SIGNING_PRIVATE_KEY",base64.b64encode(seed).decode())
    args=["prepare","--store",str(store.root),"--keys",str(keys),"--key-id",signing[0].key_id,"--public-scope-approved","--rollback","1"]
    frozen=artifacts(store);assert main(args)==1 and artifacts(store)==frozen
    token=deletion_approval(store.read(),inventory(old.files))
    assert main(args+["--deletion-approval",token])==0
    assert store.read(store.pending()["pointer"])["schemaVersion"]==1
    store.package(3);store.promote();assert store.read()["rollbackOf"]==1
