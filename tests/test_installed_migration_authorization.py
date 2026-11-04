"""Synthetic boundaries exercised with real installed publisher policies."""
import pytest
from rstone_data import catalog_revisions_v2 as v2
from rstone_data.protocol import DataError
from test_protocol_migration_policy import config, legacy
from test_structured_publication import snapshot, PATH
from test_quality_parent_review import RecordingSigner
from test_identity_migration_security import artifacts
from test_release_quality import install_legacy


def test_installed_policy_present():
    assert v2.load_approvals() and v2.load_witnesses()


def test_empty_fixed_policies_authorize_nothing(monkeypatch, tmp_path):
    config(monkeypatch, tmp_path)
    assert v2.load_approvals() == frozenset()
    assert v2.load_witnesses() == ()


@pytest.mark.parametrize('mutation', ['unknown', 'one-byte', 'malformed'])
def test_installed_grant_rejects_synthetic_without_signing(store, signing, mutation):
    assert v2.load_approvals() and v2.load_witnesses()
    after = snapshot(); before = legacy(after)
    install_legacy(store, signing[0], before)
    if mutation == 'one-byte':
        after.files[next(iter(after.files))] += b'\n'
    elif mutation == 'malformed':
        after.files[PATH] = b'---\n{"schema":"rstone-recipe/2"}\n---\nbad\n'
    signer = RecordingSigner(signing[0]); frozen = artifacts(store)
    with pytest.raises(DataError, match='invalid structured recipe' if mutation == 'malformed' else 'quality gate'):
        store.prepare(after, signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == frozen
