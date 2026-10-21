import pytest

from rstone_data.protocol import DataError, canonical_json, decode_json, logical_path, manifest_name


def test_canonical_bytes():
    assert canonical_json({"z": 1, "a": "中文"}) == '{"a":"中文","z":1}\n'.encode()
    assert manifest_name(1) == "manifests/000001.json"
    assert manifest_name(1000000) == "manifests/1000000.json"


@pytest.mark.parametrize("path", ["/产品列表/a.md", "产品列表/../a.md", "产品列表//a.md",
                                  "产品列表/C:a.md", "产品列表\\a.md", "产品列表/.env.md",
                                  "产品列表/CON.md", "产品列表/a.md ", "产品TDS/a.tds.md"])
def test_reject_unsafe_paths(path):
    with pytest.raises(DataError):
        logical_path(path)


def test_nested_paths():
    assert logical_path("产品应用/塑胶/方案.md") == "产品应用/塑胶/方案.md"


def test_reject_duplicate_json():
    with pytest.raises(DataError):
        decode_json(b'{"sequence":1,"sequence":2}')
