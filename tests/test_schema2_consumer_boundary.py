"""Python reference consumer must not activate an incomplete schema2 catalogue."""
import copy
import io
import zipfile
import pytest
from rstone_data import recipe_codec as codec
from rstone_data.protocol import DataError,canonical_json
from rstone_data.build import make_manifest
from rstone_data.client import import_package,active_directory
from test_structured_publication import snapshot,model,PATH,OWNER


def signed_package(snap,signer,sequence):
    manifest=make_manifest(snap,sequence,"2026.01.01.001","2026-01-01T00:00:00Z",signer.key_id)
    manifest["schemaVersion"]=2;manifest["minAppVersion"]="2.0.2";raw=canonical_json(manifest)
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w") as archive:
        archive.writestr("metadata/manifest.json",raw);archive.writestr("metadata/manifest.sig",signer.sign(raw))
        for path,data in snap.files.items():archive.writestr("files/"+path,data)
    buffer.seek(0);return buffer


@pytest.mark.parametrize("defect",["legacy","unknown-associated","duplicate-id"])
def test_signed_schema2_bad_generation_preserves_last_good(tmp_path,signing,defect):
    cache=tmp_path/"client";good=snapshot()
    import_package(signed_package(good,signing[0],1),cache,signing[1],app_version="2.0.2")
    frozen=(cache/"active.json").read_bytes();bad=copy.deepcopy(good);m=model()
    if defect=="legacy":bad.files[PATH]=b"| resin | 40 |\n"
    elif defect=="unknown-associated":m["associatedProducts"].append("RS990099-Q5");bad.files[PATH]=codec.emit(m)
    else:
        m["title"]=OWNER+"-亮";bad.files["产品配方/"+m["title"]+".md"]=codec.emit(m)
    with pytest.raises(DataError):import_package(signed_package(bad,signing[0],2),cache,signing[1],app_version="2.0.2")
    assert (cache/"active.json").read_bytes()==frozen
    assert (active_directory(cache,signing[1])/PATH).read_bytes()==good.files[PATH]
