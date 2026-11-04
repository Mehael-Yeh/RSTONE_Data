"""Pending state cannot erase a real signed baseline to bypass migration proof."""
import pytest
from rstone_data.protocol import DataError,canonical_json,sha256
from test_structured_publication import snapshot
from test_protocol_migration_policy import legacy
from test_release_quality import install_legacy
from test_quality_parent_review import RecordingSigner
from test_identity_migration_security import artifacts


@pytest.mark.parametrize("operation",["resume","promote","complete","package"])
@pytest.mark.parametrize("previous_hash",["actual","missing"])
def test_pending_null_baseline_cannot_authorize_a_new_legacy_conversion(store,signing,operation,previous_hash):
    after=snapshot();install_legacy(store,signing[0],legacy(after))
    previous=store.latest_bytes()
    pointer=install_legacy(store,signing[0],after,sequence=2,pending=True,schema_version=2)
    pending=store.pending();pending["qualityBaselineSequence"]=None
    pending["previousLatestSha256"]=sha256(previous) if previous_hash=="actual" else None
    (store.state/"pending.json").write_bytes(canonical_json(pending))
    if operation=="complete":(store.public/"latest.json").write_bytes(canonical_json(pointer))
    frozen=artifacts(store);signer=RecordingSigner(signing[0])
    with pytest.raises(DataError,match="quality gate"):
        if operation=="resume":store.prepare(None,signer,public_scope_approved=True)
        elif operation=="promote":store.promote(lambda _:pytest.fail("verifier reached"))
        elif operation=="complete":store.complete(lambda _:pytest.fail("verifier reached"))
        else:store.package(2)
    assert frozen==artifacts(store) and signer.calls==0
