#!/usr/bin/env bash
# Validate and atomically replace only the ACDV-owned nftables table.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES_FILE="${SCRIPT_DIR}/acdv-portal.nft"
TABLE_FAMILY="inet"
TABLE_NAME="quy-tac-mang"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "This script must run as root (for example, through systemd or sudo)." >&2
    exit 1
fi
if ! command -v nft >/dev/null 2>&1; then
    echo "nft command not found; install nftables first." >&2
    exit 1
fi
if [[ ! -r "${RULES_FILE}" ]]; then
    echo "Rules file not found: ${RULES_FILE}" >&2
    exit 1
fi

TMP_RULES="$(mktemp)"
trap 'rm -f "${TMP_RULES}"' EXIT

# nft applies a file as a batch. Deleting and recreating this named table in
# one batch avoids a partially-installed ruleset and leaves other tables alone.
if nft list table "${TABLE_FAMILY}" "${TABLE_NAME}" >/dev/null 2>&1; then
    printf 'delete table %s %s\n' "${TABLE_FAMILY}" "${TABLE_NAME}" > "${TMP_RULES}"
fi
cat "${RULES_FILE}" >> "${TMP_RULES}"

nft --check --file "${TMP_RULES}"
nft --file "${TMP_RULES}"
echo "Loaded ${RULES_FILE} (table ${TABLE_FAMILY} ${TABLE_NAME})."
