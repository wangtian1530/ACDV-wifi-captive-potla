"""Portal business logic.
This module contains the core captive portal operations.
"""
from typing import Dict, Optional, List, Tuple

from .cache import clear_cache, clear_cache_by_prefix
from .config import CONTACT_INFO, PORTAL_NAME, VOUCHER_PACKAGES
from .data import (
    create_voucher,
    find_voucher,
    get_ip_entry,
    get_active_ips,
    get_vouchers,
    get_vouchers_by_code,
    load_data,
    save_data,
)
from .ip_data import (
    add_data_transaction,
    add_time_transaction,
    get_ip_data_summary,
    load_ip_data,
    save_ip_data,
)
from .network import (
    check_internet_access,
    check_ip_online,
    get_connected_devices,
    get_dhcp_clients,
    get_arp_table,
    get_gateway,
    get_interface_ip,
    get_traffic_for_ip,
)
from .nft import add_ip_to_nft, allow_portal_access, delete_ip_from_nft, is_ip_in_nft, remove_portal_access, sync_nft_with_active_ips
from .utils import is_valid_ipv4, timestamp_now


class Portal:
    def load_data(self) -> Dict:
        return load_data()

    def save_data(self, data: Dict) -> None:
        save_data(data)

    def create_voucher(self, data: Dict, package_key: str) -> Optional[Dict]:
        return create_voucher(data, package_key)

    def get_portal_info(self) -> Dict:
        return {"portal_name": PORTAL_NAME, "contact": CONTACT_INFO}

    def sync_nft_with_active_ips(self, data: Dict) -> None:
        sync_nft_with_active_ips(data)

    def cleanup_expired(self, data: Dict) -> None:
        """
        Dọn dẹp IP hết hạn:
        - Time voucher: kiểm tra expires_at
        - Data voucher: kiểm tra qua ip_data (file riêng)
        Bot trong data_bot2 sẽ xử lý việc cắt data,
        hàm này chỉ xóa những entry hết hạn khỏi active_ips
        """
        now = timestamp_now()
        active_ips = data.get("active_ips", [])
        expired = []

        for entry in active_ips:
            ip = entry.get("ip")
            if not ip:
                continue

            is_expired = False

            # Kiểm tra hết thời gian (time voucher)
            if entry.get("expires_at", 0) <= now:
                is_expired = True

            # Kiểm tra qua ip_data (data voucher)
            if not is_expired:
                ip_data = get_ip_data_summary(ip)
                if ip_data.get("status") == "expired":
                    is_expired = True

            if is_expired:
                expired.append(entry)
                delete_ip_from_nft(ip, quiet=True)
                allow_portal_access(ip)
                for pfx in ["online", "internet", "status", "nft", "traffic"]:
                    clear_cache_by_prefix(f"{pfx}_{ip}")

        data["active_ips"] = [entry for entry in active_ips if entry not in expired]
        if expired:
            save_data(data)
            clear_cache()

    def get_user_status_payload(self, ip: str, data: Dict, device_id: str = "") -> Dict:
        # Nếu thiết bị (device_id) đã bound 1 voucher data nhưng IP đổi
        # (Android random MAC) → tự nối lại voucher sang IP mới.
        if device_id:
            self._reassign_if_needed(ip, data, device_id)

        status = self.check_ip_connection_status(ip, data)
        download, upload = get_traffic_for_ip(ip)

        # Lấy thông tin từ ip_data (file riêng)
        ip_data = get_ip_data_summary(ip)
        total_data_mb = ip_data["total_data_mb"]
        data_used_mb = ip_data["data_used_mb"]
        remaining_mb = ip_data["remaining_mb"]
        
        # Danh sách voucher IP đã nạp (từ transactions)
        raw = load_ip_data(ip)
        vouchers_used = [t.get("voucher_code") for t in raw.get("transactions", []) if t.get("voucher_code")]
        last_active = raw.get("last_active_ts", 0)
        consuming = (timestamp_now() - last_active) < 90

        return {
            "status": status.get("status", "inactive"),
            "ip": ip,
            "in_nft": status.get("in_nft", False),
            "is_online": status.get("is_online", False),
            "has_internet": status.get("has_internet", False),
            "remaining_time": status.get("remaining_time"),
            "remaining_data": remaining_mb if total_data_mb > 0 else status.get("remaining_data"),
            "max_data_mb": total_data_mb,
            "traffic_used_mb": data_used_mb if total_data_mb > 0 else status.get("traffic_used_mb", 0),
            "download": download,
            "upload": upload,
            "connet_data": total_data_mb,
            "vouchers_active": len(vouchers_used),
            "voucher_codes": vouchers_used,
            "data_used_mb": data_used_mb,
            "remaining_data_mb": remaining_mb,
            "consuming": consuming,
        }

    def _reassign_if_needed(self, ip: str, data: Dict, device_id: str) -> None:
        """Tự nối lại voucher data của thiết bị (device_id) khi IP đổi
        (Android random MAC làm DHCP/IP thay đổi). Tìm voucher data mà
        device này đang bound; nếu còn dung lượng và IP hiện tại chưa
        được gán voucher đó → chuyển active_ip sang IP mới, giữ data đã dùng."""
        if not device_id:
            return

        # Tìm voucher data bound cho device này
        voucher = None
        for v in data.get("vouchers", []):
            if v.get("type") == "data" and v.get("bound_device") == device_id:
                voucher = v
                break
        if not voucher:
            return

        code = voucher.get("code")
        remaining = self._voucher_data_remaining(data, voucher)
        if remaining <= 0:
            return  # voucher hết, không nối lại

        # IP hiện tại đang active voucher khác (vd time) → không ghi đè
        current = get_ip_entry(ip, data)
        if current and current.get("voucher_code") != code:
            return

        # Đã có IP này dùng voucher rồi → không cần làm gì
        if current and current.get("voucher_code") == code:
            # Đảm bảo IP đang nằm trong nftables
            if not is_ip_in_nft(ip):
                add_ip_to_nft(ip, quiet=True)
            return

        # Tìm IP cũ khác của cùng device đang dùng voucher này (nếu còn)
        old_entry = None
        for entry in data.get("active_ips", []):
            if entry.get("voucher_code") == code and entry.get("ip") != ip:
                old_entry = entry
                break

        # Nếu không còn entry IP cũ trong active_ips, tìm trong data_voucher (lịch sử)
        old_ip_data = None
        if old_entry:
            old_ip_data = load_ip_data(old_entry["ip"])
        else:
            # Duyệt devices từng dùng voucher để tìm file có data_used đã dùng
            dv = data.get("data_voucher", {}).get(code, {})
            for d in dv.get("devices", []):
                old_ip = d.get("ip")
                if not old_ip or old_ip == ip:
                    continue
                candidate = load_ip_data(old_ip)
                if candidate.get("data_used_mb", 0) > 0 or candidate.get("total_data_mb", 0) > 0:
                    old_ip_data = candidate
                    old_entry = {"ip": old_ip}
                    break

        # Nếu IP hiện tại chưa nằm trong nftables thì thêm
        if not is_ip_in_nft(ip):
            add_ip_to_nft(ip, quiet=True)

        now_ts = timestamp_now()
        new_entry = {
            "ip": ip,
            "added_at": now_ts,
            "expires_at": now_ts + (30 * 24 * 3600),
            "voucher_code": code,
            "max_data_mb": 0,
            "traffic_used": 0,
            "bound_device": device_id,
        }

        # Cập nhật vào active_ips: bỏ IP cũ, thêm IP mới
        active = []
        for entry in data.get("active_ips", []):
            if entry.get("voucher_code") == code and entry.get("ip") != ip:
                # IP cũ của device này → hết IP cũ, xóa khỏi white-list và nftables
                if is_ip_in_nft(entry["ip"]):
                    delete_ip_from_nft(entry["ip"], quiet=True)
                # Giữ dữ liệu data_used của IP cũ để chuyển sang IP mới
                if old_ip_data is None:
                    old_ip_data = load_ip_data(entry["ip"])
                continue
            active.append(entry)
        if not any(e.get("ip") == ip for e in active):
            active.append(new_entry)
        data["active_ips"] = active

        # Chuyển dữ liệu data (data_packages, total, used, transactions) từ IP cũ → IP mới
        if old_ip_data:
            new_ip_data = load_ip_data(ip)  # có thể là file mới
            new_ip_data["data_packages"] = [
                dict(p) for p in old_ip_data.get("data_packages", [])
            ]
            new_ip_data["total_data_mb"] = old_ip_data.get("total_data_mb", 0)
            new_ip_data["data_used_mb"] = old_ip_data.get("data_used_mb", 0)
            new_ip_data["transactions"] = old_ip_data.get("transactions", [])
            new_ip_data["status"] = old_ip_data.get("status", "active")
            new_ip_data["last_counter_dl_bytes"] = 0
            new_ip_data["last_counter_ul_bytes"] = 0
            save_ip_data(ip, new_ip_data)

        save_data(data)
        clear_cache()

    def _calculate_voucher_usage(self, data: Dict, code: str) -> int:
        """Tính dung lượng (MB) đã dùng chung của 1 voucher data dựa trên
        TẤT CẢ IP đang active dùng voucher đó. Trả về MB đã tiêu thụ."""
        total = 0
        for entry in data.get("active_ips", []):
            if entry.get("voucher_code") != code:
                continue
            ip = entry.get("ip")
            if not ip:
                continue
            summary = get_ip_data_summary(ip)
            total += summary["data_used_mb"]
        return total

    def _voucher_data_remaining(self, data: Dict, voucher: Dict) -> int:
        """Dung lượng (MB) còn lại của voucher data theo số liệu TÍCH LŨY
        (connet_data — không bao giờ giảm). Đảm bảo không cấp quá max."""
        max_data = voucher.get("max_data", 0)
        if max_data <= 0:
            return max_data
        used = voucher.get("connet_data", 0)
        return max(0, max_data - used)

    def activate_voucher(self, ip: str, code: str, data: Dict, device_id: str = "") -> Dict:
        if not is_valid_ipv4(ip):
            return {"status": "error", "message": f"Không thể xác định IP: {ip}", "status_code": 400}

        code = code.strip().upper()
        if not code:
            return {"status": "error", "message": "Vui lòng nhập mã thẻ cào.", "status_code": 400}

        voucher = find_voucher(data, code)
        if not voucher:
            return {"status": "error", "message": "Mã thẻ cào không tồn tại.", "status_code": 404}

        package_key = voucher.get("package_key", "")
        config = VOUCHER_PACKAGES.get(package_key)
        if not config:
            return {"status": "error", "message": "Gói thẻ cào không hợp lệ.", "status_code": 400}

        voucher_type = config["type"]

        # ===================== XỬ LÝ CHUNG: TIMEO HAY DATA ĐỀU KIỂM TRA TRƯỚC =====================
        if voucher_type == "data":
            # Ràng buộc voucher vào đúng 1 thiết bị: device khác bị từ chối
            bound = voucher.get("bound_device")
            if bound and bound != device_id:
                return {"status": "error", "message": "Mã thẻ đã được sử dụng bởi thiết bị khác.", "status_code": 403}
            # Kiểm tra dung lượng dùng chung THỰC TẾ còn lại
            remaining = self._voucher_data_remaining(data, voucher)
            if remaining <= 0:
                return {"status": "error", "message": "Mã thẻ đã hết dung lượng.", "status_code": 403}

        # Nếu IP hiện tại đã active rồi -> kiểm tra nếu là nạp thêm data thì cho phép
        existing_entry = get_ip_entry(ip, data)
        if existing_entry:
            # Nếu IP đang dùng voucher time -> từ chối (mỗi IP chỉ 1 voucher time)
            existing_voucher = find_voucher(data, existing_entry.get("voucher_code", ""))
            if existing_voucher and existing_voucher.get("type") == "time" and voucher_type == "time":
                return {"status": "error", "message": "Bạn đang sử dụng gói thời gian, không thể nhập thêm.", "status_code": 403}
            
            # Nếu IP đang active và nhập cùng loại -> cộng dồn
            if not is_ip_in_nft(ip):
                add_ip_to_nft(ip, quiet=True)
            
            # Nếu là voucher time: nếu entry cũ là time thì cộng thời gian
            if voucher_type == "time" and existing_voucher and existing_voucher.get("type") == "time":
                existing_entry["expires_at"] += (config["value"] * 3600)
                voucher["used"] = True
                # Tracking
                data.setdefault("device_tracking", {})[code] = {
                    "voucher_code": code,
                    "active_ip": ip,
                    "activated_at": timestamp_now(),
                    "updated_at": timestamp_now(),
                }
                save_data(data)
                clear_cache()
                return {"status": "success", "redirect": "/user"}
            
            # Nếu là voucher data -> cấp phát cho IP này
            if voucher_type == "data":
                data_mb = config.get("value", 1024)
                remaining = self._voucher_data_remaining(data, voucher)
                if remaining <= 0:
                    return {"status": "error", "message": "Mã thẻ đã hết dung lượng.", "status_code": 403}
                # Ràng buộc voucher vào đúng 1 thiết bị (bound_device)
                bound = voucher.get("bound_device")
                if bound and bound != device_id:
                    return {"status": "error", "message": "Mã thẻ đã được sử dụng bởi thiết bị khác.", "status_code": 403}
                voucher["bound_device"] = device_id

                # Nếu IP đang dùng ĐÚNG voucher này -> nối lại (Android đổi IP/MAC),
                # không cấp thêm data, chỉ đảm bảo IP active trong nftables.
                if existing_entry.get("voucher_code") == code:
                    if not is_ip_in_nft(ip):
                        add_ip_to_nft(ip, quiet=True)
                    save_data(data)
                    clear_cache()
                    return {"status": "success", "redirect": "/user"}

                # Nếu IP đang dùng voucher khác (time) -> từ chối nạp data (không có device_id)
                if not device_id and existing_entry.get("voucher_code"):
                    return {"status": "error", "message": "Thiết bị đang sử dụng mã khác.", "status_code": 403}

                actual_mb = min(data_mb, remaining)
                voucher["used"] = True
                add_data_transaction(ip, voucher["code"], actual_mb)

                # Chuyển entry sang voucher data để bot quản lý cắt đúng theo
                # data packages (giữ nguyên thời gian nếu IP vẫn còn time voucher).
                try:
                    existing_entry["voucher_code"] = voucher["code"]
                    # Giữ nguyên expires_at nếu là voucher time, không reset về 30 ngày
                except Exception:
                    pass
                # connet_data sẽ được bot cập nhật theo data_used thực tế.
                # Đăng ký IP trong data_voucher tracking
                data_voucher = data.setdefault("data_voucher", {})
                if code not in data_voucher:
                    data_voucher[code] = {"voucher_code": code, "max_data": data_mb, "connet_data": voucher.get("connet_data", 0), "devices": [], "bound_device": device_id}
                devs = data_voucher[code].setdefault("devices", [])
                if not any(d.get("ip") == ip for d in devs):
                    devs.append({"ip": ip, "added_at": timestamp_now()})
                save_data(data)
                clear_cache()
                return {"status": "success", "redirect": "/user"}

            return {"status": "success", "redirect": "/user"}

        # ===================== VOUCHER LOẠI TIME =====================
        if voucher_type == "time":
            if voucher.get("used", False):
                device_tracking = data.get("device_tracking", {})
                old_ip = device_tracking.get(code, {}).get("active_ip")
                
                if old_ip and old_ip != ip:
                    old_entry = get_ip_entry(old_ip, data)
                    if old_entry and check_internet_access(old_ip):
                        return {"status": "error", "message": "Mã thẻ đã được sử dụng bởi thiết bị khác.", "status_code": 403}
                    
                    if delete_ip_from_nft(old_ip):
                        data["active_ips"] = [e for e in data.get("active_ips", []) if e.get("ip") != old_ip]
                        if code in device_tracking:
                            del device_tracking[code]

                if old_ip == ip:
                    old_entry = get_ip_entry(old_ip, data)
                    if old_entry:
                        if not is_ip_in_nft(ip):
                            add_ip_to_nft(ip, quiet=True)
                        return {"status": "success", "redirect": "/user"}

            if add_ip_to_nft(ip):
                remove_portal_access(ip)
                now = timestamp_now()
                expires_at = now + (config["value"] * 3600)
                entry = {
                    "ip": ip,
                    "added_at": now,
                    "expires_at": expires_at,
                    "voucher_code": voucher["code"],
                    "max_data_mb": 0,
                    "traffic_used": 0,
                }
                data.setdefault("active_ips", []).append(entry)
                voucher["used"] = True
                data.setdefault("device_tracking", {})[code] = {
                    "voucher_code": code,
                    "active_ip": ip,
                    "activated_at": now,
                    "updated_at": now,
                }
                save_data(data)
                clear_cache()
                return {"status": "success", "redirect": "/user"}

            return {"status": "error", "message": "Không thể thêm IP vào nftables.", "status_code": 500}

        # ===================== VOUCHER LOẠI DATA =====================
        if voucher_type == "data":
            # Kiểm tra dung lượng dùng chung còn lại (đã tính ở đầu hàm)
            if remaining <= 0:
                return {"status": "error", "message": "Mã thẻ đã hết dung lượng.", "status_code": 403}

            # Ràng buộc voucher vào đúng 1 thiết bị (bound_device)
            bound = voucher.get("bound_device")
            if bound and bound != device_id:
                return {"status": "error", "message": "Mã thẻ đã được sử dụng bởi thiết bị khác.", "status_code": 403}
            voucher["bound_device"] = device_id

            data_mb = config.get("value", 1024)  # MB của gói data
            actual_mb = min(data_mb, remaining)  # chỉ cộng phần còn lại của voucher

            if add_ip_to_nft(ip):
                remove_portal_access(ip)
                now_ts = timestamp_now()
                
                # Thêm entry vào active_ips
                entry = {
                    "ip": ip,
                    "added_at": now_ts,
                    "expires_at": now_ts + (30 * 24 * 3600),  # 30 ngày
                    "voucher_code": voucher["code"],
                    "max_data_mb": 0,
                    "traffic_used": 0,
                    "bound_device": device_id,
                }
                data.setdefault("active_ips", []).append(entry)
                
                # Cộng dồn data vào file IP riêng
                add_data_transaction(ip, voucher["code"], actual_mb)
                
                # Đánh dấu voucher là đã dùng; connet_data do bot cập nhật theo data_used
                voucher["used"] = True
                
                # Lưu lại lịch sử voucher data
                data.setdefault("data_voucher", {})
                data_voucher = data.get("data_voucher", {})
                if code not in data_voucher:
                    data_voucher[code] = {
                        "voucher_code": code,
                        "max_data": data_mb,
                        "connet_data": voucher.get("connet_data", 0),
                        "devices": [],
                        "bound_device": device_id,
                    }
                devs = data_voucher[code].setdefault("devices", [])
                if not any(d.get("ip") == ip for d in devs):
                    devs.append({"ip": ip, "added_at": now_ts})
                
                save_data(data)
                clear_cache()
                return {"status": "success", "redirect": "/user"}

            return {"status": "error", "message": "Không thể thêm IP vào nftables.", "status_code": 500}

        return {"status": "error", "message": "Loại thẻ không hợp lệ.", "status_code": 400}

    def check_ip_connection_status(self, ip: str, data: Dict) -> Dict:
        status = {
            "ip": ip,
            "in_whitelist": False,
            "is_online": False,
            "has_internet": False,
            "is_dhcp_client": False,
            "has_arp_entry": False,
            "traffic_used_mb": 0,
            "remaining_time": None,
            "remaining_data": None,
            "voucher_code": None,
            "status": "unknown",
            "in_nft": False,
            "is_expired": False,
        }

        entry = get_ip_entry(ip, data)
        if entry:
            status["in_whitelist"] = True
            status["voucher_code"] = entry.get("voucher_code")
            now = timestamp_now()
            expired_time = entry.get("expires_at", 0) <= now
            expired_data = False
            
            # Lấy dữ liệu từ ip_data (hệ thống file riêng)
            ip_data = get_ip_data_summary(ip)
            
            if ip_data["total_data_mb"] > 0:
                # Đã nạp data qua hệ thống mới
                status["traffic_used_mb"] = ip_data["data_used_mb"]
                status["remaining_data"] = ip_data["remaining_mb"]
                if ip_data["status"] == "expired":
                    expired_data = True
            else:
                # Fallback: kiểm tra data từng IP cũ (voucher time có data limit)
                if entry.get("max_data_mb", 0) > 0:
                    download, upload = get_traffic_for_ip(ip)
                    used = download + upload
                    status["traffic_used_mb"] = used
                    status["remaining_data"] = max(0, entry.get("max_data_mb", 0) - used)
                    expired_data = used >= entry.get("max_data_mb", 0)

            if expired_time or expired_data:
                status["is_expired"] = True
                status["remaining_time"] = 0
                if is_ip_in_nft(ip):
                    delete_ip_from_nft(ip, quiet=True)
                    clear_cache_by_prefix(f"internet_{ip}")
            else:
                status["remaining_time"] = max(0, entry.get("expires_at", 0) - now)

        status["in_nft"] = is_ip_in_nft(ip)

        if status["in_whitelist"] and not status["in_nft"] and not status["is_expired"]:
            if add_ip_to_nft(ip, quiet=True):
                status["in_nft"] = True
                clear_cache_by_prefix(f"internet_{ip}")

        status["is_online"] = check_ip_online(ip)

        if status["in_nft"] and not status["is_expired"]:
            status["has_internet"] = check_internet_access(ip)
        else:
            status["has_internet"] = False

        for client in get_dhcp_clients():
            if client.get("ip") == ip:
                status["is_dhcp_client"] = True
                break

        for dev in get_arp_table():
            if dev.get("ip") == ip:
                status["has_arp_entry"] = True
                break

        if status["is_expired"]:
            status["status"] = "expired"
        elif status["in_whitelist"] and status["in_nft"] and status["has_internet"]:
            status["status"] = "active"
        elif status["in_whitelist"] and status["in_nft"] and status["is_online"]:
            status["status"] = "waiting_internet"
        elif status["in_whitelist"] and not status["in_nft"]:
            status["status"] = "nft_missing"
        elif status["in_whitelist"] and not status["is_online"]:
            status["status"] = "offline"
        elif not status["in_whitelist"] and status["is_online"]:
            status["status"] = "unknown_device"
        else:
            status["status"] = "inactive"

        return status

    def get_connected_devices(self) -> List[Dict]:
        return get_connected_devices()

    def get_admin_analysis(self, data: Dict) -> Dict:
        """Phân tích chi tiết cho Admin: theo IP và theo voucher."""
        now = timestamp_now()
        active_ips = data.get("active_ips", [])
        vouchers = data.get("vouchers", [])

        ip_analysis = []
        for entry in active_ips:
            ip = entry.get("ip")
            if not ip:
                continue
            ip_data = get_ip_data_summary(ip)
            raw = load_ip_data(ip)
            voucher_codes = list({t.get("voucher_code") for t in raw.get("transactions", []) if t.get("voucher_code")})
            last_active = raw.get("last_active_ts", 0)
            consuming = (now - last_active) < 90
            # Lịch sử giao dịch (nạp thẻ) của IP — từ file ip_data
            transactions = raw.get("transactions", [])
            ip_analysis.append({
                "ip": ip,
                "voucher_codes": voucher_codes,
                "total_data_mb": ip_data["total_data_mb"],
                "used_mb": ip_data["data_used_mb"],
                "remaining_mb": ip_data["remaining_mb"],
                "consuming": consuming,
                "status": ip_data["status"],
                "in_nft": is_ip_in_nft(ip),
                "transactions": transactions,
            })

        voucher_analysis = []
        for voucher in vouchers:
            if voucher.get("type") != "data":
                continue
            code = voucher.get("code")
            max_data = voucher.get("max_data", 0)
            connet = voucher.get("connet_data", 0)

            # devices = tất cả IP từng gắn voucher này (active + lịch sử data_voucher)
            devices = [e.get("ip") for e in active_ips if e.get("voucher_code") == code]
            dv = data.get("data_voucher", {}).get(code, {})
            hist_devices = [d.get("ip") for d in dv.get("devices", [])]

            voucher_analysis.append({
                "code": code,
                "label": voucher.get("label", ""),
                "max_data_mb": max_data,
                "connet_data_mb": connet,
                "remaining_mb": max(0, max_data - connet),
                "active_devices": devices,
                "all_devices": list(dict.fromkeys(hist_devices + devices)),
                "bound_device": voucher.get("bound_device", ""),
                "active_count": len(devices),
            })

        return {"ip_analysis": ip_analysis, "voucher_analysis": voucher_analysis}

    def revoke_ip(self, data: Dict, ip: str) -> bool:
        if not is_valid_ipv4(ip):
            return False

        active_ips = data.get("active_ips", [])
        entry = next((item for item in active_ips if item.get("ip") == ip), None)
        if not entry:
            return False

        if delete_ip_from_nft(ip, quiet=True):
            data["active_ips"] = [item for item in active_ips if item.get("ip") != ip]
            
            # Xóa device_tracking (time voucher)
            device_tracking = data.get("device_tracking", {})
            for code, info in list(device_tracking.items()):
                if info.get("active_ip") == ip:
                    del device_tracking[code]
                    break
            
            # Xóa data_voucher tracking
            voucher_code = entry.get("voucher_code")
            if voucher_code:
                data_voucher = data.get("data_voucher", {})
                if voucher_code in data_voucher:
                    devices = data_voucher[voucher_code].get("devices", [])
                    data_voucher[voucher_code]["devices"] = [d for d in devices if d.get("ip") != ip]
                    if not data_voucher[voucher_code]["devices"]:
                        del data_voucher[voucher_code]
            
            save_data(data)
            clear_cache()
            return True
        return False
