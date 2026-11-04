"""Synthetic regression: canonical whitespace must not create nonempty notes."""
import json
import pytest

from rstone_data.protocol import DataError
from rstone_data import quality_gate as gate
from test_identity_migration_security import artifacts
from test_quality_parent_review import RecordingSigner
from test_structured_publication import model, snapshot


def test_parent_whitespace_only_notes_refuse_before_signing(store, signing):
    value = model()
    value['content'] = [{'kind': 'process', 'label': '特别说明', 'steps': ['   ', '\t\n']}]
    signer = RecordingSigner(signing[0])
    before = artifacts(store)
    rejected = False
    try:
        store.prepare(snapshot(value), signer, public_scope_approved=True)
    except DataError as error:
        assert 'checks=notes' in str(error)
        assert 'notes-empty' in {f['category'] for f in gate.check_notes(gate.snapshot_corpus(snapshot(value)))}
        rejected = True
    print(json.dumps({'case': 'whitespace-only-structured-notes', 'rejected': rejected,
        'signCalls': signer.calls, 'artifactsUnchanged': artifacts(store) == before}))
    assert rejected, 'Whitespace-only notes reached publication instead of notes-empty'
    assert signer.calls == 0 and artifacts(store) == before


def test_parent_whitespace_note_after_empty_notes_section_is_also_empty(store, signing):
    import pytest
    value = model()
    value['content'] = [
        {'kind': 'process', 'label': '特别说明', 'steps': ['   ']},
        {'kind': 'note', 'text': '\t \n\u3000'},
    ]
    signer = RecordingSigner(signing[0]);before = artifacts(store)
    assert 'notes-empty' in {f['category'] for f in gate.check_notes(gate.snapshot_corpus(snapshot(value)))}
    with pytest.raises(DataError, match='checks=notes'):
        store.prepare(snapshot(value), signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before


def test_parent_all_supported_unicode_whitespace_cases_are_empty(store, signing):
    import pytest
    for steps in [[''], ['\t \n'], ['\u00a0', '\u3000'], [' ', '\n', '']]:
        value = model();value['content'] = [{'kind':'process','label':'特别说明','steps':steps}]
        signer = RecordingSigner(signing[0]);before = artifacts(store)
        assert 'notes-empty' in {f['category'] for f in gate.check_notes(gate.snapshot_corpus(snapshot(value)))}
        with pytest.raises(DataError, match='checks=notes'):
            store.prepare(snapshot(value), signer, public_scope_approved=True)
        assert signer.calls == 0 and artifacts(store) == before


def test_parent_nonempty_note_keeps_all_original_whitespace_bytes(store, signing):
    import zipfile
    from rstone_data import recipe_codec as codec
    from test_structured_publication import PATH
    value = model();steps = ['   ', '\t测试条件：PC底材，100℃水煮3h。  ', '\n\u3000']
    value['content'] = [{'kind':'process','label':'特别说明','steps':steps}]
    candidate = snapshot(value);raw = candidate.files[PATH]
    assert gate.check_notes(gate.snapshot_corpus(candidate)) == []
    signer = RecordingSigner(signing[0]);result = store.prepare(candidate, signer, public_scope_approved=True)
    with zipfile.ZipFile(store.public / store.package(result['sequence'])) as package:
        assert package.read('files/' + PATH) == raw
    assert store.promote()['status'] == 'published' and signer.calls == 1
    assert codec.parse(raw)['content'][0]['steps'] == steps
    assert codec.emit(codec.parse(raw)) == raw


@pytest.mark.parametrize('invalid_steps', [[], ['\r']])
def test_parent_codec_still_rejects_empty_step_array_and_disallowed_carriage_return(invalid_steps):
    from rstone_data import recipe_codec as codec
    value = model();value['content'] = [{'kind':'process','label':'特别说明','steps':invalid_steps}]
    with pytest.raises(ValueError, match='invalid-structured-recipe'):
        codec.emit(value)


@pytest.mark.parametrize('operation', ['resume', 'package', 'promote', 'rollback', 'complete'])
def test_parent_already_signed_whitespace_notes_refuse_every_other_exit(store, signing, operation):
    from test_release_quality import install_legacy
    value = model();value['content'] = [{'kind':'process','label':'特别说明','steps':[' ', '\t\n']}]
    install_legacy(store, signing[0], snapshot(value), pending=operation != 'rollback', schema_version=2)
    signer = RecordingSigner(signing[0]);before = artifacts(store)
    with pytest.raises(DataError, match='checks=notes'):
        if operation == 'resume':store.prepare(None, signer, public_scope_approved=True)
        elif operation == 'package':store.package(1)
        elif operation == 'promote':store.promote()
        elif operation == 'rollback':store.rollback(1, signer, public_scope_approved=True)
        else:store.complete(lambda _: pytest.fail('Remote verifier must not run'))
    assert signer.calls == 0 and artifacts(store) == before

