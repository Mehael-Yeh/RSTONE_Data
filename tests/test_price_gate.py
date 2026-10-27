"""Synthetic-only public price barrier regression tests."""
import io
import zipfile

import pytest

from rstone_data.build import make_manifest
from rstone_data.protocol import canonical_json, sha256
from rstone_data.release import pointer_for
from rstone_data.storage import atomic_write
from rstone_data.client import import_package, synchronize
from rstone_data.protocol import DataError
from rstone_data.sources import collect, validate_markdown
from conftest import commit_all


def legacy_release(store, snapshot, signer):
    """Valid signed legacy release; no mocked verification bypass."""
    manifest = make_manifest(snapshot, 1, "2026.01.01.001", "2026-01-01T00:00:00Z", signer.key_id)
    raw = canonical_json(manifest)
    signature = signer.sign(raw)
    pointer = pointer_for(manifest, raw)
    for content in snapshot.files.values():
        atomic_write(store.public / "objects" / sha256(content), content)
    atomic_write(store.public / pointer["manifestPath"], raw)
    atomic_write(store.public / pointer["signaturePath"], signature)
    atomic_write(store.state / "pending.json", canonical_json({
        "pointer": pointer, "previousLatestSha256": None, "summary": {"sequence": 1}}))
    return raw, signature


@pytest.mark.parametrize("action", ["resume", "promote", "package", "rollback", "complete"])
def test_legacy_signed_price_release_cannot_escape(store, sources, signing, action):
    snapshot = collect(*sources)
    snapshot.files["产品TDS/RS100.TDS.md"] = "# TDS\n9876.54元/公斤".encode()
    legacy_release(store, snapshot, signing[0])
    calls = []
    with pytest.raises(DataError, match="public price gate"):
        if action == "resume":
            store.prepare(None, signing[0], public_scope_approved=True)
        elif action == "promote":
            store.promote(lambda _: calls.append(True))
        elif action == "package":
            store.package(1)
        elif action == "rollback":
            store.rollback(1, signing[0], public_scope_approved=True)
        else:
            store.complete(lambda _: calls.append(True))
    assert store.latest_bytes() is None
    assert not calls
    assert not (store.public / "packages/000001.zip").exists()


@pytest.mark.parametrize("extra", ["objects/orphan", "packages/000009.zip"])
def test_unreferenced_public_payload_also_blocks_resume(store, sources, signing, extra):
    legacy_release(store, collect(*sources), signing[0])
    raw = "采购价：9876.54".encode()
    if extra.endswith(".zip"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("files/产品TDS/RS9.TDS.md", raw)
        raw = buffer.getvalue()
    atomic_write(store.public / extra, raw)
    with pytest.raises(DataError, match="public price gate"):
        store.prepare(None, signing[0], public_scope_approved=True)
    assert store.latest_bytes() is None


@pytest.mark.parametrize("mode", ["import", "sync"])
def test_legacy_signed_price_cannot_be_imported(store, sources, signing, tmp_path, serve, mode):
    snapshot = collect(*sources)
    snapshot.files["产品TDS/RS100.TDS.md"] = "# TDS\n9876.54元/公斤".encode()
    raw, signature = legacy_release(store, snapshot, signing[0])
    pointer = store.pending()["pointer"]
    atomic_write(store.public / "latest.json", canonical_json(pointer))
    cache = tmp_path / "cache"
    package = tmp_path / "legacy.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("metadata/manifest.json", raw)
        archive.writestr("metadata/manifest.sig", signature)
        for path, content in snapshot.files.items():
            archive.writestr("files/" + path, content)
    with pytest.raises(DataError, match="public price gate"):
        if mode == "import":
            import_package(package, cache, signing[1])
        else:
            with serve(store.public) as url:
                synchronize([url], cache, signing[1], allow_local_http=True)
    assert not (cache / "active.json").exists()


@pytest.mark.parametrize("path,raw", [
    ("产品TDS/报价9876.54.TDS.md", b"technical"),
    ("产品配方/9876.54元-示例.md", b"technical"),
    ("产品应用/报价/示例.md", b"---\ntags: []\n---\n"),
    ("产品列表/RS100.md", b'---\n"\\u4ef7\\u683c": 9876.54\n---\n'),
    ("产品列表/RS100.md", b'---\nnote: "\\u00a59876.54"\n---\n'),
    ("产品列表/RS100.md", b'---\ntags: ["\\u4ef7\\u683c"]\n---\n'),
])
def test_filename_and_decoded_metadata(path, raw):
    with pytest.raises(DataError, match="public price gate") as error:
        validate_markdown(path, raw)
    assert "9876.54" not in str(error.value)
    assert "path-sha256=" in str(error.value)


def test_committed_source_and_prepare_fail_before_objects(sources, store, signing):
    snapshot = collect(*sources)
    raw = "---\ntags: []\n---\n报价：9876.54".encode()
    snapshot.files["产品列表/RS100.md"] = raw
    with pytest.raises(DataError, match="public price gate"):
        store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert not (store.public / "objects").exists()
    (sources[0] / "产品列表/RS100.md").write_bytes(raw)
    commit_all(sources[0])
    with pytest.raises(DataError, match="public price gate"):
        collect(*sources)


@pytest.mark.parametrize("text", [
    "价格: 9876.54", "报价待定", "单价保密", "成交价另议", "供货价另议",
    "采购价格另议", "成本价另议", "含税价另议", "未税价另议", "税价另议",
    "折扣: 5%", "返利另议", "price: null", "unit_price: 9876.54",
    "quotation: pending", "discount: 5%", "rebate: pending",
    "CNY 9876.54", "RMB9876.54", "USD 9876.54", "$9876.54", "￥９８７６.５４",
    "9876.54 元/公斤", "9876.54元/kg", "9876.54万元/吨", "9876.54美元", "9876.54 EUR",
    "| 项目 | 报价 |\n| - | - |\n| A | 9876.54 |",
])
def test_price_content_blocked_without_echo(text):
    with pytest.raises(DataError, match="public price gate") as error:
        validate_markdown("产品TDS/RS-DEMO.TDS.md", text.encode())
    assert "9876" not in str(error.value)
    assert text not in str(error.value)


def test_generated_hex_digest_is_not_a_currency_amount():
    from rstone_data.price_gate import check_payload
    raw=b'# Technical TDS\nViscosity 14192 mPa.s\n'
    digest=sha256(raw)
    assert digest.startswith('cad49')
    check_payload('objects/'+digest,raw)
    check_payload('metadata.json',canonical_json({'sha256':digest}))


@pytest.mark.parametrize('raw', [
    b'---\nnote: "USD\\n9876.54"\n---\n',
    b'---\n"\\u4ef7\\u200b\\u683c": 9876.54\n---\n',
])
def test_decoded_yaml_strings_are_scanned_without_repr_escaping(raw):
    with pytest.raises(DataError,match='public price gate'):
        validate_markdown('产品列表/RS90001.md',raw)


def test_decoded_nested_json_strings_are_scanned_without_repr_escaping():
    from rstone_data.price_gate import check_payload
    with pytest.raises(DataError,match='public price gate'):
        check_payload('synthetic.json',b'{"data":[{"note":"USD\\n9876.54"}]}')


@pytest.mark.parametrize("text", ["售价: 9876.54", "成本: 9876.54", "cost: 9876.54"])
def test_additional_monetary_fields_are_blocked(text):
    with pytest.raises(DataError, match="public price gate"):
        validate_markdown("产品TDS/RS-DEMO.TDS.md", text.encode())


@pytest.mark.parametrize("text", ["&#20215;&#26684;: synthetic", "&#165;9876.54"])
def test_rendered_html_entities_cannot_hide_prices(text):
    with pytest.raises(DataError, match="public price gate"):
        validate_markdown("产品TDS/RS-DEMO.TDS.md", text.encode())


@pytest.mark.parametrize("text", [
    "25℃; 67%; 25 mgKOH/g; 200 mPa·s; 1.05 g/cm³; 500 g/mol",
    "固含 67%；酸值 25 mgKOH/g；失败：附着力 0级；耐水失败；成本地测试数据",
    "TDS: tensile strength 25 MPa; viscosity at 25°C; 元素分析；单元测试",
    "4b17d959cadee179cc57795d12bb0cd5bee2ad21",
])
def test_technical_data_unchanged(text):
    assert validate_markdown("产品TDS/RS-DEMO.TDS.md", text.encode()) == text


def test_transport_verification_rejects_legacy_prices(store, sources, signing, serve):
    from rstone_data.transport import fetch_release
    snapshot = collect(*sources)
    snapshot.files["产品TDS/RS100.TDS.md"] = "9876.54元".encode()
    legacy_release(store, snapshot, signing[0])
    atomic_write(store.public / "latest.json", canonical_json(store.pending()["pointer"]))
    with serve(store.public) as url:
        with pytest.raises(DataError, match="public price gate"):
            fetch_release(url, signing[1], allow_local_http=True)


def test_checkpoint_price_gate_runs_before_git(store, monkeypatch):
    import ci_state
    store.root.mkdir(parents=True)
    (store.root / ".git").touch()
    atomic_write(store.public / "objects/orphan", "9876.54元".encode())
    calls = []
    monkeypatch.setattr(ci_state, "git", lambda *args: calls.append(args))
    with pytest.raises(DataError, match="public price gate"):
        ci_state.checkpoint(store.root, "synthetic")
    assert calls == []


def test_prepare_checks_report_metadata_before_writes(store, sources, signing):
    snapshot = collect(*sources)
    snapshot.warnings.append({"path": "synthetic", "note": "USD 9876.54"})
    with pytest.raises(DataError, match="public price gate"):
        store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert not (store.public / "objects").exists()


def test_cli_error_and_report_do_not_echo_filename_amount(store, sources, signing, tmp_path, capsys):
    from rstone_data.cli import main
    snapshot = collect(*sources)
    snapshot.files["产品TDS/9876.54元.TDS.md"] = b"technical"
    legacy_release(store, snapshot, signing[0])
    keys = tmp_path / "keys.json"
    atomic_write(keys, canonical_json({"keys": signing[1]}))
    report = tmp_path / "report.json"
    assert main(["package", "--store", str(store.root), "--keys", str(keys),
                 "--sequence", "1", "--report", str(report)]) == 1
    captured = capsys.readouterr()
    assert "9876.54" not in captured.out + captured.err + report.read_text()
    assert "public price gate" in captured.err


@pytest.mark.parametrize("target", ["import-manifest", "http-latest"])
def test_signed_control_metadata_cannot_carry_prices(store, sources, signing, tmp_path, serve, target):
    from rstone_data.protocol import decode_json
    from rstone_data.transport import fetch_release
    snapshot = collect(*sources)
    raw, signature = legacy_release(store, snapshot, signing[0])
    manifest = decode_json(raw)
    if target == "import-manifest":
        manifest["note"] = "USD 9876.54"
        raw = canonical_json(manifest)
        signature = signing[0].sign(raw)
        package = tmp_path / "metadata.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("metadata/manifest.json", raw)
            archive.writestr("metadata/manifest.sig", signature)
            for path, content in snapshot.files.items():
                archive.writestr("files/" + path, content)
        with pytest.raises(DataError, match="public price gate"):
            import_package(package, tmp_path / "cache", signing[1])
    else:
        pointer = dict(store.pending()["pointer"], note="USD 9876.54")
        atomic_write(store.public / "latest.json", canonical_json(pointer))
        with serve(store.public) as url:
            with pytest.raises(DataError, match="public price gate"):
                fetch_release(url, signing[1], allow_local_http=True)


def test_clean_package_keeps_technical_failures_and_has_zero_price_hits(store, sources, signing):
    from rstone_data.price_gate import check_payload
    snapshot = collect(*sources)
    technical = "# Synthetic TDS\n25℃; 67%; 25 mgKOH/g; 耐水失败; 附着力失败\n".encode()
    snapshot.files["产品TDS/RS100.TDS.md"] = technical
    store.prepare(snapshot, signing[0], public_scope_approved=True)
    store.promote()
    relative = store.package(1)
    with zipfile.ZipFile(store.public / relative) as archive:
        assert archive.read("files/产品TDS/RS100.TDS.md") == technical
        for entry in archive.infolist():
            check_payload(entry.filename, archive.read(entry))
    assert store.read()["files"]
