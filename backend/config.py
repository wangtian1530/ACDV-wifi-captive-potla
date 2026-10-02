import os
from typing import Dict, Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
DATA_FILE = os.path.join(ROOT_DIR, "acdv_data.json")
# File riêng chuyên lưu trữ tin nhắn chat (tách khỏi DATA_FILE)
CHAT_FILE = os.path.join(ROOT_DIR, "acdv_chat.json")
NFT_TABLE = "inet"
NFT_CHAIN = "quy-tac-mang"
NFT_SET = "danh_sach_v4"
PORTAL_IP = "10.10.10.1"
PORTAL_PORTS = ("80", "8080")
# Admin password đọc từ biến môi trường (không hardcode trong source).
# Nếu không set, hệ thống sẽ từ chối đăng nhập admin để tránh lộ mật khẩu.
ADMIN_PASSWORD = os.environ.get("ACDV_ADMIN_PASSWORD", "")

# Mật khẩu sudo dùng cho `sudo -S` khi gọi nft/iptables.
# Khuyến nghị set qua biến môi trường ACDV_SUDO_PASSWORD (hoặc trong .env),
# KHÔNG hardcode trong source. Nếu để trống, các lệnh sudo sẽ không chạy được.
SUDO_PASSWORD = os.environ.get("ACDV_SUDO_PASSWORD", "")

PORTAL_NAME = "ACDV-Teams Network Routing"

CONTACT_INFO: Dict[str, str] = {
    "facebook": "ic.wo.de.tian",
    "telegram": "@wangtian99",
    "zalo": "0336879612",
    "phone": "00000000",
    "email": "2296656946@qq.com"
}

INTERFACE = "eth0"
DHCP_LEASES_FILE = "/var/lib/dhcp/dhcpd.leases"

# WebSocket realtime
WS_PORT = 8765
WS_PUSH_INTERVAL = 3  # đẩy status mỗi 3 giây

CACHE_TTL: Dict[str, int] = {
    "online": 2,
    "internet": 3,
    "traffic": 10,
    "arp": 5,
    "dhcp": 10,
    "gateway": 60,
    "status": 1,
    "nft_check": 5,
}

VOUCHER_PACKAGES: Dict[str, Dict[str, Any]] = {
    "time_1h": {"type": "time", "value": 1, "label": "1 Giờ", "unit": "giờ", "price": 10000},
    "time_3h": {"type": "time", "value": 3, "label": "3 Giờ", "unit": "giờ", "price": 25000},
    "time_6h": {"type": "time", "value": 6, "label": "6 Giờ", "unit": "giờ", "price": 45000},
    "time_12h": {"type": "time", "value": 12, "label": "12 Giờ", "unit": "giờ", "price": 80000},
    "time_24h": {"type": "time", "value": 24, "label": "24 Giờ", "unit": "giờ", "price": 150000},
    "time_48h": {"type": "time", "value": 48, "label": "48 Giờ", "unit": "giờ", "price": 280000},
    "time_7d": {"type": "time", "value": 168, "label": "7 Ngày", "unit": "ngày", "price": 500000},
    "time_30d": {"type": "time", "value": 720, "label": "30 Ngày", "unit": "ngày", "price": 1800000},
    "data_1gb": {"type": "data", "value": 1024, "label": "1 GB", "unit": "GB", "price": 20000},
    "data_2gb": {"type": "data", "value": 2048, "label": "2 GB", "unit": "GB", "price": 35000},
    "data_5gb": {"type": "data", "value": 5120, "label": "5 GB", "unit": "GB", "price": 80000},
    "data_10gb": {"type": "data", "value": 10240, "label": "10 GB", "unit": "GB", "price": 150000},
    "data_20gb": {"type": "data", "value": 20480, "label": "20 GB", "unit": "GB", "price": 280000},
    "data_50gb": {"type": "data", "value": 51200, "label": "50 GB", "unit": "GB", "price": 650000},
    "data_100gb": {"type": "data", "value": 102400, "label": "100 GB", "unit": "GB", "price": 1200000},
    "data_unlimited": {"type": "data", "value": 999999, "label": "Không giới hạn", "unit": "GB", "price": 2500000},
}

ADMIN_COOKIE_NAME = "ACDV_ADMIN"
SESSION_TIMEOUT = 3600
QR_TOKEN_TTL = 600
