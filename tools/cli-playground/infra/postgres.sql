-- Run as PostgreSQL administrator with playground_password set by provision.sh.
SELECT 'CREATE ROLE cli_playground LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cli_playground') \gexec
SELECT format('ALTER ROLE cli_playground PASSWORD %L', :'playground_password') \gexec
SELECT 'CREATE DATABASE cli_playground OWNER cli_playground'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'cli_playground') \gexec
REVOKE CONNECT ON DATABASE cli_playground FROM PUBLIC;
GRANT CONNECT ON DATABASE cli_playground TO cli_playground;
