"""Synthetic adversarial inputs must fail at real Store publication boundaries."""
import copy
import json

import pytest

from rstone_data.build import deletion_approval, inventory
from rstone_data.protocol import DataError
from rstone_data.sources import Snapshot
from test_identity_migrations import SCHEMA, approval_for, approve, migration_pair, product
from test_quality_parent_review import RecordingSigner
from test_release_quality import install_legacy


def artifacts(store):
    return {str(path.relative_to(store.root)): path.read_bytes()
            for folder in (store.public, store.state) for path in folder.rglob("*")
            if path.is_file()}


def reject_without_artifacts(store, signer, snapshot, match=None, **kwargs):
    before = artifacts(store)
    with pytest.raises(DataError, match=match) as error:
        store.prepare(snapshot, signer, public_scope_approved=True, **kwargs)
    assert signer.calls == 0
    assert artifacts(store) == before  # No new objects, signatures, pending or ZIP.
    assert not list(store.public.rglob("*.zip"))
    return error.value


@pytest.mark.parametrize("case", [
    "no-policy", "partial-policy", "target-missing", "alias-missing", "alias-null",
    "alias-list", "alias-wrong-suffix", "alias-bare", "alias-whitespace", "alias-lowercase",
    "alias-comma", "duplicate-claim", "many-to-one", "many-to-one-owner-receipts",
    "source-bytes-changed", "source-path-changed",
    "baseline-other-bytes-changed", "target-bytes-changed", "target-path-changed",
    "same-number-suffix-dropped", "same-number-letter-dropped", "same-number-wrong-suffix",
    "source-declares-approvals", "metadata-declares-approvals",
])
def test_invalid_migration_has_zero_publication_side_effects(
        store, signing, monkeypatch, tmp_path, case):
    before, after = migration_pair()
    policy = approve(monkeypatch, tmp_path, before, after)
    target = "产品列表/RS9401-6B.md"
    source = "产品列表/RD9401-6B.md"
    if case == "no-policy":
        policy.write_text(json.dumps({"schema": SCHEMA, "approvals": []}))
    elif case == "partial-policy":
        approve(monkeypatch, tmp_path, before, after, suffixes=("2B", "8B"))
    elif case == "target-missing":
        del after.files[target]
    elif case.startswith("alias-"):
        values = {"missing": None, "null": "null", "list": "[RD9401-6B]",
                  "wrong-suffix": "RD9401-8B", "bare": "9401-6B",
                  "whitespace": "' RD9401-6B'", "lowercase": "rd9401-6B",
                  "comma": "'RD9401-6B,RD9401-8B'"}
        after.files[target] = product(values[case[6:]])
    elif case == "duplicate-claim":
        after.files["产品列表/RS9500.md"] = product("RD9401-6B")
    elif case == "many-to-one":
        del after.files[target]
        after.files["产品列表/RS9401-2B.md"] = product("'RD9401-2B RD9401-6B'")
    elif case == "many-to-one-owner-receipts":
        del after.files[target]
        contents = json.loads(policy.read_text())
        contents["approvals"].append(approval_for(before, after, source, "产品列表/RS9401-2B.md"))
        policy.write_text(json.dumps(contents))
    elif case == "source-bytes-changed":
        before.files[source] += b"changed baseline source\n"
    elif case == "source-path-changed":
        before.files["产品列表/nested/RD9401-6B.md"] = before.files.pop(source)
    elif case == "baseline-other-bytes-changed":
        before.files["产品列表/RS9900.md"] += b"different baseline\n"
    elif case == "target-bytes-changed":
        after.files[target] += b"changed current source\n"
    elif case == "target-path-changed":
        after.files["产品列表/nested/RS9401-6B.md"] = after.files.pop(target)
    elif case.startswith("same-number-"):
        stem = {"same-number-suffix-dropped": "RS9401",
                "same-number-letter-dropped": "RS9401-6",
                "same-number-wrong-suffix": "RS9401-9B"}[case]
        new = "产品列表/" + stem + ".md"
        after.files[new] = after.files.pop(target)
        # Even a fingerprint for the exact malformed move cannot authorize
        # same-number truncation or cross-suffix substitution.
        contents = json.loads(policy.read_text())
        contents["approvals"].append(approval_for(before, after, source, new))
        policy.write_text(json.dumps(contents))
    elif case == "source-declares-approvals":
        payload = policy.read_text()
        policy.write_text(json.dumps({"schema": SCHEMA, "approvals": []}))
        after.files["产品配方/forged-approval.md"] = payload.encode()
    else:
        after.warnings = [{"identityApprovals": json.loads(policy.read_text())["approvals"]}]
        policy.write_text(json.dumps({"schema": SCHEMA, "approvals": []}))
    install_legacy(store, signing[0], before)
    error = reject_without_artifacts(store, RecordingSigner(signing[0]), after,
                                    approval=deletion_approval(store.read(), inventory(after.files)))
    assert "RD9401-6B" not in str(error)  # Logical RS paths may be reported; alias values are not.


@pytest.mark.parametrize("suffixes", [("2", "2B", "2C"), ("6B", "6B-V02", "8B")])
def test_all_complete_numeric_and_letter_suffixes_stay_independent(
        store, signing, monkeypatch, tmp_path, suffixes):
    before = Snapshot({f"产品列表/RD9401-{suffix}.md": product() for suffix in suffixes},
                      migration_pair()[0].commits)
    after = Snapshot({f"产品列表/RS9401-{suffix}.md": product(f"RD9401-{suffix}")
                      for suffix in suffixes}, before.commits)
    approve(monkeypatch, tmp_path, before, after, suffixes=suffixes)
    install_legacy(store, signing[0], before)
    result = store.prepare(after, signing[0], public_scope_approved=True,
                           approval=deletion_approval(store.read(), inventory(after.files)))
    assert result["qualityGate"]["summary"]["ok"]
    assert len(store.read(store.pending()["pointer"])["files"]) == len(suffixes)


@pytest.mark.parametrize("bad", [None, "{", "[]", '{"schema":"other","approvals":[]}',
                                '{"schema":"rstone-identity-migrations/1","approvals":["*"]}',
                                '{"schema":"rstone-identity-migrations/1","approvals":{},"extra":1}',
                                '{"schema":"rstone-identity-migrations/1","approvals":[],"approvals":[]}'])
def test_missing_or_invalid_policy_fails_closed_before_signing(
        store, signing, monkeypatch, tmp_path, bad):
    before, after = migration_pair()
    policy = approve(monkeypatch, tmp_path, before, after)
    if bad is None:
        policy.unlink()
    else:
        policy.write_text(bad)
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after,
                             match="configuration unavailable")


@pytest.mark.parametrize("operation", ["resume", "promote", "complete", "package"])
def test_unapproved_signed_pending_migration_cannot_escape_via_another_exit(
        store, signing, monkeypatch, tmp_path, operation):
    before, after = migration_pair()
    approve(monkeypatch, tmp_path, before, after, suffixes=())
    install_legacy(store, signing[0], before, sequence=1)
    install_legacy(store, signing[0], after, sequence=2, pending=True)
    previous = artifacts(store)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate"):
        if operation == "resume":
            store.prepare(None, signer, public_scope_approved=True)
        elif operation == "promote":
            store.promote()
        elif operation == "complete":
            store.complete(lambda _: pytest.fail("must not reach network verifier"))
        else:
            store.package(2)
    assert signer.calls == 0
    assert artifacts(store) == previous


def test_identity_approval_does_not_approve_bulk_deletion(
        store, signing, monkeypatch, tmp_path):
    before, after = migration_pair()
    approve(monkeypatch, tmp_path, before, after)
    install_legacy(store, signing[0], before)
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="bulk deletion")


@pytest.mark.parametrize("case", ["tags", "functionality", "empty", "residue", "internal",
                                 "new-link", "amount", "secret"])
def test_identity_approval_never_bypasses_other_payload_gates(
        store, signing, monkeypatch, tmp_path, case):
    before, after = migration_pair()
    approve(monkeypatch, tmp_path, before, after)
    # Change a non-migrated document: the migration fingerprints remain valid,
    # so failures must come from the independent content/secret gates.
    bad = {"tags": "---\ntags: []\n固含: 75\n---\n",
           "functionality": "---\ntags: [油性, UV, 3官]\n官能度: 4\n---\n",
           "empty": "---\ntags: [油性, UV]\n---\n",
           "residue": "---\ntags: [油性, UV]\n固含: 75\n技术源: 'None'\n---\n",
           "internal": "---\ntags: [油性, UV]\n固含: 75\n---\n##### 特别说明\n仅内部参考。\n",
           "new-link": "---\ntags: [油性, UV]\n固含: 75\n---\n[[RS999999]]\n",
           "amount": "---\ntags: [油性, UV]\n固含: 75\n---\n报价 987654 元/kg\n",
           "secret": "---\ntags: [油性, UV]\n固含: 75\n---\npassword: SYNTHETIC_SECRET\n"}[case]
    after.files["产品列表/RS9900.md"] = bad.encode()
    install_legacy(store, signing[0], before)
    error = reject_without_artifacts(store, RecordingSigner(signing[0]), after,
                                    approval=deletion_approval(store.read(), inventory(after.files)))
    assert "987654" not in str(error) and "SYNTHETIC_SECRET" not in str(error)
    assert "checks=identity" not in str(error)


def test_byte_identical_one_to_one_formal_move_is_not_a_fresh_rd_replacement(
        store, signing, monkeypatch, tmp_path):
    before = Snapshot({"产品列表/RD9402-5.md": product(),
                       "产品列表/RS9900.md": product()}, migration_pair()[0].commits)
    after = copy.deepcopy(before)
    del after.files["产品列表/RD9402-5.md"]
    after.files["产品列表/RS9901.md"] = after.files.pop("产品列表/RS9900.md")
    install_legacy(store, signing[0], before)
    approve(monkeypatch, tmp_path, before, after, suffixes=())
    result = store.prepare(after, signing[0], public_scope_approved=True,
                           approval=deletion_approval(store.read(), inventory(after.files)))
    assert result["qualityGate"]["summary"]["ok"]


@pytest.mark.parametrize("ambiguity", ["multiple-old", "multiple-new", "changed-bytes"])
def test_formal_byte_reuse_is_not_a_blanket_rd_rename_exception(
        store, signing, monkeypatch, tmp_path, ambiguity):
    before = Snapshot({"产品列表/RD9402-5.md": product(),
                       "产品列表/RS9900.md": product()}, migration_pair()[0].commits)
    if ambiguity == "multiple-old":
        before.files["产品列表/RS9902.md"] = product()
    after = Snapshot({"产品列表/RS9901.md": product()}, before.commits)
    if ambiguity == "multiple-new":
        after.files["产品列表/RS9902.md"] = product()
    elif ambiguity == "changed-bytes":
        after.files["产品列表/RS9901.md"] += b"new payload\n"
    install_legacy(store, signing[0], before)
    approve(monkeypatch, tmp_path, before, after, suffixes=())
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="quality gate",
                             approval=deletion_approval(store.read(), inventory(after.files)))


def test_omitting_alias_cannot_hide_a_potential_singleton_renumbering(
        store, signing, monkeypatch, tmp_path):
    before = Snapshot({"产品列表/RD9402-5.md": product(),
                       "产品列表/RS9900.md": product()}, migration_pair()[0].commits)
    after = copy.deepcopy(before)
    del after.files["产品列表/RD9402-5.md"]
    after.files["产品列表/RS9502.md"] = product()  # No claim, no trusted mapping.
    install_legacy(store, signing[0], before)
    approve(monkeypatch, tmp_path, before, after, suffixes=())
    reject_without_artifacts(store, RecordingSigner(signing[0]), after, match="quality gate",
                             approval=deletion_approval(store.read(), inventory(after.files)))
