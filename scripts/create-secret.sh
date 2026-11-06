#!/usr/bin/env bash
set -euo pipefail
read -r -s -p 'HF token (Enter for the public model): ' HF_TOKEN
printf '\n'
read -r -s -p 'API token (32+ characters, Enter to generate): ' API_TOKEN
printf '\n'
if [ -z "$API_TOKEN" ]; then
  API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
if [ "${#API_TOKEN}" -lt 32 ]; then
  printf 'API token must contain at least 32 characters\n' >&2
  exit 1
fi
kubectl apply -f k8s/namespace.yaml
secret_dir="$(mktemp -d)"
trap 'rm -f -- "$secret_dir/hf-token" "$secret_dir/api-token"; rmdir -- "$secret_dir"' EXIT
chmod 700 "$secret_dir"
umask 077
printf '%s' "$HF_TOKEN" > "$secret_dir/hf-token"
printf '%s' "$API_TOKEN" > "$secret_dir/api-token"
kubectl -n llm-cpu create secret generic inference-secrets \
  --from-file="hf-token=$secret_dir/hf-token" --from-file="api-token=$secret_dir/api-token" \
  --dry-run=client -o yaml | kubectl apply -f -
printf 'Secret saved. Retrieve the API token locally as described in README.\n'
