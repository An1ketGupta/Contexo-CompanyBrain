#!/usr/bin/env bash
# Print freshly-generated values for the three secret env vars Documenso needs.
# Paste the output into .env. Safe to re-run (does not touch any file).
set -euo pipefail

echo "NEXTAUTH_SECRET=$(openssl rand -base64 32)"
echo "NEXT_PRIVATE_ENCRYPTION_KEY=$(openssl rand -base64 32)"
echo "NEXT_PRIVATE_ENCRYPTION_SECONDARY_KEY=$(openssl rand -base64 32)"
