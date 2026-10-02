import threading
import time
import os
import signal

# Xác định thư mục dự án ngay tại đây và nạp .env TRƯỚC mọi import backend,
# để config.py đọc được ACDV_ADMIN_PASSWORD / ACDV_SUDO_PASSWORD từ .env.
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Dùng psutil để liệt kê tiến trình (đã được cài trong requirements).
# Có backtrack bằng subprocess nếu thiếu psutil.
try:
    import psutil
    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False


def stop_previous_instance() -> None:
    """
    Tự tắt tiến trình main.py cũ còn sót (đang chiếm port 8080/8765) trước khi
    tiến trình mới bind port, tránh lỗi "Address already in use" khi khởi động
    lại mà quá trình cũ chưa thoát hẳn.

    Chỉ tác động đến các tiến trình đang chạy chính file main.py trong cùng
    thư mục dự án này, và SKIP chính PID hiện tại.
    """
    main_script = os.path.abspath(__file__)
    current_pid = os.getpid()
    killed = []

    def _process_args(proc):
        """Lấy cmdline + kiểm tra có phải tiến trình python chạy main.py không."""
        try:
            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                return False
            # Tiến trình python (exe chứa 'python') — loại bỏ bash/shell/lệnh
            # khác chỉ có chuỗi "main.py" nằm trong tham số.
            try:
                exe = proc.exe() or ""
            except Exception:
                exe = ""
            if "python" not in os.path.basename(str(exe)):
                return False
            # cmdline là ['python3', 'main.py'] — main.py phải là argument đứng
            # riêng (không lẫn trong chuỗi lệnh shell).
            if not any(part == "main.py" or part.endswith("/main.py") for part in cmdline):
                return False
            # Cùng thư mục dự án
            try:
                cwd = proc.cwd()
            except Exception:
                cwd = None
            return bool(cwd) and os.path.abspath(cwd) == ROOT_DIR
        except Exception:
            return False

    try:
        if HAS_PSUTIL:
            for proc in psutil.process_iter(["pid", "cmdline"]):
                try:
                    if not _process_args(proc):
                        continue
                    if proc.pid != current_pid:
                        killed.append(proc.pid)
                        print(f"🛑 Đang tắt tiến trình cũ (PID {proc.pid})...")
                        proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    continue
        else:
            # Fallback: dùng pgrep -f để tìm, chỉ nhận tiến trình python
            import subprocess
            out = subprocess.run(["pgrep", "-f", "main.py"], capture_output=True, text=True).stdout
            for pid_str in out.split():
                try:
                    pid = int(pid_str)
                    if pid == current_pid:
                        continue
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmd = f.read().decode(errors="ignore")
                    # Chỉ tắt tiến trình 'python3 main.py' trong đúng project
                    argv = [a for a in cmd.split("\x00") if a]
                    if len(argv) >= 2 and "python" in argv[0] and \
                       (argv[1] == "main.py" or argv[1].endswith("/main.py")) and \
                       ROOT_DIR in cmd:
                        killed.append(pid)
                        print(f"🛑 Đang tắt tiến trình cũ (PID {pid})...")
                        os.kill(pid, signal.SIGTERM)
                except Exception:
                    continue

        if killed:
            # Chờ tiến trình cũ thoát sạch và giải phóng port (tối đa 3s)
            deadline = time.time() + 3
            for pid in killed:
                try:
                    if HAS_PSUTIL and psutil.pid_exists(pid):
                        while time.time() < deadline and psutil.pid_exists(pid):
                            time.sleep(0.1)
                    elif not HAS_PSUTIL:
                        while time.time() < deadline:
                            try:
                                os.kill(pid, 0)  # kiểm tra còn tồn tại
                                time.sleep(0.1)
                            except OSError:
                                break
                except Exception:
                    pass
            print("✅ Đã dừng tiến trình main.py trước đó.")
    except Exception as e:
        print(f"⚠️ Lỗi khi dừng tiến trình cũ: {e}")


def load_env_file() -> None:
    """Nạp file .env (nếu có) để lấy ACDV_ADMIN_PASSWORD / ACDV_SUDO_PASSWORD,
    KHÔNG ghi đè biến môi trường đã được set sẵn (ưu tiên systemd hơn)."""
    env_path = os.path.join(ROOT_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:  # không ghi đè biến đã có
                    os.environ[key] = value
    except Exception as e:
        print(f"⚠️ Không đọc được .env: {e}")


# Nạp mật khẩu từ .env NGAY lúc này, trước khi import backend.
load_env_file()

from backend.server import run_server  # noqa: E402
from backend.portal import Portal  # noqa: E402
from backend.data_bot2 import start_data_bot  # noqa: E402
from backend.nft import sync_nft_with_active_ips  # noqa: E402
from backend.data import load_data  # noqa: E402
from backend.config import DATA_DIR  # noqa: E402


def background_cleanup_task():
    portal = Portal()
    while True:
        try:
            data = portal.load_data()
            portal.cleanup_expired(data)
        except Exception as e:
            print(f"Lỗi rà soát background: {e}")
        time.sleep(60)

if __name__ == "__main__":
    # Tự tắt tiến trình main.py cũ còn sót (đang chiếm port) để khởi động lại
    # không gặp lỗi "Address already in use".
    stop_previous_instance()

    # Tạo thư mục data nếu chưa có
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(os.path.join(DATA_DIR, "ip_data")):
        os.makedirs(os.path.join(DATA_DIR, "ip_data"), exist_ok=True)
    
    # Sau khi máy reboot, nftables bị reset hoàn toàn (các IP active mất
    # quyền Internet). Đồng bộ ngay để đưa toàn bộ active_ips đã lưu quay lại
    # nftables + iptables, khôi phục truy cập mà không cần user vào lại portal.
    try:
        data = load_data()
        sync_nft_with_active_ips(data)
        print("🔄 Đã đồng bộ nftables với các IP đang active.")
    except Exception as e:
        print(f"⚠️ Lỗi đồng bộ nftables khi khởi động: {e}")

    # Khởi động các luồng nền
    cleanup_thread = threading.Thread(target=background_cleanup_task, daemon=True)
    cleanup_thread.start()
    
    # Khởi động bot kiểm tra data
    start_data_bot()
    
    # Khởi động WebSocket server (realtime, thay HTTP polling)
    from backend.wsserver import start_ws_server
    start_ws_server()
    
    run_server()
