"""Parent independent real-entry checks; no production keys or external Git writes."""
from datetime import timedelta
import importlib.util
from pathlib import Path
import subprocess
import os

import pytest

from rstone_data.mirrors import deploy_mirror
from rstone_data.protocol import DataError
from rstone_data.release import Store, utc_now
from test_structured_publication import snapshot


def parent_publish(store, signer, count):
    for sequence in range(1, count + 1):
        candidate = snapshot()
        candidate.files['产品TDS/RS990001-Q5.TDS.md'] = (
            f'# parent-owned retention canary cycle {sequence}\n').encode()
        assert store.prepare(candidate, signer, public_scope_approved=True)['sequence'] == sequence
        store.promote()


def parent_git(directory, *args):
    result = subprocess.run(['git', '-c', 'core.hooksPath='+os.devnull, '-C', str(directory), *args],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip()


def test_parent_real_ci_state_roundtrip_retains_private_evidence_and_mirrors(signing, tmp_path, serve, monkeypatch):
    monkeypatch.delenv('GH_TOKEN', raising=False)
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    spec = importlib.util.spec_from_file_location('parent_ci_state', scripts / 'ci_state.py')
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    bare = tmp_path / 'remote.git';control = tmp_path / 'control'
    subprocess.run(['git','init','--bare',str(bare)],check=True,capture_output=True)
    subprocess.run(['git','init',str(control)],check=True,capture_output=True)
    parent_git(control,'config','core.fileMode','false')
    parent_git(control,'remote','add','origin',str(bare))
    (control/'README.md').write_text('parent-owned synthetic CI source\n')
    parent_git(control,'add','README.md')
    parent_git(control,'-c','user.name=ParentVerifier','-c','user.email=verify@example.invalid','commit','-m','synthetic seed')
    first=tmp_path/'first-state';ci.checkout(control,first)
    store=Store(first,signing[1]);parent_publish(store,signing[0],6)
    store.gc(retain=1,grace_days=0,apply=True,now=utc_now()+timedelta(days=1))
    expected={p.relative_to(first/'state/quality-baselines').as_posix():p.read_bytes()
              for p in (first/'state/quality-baselines').iterdir()}
    assert set(expected)=={'000004.json','000004.sig'}
    ci.checkpoint(first,'parent verify persisted baseline evidence')
    tracked=parent_git(first,'ls-tree','-r','--name-only','HEAD').splitlines()
    assert 'state/quality-baselines/000004.json' in tracked
    second=tmp_path/'restored-state';ci.checkout(control,second)
    restored=Store(second,signing[1]);assert restored.sequences()==[5,6]
    assert {p.name:p.read_bytes() for p in (second/'state/quality-baselines').iterdir()}==expected
    target=tmp_path/'mirror';target.mkdir()
    with serve(target) as url:
        result=deploy_mirror(second,target,url,signing[1],allow_local_http=True)
    assert result['status']=='mirrored' and result['sequence']==6
    assert (target/'latest.json').read_bytes()==restored.latest_bytes()
    assert not (target/'manifests/000004.json').exists()
    assert not any('quality-baselines' in p.parts for p in target.rglob('*'))


def test_parent_damaged_public_baseline_never_falls_back_to_valid_private_copy(store, signing, tmp_path, serve):
    parent_publish(store,signing[0],4)
    store.gc(retain=1,grace_days=0,apply=True,now=utc_now()+timedelta(days=1))
    private=store.state/'quality-baselines/000002.json'
    (store.public/'manifests/000002.json').write_bytes(private.read_bytes())
    (store.public/'manifests/000002.sig').write_bytes(b'invalid public signature\n')
    target=tmp_path/'target';target.mkdir();(target/'sentinel').write_bytes(b'keep')
    with serve(target) as url:
        with pytest.raises(DataError):deploy_mirror(store.root,target,url,signing[1],allow_local_http=True)
    assert {p.name:p.read_bytes() for p in target.iterdir()}=={'sentinel':b'keep'}


def test_parent_authentic_signature_from_wrong_baseline_sequence_is_rejected(store, signing, tmp_path, serve):
    parent_publish(store,signing[0],4)
    original=(store.public/'manifests/000001.json').read_bytes()
    signature=(store.public/'manifests/000001.sig').read_bytes()
    store.gc(retain=1,grace_days=0,apply=True,now=utc_now()+timedelta(days=1))
    (store.state/'quality-baselines/000002.json').write_bytes(original)
    (store.state/'quality-baselines/000002.sig').write_bytes(signature)
    target=tmp_path/'target';target.mkdir()
    with serve(target) as url:
        with pytest.raises(DataError,match='quality baseline sequence mismatch'):
            deploy_mirror(store.root,target,url,signing[1],allow_local_http=True)
    assert not list(target.rglob('*'))
