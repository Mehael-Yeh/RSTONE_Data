"""Read-only v2 proposal from exact before/after trees and baseline bytes."""
import json
import pytest
from rstone_data import catalog_revisions_v2 as v2, quality_gate as q
from rstone_data.build import make_manifest
from rstone_data.protocol import canonical_json,sha256
from test_typed_revision_matrix import pair
from test_protocol_migration_policy import config


def test_new_proposal_cli_binds_baseline_and_never_installs_permission(monkeypatch,tmp_path,capsys):
    assert callable(getattr(v2,"main",None)), "missing v2 exact proposal CLI"
    before,after=pair();config(monkeypatch,tmp_path)
    for label,snap in (("before",before),("after",after)):
        for p,raw in snap.files.items():
            target=tmp_path/label/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    manifest=make_manifest(before,1,"2026.01.01.001","2026-01-01T00:00:00Z","test-ephemeral")
    manifest["schemaVersion"]=2;manifest["minAppVersion"]="2.0.2"
    manifest_path=tmp_path/"baseline.json";manifest_path.write_bytes(canonical_json(manifest))
    frozen={p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    args=["--before",str(tmp_path/"before"),"--after",str(tmp_path/"after"),"--baseline-manifest",str(manifest_path)]
    assert v2.main(args)==0
    result=json.loads(capsys.readouterr().out);assert result["schema"]==v2.SCHEMA and result["approved"] is False
    b=q.snapshot_corpus(before);b.manifest_sha256=sha256(manifest_path.read_bytes());a=q.snapshot_corpus(after)
    assert result["fingerprint"]==v2.proposal(b,a,q.check_identity(a,b))["fingerprint"]
    assert frozen=={p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    manifest["files"][0]["size"]+=1;manifest_path.write_bytes(canonical_json(manifest))
    assert v2.main(args)==1
    failed=json.loads(capsys.readouterr().out);assert failed["approved"] is False and failed["status"]=="unavailable"
