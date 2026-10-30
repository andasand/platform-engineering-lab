"""Host-side rollout regressions: standard library only, executable on Python 3.9."""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_phase3', ROOT / 'scripts/verify_phase3.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class TimestampTests(unittest.TestCase):
    def test_fractional_precision_on_python39(self):
        for fraction, micros in [('', 0), ('.1', 100000), ('.123', 123000), ('.1234', 123400),
                                 ('.123456', 123456), ('.123456789', 123456), ('.000000001', 0)]:
            with self.subTest(fraction=fraction):
                self.assertEqual(verify.parse_prometheus_timestamp('2026-10-30T15:04:01' + fraction + 'Z'),
                                 datetime(2026, 10, 30, 15, 4, 1, micros, tzinfo=timezone.utc))

    def test_offsets_and_nine_digits(self):
        expected = datetime(2026, 10, 30, 15, 4, 1, 660104, tzinfo=timezone.utc)
        for stamp in ('2026-10-30T15:04:01.660104844Z', '2026-10-30T15:04:01.660104844+00:00',
                      '2026-10-30T11:04:01.660104844-04:00', '2026-10-30T20:34:01.660104844+05:30'):
            self.assertEqual(verify.parse_prometheus_timestamp(stamp), expected)

    def test_malformed_and_naive_dates_are_rejected(self):
        for stamp in (None, '', '2026-10-30T15:04:01', 'not-a-date', '2026-02-30T15:04:01Z',
                      '2026-10-30T15:04:01.1234567890Z', '2026-10-30T15:04:01+99:00',
                      '2026-10-30T15:04:01+00:60'):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                verify.parse_prometheus_timestamp(stamp)

    def payload(self):
        return {'data': {'activeTargets': [
            {'labels': {'job': job}, 'health': 'up', 'lastError': '', 'lastScrape': '2026-10-30T15:04:01.660104844Z'}
            for job in ('eventflow', 'log-ingestor')]}}

    def test_fresh_scrapes_pass_with_nanoseconds(self):
        cutoff = datetime(2026, 10, 30, 15, 4, 1, 660104, tzinfo=timezone.utc)
        self.assertTrue(verify.targets_healthy(self.payload(), cutoff))
        # Truncation is conservative: a later cutoff cannot admit the older sample.
        self.assertFalse(verify.targets_healthy(self.payload(), cutoff + timedelta(microseconds=1)))

    def test_failed_missing_stale_or_ambiguous_targets(self):
        cutoff = datetime(2026, 10, 30, 15, tzinfo=timezone.utc)
        for change in ('down', 'error', 'missing', 'stale'):
            payload = self.payload()
            target = payload['data']['activeTargets'][1]
            if change == 'down': target['health'] = 'down'
            if change == 'error': target['lastError'] = 'synthetic failure'
            if change == 'missing': payload['data']['activeTargets'].pop()
            if change == 'stale': target['lastScrape'] = '2026-10-30T14:00:00Z'
            self.assertFalse(verify.targets_healthy(payload, cutoff))
        payload = self.payload()
        payload['data']['activeTargets'].append(copy.deepcopy(payload['data']['activeTargets'][0]))
        with self.assertRaises(ValueError): verify.targets_healthy(payload, cutoff)

    def test_naive_cutoff_is_rejected(self):
        with self.assertRaises(ValueError):
            verify.targets_healthy(self.payload(), datetime(2026, 10, 30))


class RetryBaselineTests(unittest.TestCase):
    def snapshot(self):
        return dict(rows=4, records=[dict(id=n, digest=str(n)) for n in (1, 2, 3, 6)],
                    column_ok=True, constraint_ok=True, prior_ids=[3], synthetic_rows=2,
                    legacy_synthetic_rows=1, sequence=6)

    def test_four_rows_with_preapplied_migration_pass(self):
        verify.validate_baseline(self.snapshot())

    def test_two_rows_missing_migration_or_changed_history_stop(self):
        for key, value in [('rows', 2), ('rows', 5), ('column_ok', False), ('constraint_ok', False),
                           ('prior_ids', [7]), ('synthetic_rows', 3), ('legacy_synthetic_rows', 0), ('sequence', 1)]:
            snapshot = self.snapshot()
            snapshot[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): verify.validate_baseline(snapshot)

    def test_sequence_gaps_are_safe_but_mutated_rows_are_not(self):
        before = self.snapshot()
        after = copy.deepcopy(before)
        after['sequence'] = 10
        verify.compare_baseline(before, after)
        after['records'][0]['digest'] = 'changed'
        with self.assertRaises(ValueError): verify.compare_baseline(before, after)
        after = self.snapshot()
        before['sequence'] = 10
        with self.assertRaises(ValueError): verify.compare_baseline(before, after)

    def test_acceptance_replays_prior_id_without_legacy_insert(self):
        calls = []
        def send(path, payload=None, expected=200):
            calls.append((path, payload, expected))
            if path == '/health': return {'status': 'healthy'}
            if path == '/ready': return {'capabilities': {'source_event_id': 'v1'}}
            if expected == 200:
                return dict(status='stored', event_id=3, source_event_id=verify.PRIOR_KEY)
            return {'detail': 'synthetic rejection'}
        self.assertEqual(verify.acceptance(send)['new_rows'], 0)
        posts = [call for call in calls if call[0] == '/events']
        self.assertEqual([call[2] for call in posts], [200, 200, 409, 422])
        self.assertTrue(all(call[1]['source_event_id'] == verify.PRIOR_KEY for call in posts))
        self.assertEqual(posts[0][1], posts[1][1])

    def test_acceptance_fails_when_server_returns_new_id(self):
        def send(path, payload=None, expected=200):
            if path == '/health': return {'status': 'healthy'}
            if path == '/ready': return {'capabilities': {'source_event_id': 'v1'}}
            return dict(status='stored', event_id=7, source_event_id=verify.PRIOR_KEY)
        with self.assertRaises(RuntimeError): verify.acceptance(send)

    def test_retained_helper_runs_outside_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            copied = Path(folder) / 'verify_phase3.py'
            shutil.copyfile(ROOT / 'scripts/verify_phase3.py', copied)
            result = subprocess.run([sys.executable, '-B', str(copied), 'prometheus', '--help'],
                                    cwd=folder, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--timeout', result.stdout)

    def test_all_host_python_checks_compile(self):
        candidates = [*sorted((ROOT / 'scripts').glob('*.py')), ROOT / 'infra/deploy/check-config.py']
        for path in candidates:
            with self.subTest(path=path.name):
                compile(path.read_text(), str(path), 'exec')


if __name__ == '__main__':
    unittest.main()
