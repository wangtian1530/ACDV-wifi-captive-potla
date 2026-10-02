
import json
import os
import random
import hashlib
import threading
from typing import Dict, Optional, List

from .config import DATA_FILE, VOUCHER_PACKAGES
from .utils import timestamp_now

# Khóa toàn cục bảo vệ việc đọc/ghi file dữ liệu từ nhiều luồng
# (HTTP + WebSocket) cùng lúc, tránh ghi đè / mất dữ liệu cũ.
_SAVE_LOCK = threading.RLock()


def ensure_data_defaults(data: Dict) -> Dict:
    data.setdefault("vouchers", [])
    data.setdefault("active_ips", [])
    data.setdefault("device_tracking", {})
    data.setdefault("data_voucher", {})
    data.setdefault("chat_messages", [])
    data.setdefault("pending_activations", {})
    return data


def load_data() -> Dict:
    with _SAVE_LOCK:
        if not os.path.exists(DATA_FILE):
            default_data = {"vouchers": [], "active_ips": [], "device_tracking": {}, "data_voucher": {}, "chat_messages": [], "pending_activations": {}}
            save_data(default_data)
            return default_data

        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                default_data = {"vouchers": [], "active_ips": [], "device_tracking": {}, "data_voucher": {}, "chat_messages": [], "pending_activations": {}}
                save_data(default_data)
                return default_data

        data = ensure_data_defaults(data)
        # Migrate dữ liệu cũ: thêm các field mới nếu chưa có
        for voucher in data.get("vouchers", []):
            if "max_data" not in voucher:
                package_key = voucher.get("package_key", "")
                config = VOUCHER_PACKAGES.get(package_key)
                if config and config["type"] == "data":
                    voucher["max_data"] = config["value"]
                else:
                    voucher["max_data"] = 0
            if "connet_data" not in voucher:
                voucher["connet_data"] = 0
            if voucher.get("type") == "data" and "bound_device" not in voucher:
                voucher["bound_device"] = None
        # Migration data_voucher: thêm bound_device cho từng voucher data
        for code, info in data.get("data_voucher", {}).items():
            if "bound_device" not in info:
                v = find_voucher(data, code)
                info["bound_device"] = v.get("bound_device") if v else None
            if "devices" not in info:
                info["devices"] = []
        return data


def save_data(data: Dict) -> None:
    ensure_data_defaults(data)
    with _SAVE_LOCK:
        # Ghi vào file tạm rồi rename (atomic) để tránh file hỏng / mất dữ liệu
        # nếu tiến trình bị dừng giữa chừng khi đang ghi.
        tmp_path = DATA_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, DATA_FILE)


def generate_voucher_code() -> str:
    parts = ["".join(random.choices("ABCDEFGHJKMNPQRSTUVWXYZ23456789", k=4)) for _ in range(3)]
    return "-".join(parts)


def find_voucher(data: Dict, code: str) -> Optional[Dict]:
    code = code.strip().upper()
    for voucher in data.get("vouchers", []):
        if voucher.get("code") == code:
            return voucher
    return None


def add_chat_message(data: Dict, sender: str, device_id: str, text: str, ip: str = "") -> Dict:
    text = (text or "").strip()
    if not text:
        return {}
    data = ensure_data_defaults(data)
    message = {
        "id": f"msg-{timestamp_now()}-{len(data['chat_messages'])}",
        "sender": sender,
        "device_id": str(device_id or "unknown"),
        "ip": str(ip or ""),  # IP liên kết với tin nhắn (để admin chọn theo IP)
        "text": text,
        "created_at": timestamp_now(),
    }
    data.setdefault("chat_messages", []).append(message)
    save_data(data)
    return message


def get_pending_activation(data: Dict, device_id: str = "", ip: str = "") -> Optional[Dict]:
    data = ensure_data_defaults(data)
    candidates = []
    if device_id:
        candidates.append(str(device_id))
    if ip:
        candidates.append(str(ip))
    for key in candidates:
        payload = data.get("pending_activations", {}).get(key)
        if payload:
            return payload
    for payload in data.get("pending_activations", {}).values():
        if payload.get("device_id") == device_id or payload.get("ip") == ip:
            return payload
    return None


def add_pending_activation(data: Dict, device_id: str, ip: str, voucher_code: str, message: str = "") -> Dict:
    data = ensure_data_defaults(data)
    code = str(voucher_code or "").strip().upper()
    record = {
        "device_id": str(device_id or ""),
        "ip": str(ip or ""),
        "voucher_code": code,
        "message": message or "Admin đã gửi cho bạn một voucher kích hoạt Internet.",
        "created_at": timestamp_now(),
        "status": "pending",
    }
    mapping = data.setdefault("pending_activations", {})
    if device_id:
        mapping[str(device_id)] = record
    if ip:
        mapping[str(ip)] = record
    save_data(data)
    return record


def clear_pending_activation(data: Dict, device_id: str = "", ip: str = "") -> None:
    data = ensure_data_defaults(data)
    mapping = data.get("pending_activations", {})
    for key in [str(device_id), str(ip)]:
        if key:
            mapping.pop(key, None)
    for key, payload in list(mapping.items()):
        if payload.get("device_id") == device_id or payload.get("ip") == ip:
            mapping.pop(key, None)
    save_data(data)


def create_voucher(data: Dict, package_key: str) -> Optional[Dict]:
    if package_key not in VOUCHER_PACKAGES:
        return None

    config = VOUCHER_PACKAGES[package_key]
    code = generate_voucher_code()
    voucher = {
        "code": code,
        "package_key": package_key,
        "type": config["type"],
        "value": config["value"],
        "unit": config["unit"],
        "label": config["label"],
        "price": config["price"],
        "created_at": timestamp_now(),
        "used": False,
        "max_data": config["value"] if config["type"] == "data" else 0,
        "connet_data": 0,
        "bound_device": None,
    }
    data.setdefault("vouchers", []).append(voucher)
    save_data(data)
    return voucher


def get_ip_entry(ip: str, data: Dict) -> Optional[Dict]:
    for entry in data.get("active_ips", []):
        if entry.get("ip") == ip:
            return entry
    return None


def is_ip_active(ip: str, data: Dict) -> bool:
    return get_ip_entry(ip, data) is not None


def get_active_ips(data: Dict) -> List[Dict]:
    return data.get("active_ips", [])


def get_vouchers(data: Dict) -> List[Dict]:
    return data.get("vouchers", [])


def get_vouchers_by_code(data: Dict, code: str) -> List[Dict]:
    """Lấy tất cả active_ips có cùng voucher_code"""
    code = code.strip().upper()
    return [entry for entry in data.get("active_ips", []) if entry.get("voucher_code") == code]


def hash_qr_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
