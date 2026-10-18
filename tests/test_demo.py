"""Hand-calculated synthetic metrics and public build boundaries."""
import math
import json
import pytest
from amazon_eval_agent.demo import score, compare, build_report, build_site


def test_hand_calculated_scopes_and_failures():
    rows = [dict(task='a', user='u', target=1, prediction=2, status='success'), dict(task='b', user='u', target=5, prediction=3, status='degraded'), dict(task='c', user='v', target=4, prediction=None, status='failed')]
    assert score(rows)['mae'] == 1
    full = score(rows, ('success', 'degraded'))
    assert full['mae'] == 1.5
    assert full['rmse'] == math.sqrt(2.5)
    assert full['bias'] == -.5
    assert full['within_one'] == .5
    assert full['eligible'] == 2 and full['requested'] == 3
    assert score([])['mae'] is None
    with pytest.raises(ValueError): score(rows + [rows[0]])
    with pytest.raises(ValueError): score([dict(rows[0], prediction=float('nan'))])


def test_alignment_common_denominators_and_unknown():
    a = [dict(task='a', user='u', target=2, prediction=3, status='success')]
    with pytest.raises(ValueError): compare(a, [dict(a[0], target=3)])
    with pytest.raises(ValueError): compare(a, [dict(a[0], user='other')])
    assert compare(a, [dict(a[0], status='failed', prediction=None)])['mae_b_minus_a'] is None
    r = build_report('survivor')
    assert r['comparison']['common_n'] == 3
    assert r['schemes'][1]['success_only']['coverage'] == .25
    assert r['comparison']['interval'] is None
    assert build_report('insufficient')['comparison']['common_n'] == 0


def test_repeatable_public_whitelist_build(tmp_path):
    assert build_report('balanced') == build_report('balanced')
    build_site(tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {'index.html', 'app.js', 'style.css', 'reports.json'}
    reports = json.loads((tmp_path / 'reports.json').read_text(encoding='utf-8'))
    for r in reports:
        assert r['source'] == 'handwritten_synthetic_only' and r['real_llm'] is False
        ids = {e['id'] for e in r['evidence']}
        assert all(set(c['evidence']) <= ids for c in r['claims'])
    with pytest.raises(ValueError): build_report('private-data')
