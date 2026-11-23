import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'codex/scripts'))
import quota
import recommend


class QuotaTests(unittest.TestCase):
    def test_aa_reset_changes_route_and_can_recover_dominated_candidate(self):
        now = 1800000000
        rows = [dict(id=p, name=p, intelligence=q, cost_per_task_usd=c, access=[dict(provider=p, model_id=p)])
                for p, q, c in [('codex', 60, 1), ('claude', 50, 2)]]
        floors = {k: {'min_intelligence':40, 'min_percentile':50} for k in recommend.MIN_PERCENTILES}
        for until, expected in [(600, 'codex'), (14400, 'claude')]:
            def reading(p):
                return [quota.window('5h', 80, now + (until if p == 'codex' else 600), 18000)], False
            with patch('quota.time.time', return_value=now), patch('quota.provider_read', side_effect=reading):
                limits = quota.read(['claude', 'codex'])
            for lane in recommend.lane_recommendations(rows, 'claude', floors, limits):
                self.assertEqual(lane['access']['provider'], expected)
        limits = {'codex': {'status':'exhausted'}, 'claude': {'status':'exhausted'}}
        self.assertTrue(all(r['model'] is None for r in recommend.lane_recommendations(rows,'codex',floors,limits)))

    def test_claude_collector_keeps_only_windows_and_expires(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, ARCH_ADVISOR_STATE_DIR=tmp):
            with patch('quota.time.time', return_value=10000):
                quota.save_claude({'other':'private', 'rate_limits':{'five_hour':{'used_percentage':80, 'resets_at':11000},
                    'seven_day':{'used_percentage':100, 'resets_at':20000}}})
                self.assertEqual(quota.read(['claude'])['claude']['status'], 'exhausted')
                self.assertNotIn('private', quota.claude_file().read_text())
            with patch('quota.time.time', return_value=10950):
                self.assertEqual(quota.read(['claude'])['claude']['status'], 'unknown')

    def test_codex_permission_and_go_shared_windows(self):
        with patch('quota.codex_limits', return_value={'ordinaryUsageAllowed':False, 'rateLimits':{}}):
            self.assertEqual(quota.read(['codex'])['codex']['status'], 'exhausted')
        with patch('quota.build_opener') as opener, patch.dict(os.environ, OPENCODE_GO_API_KEY='fake'):
            meters = {k:dict(status='ok', percent=12, resetsAt='2030-01-01T00:00:00Z') for k in ('rolling','weekly','monthly')}
            meters['weekly']['status'] = 'rate-limited'
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({'usage':meters}).encode()
            result = quota.read(['opencode'])
            self.assertEqual(result['opencode']['status'], 'exhausted')
            self.assertEqual(len(result['opencode']['windows']), 3)
            result['opencode']['free_models'] = ['free']
            self.assertGreater(quota.tier(result, 'opencode', 'free'), quota.tier(result, 'opencode', 'paid'))
