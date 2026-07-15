# pgvector/pgvector:pg16 extends postgres:16 with pgvector pre-installed (Phase 9).
# pg_partman is added on top via the PGDG apt repository — same as before, Debian-based
# so the package is readily available.
FROM --platform=linux/amd64 pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-partman \
    && rm -rf /var/lib/apt/lists/*

# Baked into the image (not bind-mounted) so the execute bit on init-rls.sh
# survives regardless of the host filesystem (Windows bind mounts don't reliably
# preserve POSIX permissions).
COPY docker/postgres/init-rls.sh /docker-entrypoint-initdb.d/10-init-rls.sh
COPY docker/postgres/create-test-db.sh /docker-entrypoint-initdb.d/15-create-test-db.sh
COPY docker/postgres/init-partman.sql /docker-entrypoint-initdb.d/20-init-partman.sql
RUN chmod 755 /docker-entrypoint-initdb.d/10-init-rls.sh /docker-entrypoint-initdb.d/15-create-test-db.sh
