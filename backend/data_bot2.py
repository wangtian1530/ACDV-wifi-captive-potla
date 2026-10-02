"""Bot kiểm tra và xử lý hết data tự động - chạy ngầm"""
import threading
import time
from typing import Dict

from .cache import clear_cache_by_prefix
from .data import load_data, save_data, get_active_ips, find_voucher
from .ip_data import (
    load_ip_data,
    save_ip_data,
    apply_traffic_delta,
    get_data_usage_by_voucher,
    check_ip_data_expired,
)
from .network import get_traffic_bytes_for_ip
from .nft import delete_ip_from_nft, is_ip_in_nft


class DataBot:
    """
    Bot chạy ngầm kiểm tra data đã dùng của từng IP.
    - Cập nhật data_used theo DELTA vào data_packages (FIFO).
    - IP chỉ bị cắt khi TẤT CẢ data packages đều hết.
    - Chạy mỗi 15 giây.
    """
    
    def __init__(self):
        self.running = False
        self.thread = None
    
    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        print("🤖 DataBot: Đã khởi động")
    
    def stop(self):
        self.running = False
    
    def _run_loop(self):
        while self.running:
            try:
                self._check_all_ips()
            except Exception as e:
                print(f"🤖 DataBot Lỗi: {e}")
            time.sleep(15)
    
    def _check_all_ips(self):
        data = load_data()
        active_ips = get_active_ips(data)
        
        for entry in active_ips:
            ip = entry.get("ip")
            if not ip:
                continue
            
            try:
                # Đọc counter BYTES thô (không chia MB) cho cơ chế delta
                dl_bytes, ul_bytes = get_traffic_bytes_for_ip(ip)

                # Cập nhật data_used theo DELTA vào packages (nếu IP có data)
                ip_data = load_ip_data(ip)
                pkgs = ip_data.get("data_packages", [])
                if pkgs:
                    # Nếu đang có package "ẩn danh" (voucher_code=None, do migrate
                    # dữ liệu cũ) và entry đang là voucher data -> gán voucher_code
                    vcode_entry = entry.get("voucher_code")
                    if vcode_entry:
                        v = find_voucher(data, vcode_entry)
                        if v and v.get("type") == "data":
                            for pkg in pkgs:
                                if not pkg.get("voucher_code"):
                                    pkg["voucher_code"] = vcode_entry
                    if pkgs:
                        apply_traffic_delta(ip, dl_bytes, ul_bytes)
                else:
                    # Chưa có package data -> tạo từ voucher data (nếu entry là data)
                    voucher_code = entry.get("voucher_code")
                    if voucher_code:
                        voucher = find_voucher(data, voucher_code)
                        if voucher and voucher.get("type") == "data":
                            max_data = voucher.get("max_data", 0)
                            if max_data > 0:
                                ip_data["data_packages"] = [{
                                    "voucher_code": voucher_code,
                                    "allocated_mb": max_data,
                                    "used_mb": 0,
                                }]
                                ip_data["total_data_mb"] = max_data
                                ip_data["status"] = "active"
                                ip_data["data_used_mb"] = 0
                                ip_data["last_counter_dl_bytes"] = 0
                                ip_data["last_counter_ul_bytes"] = 0
                                save_ip_data(ip, ip_data)
            except Exception as e:
                print(f"🤖 DataBot Lỗi IP {ip}: {e}")
        
        # Tổng hợp connet_data theo voucher (để hiển thị admin)
        self._update_voucher_usage(data)
        # Cắt IP đã hết TẤT CẢ data packages
        self._cleanup_expired(data)
    
    def _update_voucher_usage(self, data: Dict):
        """
        Tổng hợp connet_data cho từng voucher data dựa trên tổng used_mb
        của các data_packages thuộc voucher đó (mọi IP). Ghi vào
        voucher["connet_data"] và data_voucher[code]["connet_data"].
        """
        active_ips = data.get("active_ips", [])
        data_voucher = data.get("data_voucher", {})

        # {voucher_code: tổng used_mb từ các package của mọi IP}
        usage_by_voucher = {}
        seen = set()
        for entry in active_ips:
            ip = entry.get("ip")
            if not ip or ip in seen:
                continue
            seen.add(ip)
            per_voucher = get_data_usage_by_voucher(ip)
            for code, used in per_voucher.items():
                usage_by_voucher[code] = usage_by_voucher.get(code, 0) + used

        changed = False
        for voucher in data.get("vouchers", []):
            if voucher.get("type") != "data":
                continue
            code = voucher.get("code")
            connet = usage_by_voucher.get(code, 0)

            # CHỈ cập nhật khi tăng (không bao giờ giảm)
            if connet > voucher.get("connet_data", 0):
                voucher["connet_data"] = connet
                changed = True

            if code in data_voucher:
                if connet > data_voucher[code].get("connet_data", 0):
                    data_voucher[code]["connet_data"] = connet
                    changed = True

        if changed:
            save_data(data)

    def _cleanup_expired(self, data: Dict):
        """Cắt IP khi TẤT CẢ data_packages đều hết. Chỉ áp dụng cho IP đang
        active voucher DATA (IP dùng voucher TIME do portal.cleanup_expired
        xử lý theo thời gian, không bị cắt bởi bot)."""
        active_ips = data.get("active_ips", [])
        expired_to_remove = []
        
        for entry in active_ips:
            ip = entry.get("ip")
            if not ip:
                continue

            # Chỉ xử lý IP đang active voucher DATA
            vcode = entry.get("voucher_code")
            if not vcode:
                continue
            voucher = find_voucher(data, vcode)
            if not voucher or voucher.get("type") != "data":
                continue

            ip_data = load_ip_data(ip)
            if not ip_data.get("data_packages", []):
                continue

            if check_ip_data_expired(ip):
                expired_to_remove.append(entry)
                
                if is_ip_in_nft(ip):
                    delete_ip_from_nft(ip, quiet=True)
                
                for prefix in ["online", "internet", "status", "nft", "traffic"]:
                    clear_cache_by_prefix(f"{prefix}_{ip}")
                print(f"🤖 Bot: Cắt IP {ip} - hết toàn bộ data packages")

        if expired_to_remove:
            data["active_ips"] = [e for e in active_ips if e not in expired_to_remove]
            save_data(data)


_data_bot = None

def start_data_bot():
    global _data_bot
    if _data_bot is None:
        _data_bot = DataBot()
    _data_bot.start()
    return _data_bot
