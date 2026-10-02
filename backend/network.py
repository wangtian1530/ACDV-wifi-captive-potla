import os
import re
import socket
import struct
import subprocess
import fcntl
import time
from typing import Dict, List, Optional, Tuple

from .cache import get_cache, set_cache
from .config import CACHE_TTL, DHCP_LEASES_FILE, INTERFACE, SUDO_PASSWORD
from .utils import is_valid_ipv4


gateway_cache: Optional[str] = None
gateway_timestamp: float = 0.0


def get_gateway() -> Optional[str]:
    global gateway_cache, gateway_timestamp
    now = time.time()
    if gateway_cache and now - gateway_timestamp < CACHE_TTL["gateway"]:
        return gateway_cache

    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            parts = result.stdout.split()
            for i, part in enumerate(parts):
                if part == "via" and i + 1 < len(parts):
                    gateway_cache = parts[i + 1]
                    gateway_timestamp = now
                    return gateway_cache
    except Exception:
        pass
    return None


def get_interface_ip(interface: str = INTERFACE) -> Optional[str]:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ip = socket.inet_ntoa(
            fcntl.ioctl(
                sock.fileno(),
                0x8915,
                struct.pack("256s", interface[:15].encode()),
            )[20:24]
        )
        return ip
    except Exception:
        return None


def check_ip_online(ip: str) -> bool:
    cache_key = f"online_{ip}"
    cached = get_cache(cache_key, CACHE_TTL["online"])
    if cached is not None:
        return cached

    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "0.3", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.8,
        )
        status = result.returncode == 0
        set_cache(cache_key, status)
        return status
    except Exception:
        return False


def check_internet_access(ip: str) -> bool:
    cache_key = f"internet_{ip}"
    cached = get_cache(cache_key, CACHE_TTL["internet"])
    if cached is not None:
        return cached

    from .nft import is_ip_in_nft

    if not is_ip_in_nft(ip):
        set_cache(cache_key, False)
        return False

    gateway = get_gateway()
    if gateway and gateway == ip:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "1", "8.8.8.8"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            )
            status = result.returncode == 0
            set_cache(cache_key, status)
            return status
        except Exception:
            set_cache(cache_key, False)
            return False

    if gateway:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "0.5", gateway],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
            if result.returncode == 0:
                set_cache(cache_key, True)
                return True
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", "8.8.8.8"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        status = result.returncode == 0
        set_cache(cache_key, status)
        return status
    except Exception:
        set_cache(cache_key, False)
        return False


def get_dhcp_clients() -> List[Dict]:
    cache_key = "dhcp_clients"
    cached = get_cache(cache_key, CACHE_TTL["dhcp"])
    if cached is not None:
        return cached

    clients: List[Dict] = []
    if os.path.exists(DHCP_LEASES_FILE):
        try:
            with open(DHCP_LEASES_FILE, "r") as f:
                content = f.read()
                lease_blocks = re.findall(r'lease ([\d.]+) \{([^}]+)\}', content, re.DOTALL)
                for ip, block in lease_blocks:
                    client = {"ip": ip}
                    mac_match = re.search(r'hardware ethernet ([0-9a-f:]+);', block)
                    if mac_match:
                        client["mac"] = mac_match.group(1)
                    hostname_match = re.search(r'client-hostname "([^"]+)";', block)
                    if hostname_match:
                        client["hostname"] = hostname_match.group(1)
                    clients.append(client)
        except Exception as e:
            print(f"Lỗi đọc DHCP leases: {e}")

    set_cache(cache_key, clients)
    return clients


def get_arp_table() -> List[Dict]:
    cache_key = "arp_table"
    cached = get_cache(cache_key, CACHE_TTL["arp"])
    if cached is not None:
        return cached

    devices: List[Dict] = []
    try:
        result = subprocess.run(["arp", "-n"], capture_output=True, text=True, timeout=1)
        lines = result.stdout.split("\n")[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                ip = parts[0]
                mac = parts[2]
                if ip and mac and mac != "(incomplete)":
                    devices.append({"ip": ip, "mac": mac, "status": "online"})
    except Exception as e:
        print(f"Lỗi đọc ARP table: {e}")

    set_cache(cache_key, devices)
    return devices


def get_connected_devices() -> List[Dict]:
    cache_key = "connected_devices"
    cached = get_cache(cache_key, CACHE_TTL["arp"])
    if cached is not None:
        return cached

    devices_by_ip: Dict[str, Dict] = {}

    def is_unknown(value: str) -> bool:
        return not value or value.strip().lower() in {"unknown", "-", "none", "n/a"}

    def merge_device(ip: str, device: Dict, source: str) -> None:
        if not ip:
            return
        current = devices_by_ip.get(ip)
        if current is None:
            current = {
                "ip": ip,
                "mac": "",
                "hostname": "",
                "sources": [],
                "status": "online",
            }
            devices_by_ip[ip] = current

        mac = device.get("mac", "")
        hostname = device.get("hostname", "")
        if not is_unknown(mac):
            current["mac"] = mac
        if not is_unknown(hostname):
            current["hostname"] = hostname
        if source not in current["sources"]:
            current["sources"].append(source)

    dhcp_clients = get_dhcp_clients()
    for client in dhcp_clients:
        merge_device(client.get("ip", ""), client, "DHCP")

    arp_devices = get_arp_table()
    for arp in arp_devices:
        merge_device(arp.get("ip", ""), arp, "ARP")

    devices = []
    for device in devices_by_ip.values():
        device["mac"] = device["mac"] or "Chưa xác định MAC"
        device["hostname"] = device["hostname"] or f"Thiết bị {device['ip']}"
        device["source"] = " + ".join(device.pop("sources")) or "Mạng nội bộ"
        device["group"] = "DHCP + ARP" if device["source"] == "DHCP + ARP" else device["source"]
        devices.append(device)

    set_cache(cache_key, devices)
    return devices


def get_traffic_bytes_for_ip(ip: str) -> Tuple[int, int]:
    """Đọc counter BYTES thô (download_bytes, upload_bytes) của 1 IP.
    KHÔNG chia MB để bot tính DELTA và lưu persistent (chống mất số liệu
    khi sập nguồn / nftables bị reset)."""
    download_bytes = 0
    upload_bytes = 0

    # ƯU TIÊN iptables: các rules FORWARD per-IP tạo bởi add_ip_to_nft
    # có counter per-IP chính xác nhất. (nft set chỉ là danh sách IP,
    # counter của nft không phân biệt riêng từng IP.)
    try:
        cmd2 = ["sudo", "-S", "iptables", "-L", "FORWARD", "-v", "-n", "-x"]
        proc2 = subprocess.Popen(
            cmd2,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        out2, _ = proc2.communicate(input=(SUDO_PASSWORD + "\n"), timeout=3)
        if proc2.returncode == 0:
            for line in out2.split("\n"):
                if ip in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            bytes_val = int(parts[1].replace(",", ""))
                            if "-d" in line:
                                download_bytes += bytes_val
                            elif "-s" in line:
                                upload_bytes += bytes_val
                        except:
                            pass
    except:
        pass

    # Fallback: đọc counter per-IP từ nftables ruleset nếu iptables không có
    if download_bytes == 0 and upload_bytes == 0:
        try:
            cmd = ["sudo", "-S", "nft", "list", "ruleset"]
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                input=(SUDO_PASSWORD + "\n"), timeout=3
            )

            if process.returncode == 0:
                lines = stdout.split("\n")
                for i, line in enumerate(lines):
                    if f'"{ip}"' in line or ip in line:
                        counter_line = line
                        if "counter" not in counter_line and i > 0:
                            counter_line = lines[i - 1] + " " + line

                        counter_match = re.search(
                            r"counter\s+packets\s+\d+\s+bytes\s+(\d+)", counter_line
                        )
                        if counter_match:
                            bytes_val = int(counter_match.group(1))
                            if "saddr" in line:
                                upload_bytes += bytes_val
                            if "daddr" in line:
                                download_bytes += bytes_val
        except Exception as e:
            print(f"Lỗi đếm traffic bytes nftables cho IP {ip}: {e}")

    return download_bytes, upload_bytes


def get_traffic_for_ip(ip: str) -> Tuple[int, int]:
    cache_key = f"traffic_{ip}"
    cached = get_cache(cache_key, CACHE_TTL["traffic"])
    if cached is not None:
        return cached

    download_bytes, upload_bytes = get_traffic_bytes_for_ip(ip)

    download_mb = download_bytes // (1024 * 1024)
    upload_mb = upload_bytes // (1024 * 1024)
    set_cache(cache_key, (download_mb, upload_mb))
    return download_mb, upload_mb
