-- Read-Only Role for generated SQL execution (Requirement 7.1, 7.3)
--
-- SAFETY-CRITICAL. Generated SQL — even after passing verify.check() — runs
-- only as this role, never as the application's read/write role. A write or
-- schema-modifying statement that slips past verification is then rejected by
-- Postgres itself (Req 7.3): defense in depth, not a substitute for the
-- verifier.
--
-- Design note / deviation from design.md: design.md sketches a LOGIN role with
-- its own password and a second connection pool. On a managed instance
-- (Supabase) creating a second login role and wiring a separate DATABASE_URL
-- is friction, and the password would be a third secret to manage. Instead
-- differ_ro is a NOLOGIN role: readonly_store acquires a normal connection and
-- switches into it with `SET LOCAL ROLE differ_ro` inside a `READ ONLY`
-- transaction. The privilege boundary is identical — the session has exactly
-- differ_ro's grants while generated SQL runs — without a second credential.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'differ_ro') THEN
        CREATE ROLE differ_ro NOLOGIN;
    END IF;
END
$$;

-- SELECT-only, on everything that exists now and everything created later.
GRANT USAGE ON SCHEMA public TO differ_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO differ_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO differ_ro;

-- No INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER granted anywhere.

-- The application's login role must be allowed to `SET ROLE differ_ro`.
DO $$
DECLARE
    app_role text := current_user;
BEGIN
    EXECUTE format('GRANT differ_ro TO %I', app_role);
END
$$;
