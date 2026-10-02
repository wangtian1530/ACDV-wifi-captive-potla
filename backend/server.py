import http.cookies
import http.server
import json
import mimetypes
import os
import socketserver
import urllib.parse
import base64
import io
import secrets
import threading
import time

from .auth import authorized, create_admin_session
from .config import ADMIN_COOKIE_NAME, ADMIN_PASSWORD, QR_TOKEN_TTL, VOUCHER_PACKAGES
from .data import (
    get_active_ips,
    get_vouchers,
    hash_qr_token,
    find_voucher,
    save_data,
    add_chat_message,
    add_pending_activation,
    get_pending_activation,
    clear_pending_activation,
)
from .utils import timestamp_now
from .portal import Portal
from .wsserver import broadcast_chat_message

STATIC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "static"))
portal = Portal()
RATE_LIMIT_CAPACITY = 500
RATE_LIMIT_REFILL_SECONDS = 1.0
rate_limit_lock = threading.Lock()
rate_limit_buckets = {}


def default_headers(self, content_type: str, length: int) -> None:
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Length", str(length))


class CaptivePortalHandler(http.server.BaseHTTPRequestHandler):
    def _allow_request(self) -> bool:
        """Cho phép tối đa 10 request/IP; hồi lại 1 request mỗi giây."""
        client_ip = self.client_address[0]
        now = time.monotonic()
        with rate_limit_lock:
            bucket = rate_limit_buckets.get(client_ip)
            if bucket is None:
                bucket = {"tokens": RATE_LIMIT_CAPACITY, "updated_at": now}
                rate_limit_buckets[client_ip] = bucket

            elapsed = max(0.0, now - bucket["updated_at"])
            bucket["tokens"] = min(
                RATE_LIMIT_CAPACITY,
                bucket["tokens"] + elapsed / RATE_LIMIT_REFILL_SECONDS,
            )
            bucket["updated_at"] = now
            if bucket["tokens"] < 1:
                retry_after = max(1, int((1 - bucket["tokens"]) * RATE_LIMIT_REFILL_SECONDS + 0.999))
                encoded = json.dumps({
                    "status": "error",
                    "message": "Bạn gửi quá nhiều request. Vui lòng thử lại sau.",
                    "retry_after": retry_after,
                }, ensure_ascii=False).encode("utf-8")
                self.send_response(429)
                default_headers(self, "application/json; charset=utf-8", len(encoded))
                self.send_header("Retry-After", str(retry_after))
                self.end_headers()
                self.wfile.write(encoded)
                return False
            bucket["tokens"] -= 1
        return True

    def send_text(self, text: str, status: int = 200) -> None:
        encoded = text.encode("utf-8")
        self.send_response(status)
        default_headers(self, "text/plain; charset=utf-8", len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, payload: dict, status: int = 200, cookies=None) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        default_headers(self, "application/json; charset=utf-8", len(encoded))
        if cookies:
            for cookie in cookies:
                self.send_header("Set-Cookie", cookie.OutputString())
        self.end_headers()
        self.wfile.write(encoded)

    def send_error_json(self, message: str, status: int = 400) -> None:
        self.send_json({"status": "error", "message": message}, status=status)

    def redirect(self, path: str, cookies=None) -> None:
        self.send_response(302)  # Changed from 303 to 302
        self.send_header("Location", path)
        if cookies:
            for cookie in cookies:
                self.send_header("Set-Cookie", cookie.OutputString())
        self.end_headers()

    def serve_static(self, path: str) -> None:
        static_root = STATIC_ROOT
        if path.startswith("/static/"):
            relative_path = path[len("/static/"):].lstrip("/")
        else:
            relative_path = path.lstrip("/")

        file_path = os.path.abspath(os.path.join(static_root, relative_path))
        if not file_path.startswith(static_root):
            self.send_text("403 Forbidden", status=403)
            return
        if os.path.isdir(file_path):
            file_path = os.path.join(file_path, "index.html")
        if not os.path.exists(file_path):
            self.send_text("404 Not Found", status=404)
            return
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            mime_type = "application/octet-stream"
        with open(file_path, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def get_post_data(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        return urllib.parse.parse_qs(raw)

    def do_HEAD(self) -> None:
        if not self._allow_request():
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._allow_request():
            return
        data = portal.load_data()
        portal.cleanup_expired(data)
        portal.sync_nft_with_active_ips(data)
        path = urllib.parse.urlparse(self.path).path

        # Handle captive portal detection requests
        if path == "/gen_204" or path == "/generate_204":
            # Redirect to the captive portal detection URL
            self.redirect("http://10.10.10.1/")
            return
            
        if path == "/":
            self.redirect("/user")
        elif path == "/showcase":
            self.serve_static("/static/showcase.html")
        elif path == "/user":
            self.serve_static("/static/user.html")
        elif path == "/user/status":
            # Trang trạng thái user — serve cùng trang user (WS cập nhật realtime)
            self.serve_static("/static/user.html")
        elif path == "/admin" or path.startswith("/admin/"):
            self.serve_static("/static/admin.html")
        elif path == "/static" or path.startswith("/static/"):
            self.serve_static(path)
        elif path == "/api/user/status":
            self.handle_api_user_status(data)
        elif path == "/api/user/chat":
            self.handle_api_user_chat(data)
        elif path == "/api/user/pending":
            self.handle_api_user_pending(data)
        elif path == "/api/portal-info":
            self.handle_api_portal_info()
        elif path == "/api/admin/status":
            self.handle_api_admin_status()
        elif path == "/api/admin/chat":
            self.handle_api_admin_chat(data)
        elif path == "/api/admin/dashboard":
            self.handle_api_admin_dashboard(data)
        elif path == "/api/admin/vouchers":
            self.handle_api_admin_vouchers(data)
        elif path == "/api/admin/active":
            self.handle_api_admin_active(data)
        elif path == "/api/admin/packages":
            self.handle_api_admin_packages()
        elif path == "/activate":
            self.handle_qr_redirect()
        else:
            self.serve_static("/static/404.html")

    def do_POST(self) -> None:
        if not self._allow_request():
            return
        data = portal.load_data()
        portal.cleanup_expired(data)
        path = urllib.parse.urlparse(self.path).path
        form = self.get_post_data()

        if path == "/api/user/auth":
            self.handle_api_user_auth(form, data)
        elif path == "/api/admin/login":
            self.handle_api_admin_login(form)
        elif path == "/api/admin/create":
            self.handle_api_admin_create(form, data)
        elif path == "/api/admin/revoke":
            self.handle_api_admin_revoke(form, data)
        elif path == "/api/admin/logout":
            self.handle_api_admin_logout()
        elif path == "/api/admin/sync":
            self.handle_api_admin_sync(data)
        elif path == "/api/admin/qr":
            self.handle_api_admin_qr(form, data)
        elif path == "/api/admin/send-voucher":
            self.handle_api_admin_send_voucher(form, data)
        elif path == "/api/user/qr-activate":
            self.handle_api_user_qr_activate(form, data)
        elif path == "/api/user/activate-pending":
            self.handle_api_user_activate_pending(form, data)
        elif path == "/api/user/chat":
            self.handle_api_user_chat(data)
        elif path == "/api/admin/chat":
            self.handle_api_admin_chat(data)
        else:
            self.send_error_json("Đường dẫn không tồn tại.", status=404)

    def handle_api_user_status(self, data):
        ip = self.client_address[0]
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        device_id = (query.get("device_id") or [""])[0]
        payload = portal.get_user_status_payload(ip, data, device_id=device_id)
        self.send_json(payload)

    def _safe_message_text(self, value):
        if value is None:
            return ""
        if isinstance(value, list):
            value = value[0] if value else ""
        return str(value).strip()

    def handle_api_user_chat(self, data):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        device_id = self._safe_message_text(query.get("device_id", [""]))
        if self.command == "GET":
            rows = data.get("chat_messages", [])
            filtered = [m for m in rows if not device_id or m.get("device_id") == device_id]
            self.send_json({"messages": filtered[-20:]})
            return

        payload = self.get_post_data() if hasattr(self, "get_post_data") else {}
        if isinstance(payload, dict):
            text = self._safe_message_text(payload.get("text", ""))
            device_id = self._safe_message_text(payload.get("device_id", device_id))
        else:
            text = ""
        if not text:
            self.send_error_json("Nội dung tin nhắn không được để trống.", status=400)
            return
        msg = add_chat_message(data, "user", device_id, text)
        if not msg:
            self.send_error_json("Không thể gửi tin nhắn.", status=400)
            return
        broadcast_chat_message({
            "type": "chat",
            "device_id": device_id,
            "sender": "user",
            "message": msg,
        }, device_id=device_id, sender_role="user")
        self.send_json({"status": "success", "message": msg})

    def handle_api_user_pending(self, data):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        device_id = self._safe_message_text(query.get("device_id", [""]))
        if not device_id:
            device_id = self._safe_message_text(self.headers.get("X-Device-Id", ""))
        pending = get_pending_activation(data, device_id=device_id, ip=self.client_address[0])
        if not pending:
            self.send_json({"pending": None})
            return
        self.send_json({"pending": pending})

    def handle_api_portal_info(self):
        payload = portal.get_portal_info()
        self.send_json(payload)

    def handle_api_user_auth(self, form, data):
        code = form.get("code", "")
        if isinstance(code, list):
            code = code[0] if code else ""
        device_id = form.get("device_id", "")
        if isinstance(device_id, list):
            device_id = device_id[0] if device_id else ""
        code = code.strip().upper()
        ip = self.client_address[0]
        result = portal.activate_voucher(ip, code, data, device_id=device_id)
        status_code = result.pop("status_code", 200)
        self.send_json(result, status=status_code)

    def handle_api_admin_status(self):
        self.send_json({"authorized": authorized(self)})

    def handle_api_admin_chat(self, data):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        device_id = self._safe_message_text(query.get("device_id", [""]))
        payload = self.get_post_data() if hasattr(self, "get_post_data") else {}
        if isinstance(payload, dict):
            text = self._safe_message_text(payload.get("text", ""))
            device_id = self._safe_message_text(payload.get("device_id", device_id))
        else:
            text = ""

        if self.command == "GET":
            rows = data.get("chat_messages", [])
            filtered = [m for m in rows if not device_id or m.get("device_id") == device_id]
            self.send_json({"messages": filtered[-20:]})
            return

        if not text:
            self.send_error_json("Nội dung tin nhắn không được để trống.", status=400)
            return
        msg = add_chat_message(data, "admin", device_id, text)
        if msg:
            broadcast_chat_message({
                "type": "chat",
                "device_id": device_id,
                "sender": "admin",
                "message": msg,
            }, device_id=device_id, sender_role="admin")
        self.send_json({"status": "success", "message": msg})

    def handle_api_admin_dashboard(self, data):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        vouchers = get_vouchers(data)
        active_ips = get_active_ips(data)
        packages = [
            {
                "key": key,
                "label": config["label"],
                "type": config["type"],
                "unit": config["unit"],
                "value": config["value"],
                "price": config["price"],
            }
            for key, config in VOUCHER_PACKAGES.items()
        ]
        devices = portal.get_connected_devices()
        analysis = portal.get_admin_analysis(data)
        ip_analysis = analysis.get("ip_analysis", [])
        created_value = sum(int(v.get("price", 0) or 0) for v in vouchers)
        activated_vouchers = [v for v in vouchers if v.get("used")]
        estimated_revenue = sum(int(v.get("price", 0) or 0) for v in activated_vouchers)
        voucher_usage_by_ip = []
        voucher_by_code = {v.get("code"): v for v in vouchers}
        for item in ip_analysis:
            voucher_codes = item.get("voucher_codes", [])
            spent = sum(
                int(voucher_by_code.get(code, {}).get("price", 0) or 0)
                for code in voucher_codes
            )
            voucher_usage_by_ip.append({
                "ip": item.get("ip", "-"),
                "voucher_count": len(voucher_codes),
                "voucher_codes": voucher_codes,
                "spent": spent,
                "consuming": item.get("consuming", False),
            })
        voucher_usage_by_ip.sort(key=lambda item: item["voucher_count"], reverse=True)
        self.send_json({
            "total_vouchers": len(vouchers),
            "used_vouchers": sum(1 for v in vouchers if v.get("used")),
            "active_ips": active_ips,
            "vouchers": vouchers,
            "devices": devices,
            "packages": packages,
            "data_voucher": data.get("data_voucher", {}),
            "ip_analysis": ip_analysis,
            "voucher_analysis": analysis.get("voucher_analysis", []),
            "financial_report": {
                "created_value": created_value,
                "estimated_revenue": estimated_revenue,
                "unused_value": created_value - estimated_revenue,
                "activated_count": len(activated_vouchers),
            },
            "voucher_usage_by_ip": voucher_usage_by_ip,
            "chat_messages": data.get("chat_messages", []),
            "pending_activations": data.get("pending_activations", {}),
        })

    def handle_api_admin_vouchers(self, data):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        self.send_json({"vouchers": get_vouchers(data)})

    def handle_api_admin_active(self, data):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        self.send_json({"devices": portal.get_connected_devices()})

    def handle_api_admin_packages(self):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        packages = [
            {
                "key": key,
                "label": config["label"],
                "type": config["type"],
                "unit": config["unit"],
                "value": config["value"],
                "price": config["price"],
            }
            for key, config in VOUCHER_PACKAGES.items()
        ]
        self.send_json({"packages": packages})

    def handle_api_admin_login(self, form):
        password = form.get("password", "")
        if isinstance(password, list):
            password = password[0] if password else ""
        # Chỉ cho đăng nhập khi ADMIN_PASSWORD đã được cấu hình qua biến môi
        # trường (ACDV_ADMIN_PASSWORD). Nếu chưa set, từ chối mọi đăng nhập.
        if ADMIN_PASSWORD and password == ADMIN_PASSWORD:
            token = create_admin_session()
            cookie = http.cookies.SimpleCookie()
            cookie[ADMIN_COOKIE_NAME] = token
            cookie[ADMIN_COOKIE_NAME]["path"] = "/"
            self.send_json({"status": "success"}, cookies=[cookie[ADMIN_COOKIE_NAME]])
            return
        self.send_error_json("Sai mật khẩu.", status=401)

    def handle_api_admin_create(self, form, data):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        package_key = form.get("package_key", "")
        if isinstance(package_key, list):
            package_key = package_key[0] if package_key else ""
        voucher = portal.create_voucher(data, package_key)
        if voucher:
            self.send_json({"status": "success", "voucher": voucher})
            return
        self.send_error_json("Gói không hợp lệ.", status=400)

    def handle_api_admin_revoke(self, form, data):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        ip = form.get("ip", "")
        if isinstance(ip, list):
            ip = ip[0] if ip else ""
        if portal.revoke_ip(data, ip):
            self.send_json({"status": "success"})
            return
        self.send_error_json("Không thể thu hồi IP.", status=400)

    def handle_api_admin_logout(self):
        cookie = http.cookies.SimpleCookie()
        cookie[ADMIN_COOKIE_NAME] = ""
        cookie[ADMIN_COOKIE_NAME]["path"] = "/"
        cookie[ADMIN_COOKIE_NAME]["max-age"] = 0
        self.send_json({"status": "success"}, cookies=[cookie[ADMIN_COOKIE_NAME]])

    def handle_api_admin_sync(self, data):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        portal.sync_nft_with_active_ips(data)
        self.send_json({"status": "success"})

    def handle_api_admin_qr(self, form, data):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        code = form.get("code", "")
        if isinstance(code, list):
            code = code[0] if code else ""
        voucher = find_voucher(data, str(code))
        if not voucher:
            self.send_error_json("Voucher không tồn tại.", status=404)
            return
        if voucher.get("used"):
            self.send_error_json("Voucher đã được kích hoạt, không thể phát lại QR.", status=409)
            return

        token = secrets.token_urlsafe(32)
        expires_at = timestamp_now() + QR_TOKEN_TTL
        data.setdefault("qr_tokens", {})[hash_qr_token(token)] = {
            "voucher_code": voucher["code"],
            "created_at": timestamp_now(),
            "expires_at": expires_at,
            "used": False,
        }
        save_data(data)
        query = urllib.parse.urlencode({"token": token})
        activation_url = f"http://10.10.10.1/activate?{query}"
        try:
            import qrcode
            qr = qrcode.make(activation_url)
            output = io.BytesIO()
            qr.save(output, format="PNG")
            qr_data_url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        except (ImportError, OSError):
            self.send_error_json("Chưa cài thư viện tạo QR. Hãy chạy pip install -r requirements.txt.", status=500)
            return
        self.send_json({"status": "success", "code": voucher["code"], "expires_at": expires_at, "activation_url": activation_url, "qr_data_url": qr_data_url})

    def handle_api_admin_send_voucher(self, form, data):
        if not authorized(self):
            self.send_error_json("Unauthorized", status=401)
            return
        device_id = self._safe_message_text(form.get("device_id", ""))
        ip = self._safe_message_text(form.get("ip", ""))
        package_key = self._safe_message_text(form.get("package_key", "time_1h"))
        if not device_id:
            self.send_error_json("Thiếu device_id của user.", status=400)
            return
        voucher = portal.create_voucher(data, package_key) if package_key in VOUCHER_PACKAGES else None
        if not voucher:
            self.send_error_json("Gói voucher không hợp lệ.", status=400)
            return
        pending = add_pending_activation(data, device_id, ip, voucher["code"], f"Admin đã gửi cho bạn voucher {voucher['code']}.")
        add_chat_message(data, "admin", device_id, f"Admin đã gửi voucher {voucher['code']}. Nhấn nút Kết nối Internet để kích hoạt ngay.")
        self.send_json({"status": "success", "voucher": voucher, "pending": pending})

    def handle_qr_redirect(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        token = (query.get("token") or [""])[0]
        if not token:
            self.redirect("/user")
            return
        self.redirect("/user?qr_token=" + urllib.parse.quote(token, safe=""))

    def handle_api_user_qr_activate(self, form, data):
        token = form.get("token", "")
        device_id = form.get("device_id", "")
        if isinstance(token, list):
            token = token[0] if token else ""
        if isinstance(device_id, list):
            device_id = device_id[0] if device_id else ""
        token_record = data.get("qr_tokens", {}).get(hash_qr_token(str(token)))
        if not token_record or token_record.get("used") or token_record.get("expires_at", 0) < timestamp_now():
            self.send_error_json("QR không hợp lệ hoặc đã hết hạn.", status=410)
            return
        result = portal.activate_voucher(self.client_address[0], token_record["voucher_code"], data, device_id=device_id)
        status_code = result.pop("status_code", 200)
        if result.get("status") == "success":
            token_record["used"] = True
            token_record["used_at"] = timestamp_now()
            token_record["used_ip"] = self.client_address[0]
            save_data(data)
        self.send_json(result, status=status_code)

    def handle_api_user_activate_pending(self, form, data):
        device_id = self._safe_message_text(form.get("device_id", ""))
        if not device_id:
            device_id = self._safe_message_text(self.headers.get("X-Device-Id", ""))
        pending = get_pending_activation(data, device_id=device_id, ip=self.client_address[0])
        if not pending:
            self.send_error_json("Bạn chưa có voucher nào được admin gửi.", status=404)
            return
        result = portal.activate_voucher(self.client_address[0], pending["voucher_code"], data, device_id=device_id)
        if result.get("status") == "success":
            clear_pending_activation(data, device_id=device_id, ip=self.client_address[0])
            add_chat_message(data, "system", device_id, f"Voucher {pending['voucher_code']} đã được kích hoạt thành công.")
        self.send_json(result)


class ThreadedTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server(port: int = 8080) -> None:
    server_address = ("", port)
    with ThreadedTCPServer(server_address, CaptivePortalHandler) as httpd:
        print(f"Server listening on port {port}")
        httpd.serve_forever()