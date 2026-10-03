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
NFT_SERVICE="acdv-nftables"
NFT_APPLY="$SRC_DIR/apply_nftables.sh"

echo "▶️  Kiểm tra quyền root..."
if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Vui lòng chạy với quyền root:  sudo bash install_service.sh"
    exit 1
fi

echo "▶️  Kiểm tra và cài luật nftables của ACDV..."
if ! command -v nft >/dev/null 2>&1; then
    echo "❌ Chưa cài nftables. Cài nftables rồi chạy lại script."
    exit 1
fi

echo "⚠️  Bộ luật sẽ thay thế riêng table inet quy-tac-mang."
echo "   Kiểm tra interface/IP trong systemd/acdv-portal.nft trước khi tiếp tục."
"$NFT_APPLY"

NFT_UNIT="/etc/systemd/system/$NFT_SERVICE.service"
cat > "$NFT_UNIT" <<EOF
[Unit]
Description=ACDV Captive Portal nftables rules
After=network-online.target nftables.service
Wants=network-online.target
Before=$SERVICE.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash $NFT_APPLY

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$NFT_UNIT"

echo "▶️  Copy unit file vào /etc/systemd/system/..."
cp "$UNIT" "/etc/systemd/system/$SERVICE.service"
chmod 644 "/etc/systemd/system/$SERVICE.service"

echo "▶️  Reload systemd daemon..."
systemctl daemon-reload

echo "▶️  Bật tự động chạy khi boot..."
systemctl enable "$NFT_SERVICE"
systemctl enable "$SERVICE"

echo "▶️  (Tùy chọn) Khởi động service ngay bây giờ..."
if systemctl start "$NFT_SERVICE" && systemctl start "$SERVICE" 2>/dev/null; then
    echo "✅ Service đã khởi động."
else
    echo "⚠️  Chưa thể start ngay (có thể do port/sudo). Xem log: journalctl -u $SERVICE -f"
fi

echo ""
echo "========================================================"
echo "✅ Hoàn tất. Kiểm tra trạng thái:"
echo "   systemctl status $SERVICE"
echo "   systemctl status $NFT_SERVICE"
echo "   journalctl -u $SERVICE -f   (xem log)"
echo "========================================================"
