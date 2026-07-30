-- Runs automatically, exactly once, the first time the postgres
-- container initializes an empty data volume (standard
-- docker-entrypoint-initdb.d behavior). If you've already started this
-- compose file once before and change this script, you must
-- `docker compose -f docker-compose.dev.yml down -v` (removes the
-- volume) and start again for it to take effect -- Postgres does not
-- re-run these scripts against an already-initialized data directory.
--
-- Two things this project's design requires that the plain
-- POSTGRES_USER/POSTGRES_DB image variables cannot set up alone:
--
-- 1. A second database for the test suite (TEST_DATABASE_URL in
--    backend/tests/conftest.py) alongside the primary database the app
--    itself uses.
--
-- 2. app_user must be an ordinary, non-superuser login role, not the
--    instance's bootstrap superuser. If POSTGRES_USER in
--    docker-compose.dev.yml were set to "app_user" directly, the
--    official postgres image would create it AS that bootstrap
--    superuser -- and a superuser bypasses Row-Level Security
--    unconditionally, regardless of the FORCE ROW LEVEL SECURITY clause
--    on a table (see PROJECT_STATE.md section 3 on RLS, and
--    backend/tests/conftest.py's note that "app_user has no BYPASSRLS
--    attribute"). That would make every RLS tenant-isolation test in
--    this project's integration suite pass locally for the wrong
--    reason -- silently testing nothing. So the bootstrap admin stays
--    the image's default "postgres" superuser (see
--    docker-compose.dev.yml, which leaves POSTGRES_USER unset), and
--    app_user is created here explicitly as NOSUPERUSER.
--
-- app_user owns both databases it will migrate and query, so it has
-- full rights within them (including CREATE on the public schema, which
-- Postgres 15+ restricts to the owner by default) without needing any
-- broader instance-level privilege.

CREATE ROLE app_user LOGIN PASSWORD 'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

CREATE DATABASE security_platform OWNER app_user;
CREATE DATABASE security_platform_test OWNER app_user;
