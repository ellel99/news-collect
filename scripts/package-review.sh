#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME="$(basename "${PROJECT_ROOT}")"
OUTPUT_PATH="${1:-$(dirname "${PROJECT_ROOT}")/${PROJECT_NAME}-Review.zip}"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mic-review.XXXXXX")"
STAGE_DIR="${TEMP_DIR}/${PROJECT_NAME}"
cleanup(){ rm -rf "${TEMP_DIR}"; }
trap cleanup EXIT
python3 "${SCRIPT_DIR}/validate-foundation.py"
case "${OUTPUT_PATH}" in "${PROJECT_ROOT}"/*) echo 'output must be outside project' >&2; exit 1;; esac
SUSPICIOUS="$(find "${PROJECT_ROOT}" -type f \( -name '.env' -o -name '.env.local' -o -name '.env.production' -o -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name '*.pfx' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' -o -name '*.dump' -o -name '*.backup' \) -not -path '*/.git/*' -not -path '*/.venv/*' -print)"
if [[ -n "${SUSPICIOUS}" ]]; then echo 'sensitive/local files found:' >&2; echo "${SUSPICIOUS}" >&2; exit 1; fi
PATTERN='-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|[0-9]{8,10}:[A-Za-z0-9_-]{35}|xox[baprs]-[A-Za-z0-9-]+'
if command -v rg >/dev/null 2>&1; then
  if rg -n --hidden --glob '!.git/**' --glob '!.venv/**' --glob '!venv/**' --glob '!*.zip' -- "${PATTERN}" "${PROJECT_ROOT}" >/dev/null; then echo 'possible secret detected' >&2; exit 1; fi
else
  if grep -RInE --exclude-dir=.git --exclude-dir=.venv --exclude-dir=venv --exclude='*.zip' -- "${PATTERN}" "${PROJECT_ROOT}" >/dev/null; then echo 'possible secret detected' >&2; exit 1; fi
fi
mkdir -p "${STAGE_DIR}"
rsync -a "${PROJECT_ROOT}/" "${STAGE_DIR}/" --exclude '.git/' --exclude '.idea/' --exclude '.vscode/' --exclude '.venv/' --exclude 'venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '.mypy_cache/' --exclude '.ruff_cache/' --exclude 'node_modules/' --exclude 'dist/' --exclude 'build/' --exclude 'target/' --exclude 'logs/' --exclude 'tmp/' --exclude 'runtime-data/' --exclude 'local-data/' --exclude 'postgres-data/' --exclude 'redis-data/' --exclude 'backups/' --exclude '.DS_Store' --exclude '*.log' --exclude '*.zip'
if [[ -f "${PROJECT_ROOT}/.env.example" && ! -f "${STAGE_DIR}/.env.example" ]]; then echo '.env.example excluded unexpectedly' >&2; exit 1; fi
rm -f "${OUTPUT_PATH}"
(cd "${TEMP_DIR}" && zip -qr "${OUTPUT_PATH}" "${PROJECT_NAME}")
unzip -tq "${OUTPUT_PATH}" >/dev/null
COUNT="$(unzip -Z1 "${OUTPUT_PATH}" | grep -v '/$' | wc -l | tr -d ' ')"
if command -v shasum >/dev/null 2>&1; then SHA="$(shasum -a 256 "${OUTPUT_PATH}"|awk '{print $1}')"; else SHA="$(sha256sum "${OUTPUT_PATH}"|awk '{print $1}')"; fi
echo "Created: ${OUTPUT_PATH}"
echo "Files: ${COUNT}"
echo "SHA-256: ${SHA}"
