#!/bin/sh
# certbot deploy hook: HAProxy wants the chain and the key in one file.
# Installed as /etc/letsencrypt/renewal-hooks/deploy/haproxy.sh
set -e
mkdir -p /etc/haproxy/certs
for dir in ${RENEWED_LINEAGE:-/etc/letsencrypt/live/*}; do
    [ -f "$dir/fullchain.pem" ] || continue
    name=$(basename "$dir")
    cat "$dir/fullchain.pem" "$dir/privkey.pem" > "/etc/haproxy/certs/$name.pem.tmp"
    chmod 600 "/etc/haproxy/certs/$name.pem.tmp"
    mv "/etc/haproxy/certs/$name.pem.tmp" "/etc/haproxy/certs/$name.pem"
done
systemctl reload haproxy 2>/dev/null || true
