"""Quản lý dữ liệu data theo từng IP.

Mô hình DATA PACKAGES (refactor Mức 2 - linh hoạt, tự nạp thêm):

- Mỗi IP có danh sách `data_packages` — mỗi package tương ứng 1 voucher DATA
  đã nạp, với `allocated_mb` (dung lượng riêng của thẻ) và `used_mb` (phần đã
  dùng riêng của thẻ đó).
- Khi user nạp 1 thẻ DATA mới -> tạo package mới với `used_mb = 0` -> user
  được trọn vẹn dung lượng thẻ mới, KHÔNG bị "ăn" bởi data đã dùng trong gói
  TIME trước đó.
- Bot trừ data tiêu thụ theo FIFO (package cũ nhất trước). IP chỉ bị coi là
  hết data khi TẤT CẢ package đều dùng hết.
- `total_data_mb` = tổng `allocated_mb` (tổng credit, chỉ tăng).
- `data_used_mb` = tổng `used_mb` (lũy kế, chỉ để thống kê/admin).
"""
import json
import os
from typing import Dict, List, Optional, Tuple

from .config import DATA_DIR
from .utils import timestamp_now

# Thư mục lưu dữ liệu từng IP
IP_DATA_DIR = os.path.join(DATA_DIR, "ip_data")


def _empty_ip_data(ip: str) -> Dict:
    """Tạo cấu trúc dữ liệu mặc định cho 1 IP."""
    return {
        "ip": ip,
        "data_packages": [],           # [{voucher_code, allocated_mb, used_mb}]
        "total_data_mb": 0,            # Tổng allocated (tổng credit, chỉ tăng)
        "data_used_mb": 0,             # Tổng used (lũy kế, để thống kê)
        "transactions": [],            # Lịch sử nạp
        "last_counter_dl_bytes": 0,    # Baseline counter DOWNLOAD
        "last_counter_ul_bytes": 0,    # Baseline counter UPLOAD
        "last_active_ts": 0,
        "last_updated": timestamp_now(),
        "status": "inactive",          # active, expired, inactive
    }


def _ensure_data_packages(data: Dict) -> None:
    """Migrate dữ liệu cũ (chưa có data_packages) sang mô hình mới."""
    if "data_packages" in data:
        return
    # Dữ liệu cũ chỉ có total_data_mb/data_used_mb -> tạo 1 package đại diện
    old_total = data.get("total_data_mb", 0)
    old_used = data.get("data_used_mb", 0)
    packages = []
    if old_total > 0:
        # Không xác định được voucher -> 1 package ẩn danh để giữ trạng thái
        packages.append({
            "voucher_code": None,
            "allocated_mb": old_total,
            "used_mb": min(old_used, old_total),
        })
    data["data_packages"] = packages


def ensure_ip_data_dir():
    """Tạo thư mục nếu chưa tồn tại"""
    if not os.path.exists(IP_DATA_DIR):
        os.makedirs(IP_DATA_DIR, exist_ok=True)


def get_ip_data_file(ip: str) -> str:
    """Lấy đường dẫn file JSON của IP"""
    # Thay dấu . bằng _ để làm tên file
    safe_name = ip.replace(".", "_")
    return os.path.join(IP_DATA_DIR, f"{safe_name}.json")


def load_ip_data(ip: str) -> Dict:
    """Đọc dữ liệu của 1 IP. Không bao giờ đệ quy (an toàn khi file hỏng)."""
    ensure_ip_data_dir()
    filepath = get_ip_data_file(ip)
    
    if not os.path.exists(filepath):
        return _empty_ip_data(ip)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # File hỏng / không đọc được -> tạo lại dữ liệu mặc định
        return _empty_ip_data(ip)

    # Đảm bảo có các field cần thiết
    data.setdefault("transactions", [])
    data.setdefault("data_used_mb", 0)
    data.setdefault("total_data_mb", 0)
    data.setdefault("last_counter_dl_bytes", 0)
    data.setdefault("last_counter_ul_bytes", 0)
    data.setdefault("last_active_ts", 0)
    # Migrate sang mô hình data_packages
    _ensure_data_packages(data)
    # Đồng bộ total/data_used theo packages để nhất quán
    _recompute_totals(data)
    data.setdefault("status", "active")
    return data


def _recompute_totals(data: Dict) -> None:
    """Tính lại total_data_mb và data_used_mb từ data_packages."""
    total = sum(p.get("allocated_mb", 0) for p in data.get("data_packages", []))
    used = sum(p.get("used_mb", 0) for p in data.get("data_packages", []))
    data["total_data_mb"] = total
    # data_used_mb là lũy kế không bao giờ giảm; chỉ cập nhật nếu lớn hơn
    if used > data.get("data_used_mb", 0):
        data["data_used_mb"] = used


def save_ip_data(ip: str, data: Dict):
    """Lưu dữ liệu của 1 IP"""
    ensure_ip_data_dir()
    filepath = get_ip_data_file(ip)
    data["last_updated"] = timestamp_now()
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def add_data_transaction(ip: str, voucher_code: str, data_mb: int) -> Dict:
    """Nạp 1 thẻ DATA cho IP -> TẠO package mới với used=0 (user được trọn MB thẻ)."""
    ip_data = load_ip_data(ip)
    
    transaction = {
        "voucher_code": voucher_code,
        "data_mb": data_mb,
        "timestamp": timestamp_now(),
        "type": "data",
    }
    
    ip_data["transactions"].append(transaction)

    # Tìm package cũ của cùng voucher (nếu nạp lại cùng 1 voucher -> cộng dồn)
    pkgs = ip_data.setdefault("data_packages", [])
    existing = next((p for p in pkgs if p.get("voucher_code") == voucher_code and p.get("used_mb", 0) < p.get("allocated_mb", 0)), None)
    if existing:
        existing["allocated_mb"] += data_mb
    else:
        pkgs.append({
            "voucher_code": voucher_code,
            "allocated_mb": data_mb,
            "used_mb": 0,
        })

    ip_data["status"] = "active"
    _recompute_totals(ip_data)
    save_ip_data(ip, ip_data)
    return ip_data


def add_time_transaction(ip: str, voucher_code: str, hours: int, expires_at: int) -> Dict:
    """Thêm 1 giao dịch nạp thời gian cho IP (không tạo package data)."""
    ip_data = load_ip_data(ip)
    
    transaction = {
        "voucher_code": voucher_code,
        "hours": hours,
        "expires_at": expires_at,
        "timestamp": timestamp_now(),
        "type": "time",
    }
    
    ip_data["transactions"].append(transaction)
    ip_data["status"] = "active"
    save_ip_data(ip, ip_data)
    return ip_data


def update_data_used(ip: str, used_mb: int):
    """Cập nhật tổng data đã dùng cho IP (gán thẳng MB)."""
    ip_data = load_ip_data(ip)

    if used_mb > ip_data.get("data_used_mb", 0):
        ip_data["data_used_mb"] = used_mb
    
    _refresh_status(ip_data)
    save_ip_data(ip, ip_data)


def apply_traffic_delta(ip: str, download_bytes: int, upload_bytes: int) -> bool:
    """
    Cơ chế DELTA + PERSISTENT chống mất số liệu khi sập nguồn.

    - Lưu baseline counter trong file ip_data.
    - delta = current - last; nếu counter reset (current < last) -> delta = 0.
    - Trừ delta_mb vào data_packages theo FIFO (package cũ nhất trước).
    - IP chỉ hết data khi TẤT CẢ package đều dùng hết.

    Trả về True nếu có delta mới (data_used_mb tăng), False nếu không.

    Về việc counter RESET sau reboot:
    - Khi hệ điều hành reboot hoặc service nftables/iptables restart, các
      counters bị đưa về 0 trong khi baseline `last_counter_*` trong file vẫn
      giữ giá trị cũ (lớn). Lúc đó delta = current - last trở nên âm và bị
      clamp về 0, khiến user được "miễn phí" toàn bộ data cho tới khi counter
      tăng lại ngang baseline cũ.
    - Để chống lỗ hổng này, nếu ta thấy counter mới THẤP HƠN baseline một
      lượng đáng kể (>= 100MB — trong hoạt động bình thường counter chỉ tăng,
      không bao giờ tự giảm mức như vậy) thì coi như counter đã bị reset và
      đặt lại baseline = counter hiện tại để không tính sai những MB"ăn chùa".
    """
    ip_data = load_ip_data(ip)

    last_dl = ip_data.get("last_counter_dl_bytes", 0)
    last_ul = ip_data.get("last_counter_ul_bytes", 0)

    dl_delta = download_bytes - last_dl
    ul_delta = upload_bytes - last_ul

    # Ngưỡng chênh lệch đủ lớn để khẳng định counter đã reset (không phải
    # nhiễu): >= 100MB. Trong hoạt động bình thường counter chỉ tăng dần.
    RESET_THRESHOLD = 100 * 1024 * 1024  # 100 MB

    dl_reset = dl_delta < 0 and (last_dl - download_bytes) >= RESET_THRESHOLD
    ul_reset = ul_delta < 0 and (last_ul - upload_bytes) >= RESET_THRESHOLD

    if dl_reset:
        # Counter DOWNLOAD đã reset -> đặt baseline = counter hiện tại,
        # bỏ qua delta để không tính thành MB "ăn chùa".
        dl_delta = 0
        last_dl = download_bytes
    if ul_reset:
        ul_delta = 0
        last_ul = upload_bytes

    if dl_delta < 0:
        dl_delta = 0
    if ul_delta < 0:
        ul_delta = 0

    delta_mb = (dl_delta + ul_delta) // (1024 * 1024)

    changed = delta_mb > 0
    if changed:
        ip_data["last_active_ts"] = timestamp_now()
        # Trừ delta theo FIFO vào các package còn hạn mức
        remaining_delta = delta_mb
        pkgs = ip_data.setdefault("data_packages", [])
        for pkg in pkgs:
            if remaining_delta <= 0:
                break
            remaining_cap = pkg["allocated_mb"] - pkg["used_mb"]
            if remaining_cap <= 0:
                continue
            take = min(remaining_cap, remaining_delta)
            pkg["used_mb"] += take
            remaining_delta -= take
        _recompute_totals(ip_data)
        # Chỉ active nếu còn ít nhất 1 package còn hạn mức
        _refresh_status(ip_data)

    # Luôn cập nhật baseline (kể cả khi reset)
    ip_data["last_counter_dl_bytes"] = download_bytes
    ip_data["last_counter_ul_bytes"] = upload_bytes

    save_ip_data(ip, ip_data)
    return changed


def _refresh_status(ip_data: Dict) -> None:
    """Cập nhật status: có package data còn hạn -> active, ngược lại expired."""
    pkgs = ip_data.get("data_packages", [])
    has_credit = any(p.get("used_mb", 0) < p.get("allocated_mb", 0) for p in pkgs)
    if pkgs and not has_credit:
        ip_data["status"] = "expired"
    elif pkgs:
        ip_data["status"] = "active"


def check_ip_data_expired(ip: str, check_used_mb: int = None) -> bool:
    """Kiểm tra IP đã hết TẤT CẢ data packages chưa."""
    ip_data = load_ip_data(ip)
    pkgs = ip_data.get("data_packages", [])
    if not pkgs:
        return False  # Không có giới hạn data

    if check_used_mb is not None:
        # Kiểm tra theo mức áp đặt
        used = check_used_mb
        total = ip_data["total_data_mb"]
        if used >= total:
            ip_data["status"] = "expired"
            save_ip_data(ip, ip_data)
            return True
        return False

    has_credit = any(p.get("used_mb", 0) < p.get("allocated_mb", 0) for p in pkgs)
    if not has_credit:
        ip_data["status"] = "expired"
        save_ip_data(ip, ip_data)
        return True
    return False


def get_ip_data_summary(ip: str) -> Dict:
    """Lấy thông tin tóm tắt data của IP (remaining = tổng hạn mức còn của packages)."""
    ip_data = load_ip_data(ip)
    pkgs = ip_data.get("data_packages", [])
    remaining = sum(max(0, p.get("allocated_mb", 0) - p.get("used_mb", 0)) for p in pkgs)

    return {
        "ip": ip,
        "total_data_mb": ip_data["total_data_mb"],
        "data_used_mb": ip_data["data_used_mb"],
        "remaining_mb": remaining,
        "data_packages": pkgs,
        "status": ip_data["status"],
        "transaction_count": len(ip_data["transactions"]),
        "last_updated": ip_data["last_updated"],
    }


def get_data_usage_by_voucher(ip: str) -> Dict[str, int]:
    """Trả {voucher_code: used_mb} theo từng package của IP (phục vụ connet_data)."""
    ip_data = load_ip_data(ip)
    result = {}
    for pkg in ip_data.get("data_packages", []):
        code = pkg.get("voucher_code")
        used = pkg.get("used_mb", 0)
        if code:
            result[code] = result.get(code, 0) + used
    return result


def get_all_active_ips() -> List[str]:
    """Lấy danh sách IP đang active"""
    ensure_ip_data_dir()
    active_ips = []
    
    if not os.path.exists(IP_DATA_DIR):
        return active_ips
    
    for filename in os.listdir(IP_DATA_DIR):
        if filename.endswith(".json"):
            ip = filename.replace("_", ".").replace(".json", "")
            try:
                ip_data = load_ip_data(ip)
                if ip_data.get("status") == "active":
                    active_ips.append(ip)
            except Exception:
                pass
    
    return active_ips
