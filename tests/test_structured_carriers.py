"""CAS/ZIP content inspection differs from logical namespace admission."""
import copy
import pytest
from rstone_data import recipe_codec as codec
from rstone_data.protocol import DataError
from rstone_data.sources import validate_markdown
from rstone_data.price_gate import check_payload, check_tree
from test_structured_publication import model, snapshot, PATH


@pytest.mark.parametrize("namespace,name", [("产品列表", "RS990001-Q5.md"), ("产品应用", "合成涂层.md"), ("产品TDS", "RS990001-Q5.TDS.md")])
def test_recipe_model_cannot_masquerade_as_other_namespace(namespace,name):
    with pytest.raises(DataError): validate_markdown(namespace+"/"+name, codec.emit(model()))


def test_content_decoder_accepts_nested_recipe_in_cas_and_zip(store, signing):
    snap = snapshot(); check_payload("objects/opaque", snap.files[PATH])
    result=store.prepare(snap,signing[0],public_scope_approved=True)
    store.package(result["sequence"]); check_tree(store.public)


@pytest.mark.parametrize("carrier", ["cas", "zip"])
@pytest.mark.parametrize("field", ["title", "cell", "preTable", "content"])
def test_entity_json_escaped_money_never_escapes_content_inspection(tmp_path, carrier, field):
    import zipfile
    m=model(); bad="报价 123 元"
    if field=="title": m["title"]=bad
    elif field=="cell": m["rows"][0][4]=bad
    elif field=="preTable": m["preTable"]=[bad]
    else: m["content"][0]["text"]=bad
    raw=codec.emit(m)
    if carrier=="cas": (tmp_path/"opaque").write_bytes(raw)
    else:
        with zipfile.ZipFile(tmp_path/"payload.zip","w") as archive: archive.writestr("files/opaque",raw)
    with pytest.raises(DataError, match="price gate"): check_tree(tmp_path)


@pytest.mark.parametrize("raw", [b"---\n{\"schema\":\"unknown\"}\n---\ntext\n", b"---\n{bad}\n---\ntext\n"])
def test_unknown_or_broken_cas_model_never_downgrades(raw):
    with pytest.raises(DataError): check_payload("objects/opaque",raw)
