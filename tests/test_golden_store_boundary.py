"""Every frozen golden is exercised through the real signing boundary.

Six accepted shapes contain a literal synthetic currency token ($1): shape
acceptance is not price/publication permission. Valid positive publication is
covered separately with non-price synthetic business text.
"""
import base64
import pytest
from test_recipe_protocol import CASES
from test_structured_publication import snapshot,PATH
from test_quality_parent_review import RecordingSigner
from test_identity_migration_security import artifacts
from rstone_data.protocol import DataError


@pytest.mark.parametrize("case",CASES,ids=lambda case:case["name"])
def test_frozen_golden_cannot_acquire_extra_publication_rights(store,signing,case):
    raw=base64.b64decode(case["rawBase64"],validate=True)
    snap=snapshot()
    if case["shapeAccepted"]:
        from rstone_data.recipe_codec import parse
        model=parse(raw)
        product=next(data for path,data in snap.files.items() if path.startswith("产品列表/"))
        for name in model["associatedProducts"]:snap.files["产品列表/"+name+".md"]=product
        snap.files.pop(PATH)
        snap.files["产品配方/"+model["title"]+".md"]=raw
    else:snap.files[PATH]=raw
    signer=RecordingSigner(signing[0]);frozen=artifacts(store)
    with pytest.raises(DataError,match="price gate" if case["shapeAccepted"] else None):
        store.prepare(snap,signer,public_scope_approved=True)
    assert signer.calls==0 and artifacts(store)==frozen
    assert store.pending() is None
    assert not list(store.public.rglob("*.sig")) and not list(store.public.rglob("*.zip"))
