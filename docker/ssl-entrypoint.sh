#!/bin/sh
# Runs inside /docker-entrypoint.d/ — nginx:alpine executes all scripts here
# before starting nginx.  This script generates a self-signed TLS certificate
# on first boot and skips generation when files already exist (e.g. replaced
# with a real cert from Let's Encrypt or a commercial CA).
#
# To use a real certificate, copy files into the certs_data volume BEFORE
# starting the container:
#   server.crt  — full-chain PEM certificate
#   server.key  — private key (PEM, unencrypted)
set -e

CERT_DIR=/etc/nginx/certs

# nginx runs as the unprivileged `nginx` user. A certificate volume made while
# the container still ran as root holds a key only root can read; nginx would
# fail on it with a permission error that does not say why. Say why.
if [ -f "$CERT_DIR/server.key" ] && [ ! -r "$CERT_DIR/server.key" ]; then
    echo "[ssl-init] ERROR: $CERT_DIR/server.key exists but $(id -un) cannot read it." >&2
    echo "[ssl-init] The certificate volume was created when this container ran as root." >&2
    echo "[ssl-init] Fix it once:  docker compose run --rm frontend-certs" >&2
    echo "[ssl-init]   (or: chown -R 101:101 on the certs volume)" >&2
    exit 1
fi

if [ ! -f "$CERT_DIR/server.crt" ] || [ ! -f "$CERT_DIR/server.key" ]; then
    echo "[ssl-init] No certificate found — generating self-signed cert..."
    mkdir -p "$CERT_DIR"
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -keyout "$CERT_DIR/server.key" \
        -out    "$CERT_DIR/server.crt" \
        -subj   "/C=SG/ST=Singapore/L=Singapore/O=Seventh AI Vision/CN=seventh.ai" \
        -addext "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1"
    chmod 600 "$CERT_DIR/server.key"
    echo "[ssl-init] Self-signed certificate created (valid 10 years)."
    echo "[ssl-init] Replace with a real cert by placing files in the certs_data volume."
else
    echo "[ssl-init] Certificate already present — skipping generation."
fi
