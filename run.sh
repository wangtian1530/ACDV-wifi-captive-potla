#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"

cd "${PROJECT_DIR}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Đang tạo môi trường ảo tại ${VENV_DIR}..."
    python3 -m venv "${VENV_DIR}"
fi

echo "Đang kiểm tra và cài dependency..."
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r requirements.txt

exec "${VENV_DIR}/bin/python" main.py "$@"