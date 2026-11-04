"""Mirroring is a real publication exit: preflight before any target artifact."""
import copy
import pytest
from rstone_data import recipe_codec as codec
from rstone_data.protocol import DataError,sha256
from rstone_data.mirrors import deploy_mirror
from test_structured_publication import snapshot,model,PATH
from test_protocol_migration_policy import legacy
from test_release_quality import install_legacy


@pytest.mark.parametrize("defect",["unapproved-conversion","identity","deletion","bad-archive"])
def test_mirror_preflights_all_new_generations_before_copying(store,signing,tmp_path,serve,defect):
    if defect=="unapproved-conversion":
        install_legacy(store,signing[0],legacy(snapshot()))
        install_legacy(store,signing[0],snapshot(),sequence=2,schema_version=2)
    elif defect=="identity":
        m=model();m["rows"][0][1]="0";install_legacy(store,signing[0],snapshot(m),schema_version=2)
    elif defect=="deletion":
        before=snapshot()
        for i in range(5):before.files["产品TDS/RS990001-Q5-"+str(i)+".TDS.md"]=str(i).encode()
        install_legacy(store,signing[0],before,schema_version=2)
        install_legacy(store,signing[0],snapshot(),sequence=2,schema_version=2)
    else:
        install_legacy(store,signing[0],snapshot(),schema_version=2)
        m=model();m["rows"][0][1]="0";install_legacy(store,signing[0],snapshot(m),sequence=2,schema_version=2)
        install_legacy(store,signing[0],snapshot(),sequence=3,schema_version=2)
    target=tmp_path/"mirror";target.mkdir()
    with serve(target) as base:
        with pytest.raises(DataError):deploy_mirror(store.root,target,base,signing[1],allow_local_http=True)
    assert not [p for p in target.rglob("*") if p.is_file()]
