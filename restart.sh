#!/usr/bin/env bash
set -euo pipefail
# ============================================================
# RESTART CHƯƠNG TRÌNH ĐỘC LẬP (KHÔNG DÙNG SYSTEMD SERVICE)
# Chương trình chạy ngoài hệ thống, tự quản lý. Script này chỉ:
#   1) Dừng mọi instance main.py cũ đang chạy
#   2) Chạy lại MỘT instance duy nhất bằng code mới (nền)
# KHÔNG đăng ký / cài đặt bất kỳ service systemd nào.
# ============================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}"

echo "==> Dừng mọi instance main.py cũ..."
pkill -f "/home/acdv-teams/Desktop/wifi/wifi.*main.py" || true
pkill -f "main.py" || true
sleep 3

echo "==> Kiểm tra trạng thái cổng sau khi dừng..."
ss -ltn 2>/dev/null | grep -E ':(80|8080|8765)\b' || echo "   (không còn process giữ cổng)"

echo "==> Khởi động lại ở chế độ nền (nohup) — KHÔNG dùng systemd..."
nohup "${PROJECT_DIR}/.venv/bin/python" "${PROJECT_DIR}/main.py" > "${PROJECT_DIR}/../acdv_portal_run.log" 2>&1 &
echo "   PID: $!"
sleep 4

echo "==> Trạng thái sau khi khởi động:"
ss -ltn 2>/dev/null | grep -E ':(80|8080|8765)\b' || echo "   (cổng chưa sẵn sàng — xem log)"
echo
echo "Xem log: tail -f ${PROJECT_DIR}/../acdv_portal_run.log"
