"""Independent regressions for publication and false-positive boundaries."""
import pytest
from rstone_data.protocol import DataError
from test_release_quality import clean_snapshot


class RecordingSigner:
    def __init__(self, signer):
        self.signer = signer
        self.key_id = signer.key_id
        self.calls = 0

    def sign(self, raw):
        self.calls += 1
        return self.signer.sign(raw)


def test_empty_product_tags_block_before_signing(store, signing):
    snapshot = clean_snapshot()
    snapshot.files['产品列表/RS100.md'] = b'---\ntags: []\n' + '固含: 77\n---\n'.encode()
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match='quality gate'):
        store.prepare(snapshot, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert not (store.public / 'objects').exists()
    assert not list(store.public.rglob('*.sig'))


def test_ascii_hyphen_numeric_range_is_legal(store, signing):
    snapshot = clean_snapshot()
    snapshot.files['产品列表/RS100.md'] = snapshot.files['产品列表/RS100.md'].replace(
        '固含: 77'.encode(), '固含: "75-80"'.encode())
    result = store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert result['qualityGate']['summary']['ok']


@pytest.mark.parametrize('value', ['None', 'null', 'undefined', 'NaN', '-'])
def test_viscosity_residue_is_not_just_unit_warning(store, signing, value):
    snapshot = clean_snapshot()
    snapshot.files['产品列表/RS100.md'] = snapshot.files['产品列表/RS100.md'].replace(
        b'---\n', ('---\n粘度: "' + value + '"\n').encode(), 1)
    with pytest.raises(DataError, match='quality gate'):
        store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert not (store.public / 'objects').exists()


@pytest.mark.parametrize('case', ['traversal', 'utf8', 'yaml-escaped-amount', 'fullwidth-amount', 'conflict'])
def test_rejections_precede_signing_and_artifacts(store, signing, case):
    snapshot = clean_snapshot()
    path = '产品列表/RS100.md'
    if case == 'traversal':
        snapshot.files['产品列表/../../RS200.md'] = snapshot.files.pop(path)
    elif case == 'utf8':
        snapshot.files[path] = b'\xff\xfe'
    elif case == 'yaml-escaped-amount':
        snapshot.files[path] = snapshot.files[path].replace(
            b'---\n', b'---\nextra: "\\u00a5 987654"\n', 1)
    elif case == 'fullwidth-amount':
        snapshot.files[path] += '９８７６５４ 元/kg'.encode()
    else:
        snapshot.files[path] = snapshot.files[path].replace('官能度: 3'.encode(), '官能度: 987654'.encode())
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError) as error:
        store.prepare(snapshot, signer, public_scope_approved=True)
    assert '987654' not in str(error.value)
    assert '９８７６５４' not in str(error.value)
    assert signer.calls == 0
    assert store.pending() is None
    assert not (store.public / 'objects').exists()
    assert not list(store.public.rglob('*.sig'))
    assert not list(store.public.rglob('*.zip'))


@pytest.mark.parametrize('field,value', [
    ('硬度', 'H(300g)'), ('硬度', 'HB'), ('硬度', '2H'),
    ('固含', '77±2'), ('固含', '75–80'), ('官能度', '>6'),
    ('粘度', '100-200 mPa·s（25℃）'), ('粘度', '≤500 cP（25℃）'),
])
def test_legal_technical_scalar_can_build(store, signing, field, value):
    snapshot = clean_snapshot()
    snapshot.files['产品列表/RS100.md'] = (
        '---\ntags: [粉末, UV, 4官]\n固含: 77\n' + field + ': "' + value + '"\n---\n'
    ).encode() if field != '固含' else (
        '---\ntags: [粉末, UV, 4官]\n固含: "' + value + '"\n---\n'
    ).encode()
    if field == '官能度':
        snapshot.files['产品列表/RS100.md'] = snapshot.files['产品列表/RS100.md'].replace(
            ', 4官'.encode(), b'')
    result = store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert result['qualityGate']['summary']['ok']
    assert (store.public / store.package(result['sequence'])).is_file()


def test_unapproved_functionality_stays_blocked(store, signing):
    snapshot = clean_snapshot()
    snapshot.files['产品列表/RS100.md'] = snapshot.files['产品列表/RS100.md'].replace(
        '3官'.encode(), '12官'.encode()).replace('官能度: 3'.encode(), '官能度: 12'.encode())
    with pytest.raises(DataError, match='quality gate'):
        store.prepare(snapshot, signing[0], public_scope_approved=True)


def test_rollback_can_restore_a_signed_release_with_historical_broken_link(store, signing):
    """Recovery path: re-admitting already-published signed bytes must not be blocked."""
    from test_release_quality import install_legacy
    old = clean_snapshot()
    old.files['产品配方/配方.md'] += b'[[RS999999]]\n'
    install_legacy(store, signing[0], old, sequence=1)
    store.prepare(clean_snapshot(), signing[0], public_scope_approved=True)
    store.promote()
    result = store.rollback(1, signing[0], public_scope_approved=True)
    assert result['status'] == 'pending'
    assert result['sequence'] == 3
    # Revalidation must reuse the restored snapshot, not the newer sequence that
    # fixed the link; otherwise promotion fails on the grandfathered link.
    promoted = store.promote()
    assert promoted['status'] == 'published'
    assert (store.public / store.package(3)).is_file()


def test_gc_keeps_the_baseline_a_retained_release_needs(store, signing):
    """A retained release must stay revalidatable after its baseline is garbage collected."""
    from datetime import timedelta
    from test_release_quality import install_legacy
    from rstone_data.release import utc_now
    old = clean_snapshot()
    old.files['产品配方/配方.md'] += b'[[RS999999]]\n'
    install_legacy(store, signing[0], old, sequence=1)
    inherited = clean_snapshot()
    inherited.files['产品配方/配方.md'] += b'[[RS999999]]\n'
    inherited.files['产品列表/RS100.md'] = inherited.files['产品列表/RS100.md'].replace(
        '固含: 77'.encode(), '固含: 80'.encode())
    result = store.prepare(inherited, signing[0], public_scope_approved=True)
    store.promote()
    assert result['sequence'] == 2
    report = store.gc(retain=1, grace_days=0, apply=True, now=utc_now() + timedelta(days=30))
    assert 1 in report['protectedSequences']
    assert 2 in report['protectedSequences']
    assert (store.public / store.package(2)).is_file()


def test_rollback_of_mismatched_snapshot_is_rejected(store, signing):
    """A rollback marker must not whitelist arbitrary new content."""
    from test_release_quality import install_legacy
    impossible = clean_snapshot()
    install_legacy(store, signing[0], impossible, sequence=1)
    tampered = clean_snapshot()
    tampered.files['产品配方/配方.md'] = b'[[RS777777]]\n'
    with pytest.raises(DataError, match='quality gate|signed release'):
        store.prepare(tampered, signing[0], public_scope_approved=True, rollback_of=1)

