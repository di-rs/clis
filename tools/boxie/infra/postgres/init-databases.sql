\set ON_ERROR_STOP on
\getenv boxie_password BOXIE_DB_PASSWORD
\getenv temporal_password TEMPORAL_DB_PASSWORD
\getenv visibility_password VISIBILITY_DB_PASSWORD
CREATE ROLE boxie LOGIN PASSWORD :'boxie_password';
CREATE ROLE temporal LOGIN PASSWORD :'temporal_password';
CREATE ROLE visibility LOGIN PASSWORD :'visibility_password';
CREATE DATABASE boxie OWNER boxie;
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER visibility;
REVOKE CONNECT ON DATABASE postgres, template1, boxie, temporal, temporal_visibility FROM PUBLIC;
GRANT CONNECT ON DATABASE boxie TO boxie;
GRANT CONNECT ON DATABASE temporal TO temporal;
GRANT CONNECT ON DATABASE temporal_visibility TO visibility;
