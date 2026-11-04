"""Synthetic real publishing/GC/HTTP mirror regression; ephemeral signing fixture."""
from datetime import datetime, timedelta, timezone

import pytest

from rstone_data.mirrors import deploy_mirror
from rstone_data.protocol import DataError, decode_json
from rstone_data.release import utc_now
from test_structured_publication import snapshot


def publish_generation(store, signer, sequence):
    candidate = snapshot()
    candidate.files["产品TDS/RS990001-Q5.TDS.md"] = f"# synthetic TDS generation {sequence}\n".encode()
    result = store.prepare(candidate, signer, public_scope_approved=True)
    assert result["sequence"] == sequence
    store.promote()


def test_four_generations_gc_then_real_mirror(store, signing, tmp_path, serve):
    for sequence in range(1, 5):
        publish_generation(store, signing[0], sequence)
    plan = store.gc(retain=1, grace_days=0, apply=True, now=utc_now() + timedelta(days=1))
    assert plan["protectedSequences"] == [3, 4]
    assert store.sequences() == [3, 4]
    report = decode_json((store.state / "reports/000003.json").read_bytes())
    assert report["qualityBaselineSequence"] == 2
    target = tmp_path / "mirror"
    target.mkdir()
    with serve(target) as base:
        result = deploy_mirror(store.root, target, base, signing[1], allow_local_http=True)
    assert result["status"] == "mirrored" and result["sequence"] == 4
    assert (target / "latest.json").read_bytes() == store.latest_bytes()
    for sequence in (3, 4):
        for suffix in ("json", "sig"):
            relative = f"manifests/{sequence:06d}.{suffix}"
            assert (target / relative).read_bytes() == (store.public / relative).read_bytes()
    assert not (target / "manifests/000002.json").exists()
    print("REAL_ENTRY_RECEIPT: published=1,2,3,4; gc_public=3,4; mirror_http=4; canonical_bytes=equal")


def test_gc_preserves_inferred_baseline_when_no_report_is_bound(store, signing):
    from test_release_quality import install_legacy
    for sequence in range(1, 5):
        candidate = snapshot()
        candidate.files["产品TDS/RS990001-Q5.TDS.md"] = f"# synthetic {sequence}\n".encode()
        install_legacy(store, signing[0], candidate, sequence=sequence, schema_version=2)
    store.gc(retain=1, grace_days=0, apply=True, now=utc_now() + timedelta(days=1))
    manifest = decode_json((store.public / "manifests/000003.json").read_bytes())
    assert store._quality_context(manifest)[0] == 2


def test_repeated_gc_keeps_only_live_one_hop_evidence(store, signing, tmp_path, serve):
    for sequence in range(1, 13):
        publish_generation(store, signing[0], sequence)
        if sequence < 4:
            continue
        plan = store.gc(retain=1, grace_days=0, apply=True, now=utc_now() + timedelta(days=1))
        assert store.sequences() == [sequence - 1, sequence]
        assert plan["baselineEvidenceSequences"] == [sequence - 2]
        evidence = store.state / "quality-baselines"
        assert sorted(p.name for p in evidence.iterdir()) == [
            f"{sequence - 2:06d}.json", f"{sequence - 2:06d}.sig"]
        needed = set()
        for raw in [*(store.public / "manifests").glob("*.json"), *evidence.glob("*.json")]:
            needed.update(r["sha256"] for r in decode_json(raw.read_bytes())["files"])
        assert {p.name for p in (store.public / "objects").iterdir()} == needed
        assert (store.public / store.package(sequence - 1)).is_file()
    target = tmp_path / "mirror"
    target.mkdir()
    with serve(target) as base:
        assert deploy_mirror(store.root, target, base, signing[1], allow_local_http=True)["sequence"] == 12


def test_gc_dry_run_never_writes_evidence(store, signing):
    for sequence in range(1, 5):
        publish_generation(store, signing[0], sequence)
    before = {str(p.relative_to(store.root)): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    plan = store.gc(retain=1, grace_days=0, apply=False, now=utc_now() + timedelta(days=1))
    assert plan["baselineEvidenceSequences"] == [2]
    after = {str(p.relative_to(store.root)): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("defect", ["manifest", "signature", "object", "missing"])
def test_damaged_private_baseline_refuses_mirror_before_any_target_write(store, signing, tmp_path, serve, defect):
    for sequence in range(1, 5):
        publish_generation(store, signing[0], sequence)
    store.gc(retain=1, grace_days=0, apply=True, now=utc_now() + timedelta(days=1))
    evidence = store.state / "quality-baselines/000002.json"
    if defect == "missing":
        evidence.unlink()
    elif defect == "manifest":
        evidence.write_bytes(evidence.read_bytes() + b" ")
    elif defect == "signature":
        signature = evidence.with_suffix(".sig")
        raw = signature.read_bytes()
        signature.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    else:
        manifest = decode_json(evidence.read_bytes())
        record = next(r for r in manifest["files"] if r["path"].startswith("产品TDS/"))
        (store.public / "objects" / record["sha256"]).write_bytes(b"corrupted synthetic evidence")
    target = tmp_path / "mirror"
    target.mkdir()
    (target / "sentinel").write_bytes(b"unchanged")
    with serve(target) as base:
        with pytest.raises(DataError):
            deploy_mirror(store.root, target, base, signing[1], allow_local_http=True)
    assert {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()} == {
        "sentinel": b"unchanged"}


def test_signed_bad_outgoing_history_still_refuses_after_gc(store, signing, tmp_path, serve):
    from test_release_quality import install_legacy
    from test_structured_publication import model
    for sequence in range(1, 5):
        publish_generation(store, signing[0], sequence)
    store.gc(retain=1, grace_days=0, apply=True, now=utc_now() + timedelta(days=1))
    latest = store.latest_bytes()
    invalid = model()
    invalid["rows"][0][1] = "0"
    install_legacy(store, signing[0], snapshot(invalid), sequence=3, schema_version=2)
    (store.public / "latest.json").write_bytes(latest)
    target = tmp_path / "mirror"
    target.mkdir()
    with serve(target) as base:
        with pytest.raises(DataError):
            deploy_mirror(store.root, target, base, signing[1], allow_local_http=True)
    assert not list(target.rglob("*"))


def test_rollback_revalidates_retained_target_with_private_evidence(store, signing, tmp_path, serve):
    for sequence in range(1, 5):
        publish_generation(store, signing[0], sequence)
    store.gc(retain=1, grace_days=0, apply=True, now=utc_now() + timedelta(days=1))
    assert store.rollback(3, signing[0], public_scope_approved=True)["sequence"] == 5
    store.promote()
    plan = store.gc(retain=1, grace_days=0, apply=True, now=utc_now() + timedelta(days=1))
    assert plan["protectedSequences"] == [3, 4, 5]
    assert plan["baselineEvidenceSequences"] == [2]
    target = tmp_path / "mirror"
    target.mkdir()
    with serve(target) as base:
        assert deploy_mirror(store.root, target, base, signing[1], allow_local_http=True)["sequence"] == 5


def test_grace_retained_rollback_keeps_its_exact_restore_dependency(store, signing, tmp_path, serve):
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.prepare(snapshot(), signing[0], public_scope_approved=True, now=origin)
    store.promote()
    for sequence, day in ((2, 1), (3, 9), (4, 10), (5, 11)):
        if sequence == 3:
            store.rollback(1, signing[0], public_scope_approved=True, now=origin + timedelta(days=day))
        else:
            candidate = snapshot()
            candidate.files["产品TDS/RS990001-Q5.TDS.md"] = f"# synthetic {sequence}\n".encode()
            store.prepare(candidate, signing[0], public_scope_approved=True, now=origin + timedelta(days=day))
        store.promote()
    plan = store.gc(retain=1, grace_days=4, apply=True, now=origin + timedelta(days=12))
    # Sequence 3 is retained by grace, not the newest/current predecessor set.
    assert 1 in plan["protectedSequences"]
    target = tmp_path / "mirror"
    target.mkdir()
    with serve(target) as base:
        assert deploy_mirror(store.root, target, base, signing[1], allow_local_http=True)["sequence"] == 5
