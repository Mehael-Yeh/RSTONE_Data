"""Frozen synthetic parent contract: byte/shape, never publication permission."""
import base64
import hashlib
import importlib
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures/recipe_protocol"
CASES = json.loads((FIXTURES / "cases.json").read_text())["cases"]

@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_frozen_codec(case):
    codec = importlib.import_module("rstone_data.recipe_codec")
    raw = base64.b64decode(case["rawBase64"], validate=True)
    assert hashlib.sha256(raw).hexdigest() == case["rawSha256"]
    if case["shapeAccepted"]:
        model = codec.parse(raw)
        assert codec.emit(model) == raw
    else:
        with pytest.raises(ValueError):
            codec.parse(raw)


def test_ingestion_consumes_the_canonical_model_not_yaml_scalars():
    from rstone_data.sources import validate_markdown
    # Golden shape includes literal $1: shape acceptance must not waive prices.
    codec = importlib.import_module("rstone_data.recipe_codec")
    model = json.loads((FIXTURES / "valid.model.json").read_text())
    model["rows"][0][4] = "合成备注原文"
    raw = codec.emit(model)
    text = validate_markdown("产品配方/" + model["title"] + ".md", raw)
    assert text == raw.decode("utf-8")
