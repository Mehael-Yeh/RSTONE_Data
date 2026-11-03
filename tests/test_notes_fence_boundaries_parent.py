"""父独立反证：真实Markdown围栏不能因缩进或伪闭合被当性能表免检。"""
import pytest
from rstone_data import quality_gate as q
from rstone_data.protocol import DataError
from test_notes_tables import snapshot, TABLE_HEAD, ROW, CountingSigner

def no_side_effects(store,signing,candidate):
    signer=CountingSigner(signing[0])
    with pytest.raises(DataError,match=r'checks=notes$'):
        store.prepare(candidate,signer,public_scope_approved=True)
    assert signer.calls==0 and store.pending() is None
    assert not (store.public/'objects').exists()
    assert not list(store.public.rglob('*.sig'))
    assert not list(store.public.rglob('*.zip'))

@pytest.mark.parametrize('indent',['',' ','  ','   '])
@pytest.mark.parametrize('marker',['```','~~~'])
def test_real_indented_fence_does_not_become_table_permission(store,signing,indent,marker):
    notes=(indent+marker+'text\n###### 合成工艺甲\n'+TABLE_HEAD+ROW+
           '\n###### 合成工艺乙\n'+TABLE_HEAD+ROW+indent+marker+'\n')
    candidate=snapshot(notes)
    assert any(f['category']=='notes-duplicated' for f in q.check_notes(q.snapshot_corpus(candidate)))
    no_side_effects(store,signing,candidate)

@pytest.mark.parametrize('marker',['```','~~~'])
def test_fence_with_trailing_word_is_not_a_closing_marker(store,signing,marker):
    notes=(marker+'text\n'+marker+'not-a-close\n###### 合成工艺甲\n'+TABLE_HEAD+ROW+
           '\n###### 合成工艺乙\n'+TABLE_HEAD+ROW+marker+'\n')
    candidate=snapshot(notes)
    assert any(f['category']=='notes-duplicated' for f in q.check_notes(q.snapshot_corpus(candidate)))
    no_side_effects(store,signing,candidate)
