"""Configuration is publisher-relative and strict; old grants are not v2 grants."""
import copy
import json
import pytest
from rstone_data import catalog_revisions as old, catalog_revisions_v2 as v2, quality_gate as q
from rstone_data.protocol import DataError, canonical_json, sha256
from test_typed_revision_matrix import pair
from test_protocol_migration_policy import config,proof,legacy
from test_structured_publication import snapshot


def test_obsolete_apply_cannot_authorize_typed_transaction():
    before,after=pair();b=q.snapshot_corpus(before);a=q.snapshot_corpus(after);findings=q.check_identity(a,b)
    proposed=old.proposal(b,a,findings);assert proposed
    remaining,audit=old.apply(a,b,findings,{proposed["fingerprint"]})
    assert remaining==findings and audit is None


@pytest.mark.parametrize("filename,key,schema",[("catalog_revisions_v2.json","approvals",v2.SCHEMA),
    ("recipe_migration_witnesses.json","witnesses",v2.WITNESSES_SCHEMA)])
@pytest.mark.parametrize("defect",["missing","malformed","wrong-root","unknown-key","duplicate-key","duplicate-value","bad-digest","wrong-version","symlink"])
def test_new_fixed_policies_fail_closed(monkeypatch,tmp_path,filename,key,schema,defect):
    root=config(monkeypatch,tmp_path);p=root/filename
    if defect=="missing":p.unlink()
    elif defect=="malformed":p.write_bytes(b"{")
    elif defect=="wrong-root":p.write_bytes(b"[]")
    elif defect=="unknown-key":p.write_bytes(canonical_json({"schema":schema,key:[],"approved":True}))
    elif defect=="duplicate-key":p.write_text('{"schema":'+json.dumps(schema)+','+json.dumps(key)+':[],'+json.dumps(key)+':[]}')
    elif defect=="duplicate-value":p.write_bytes(canonical_json({"schema":schema,key:["a"*64,"a"*64]}))
    elif defect=="bad-digest":p.write_bytes(canonical_json({"schema":schema,key:["*"]}))
    elif defect=="wrong-version":p.write_bytes(canonical_json({"schema":"unknown",key:[]}))
    else:
        target=root/(filename+".target");p.rename(target);p.symlink_to(target)
    with pytest.raises(DataError,match="configuration"):
        (v2.load_approvals if key=="approvals" else v2.load_witnesses)()


@pytest.mark.parametrize("field",["schema","protocol","renderer","baselineManifestSha256","sourceCatalogApprovalFingerprint",
    "beforeInventorySha256","approvedLegacyInventorySha256","typedInventorySha256","modelFactsSha256","records"])
def test_witness_root_is_closed_and_typed(field):
    after=snapshot();w=proof(legacy(after),after,"b"*64);w[field]=None
    with pytest.raises(DataError):v2.validate_witness(w)


@pytest.mark.parametrize("field",["pathSha256","legacySha256","legacySize","modelSha256","typedSha256","typedSize"])
def test_witness_records_are_closed_and_typed(field):
    after=snapshot();w=proof(legacy(after),after,"b"*64);w["records"][0][field]=True
    with pytest.raises(DataError):v2.validate_witness(w)


def test_witness_file_is_bound_to_its_full_byte_hash(monkeypatch,tmp_path):
    after=snapshot();w=proof(legacy(after),after,"b"*64);root=config(monkeypatch,tmp_path,w)
    path=next((root/"migration_witnesses").glob("*.json"));path.write_bytes(path.read_bytes()+b"\n")
    with pytest.raises(DataError,match="configuration"):v2.load_witnesses()
