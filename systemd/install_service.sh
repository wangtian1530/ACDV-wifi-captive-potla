#!/usr/bin/env bash
# =============================================================
#  Cài đặt ACDV Captive Portal thành systemd service
#  (để server TỰ ĐỘNG chạy mỗi khi reboot máy)
#
#  Cách chạy (cần quyền root):
#      sudo bash install_service.sh
# =============================================================
set -e

SERVICE="acdv-portal"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT="$SRC_DIR/acdv-portal.service"

echo "▶️  Kiểm tra quyền root..."
if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Vui lòng chạy với quyền root:  sudo bash install_service.sh"
    exit 1
fi

echo "▶️  Copy unit file vào /etc/systemd/system/..."
cp "$UNIT" "/etc/systemd/system/$SERVICE.service"
chmod 644 "/etc/systemd/system/$SERVICE.service"

echo "▶️  Reload systemd daemon..."
systemctl daemon-reload

echo "▶️  Bật tự động chạy khi boot..."
systemctl enable "$SERVICE"

echo "▶️  (Tùy chọn) Khởi động service ngay bây giờ..."
if systemctl start "$SERVICE" 2>/dev/null; then
    echo "✅ Service đã khởi động."
else
    echo "⚠️  Chưa thể start ngay (có thể do port/sudo). Xem log: journalctl -u $SERVICE -f"
fi

echo ""
echo "========================================================"
echo "✅ Hoàn tất. Kiểm tra trạng thái:"
echo "   systemctl status $SERVICE"
echo "   journalctl -u $SERVICE -f   (xem log)"
echo "========================================================"
