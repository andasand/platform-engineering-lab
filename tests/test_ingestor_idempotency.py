"""Synthetic integration tests against an explicitly selected disposable database.

PHASE3_TEST_DSN must name phase3_synthetic_test on a non-default loopback port.
Tests never connect to the repository's .env database or AWS.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict
from fastapi.testclient import TestClient
import uvicorn

verification_spec = importlib.util.spec_from_file_location('phase3_verification', Path(__file__).resolve().parents[1] / 'scripts/verify_phase3.py')
verification = importlib.util.module_from_spec(verification_spec)
verification_spec.loader.exec_module(verification)

from collectors.windows_security.collector import Outbox, normalize
from collectors.windows_security.local_transport import LocalHttpSender


ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv('PHASE3_TEST_DSN')


@unittest.skipUnless(DSN, 'Set PHASE3_TEST_DSN to an isolated synthetic PostgreSQL database')
class IngestorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = conninfo_to_dict(DSN)
        if options.get('dbname') != 'phase3_synthetic_test' or options.get('host') != '127.0.0.1' or options.get('port', '5432') == '5432':
            raise ValueError('Refusing a database other than the disposable non-default loopback test instance')
        cls.migration = (ROOT / 'database/migrations/002_source_event_id.sql').read_text()
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute((ROOT / 'database/init/001_security_events.sql').read_text())
            # Model a pre-migration event. Existing rows must survive unchanged.
            cls.legacy_id = conn.execute(
                "INSERT INTO security_events(event_time,service,event_type,severity,message,raw_event) "
                "VALUES ('2026-10-30T00:00:00Z','synthetic-legacy','test','INFO','preserve me','{}') RETURNING id"
            ).fetchone()[0]
            cls.legacy_before = conn.execute(
                'SELECT id,event_time,service,event_type,severity,source_ip,username,message,raw_event,created_at '
                'FROM security_events WHERE id=%s', (cls.legacy_id,)).fetchone()
            conn.execute(cls.migration)
        spec = importlib.util.spec_from_file_location('phase3_ingestor', ROOT / 'services/log-ingestor/main.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.fixture = json.loads((ROOT / 'collectors/windows_security/fixtures/security.json').read_text())

    def setUp(self):
        def connect():
            conn = psycopg.connect(DSN)
            conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
            return conn
        self.connection_patch = patch.object(self.module, 'get_connection', connect)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        self.client = TestClient(self.module.app)
        self.addCleanup(self.client.close)
        self.payload = normalize(dict(self.fixture[0], generation='test-' + uuid.uuid4().hex))[3]

    def count(self, source_id):
        with psycopg.connect(DSN) as conn:
            return conn.execute('SELECT count(*) FROM security_events WHERE source_event_id=%s', (source_id,)).fetchone()[0]

    def test_migration_repeatable_and_preserves_legacy_rows(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(self.migration)
            after = conn.execute('SELECT * FROM security_events WHERE id=%s', (self.legacy_id,)).fetchone()
            self.assertEqual(after[:-1], self.legacy_before)
            self.assertIsNone(after[-1])

    def test_duplicate_submissions_return_original_record(self):
        first = self.client.post('/events', json=self.payload)
        self.assertEqual(first.status_code, 200)
        with psycopg.connect(DSN) as conn:
            before = conn.execute('SELECT * FROM security_events WHERE id=%s', (first.json()['event_id'],)).fetchone()
        second = self.client.post('/events', json=self.payload)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(self.count(self.payload['source_event_id']), 1)
        with psycopg.connect(DSN) as conn:
            self.assertEqual(before, conn.execute('SELECT * FROM security_events WHERE id=%s', (first.json()['event_id'],)).fetchone())

    def test_simultaneous_duplicates_return_one_id(self):
        def submit(_):
            with TestClient(self.module.app) as client:
                response = client.post('/events', json=self.payload)
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()['event_id']
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(submit, range(16)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.count(self.payload['source_event_id']), 1)

    def test_conflicting_payload_returns_409_without_replacing_original(self):
        first = self.client.post('/events', json=self.payload).json()
        response = self.client.post('/events', json=dict(self.payload, message='changed synthetic payload'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.post('/events', json=self.payload).json(), first)
        self.assertEqual(self.count(self.payload['source_event_id']), 1)

    def test_unique_constraint_enforced_outside_api(self):
        self.client.post('/events', json=self.payload)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with psycopg.connect(DSN) as conn:
                conn.execute(
                    "INSERT INTO security_events(event_time,service,event_type,severity,message,raw_event,source_event_id) "
                    "VALUES (now(),'synthetic','test','INFO','test','{}',%s)", (self.payload['source_event_id'],))

    def test_legacy_clients_keep_response_shape_and_distinct_insertions(self):
        self.payload.pop('source_event_id')
        first = self.client.post('/events', json=self.payload)
        second = self.client.post('/events', json=self.payload)
        self.assertEqual(first.status_code, 200)
        self.assertNotEqual(first.json()['event_id'], second.json()['event_id'])
        self.assertEqual(set(first.json()), {'status', 'event_id', 'service', 'event_type', 'severity'})

    def test_timestamp_and_severity_canonicalization(self):
        first = self.client.post('/events', json=self.payload)
        equivalent = dict(self.payload, timestamp='2026-10-30T09:00:00-04:00', severity='info')
        self.assertEqual(self.client.post('/events', json=equivalent).json(), first.json())

    def test_identifier_validation(self):
        for bad in ('', 'spaces are invalid', 'x' * 129, 123):
            self.assertEqual(self.client.post('/events', json=dict(self.payload, source_event_id=bad)).status_code, 422)

    def test_ready_advertises_idempotency(self):
        self.assertEqual(self.client.get('/ready').json()['capabilities'], {'source_event_id': 'v1'})

    def test_missing_constraint_fails_readiness(self):
        # Transaction rollback preserves the isolated schema, including the index.
        with psycopg.connect(DSN) as conn:
            conn.execute('ALTER TABLE security_events DROP CONSTRAINT security_events_source_event_id_key')
            # Readiness uses another connection, so test via the same transaction.
            class BorrowedConnection:
                def __enter__(self):
                    return conn

                def __exit__(self, *args):
                    pass
            with patch.object(self.module, 'get_connection', BorrowedConnection):
                self.assertEqual(self.client.get('/ready').status_code, 503)
            conn.rollback()

    def test_real_http_timeout_after_commit_and_collector_restart(self):
        committed = threading.Event()
        app = self.module.app

        class LoseFirstResponse:
            lost = False

            async def __call__(self, scope, receive, send):
                if scope['type'] == 'http' and scope['path'] == '/events' and not self.lost:
                    self.lost = True
                    messages = []

                    async def capture(message):
                        messages.append(message)
                    await app(scope, receive, capture)
                    committed.set()  # FastAPI handler has committed to PostgreSQL.
                    await asyncio.sleep(1.5)  # Longer than the actual HTTP timeout.
                    for message in messages:
                        await send(message)
                else:
                    await app(scope, receive, send)

        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        server = uvicorn.Server(uvicorn.Config(LoseFirstResponse(), log_level='error', lifespan='off'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(server.started)
            sender = LocalHttpSender(sock.getsockname()[1], timeout=1)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'outbox.sqlite3'
                outbox = Outbox(path)
                self.addCleanup(outbox.close)
                event = dict(self.fixture[0], generation='timeout-' + uuid.uuid4().hex)
                payload = normalize(event)[3]
                outbox.stage([event])
                self.assertEqual(outbox.flush(sender, clock=lambda: 10), 0)
                self.assertTrue(committed.wait(2))
                self.assertEqual(outbox.summary(), {'pending': 1})
                self.assertEqual(self.count(payload['source_event_id']), 1)
                with psycopg.connect(DSN) as conn:
                    original_id = conn.execute('SELECT id FROM security_events WHERE source_event_id=%s', (payload['source_event_id'],)).fetchone()[0]
                outbox.close()
                outbox = Outbox(path)
                try:
                    self.assertEqual(outbox.stage([event]), 0)
                    self.assertEqual(outbox.flush(sender, clock=lambda: 11), 1)
                    self.assertEqual(outbox.db.execute('SELECT receipt FROM outbox').fetchone()[0], original_id)
                    self.assertEqual(self.count(payload['source_event_id']), 1)
                finally:
                    outbox.close()
        finally:
            server.should_exit = True
            thread.join(5)
            sock.close()

    def test_retry_baseline_with_preapplied_migration_and_four_records(self):
        # This connection targets only the guarded disposable test database.
        # The transaction restores its prior rows/sequence on exit; no application
        # or AWS database is used by the local test runner.
        with psycopg.connect(DSN) as conn:
            try:
                conn.execute('TRUNCATE public.security_events RESTART IDENTITY')
                conn.execute("SET TIME ZONE 'UTC'")
                for _ in range(2):
                    conn.execute("INSERT INTO security_events(event_time,service,event_type,severity,message,raw_event) VALUES (now(),'synthetic-original','test','INFO','synthetic','{}')")
                class BorrowedConnection:
                    def __enter__(self): return conn
                    def __exit__(self, *args): pass
                with patch.object(self.module, 'get_connection', BorrowedConnection):
                    def send(path, payload=None, expected=200):
                        response = self.client.get(path) if payload is None else self.client.post(path, json=payload)
                        self.assertEqual(response.status_code, expected)
                        return response.json()
                    event = verification.existing_event()
                    self.assertEqual(send('/events', event)['event_id'], 3)
                    self.assertEqual(send('/events', event)['event_id'], 3)
                    send('/events', dict(event, message='Synthetic conflicting content'), 409)
                    legacy = dict(event)
                    legacy.pop('source_event_id')
                    send('/events', legacy)
                    baseline = conn.execute(verification.SNAPSHOT_SQL).fetchone()[0]
                    verification.validate_baseline(baseline)
                    self.assertEqual([row['id'] for row in baseline['records']], [1, 2, 3, 6])
                    self.assertEqual(verification.acceptance(send)['event_id'], 3)
                    after = conn.execute(verification.SNAPSHOT_SQL).fetchone()[0]
                    verification.compare_baseline(baseline, after)
                    self.assertEqual(after['rows'], 4)
                    self.assertGreater(after['sequence'], baseline['sequence'])
            finally:
                conn.rollback()


if __name__ == '__main__':
    unittest.main()
