import re
import subprocess
from typing import Dict, List

from .cache import clear_cache_by_prefix, get_cache, set_cache
from .config import CACHE_TTL, NFT_CHAIN, NFT_SET, NFT_TABLE, PORTAL_IP, PORTAL_PORTS, SUDO_PASSWORD
from .utils import is_valid_ipv4


def _run_sudo(*args: str) -> subprocess.CompletedProcess:
    """Chạy lệnh sudo -S truyền mật khẩu qua stdin (SUDO_PASSWORD từ config).

    Lưu ý: mật khẩu lấy từ biến môi trường ACDV_SUDO_PASSWORD (hoặc .env),
    không hardcode. Nếu SUDO_PASSWORD rỗng, lệnh sẽ không có password để
    send và bị từ chối.
    """
    cmd = ["sudo", "-S", *args]
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(
            input=(SUDO_PASSWORD + "\n"), timeout=5
        )
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    completed = subprocess.CompletedProcess(
        cmd, process.returncode, stdout, stderr
    )
    return completed


def is_ip_in_nft(ip: str) -> bool:
    cache_key = f"nft_{ip}"
    cached = get_cache(cache_key, CACHE_TTL["nft_check"])
    if cached is not None:
        return cached

    try:
        result = _run_sudo("nft", "list", "set", NFT_TABLE, NFT_CHAIN, NFT_SET)

        if result.returncode == 0:
            exists = ip in result.stdout
            set_cache(cache_key, exists)
            return exists
    except Exception as e:
        print(f"Lỗi kiểm tra nftables: {e}")

    return False


def add_ip_to_nft(ip: str, quiet: bool = False) -> bool:
    try:
        result = _run_sudo(
            "nft", "add", "element", NFT_TABLE, NFT_CHAIN, NFT_SET, "{", ip, "}"
        )
        if result.returncode != 0:
            print(f"❌ Failed to add IP {ip}: {result.stderr}")
            return False

        if not quiet:
            print(f"✅ Added IP {ip} to nftables set {NFT_SET}.")

        # Thêm rules iptables để đếm traffic cho IP này
        _run_sudo("iptables", "-A", "FORWARD", "-s", ip, "-j", "ACCEPT")
        _run_sudo("iptables", "-A", "FORWARD", "-d", ip, "-j", "ACCEPT")

        clear_cache_by_prefix(f"online_{ip}")
        clear_cache_by_prefix(f"internet_{ip}")
        clear_cache_by_prefix(f"status_{ip}")
        clear_cache_by_prefix(f"nft_{ip}")
        clear_cache_by_prefix("connected_devices")
        return True
    except Exception as e:
        print(f"❌ Error adding IP {ip}: {e}")
        return False


def delete_ip_from_nft(ip: str, quiet: bool = False) -> bool:
    try:
        result = _run_sudo(
            "nft", "delete", "element", NFT_TABLE, NFT_CHAIN, NFT_SET, "{", ip, "}"
        )
        if result.returncode != 0:
            print(f"❌ Failed to remove IP {ip}: {result.stderr}")
            return False

        if not quiet:
            print(f"✅ Removed IP {ip} from nftables set {NFT_SET}.")

        # Xóa rules iptables tương ứng
        _run_sudo("iptables", "-D", "FORWARD", "-s", ip, "-j", "ACCEPT")
        _run_sudo("iptables", "-D", "FORWARD", "-d", ip, "-j", "ACCEPT")

        clear_cache_by_prefix(f"online_{ip}")
        clear_cache_by_prefix(f"internet_{ip}")
        clear_cache_by_prefix(f"status_{ip}")
        clear_cache_by_prefix(f"nft_{ip}")
        clear_cache_by_prefix("connected_devices")
        return True
    except Exception as e:
        print(f"❌ Error removing IP {ip}: {e}")
        return False


def allow_portal_access(ip: str) -> bool:
    """Cho phép client hết hạn chỉ truy cập web portal trên gateway."""
    try:
        result = _run_sudo(
            "iptables", "-I", "INPUT", "1", "-s", ip, "-d", PORTAL_IP,
            "-p", "tcp", "-m", "multiport", "--dports", ",".join(PORTAL_PORTS),
            "-j", "ACCEPT",
        )
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Error allowing portal access for {ip}: {e}")
        return False


def remove_portal_access(ip: str) -> None:
    """Dọn rule portal-only khi client đã được kích hoạt lại."""
    try:
        _run_sudo(
            "iptables", "-D", "INPUT", "-s", ip, "-d", PORTAL_IP,
            "-p", "tcp", "-m", "multiport", "--dports", ",".join(PORTAL_PORTS),
            "-j", "ACCEPT",
        )
    except Exception as e:
        print(f"⚠️ Error removing portal access for {ip}: {e}")


def sync_nft_with_active_ips(data: Dict) -> None:
    active_ips = data.get("active_ips", [])
    active_ip_list = [entry["ip"] for entry in active_ips]

    try:
        result = _run_sudo("nft", "list", "set", NFT_TABLE, NFT_CHAIN, NFT_SET)

        if result.returncode == 0:
            nft_ips: List[str] = []
            for line in result.stdout.split("\n"):
                ip_match = re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', line)
                if ip_match:
                    ip = ip_match.group()
                    if is_valid_ipv4(ip):
                        nft_ips.append(ip)

            from .network import get_gateway, get_interface_ip

            for ip in active_ip_list:
                if ip not in nft_ips:
                    print(f"🔄 Đồng bộ: Thêm IP {ip} vào nftables")
                    add_ip_to_nft(ip, quiet=True)

            for ip in nft_ips:
                if ip not in active_ip_list and ip != get_gateway():
                    server_ip = get_interface_ip()
                    if ip != server_ip:
                        print(f"🔄 Đồng bộ: Xóa IP {ip} khỏi nftables")
                        delete_ip_from_nft(ip, quiet=True)
    except Exception as e:
        print(f"Lỗi đồng bộ nftables: {e}")
