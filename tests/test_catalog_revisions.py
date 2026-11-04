"""Synthetic publisher-policy catalogue transactions; no real approval is granted."""
import copy
import json
import zipfile

import pytest

from rstone_data import quality_gate as q
from rstone_data.build import deletion_approval, inventory
from rstone_data.protocol import DataError, canonical_json, sha256
from rstone_data.sources import Snapshot
from test_identity_migration_security import artifacts, reject_without_artifacts
from test_quality_parent_review import RecordingSigner
from test_release_quality import install_legacy

SCHEMA = "rstone-catalog-revisions/1"
GRADE = "RS9607-2B"
PRODUCT = "---\ntags: [水性, UV]\n固含: 77\n---\n".encode()


def recipe(grade=GRADE):
    return (f"---\n关联产品: '[[{grade}]]'\n---\n# {grade}\n\n"
            "| 原料编号 | 投入数(g) | 百分比(%) | 供应商 | 备注 |\n"
            "| --- | --- | --- | --- | --- |\n"
            f"| [[{grade}]] | 40 | 80 | | |\n"
            "| 水 | 10-12 |  | | |\n"
            "| 固化剂 | 按NCO/OH=1.1另加 |  | | |\n\n"
            "施工按当量另加固化剂，质量未定；百分比仅为表内主剂小计基准。\n").encode()


def pair(retire=False):
    files = {f"产品列表/{GRADE}.md": PRODUCT,
             "产品应用/涂层.md": "---\ntags: [PC]\n---\n".encode(),
             f"产品TDS/{GRADE}.TDS.md": b"# synthetic TDS\n",
             f"产品配方/{GRADE}-参考配方-方案02.md": recipe()}
    before = Snapshot(files, {"Mehael-Yeh/Rstone": "a" * 40,
                              "Mehael-Yeh/RstoneTDS": "b" * 40})
    after = copy.deepcopy(before)
    if retire:
        for name in ("RS9608", "RS9608-2", "RS9608-2B", "RS9608.3", "RS9608v02"):
            before.files[f"产品列表/{name}.md"] = PRODUCT + f"# {name}\n".encode()
        after = copy.deepcopy(before)
        del after.files["产品列表/RS9608.md"]
    else:
        after.files[f"产品配方/{GRADE}.md"] = after.files.pop(
            f"产品配方/{GRADE}-参考配方-方案02.md")
    return before, after


def findings(before, after):
    return q.check_identity(q.snapshot_corpus(after), q.snapshot_corpus(before))


def fingerprint(before, after, scope=None):
    # Deliberately independent from the production proposal helper.
    scope = findings(before, after) if scope is None else scope
    scope = sorted([{"category": item["category"], "path": item["path"]}
                    for item in scope], key=lambda item: (item["category"], item["path"]))
    return sha256(canonical_json({
        "schema": SCHEMA,
        "before": {path: sha256(raw) for path, raw in before.files.items()},
        "after": {path: sha256(raw) for path, raw in after.files.items()},
        "scope": scope,
    }))


def policy(monkeypatch, tmp_path, approvals=()):
    path = tmp_path / "publisher-catalog-policy.json"
    path.write_text(json.dumps({"schema": SCHEMA, "approvals": list(approvals)}))
    monkeypatch.setattr(q, "default_catalog_revisions", lambda: path, raising=False)
    return path


@pytest.mark.parametrize("retire", [False, True], ids=["complex-recipe", "base-only-retirement"])
def test_exact_controlled_transaction_prepares_and_packages(store, signing, monkeypatch, tmp_path, retire):
    before, after = pair(retire)
    assert findings(before, after)
    policy(monkeypatch, tmp_path)
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="quality gate")
    exact = fingerprint(before, after)
    policy(monkeypatch, tmp_path, [exact])
    result = store.prepare(after, signing[0], public_scope_approved=True,
                           approval=deletion_approval(store.read(), inventory(after.files)))
    audit = result["qualityGate"]["catalogRevision"]
    assert audit["used"] is True and audit["fingerprint"] == exact
    assert audit["exemptedCount"] == len(findings(before, after))
    assert result["qualityGate"]["summary"]["ok"]
    with zipfile.ZipFile(store.public / store.package(result["sequence"])) as archive:
        for path, raw in after.files.items():
            assert archive.read("files/" + path) == raw
    store.promote()
    assert store.read()["sequence"] == 2


@pytest.mark.parametrize("change", ["before-recipe", "before-other", "after-recipe", "after-other",
                                    "after-path", "scope-extra", "scope-missing", "scope-duplicate"])
def test_every_inventory_byte_and_exact_scope_is_bound(store, signing, monkeypatch, tmp_path, change):
    before, after = pair()
    scope = findings(before, after)
    if change == "scope-extra":
        scope.append({"category": "identity-suffix-dropped", "path": "产品配方/extra.md"})
    elif change == "scope-missing":
        scope.pop()
    elif change == "scope-duplicate":
        scope.append(scope[0])
    policy(monkeypatch, tmp_path, [fingerprint(before, after, scope)])
    if change.startswith("before-"):
        path = next(p for p in before.files if p.startswith(
            "产品配方/" if change == "before-recipe" else "产品TDS/"))
        before.files[path] += b"\nbyte drift\n"
    elif change in ("after-recipe", "after-other"):
        path = next(p for p in after.files if p.startswith(
            "产品配方/" if change == "after-recipe" else "产品TDS/"))
        after.files[path] += b"\nbyte drift\n"
    elif change == "after-path":
        after.files[f"产品配方/nested/{GRADE}.md"] = after.files.pop(f"产品配方/{GRADE}.md")
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="quality gate",
                             approval=deletion_approval(store.read(), inventory(after.files)))


@pytest.mark.parametrize("case", ["suffix-deleted", "suffix-renamed", "swallowing-base", "rd-to-rs",
                                 "unknown-recipe-tail", "wrong-owner", "missing-material",
                                 "metadata-only", "no-separator", "performance-table",
                                 "negative", "reversed", "zero-owner", "fenced", "example", "extra-column",
                                 "unregistered-claim", "wrong-namespace"])
def test_even_exact_hash_cannot_authorize_ineligible_identity_or_table(
        store, signing, monkeypatch, tmp_path, case):
    before, after = pair(retire=case in ("suffix-deleted", "suffix-renamed", "swallowing-base"))
    path = f"产品配方/{GRADE}.md"
    if case == "suffix-deleted":
        del after.files["产品列表/RS9608-2B.md"]
    elif case == "suffix-renamed":
        after.files["产品列表/RS9608-8B.md"] = after.files.pop("产品列表/RS9608-2B.md")
    elif case == "swallowing-base":
        after.files["产品列表/RS9608.md"] = after.files.pop("产品列表/RS9608-2B.md")
    elif case == "rd-to-rs":
        before.files["产品列表/RD9610-2B.md"] = PRODUCT
        after.files["产品列表/RS9610-2B.md"] = PRODUCT.replace(
            b"---\n", b"---\n", 1) + b"\nnew product\n"
    elif case == "unknown-recipe-tail":
        before.files[f"产品配方/{GRADE}-未知后缀.md"] = before.files.pop(
            f"产品配方/{GRADE}-参考配方-方案02.md")
    elif case == "wrong-owner":
        before.files["产品列表/RS9607.md"] = PRODUCT
        after.files["产品列表/RS9607.md"] = PRODUCT
        after.files["产品配方/RS9607.md"] = recipe("RS9607")
        del after.files[path]
    elif case == "missing-material":
        after.files[path] = after.files[path].replace("| 水 | 10-12 |  | | |\n".encode(), b"")
    elif case == "metadata-only":
        after.files[path] = f"---\n关联产品: '[[{GRADE}]]'\n---\n".encode()
    elif case == "no-separator":
        after.files[path] = after.files[path].replace(b"| --- | --- | --- | --- | --- |\n", b"")
    elif case == "performance-table":
        after.files[path] = after.files[path].replace("投入数(g)".encode(), "硬度".encode())
    elif case == "extra-column":
        after.files[path] = after.files[path].replace(f"| [[{GRADE}]]".encode(), f"|| [[{GRADE}]]".encode())
    elif case in ("negative", "reversed", "zero-owner"):
        value = {"negative": "-40", "reversed": "40-10", "zero-owner": "0"}[case]
        after.files[path] = after.files[path].replace(b"| 40 |", f"| {value} |".encode())
    elif case == "fenced":
        after.files[path] = b"```markdown\n" + after.files[path] + b"```\n"
    elif case == "example":
        after.files[path] = after.files[path].replace(f"# {GRADE}".encode(), "## 示例".encode())
    elif case == "unregistered-claim":
        after.files[path] = after.files[path].replace(
            f"关联产品: '[[{GRADE}]]'".encode(), f"关联产品: '[[{GRADE}]][[RS9699]]'".encode())
    elif case == "wrong-namespace":
        after.files[f"产品应用/{GRADE}.md"] = after.files.pop(path)
    assert findings(before, after)
    policy(monkeypatch, tmp_path, [fingerprint(before, after)])
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="quality gate",
                             approval=deletion_approval(store.read(), inventory(after.files)))


@pytest.mark.parametrize("carrier", ["warnings", "frontmatter", "environment", "source-file"])
def test_source_or_operator_cannot_grant_a_revision(store, signing, monkeypatch, tmp_path, carrier):
    before, after = pair()
    exact = fingerprint(before, after)
    policy(monkeypatch, tmp_path)
    if carrier == "warnings":
        after.warnings = [{"catalogRevision": {"approved": True, "fingerprint": exact}}]
    elif carrier == "frontmatter":
        path = f"产品配方/{GRADE}.md"
        after.files[path] = after.files[path].replace(b"---\n", f"---\ncatalogApproval: {exact}\n".encode(), 1)
    elif carrier == "environment":
        monkeypatch.setenv("CATALOG_REVISION_APPROVAL", exact)
        monkeypatch.setenv("CATALOG_REVISIONS_CONFIG", str(tmp_path / "forged.json"))
    else:
        after.files["产品配方/catalog_revisions.md"] = canonical_json({"schema": SCHEMA, "approvals": [exact]})
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="quality gate")


@pytest.mark.parametrize("bad", [None, "{", "[]", '{"schema":"unknown","approvals":[]}',
                                '{"schema":"rstone-catalog-revisions/1","approvals":["*"]}',
                                '{"schema":"rstone-catalog-revisions/1","approvals":[],"extra":1}',
                                '{"schema":"rstone-catalog-revisions/1","approvals":[],"approvals":[]}'])
def test_unavailable_or_unknown_policy_fails_closed(store, signing, monkeypatch, tmp_path, bad):
    before, after = pair()
    path = policy(monkeypatch, tmp_path)
    if bad is None:
        path.unlink()
    else:
        path.write_text(bad)
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="configuration unavailable")


@pytest.mark.parametrize("retire", [False, True])
@pytest.mark.parametrize("operation", ["resume", "promote", "complete", "package"])
def test_unapproved_legacy_pending_cannot_escape(store, signing, monkeypatch, tmp_path, retire, operation):
    before, after = pair(retire)
    policy(monkeypatch, tmp_path)
    install_legacy(store, signing[0], before)
    install_legacy(store, signing[0], after, sequence=2, pending=True)
    previous = artifacts(store)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate"):
        if operation == "resume":
            store.prepare(None, signer, public_scope_approved=True)
        elif operation == "promote":
            store.promote()
        elif operation == "complete":
            store.complete(lambda _: pytest.fail("must not call verifier"))
        else:
            store.package(2)
    assert artifacts(store) == previous and signer.calls == 0


def test_rollback_does_not_reuse_forward_transaction_approval(store, signing, monkeypatch, tmp_path):
    before, after = pair()
    policy(monkeypatch, tmp_path, [fingerprint(before, after)])
    install_legacy(store, signing[0], before)
    install_legacy(store, signing[0], after, sequence=2)
    previous = artifacts(store)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate"):
        store.rollback(2, signer, public_scope_approved=True)
    assert artifacts(store) == previous and signer.calls == 0


def test_catalogue_approval_keeps_original_quarter_deletion_gate(store, signing, monkeypatch, tmp_path):
    before, after = pair(True)
    for index in range(4):
        before.files[f"产品配方/旧档{index}.md"] = recipe() + f"\narchive {index}\n".encode()
    policy(monkeypatch, tmp_path, [fingerprint(before, after)])
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="bulk deletion")


def test_proposal_cli_is_read_only_and_never_approves(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    before, after = pair()
    for label, snapshot in (("before", before), ("after", after)):
        for path, raw in snapshot.files.items():
            target = tmp_path / label / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    frozen = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = subprocess.run([sys.executable, "-m", "rstone_data.catalog_revisions",
                             "--before", str(tmp_path / "before"), "--after", str(tmp_path / "after")],
                            capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "scripts")))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "proposal CLI must return proposed-only JSON"
    payload = json.loads(result.stdout)
    assert payload["status"] == "proposed-only" and payload["approved"] is False
    assert payload["fingerprint"] == fingerprint(before, after)
    assert payload["unexemptedCount"] == 0
    assert GRADE not in result.stdout and str(tmp_path) not in result.stdout
    assert frozen == {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_selected_main_preserves_existing_shared_product_associations(
        store, signing, monkeypatch, tmp_path):
    before, after = pair()
    alias = "RD9607-2B"
    for snap in (before, after):
        snap.files[f"产品列表/{alias}.md"] = PRODUCT
    source = f"产品配方/{GRADE}-参考配方-方案02.md"
    before.files[source] = before.files[source].replace(
        f"关联产品: '[[{GRADE}]]'".encode(), f"关联产品: '[[{GRADE}]][[{alias}]]'".encode())
    # A controlled byte approval still cannot silently lose the other explicit
    # full-product association from the selected original recipe.
    policy(monkeypatch, tmp_path, [fingerprint(before, after)])
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="quality gate",
                             approval=deletion_approval(store.read(), inventory(after.files)))
    after.files[f"产品配方/{GRADE}.md"] = before.files[source]
    policy(monkeypatch, tmp_path, [fingerprint(before, after)])
    result = store.prepare(after, signing[0], public_scope_approved=True)
    assert result["qualityGate"]["catalogRevision"]["used"]
    assert (store.public / store.package(result["sequence"])).is_file()


def test_matching_partial_scope_cannot_clear_another_namespace_violation(
        store, signing, monkeypatch, tmp_path):
    before, after = pair()
    after.files[f"产品应用/{GRADE}.md"] = "---\ntags: [PC]\n---\n".encode()
    scope = [item for item in findings(before, after) if item["path"].startswith("产品配方/")]
    policy(monkeypatch, tmp_path, [fingerprint(before, after, scope)])
    report = q.evaluate(q.snapshot_corpus(after), q.snapshot_corpus(before), q.load_vocabulary(None), 0, 0, True)
    assert report.catalog_revision["exemptedCount"] == len(scope)
    assert report.check("identity")["violations"] == [{
        "path": f"产品应用/{GRADE}.md", "category": "stem-collision-cross-namespace"}]
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="quality gate")


def test_audit_is_separate_from_unchanged_warning_summary(monkeypatch, tmp_path):
    before, after = pair()
    for snap in (before, after):
        snap.files["产品配方/历史.md"] = b"[[RS9699]]\n"
    path = policy(monkeypatch, tmp_path)
    def evaluate():
        return q.evaluate(q.snapshot_corpus(after), q.snapshot_corpus(before),
                          q.load_vocabulary(None), 0, 0, True).json()
    unapproved = evaluate()
    assert "catalogRevision" not in unapproved
    path.write_text(json.dumps({"schema": SCHEMA, "approvals": [fingerprint(before, after)]}))
    approved = evaluate()
    assert approved["catalogRevision"]["used"]
    assert approved["warnings"] == unapproved["warnings"]
    assert approved["summary"]["warnings"] == unapproved["summary"]["warnings"] > 0


def test_installed_policy_is_loaded_exactly_and_only_publisher_relative():
    from pathlib import Path
    path = Path(q.__file__).resolve().parents[2] / "config/catalog_revisions.json"
    assert q.default_catalog_revisions() == path
    installed = json.loads(path.read_text(encoding="utf-8"))
    assert set(installed) == {"schema", "approvals"} and installed["schema"] == SCHEMA
    assert isinstance(installed["approvals"], list)
    assert all(isinstance(value, str) and len(value) == 64 and
               all(char in "0123456789abcdef" for char in value)
               for value in installed["approvals"])
    expected = frozenset(installed["approvals"])
    assert len(expected) == len(installed["approvals"])
    assert q.load_catalog_revisions() == expected


def test_empty_publisher_policy_still_approves_nothing(monkeypatch, tmp_path):
    policy(monkeypatch, tmp_path)
    assert q.load_catalog_revisions() == frozenset()


@pytest.mark.parametrize("kind", ["tags", "empty-product", "internal-note", "secret", "money", "new-link"])
def test_exact_revision_does_not_waive_other_publication_gates(
        store, signing, monkeypatch, tmp_path, kind):
    before, after = pair()
    path = f"产品列表/{GRADE}.md"
    if kind == "tags":
        after.files[path] = PRODUCT.replace("[水性, UV]".encode(), b"[]")
    elif kind == "empty-product":
        after.files[path] = "---\ntags: [水性, UV]\n---\n".encode()
    elif kind == "internal-note":
        after.files[path] += "\n##### 特别说明\n仅内部参考。\n".encode()
    elif kind == "secret":
        after.files[path] += b"\npassword: SYNTHETIC_SECRET\n"
    elif kind == "money":
        after.files[path] += "\n报价 987654 元/kg\n".encode()
    else:
        after.files[path] += b"\n[[RS9699]]\n"
    # Recompute for the exact bad bytes, so independent gates—not stale hashes—
    # are what must reject the payload.
    policy(monkeypatch, tmp_path, [fingerprint(before, after)])
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after,
                             approval=deletion_approval(store.read(), inventory(after.files)))
