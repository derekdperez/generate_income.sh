-- Second database for binary blobs (EF EnsureCreated builds tables on first app start).
-- Runs only on fresh Postgres data volume.
CREATE DATABASE nightmare_v2_files OWNER nightmare;
