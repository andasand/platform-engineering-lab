-- Additive, repeatable migration. No existing event or raw_event is rewritten.
-- Apply after 001_security_events.sql, before the new ingestor is started.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE public.security_events
    ADD COLUMN IF NOT EXISTS source_event_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.security_events'::regclass
          AND conname = 'security_events_source_event_id_key'
    ) THEN
        ALTER TABLE public.security_events
            ADD CONSTRAINT security_events_source_event_id_key UNIQUE (source_event_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.security_events'::regclass
          AND conname = 'security_events_source_event_id_key'
          AND contype = 'u' AND convalidated AND NOT condeferrable
          AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
              WHERE attrelid = 'public.security_events'::regclass
                AND attname = 'source_event_id')]
    ) THEN
        RAISE EXCEPTION 'Existing source-event constraint is incompatible';
    END IF;
END $$;

COMMIT;
