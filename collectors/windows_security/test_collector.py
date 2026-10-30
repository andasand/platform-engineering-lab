import ast
import copy
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv6Address
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from .collector import Outbox, SyntheticReceiver, exclusive_state, normalize


FIXTURE = Path(__file__).with_name('fixtures') / 'security.json'
ROOT = Path(__file__).resolve().parents[2]


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.events = json.loads(FIXTURE.read_text())
        self.outbox = Outbox(self.directory / 'outbox.sqlite3')
        self.receiver = SyntheticReceiver(self.directory / 'receiver.sqlite3')

    def tearDown(self):
        self.receiver.close()
        self.outbox.close()
        self.temp.cleanup()

    def received(self):
        return self.receiver.db.execute('SELECT count(*) FROM received').fetchone()[0]

    def test_filter_and_redact_before_persistence(self):
        self.assertEqual(self.outbox.stage(self.events), 3)
        payloads = [json.loads(row[0]) for row in self.outbox.db.execute('SELECT payload FROM outbox')]
        self.assertEqual([p['severity'] for p in payloads], ['INFO', 'WARNING', 'CRITICAL'])
        self.assertTrue(all(p['username'] is None and p['source_ip'] is None for p in payloads))
        for path in self.directory.iterdir():
            content = path.read_bytes()
            for forbidden in (b'alice', b'bob', b'DO-NOT-PERSIST', b'192.0.2.10', b'SYNTHETIC-SID'):
                self.assertNotIn(forbidden, content)
        altered = copy.deepcopy(self.events[0])
        altered['provider'] = 'untrusted-provider'
        self.assertIsNone(normalize(altered)[3])

    def test_payloads_validate_against_actual_api_model(self):
        # Compile the exact existing model in isolation: importing main.py would
        # require Psycopg/instrumentation. This does not start FastAPI or touch PG.
        from pydantic import BaseModel, Field, field_validator
        tree = ast.parse((ROOT / 'services/log-ingestor/main.py').read_text())
        model = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'SecurityEvent')
        namespace = dict(BaseModel=BaseModel, Field=Field, field_validator=field_validator, datetime=datetime, timezone=timezone,
                         IPv4Address=IPv4Address, IPv6Address=IPv6Address, __name__=__name__)
        exec(compile(ast.Module(body=[model], type_ignores=[]), 'existing-api-model', 'exec'), namespace)
        schema = namespace['SecurityEvent']
        for event in self.events:
            payload = normalize(event)[3]
            if payload:
                parsed = schema.model_validate(payload)
                self.assertEqual(set(schema.model_fields), set(payload))
                self.assertEqual(parsed.model_dump(mode='json'), payload)

    def test_restart_replay_and_batch_bound(self):
        self.outbox.stage(self.events)
        self.assertEqual(self.outbox.flush(self.receiver, batch_size=2), 2)
        self.outbox.close()
        self.outbox = Outbox(self.directory / 'outbox.sqlite3')
        self.assertEqual(self.outbox.stage(self.events), 0)
        self.assertEqual(self.outbox.flush(self.receiver), 1)
        self.assertEqual(self.outbox.flush(self.receiver), 0)
        self.assertEqual(self.received(), 3)
        self.assertEqual(self.outbox.summary(), {'acked': 3})

    def test_retry_known_nondelivery_with_backoff(self):
        self.outbox.stage(self.events)
        self.receiver.failure = 'before-send'
        self.assertEqual(self.outbox.flush(self.receiver, clock=lambda: 10, jitter=lambda: 0), 0)
        self.assertEqual(self.received(), 0)
        self.assertEqual(self.outbox.flush(self.receiver, clock=lambda: 10.1), 0)
        self.assertEqual(self.outbox.flush(self.receiver, clock=lambda: 11), 3)
        self.assertEqual(self.received(), 3)

    def test_retry_exhaustion_retains_event(self):
        self.outbox.stage(self.events)
        self.receiver.failure = 'before-send'
        self.outbox.flush(self.receiver, max_attempts=1)
        self.assertEqual(self.outbox.summary(), {'blocked': 1, 'pending': 2})
        self.assertEqual(self.outbox.flush(self.receiver), 0)
        self.assertEqual(self.received(), 0)

    def test_commit_then_lost_response_safely_retries_after_restart(self):
        self.outbox.stage(self.events)
        self.receiver.failure = 'after-commit'
        self.outbox.flush(self.receiver, clock=lambda: 10)
        self.assertEqual(self.received(), 1)
        self.assertEqual(self.outbox.summary(), {'pending': 3})
        self.outbox.close()
        self.outbox = Outbox(self.directory / 'outbox.sqlite3')
        self.assertEqual(self.outbox.stage(self.events), 0)
        self.assertEqual(self.outbox.flush(self.receiver, clock=lambda: 11), 3)
        self.assertEqual(self.received(), 3)
        self.assertEqual(self.outbox.db.execute('SELECT receipt FROM outbox WHERE rowid=1').fetchone()[0], 1)

    def test_crash_before_receipt_commit_replays_original_id(self):
        self.outbox.stage(self.events)
        self.receiver(normalize(self.events[0])[3])
        with self.outbox.db:
            self.outbox.db.execute("UPDATE outbox SET state='sending' WHERE rowid=1")
        self.outbox.close()
        self.outbox = Outbox(self.directory / 'outbox.sqlite3')
        self.assertEqual(self.outbox.summary(), {'pending': 2, 'unknown': 1})
        self.assertEqual(self.outbox.flush(self.receiver), 3)
        self.assertEqual(self.received(), 3)

    def test_malformed_ack_is_uncertain(self):
        self.outbox.stage(self.events)
        class BadReceiver:
            def idempotency_ready(self):
                return True

            def __call__(self, payload):
                return {'status': 'stored', 'event_id': True}
        self.outbox.flush(BadReceiver())
        self.assertEqual(self.outbox.summary(), {'pending': 3})

    def test_older_receiver_cannot_trigger_retries_or_sends(self):
        self.outbox.stage(self.events)
        self.receiver.idempotency_ready = lambda: False
        with self.assertRaises(ValueError):
            self.outbox.flush(self.receiver)
        self.assertEqual(self.received(), 0)

    def test_unkeyed_v1_state_is_refused_without_relabeling(self):
        self.outbox.stage(self.events)
        with self.outbox.db:
            self.outbox.db.execute("UPDATE settings SET policy='synthetic-security-v1'")
        self.outbox.close()
        path = self.directory / 'outbox.sqlite3'
        with self.assertRaisesRegex(ValueError, 'Policy changed'):
            Outbox(path)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT policy FROM settings').fetchone()[0], 'synthetic-security-v1')
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0], 3)

    def test_simulated_duplicate_returns_original_id_and_conflict_is_blocked(self):
        payload = normalize(self.events[0])[3]
        first = self.receiver(payload)
        self.assertEqual(self.receiver(payload), first)
        self.assertEqual(self.received(), 1)
        self.outbox.stage(self.events)
        with self.receiver.db:
            self.receiver.db.execute("UPDATE received SET payload='{}'")
        self.outbox.flush(self.receiver)
        self.assertEqual(self.outbox.summary(), {'blocked': 1, 'pending': 2})

    def test_capacity_rolls_back_checkpoint_and_queue(self):
        with self.assertRaises(ValueError):
            self.outbox.stage(self.events, max_pending=1)
        self.assertEqual(self.outbox.summary(), {})
        self.assertEqual(self.outbox.db.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 0)

    def test_invalid_input_does_not_advance_checkpoint(self):
        bad_cases = []
        for field, value in [('synthetic', False), ('timestamp', '2026-10-30T12:00:00'), ('record_id', -1)]:
            events = copy.deepcopy(self.events)
            events[-1][field] = value
            bad_cases.append(events)
        bad_cases.append(list(reversed(self.events)))
        for events in bad_cases:
            with self.assertRaises(ValueError):
                self.outbox.stage(events)
            self.assertEqual(self.outbox.summary(), {})

    def test_generation_separates_reused_record_ids(self):
        self.outbox.stage(self.events)
        new = copy.deepcopy(self.events)
        for event in new:
            event['generation'] = 'synthetic-generation-2'
        self.assertEqual(self.outbox.stage(new), 3)

    def test_second_collector_is_rejected(self):
        with exclusive_state(self.directory):
            with self.assertRaises(FileExistsError):
                with exclusive_state(self.directory):
                    self.fail('Second collector acquired lock')

    def test_byte_bound_leaves_remaining_work_durable(self):
        events = [dict(self.events[0], record_id=n) for n in range(1, 21)]
        self.outbox.stage(events)
        sent = self.outbox.flush(self.receiver, max_bytes=2048)
        self.assertGreater(sent, 0)
        self.assertLess(sent, 20)
        encoded = self.outbox.db.execute("SELECT payload FROM outbox WHERE state='acked'").fetchall()
        self.assertLessEqual(sum(len(row[0].encode()) for row in encoded), 2048)
        self.assertEqual(self.outbox.summary()['pending'], 20 - sent)


if __name__ == '__main__':
    unittest.main()
