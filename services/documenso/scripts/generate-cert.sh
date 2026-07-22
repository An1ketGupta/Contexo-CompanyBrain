#!/usr/bin/env bash
# Generate the self-signed PAdES signing certificate Documenso uses to seal
# PDFs. Documenso deliberately does NOT ship one — the private key must be
# yours. This produces ./cert.p12 next to docker-compose.yml.
#
# A self-signed cert is fine for internal onboarding docs: it proves integrity
# (the sealed PDF can't be altered without breaking the signature) and carries
# the audit trail. It will show as "not trusted" in Adobe Reader's blue bar
# because no public CA vouches for it — if you need the green check, buy an
# AATL/eIDAS cert from a CA and drop it in as cert.p12 instead (same mount).
#
# Usage:
#   bash scripts/generate-cert.sh "your-cert-passphrase"
set -euo pipefail

PASSPHRASE="${1:-}"
if [[ -z "$PASSPHRASE" ]]; then
  echo "usage: bash scripts/generate-cert.sh <passphrase>" >&2
  echo "  (put the same value in .env as NEXT_PRIVATE_SIGNING_PASSPHRASE)" >&2
  exit 1
fi

OUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_P12="$OUT_DIR/cert.p12"

if [[ -f "$CERT_P12" ]]; then
  echo "refusing to overwrite existing $CERT_P12 (delete it first if intended)" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 10-year self-signed cert. CN is cosmetic (shown in the signature panel).
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
  -subj "/CN=NirnayaIQ E-Sign/O=NirnayaIQ/C=IN"

# Bundle key + cert into the PKCS#12 file Documenso expects.
openssl pkcs12 -export \
  -out "$CERT_P12" \
  -inkey "$TMP/key.pem" \
  -in "$TMP/cert.pem" \
  -passout "pass:$PASSPHRASE"

chmod 400 "$CERT_P12"
echo "wrote $CERT_P12"
echo "next: set NEXT_PRIVATE_SIGNING_PASSPHRASE=$PASSPHRASE in .env"
