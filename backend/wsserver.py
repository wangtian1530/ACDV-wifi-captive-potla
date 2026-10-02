"""WebSocket server: kênh realtime thay thế HTTP polling/requests.
- Client (user/admin) kết nối tới WS, server nhận IP + device_id + role.
- Server track các connection theo IP, định kỳ push trạng thái user.
- Hỗ trợ toàn bộ nghiệp vụ qua WebSocket: chat, kích hoạt thẻ, gửi voucher,
  đăng nhập admin, dashboard admin — tránh dùng HTTP request.
- Vẫn giữ broadcast_chat_message (được HTTP server gọi) cho tương thích.
"""
import asyncio
import base64
import io
import json
import secrets
import threading
import urllib.parse

import websockets

from .auth import create_admin_session
from .config import ADMIN_PASSWORD, QR_TOKEN_TTL
from .data import (
    add_chat_message,
    add_pending_activation,
    clear_pending_activation,
    find_voucher,
    get_pending_activation,
    hash_qr_token,
    save_data,
)
from .portal import Portal
from .utils import timestamp_now
from .config import WS_PORT
from . import chat_store

# { ip: set_of_websocket }
_connections: dict = {}
# { websocket: device_id }
_device_ids: dict = {}
# { websocket: role } — role: user/admin
_roles: dict = {}
# { websocket: bool } — admin đã đăng nhập (session) trên kết nối này
_admin_authed: dict = {}
_portal = Portal()
_lock = threading.Lock()


def _client_ip(websocket) -> str:
    try:
        return websocket.remote_address[0]
    except Exception:
        return ""


def _device_id(websocket) -> str:
    return _device_ids.get(websocket, "")


def _client_role(websocket) -> str:
    return _roles.get(websocket, "user")


def _resolve_device_for_ip(ip: str) -> str:
    """Tìm device_id của user đang kết nối tại một IP."""
    if not ip:
        return ""
    with _lock:
        conns = _connections.get(ip)
        if not conns:
            return ""
        for ws in list(conns):
            if _roles.get(ws) != "admin" and _device_ids.get(ws):
                return _device_ids[ws]
    return "" 


def _is_admin(websocket) -> bool:
    return bool(_admin_authed.get(websocket, False))


async def _send(websocket, payload: dict) -> None:
    try:
        await websocket.send(json.dumps(payload, ensure_ascii=False))
    except Exception:
        _remove_connection(websocket)


async def _respond(websocket, request_id, ok=True, **payload) -> None:
    base = {"type": "response", "id": request_id or "", "ok": ok}
    base.update(payload)
    await _send(websocket, base)


async def _send_status(websocket, ip: str) -> None:
    try:
        did = _device_id(websocket)
        role = _client_role(websocket)

        def _compute():
            data = _portal.load_data()
            if role == "admin":
                return {"type": "admin_status_refresh", "authorized": bool(_is_admin(websocket))}
            return _portal.get_user_status_payload(ip, data, device_id=did)

        payload = await asyncio.to_thread(_compute)
        await websocket.send(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


async def _push_status_loop() -> None:
    from .config import WS_PUSH_INTERVAL

    while True:
        await asyncio.sleep(WS_PUSH_INTERVAL)
        targets = []
        with _lock:
            for ip, conns in list(_connections.items()):
                for ws in list(conns):
                    targets.append((ws, ip))
        for ws, ip in targets:
            try:
                asyncio.get_running_loop().create_task(_send_status(ws, ip))
            except Exception:
                _remove_connection(ws)


def _remove_connection(websocket) -> None:
    with _lock:
        _device_ids.pop(websocket, None)
        _roles.pop(websocket, None)
        _admin_authed.pop(websocket, None)
        ip = _client_ip(websocket)
        conns = _connections.get(ip)
        if conns:
            conns.discard(websocket)
            if not conns:
                _connections.pop(ip, None)


async def _broadcast_chat(payload: dict, device_id: str = "", sender_role: str = "user") -> None:
    with _lock:
        targets = list(_device_ids.keys())
    for ws in targets:
        try:
            ws_role = _client_role(ws)
            ws_device = _device_id(ws)
            if sender_role == "user":
                if (ws_role == "admin" and _is_admin(ws)) or ws_device == device_id:
                    await ws.send(json.dumps(payload, ensure_ascii=False))
            else:
                if ws_role == "user" and ws_device == device_id:
                    await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            _remove_connection(ws)


def broadcast_chat_message(payload: dict, device_id: str = "", sender_role: str = "user") -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_broadcast_chat(payload, device_id=device_id, sender_role=sender_role))
        return
    loop = asyncio.get_running_loop()
    loop.create_task(_broadcast_chat(payload, device_id=device_id, sender_role=sender_role))


def _get_chat_rows(data, device_id: str = "", ip: str = "") -> list:
    rows = chat_store.list_all()
    if device_id:
        rows = [m for m in rows if m.get("peer") == device_id]
    elif ip:
        rows = [m for m in rows if m.get("ip") == ip]
    return rows[-30:]


def _process_admin_dashboard():
    from .config import VOUCHER_PACKAGES
    data = _portal.load_data()
    _portal.cleanup_expired(data)
    vouchers = data.get("vouchers", [])
    active_ips = data.get("active_ips", [])
    packages = [
        {
            "key": key,
            "label": c["label"],
            "type": c["type"],
            "unit": c["unit"],
            "value": c["value"],
            "price": c["price"],
        }
        for key, c in VOUCHER_PACKAGES.items()
    ]
    devices = _portal.get_connected_devices()
    analysis = _portal.get_admin_analysis(data)
    ip_analysis = analysis.get("ip_analysis", [])
    created_value = sum(int(v.get("price", 0) or 0) for v in vouchers)
    activated_vouchers = [v for v in vouchers if v.get("used")]
    estimated_revenue = sum(int(v.get("price", 0) or 0) for v in activated_vouchers)
    voucher_usage_by_ip = []
    voucher_by_code = {v.get("code"): v for v in vouchers}
    for item in ip_analysis:
        voucher_codes = item.get("voucher_codes", [])
        spent = sum(int(voucher_by_code.get(c, {}).get("price", 0) or 0) for c in voucher_codes)
        voucher_usage_by_ip.append({
            "ip": item.get("ip", "-"),
            "voucher_count": len(voucher_codes),
            "voucher_codes": voucher_codes,
            "spent": spent,
            "consuming": item.get("consuming", False),
        })
    voucher_usage_by_ip.sort(key=lambda x: x["voucher_count"], reverse=True)
    return {
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
        "chat_messages": chat_store.list_all(),
        "admin_chat_unread": chat_store.admin_unread_total(),
        "chat_conversations": chat_store.conversations(),
        "pending_activations": data.get("pending_activations", {}),
    }


def _admin_create_qr(code: str) -> dict:
    data = _portal.load_data()
    voucher = find_voucher(data, str(code))
    if not voucher:
        raise ValueError("Voucher không tồn tại.")
    if voucher.get("used"):
        raise ValueError("Voucher đã được kích hoạt, không thể phát lại QR.")
    token = secrets.token_urlsafe(32)
    expires_at = timestamp_now() + QR_TOKEN_TTL
    data.setdefault("qr_tokens", {})[hash_qr_token(token)] = {
        "voucher_code": voucher["code"],
        "created_at": timestamp_now(),
        "expires_at": expires_at,
        "used": False,
    }
    save_data(data)
    activation_url = "http://10.10.10.1/activate?token=" + urllib.parse.quote(token, safe="")
    import qrcode
    qr = qrcode.make(activation_url)
    output = io.BytesIO()
    qr.save(output, format="PNG")
    qr_data_url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    return {"code": voucher["code"], "expires_at": expires_at, "activation_url": activation_url, "qr_data_url": qr_data_url}


def _admin_ops(websocket, msg) -> dict:
    mtype = msg.get("type")
    if mtype == "admin_dashboard":
        return {"dashboard": _process_admin_dashboard()}
    if mtype == "admin_create_voucher":
        package_key = str(msg.get("package_key") or "")
        voucher = _portal.create_voucher(_portal.load_data(), package_key)
        if not voucher:
            raise ValueError("Gói không hợp lệ.")
        return {"voucher": voucher, "dashboard": _process_admin_dashboard()}
    if mtype == "admin_revoke":
        target_ip = str(msg.get("ip") or "")
        if not _portal.revoke_ip(_portal.load_data(), target_ip):
            raise ValueError("Không thể thu hồi IP.")
        return {"dashboard": _process_admin_dashboard()}
    if mtype == "admin_send_voucher":
        from .config import VOUCHER_PACKAGES
        device_id = str(msg.get("device_id") or "")
        target_ip = str(msg.get("ip") or "")
        package_key = str(msg.get("package_key") or "time_1h")
        data = _portal.load_data()
        voucher = _portal.create_voucher(data, package_key) if package_key in VOUCHER_PACKAGES else None
        if not voucher:
            raise ValueError("Gói voucher không hợp lệ.")
        add_pending_activation(data, device_id, target_ip, voucher["code"],
                               f"Admin đã gửi cho bạn voucher {voucher['code']}.")
        peer_key = device_id or target_ip or "unknown"
        chat_store.add_message("admin", peer_key,
                               f"Admin đã gửi voucher {voucher['code']}. Nhấn nút Kết nối Internet để kích hoạt ngay.",
                               ip=target_ip)
        return {"voucher": voucher, "peer": peer_key, "dashboard": _process_admin_dashboard()}
    if mtype == "admin_qr":
        code = str(msg.get("code") or "")
        return {"qr": _admin_create_qr(code)}
    if mtype == "admin_nft_sync":
        data = _portal.load_data()
        _portal.sync_nft_with_active_ips(data)
        return {"dashboard": _process_admin_dashboard()}
    return {}


async def _send_dashboard_to_admin(websocket) -> None:
    try:
        dashboard = await asyncio.to_thread(_process_admin_dashboard)
        await _respond(websocket, "init", True, type="admin_dashboard", dashboard=dashboard)
    except Exception:
        pass


async def _handle_message(websocket, msg: dict) -> None:
    mtype = msg.get("type", "")
    ip = _client_ip(websocket)
    role = _client_role(websocket)

    if mtype == "admin_login":
        password = str(msg.get("password") or "")
        if ADMIN_PASSWORD and password == ADMIN_PASSWORD:
            create_admin_session()
            _admin_authed[websocket] = True
            _roles[websocket] = "admin"
            await _respond(websocket, msg.get("id"), True, status="success", authorized=True)
            await _send_dashboard_to_admin(websocket)
        else:
            await _respond(websocket, msg.get("id"), False, message="Sai mật khẩu.", status="error")
        return

    if mtype == "admin_logout":
        _admin_authed[websocket] = False
        _roles[websocket] = "user"
        await _respond(websocket, msg.get("id"), True, status="success")
        return

    if role == "admin" and mtype in ("admin_dashboard", "admin_create_voucher", "admin_revoke",
                                     "admin_send_voucher", "admin_qr", "admin_nft_sync"):
        if not _is_admin(websocket):
            await _respond(websocket, msg.get("id"), False, message="Unauthorized.", status=401)
            return
        try:
            result = await asyncio.to_thread(_admin_ops, websocket, msg)
        except ValueError as e:
            await _respond(websocket, msg.get("id"), False, message=str(e))
            return
        except Exception as e:
            await _respond(websocket, msg.get("id"), False, message=str(e))
            return
        await _respond(websocket, msg.get("id"), True, **result)
        if mtype == "admin_send_voucher" and result.get("peer"):
            pmsg = {"type": "chat_new", "message": None, "peer": result.get("peer"),
                    "user_unread": chat_store.peer_unread(result.get("peer")),
                    "voucher_notice": True}
            await _broadcast_chat(pmsg, device_id=result.get("peer"), sender_role="admin")
        return

    if mtype in ("chat", "chat_send"):
        # Gửi tin nhắn. User gửi tới admin; Admin gửi tới 1 peer (device_id/ip).
        text = str(msg.get("text") or "").strip()
        sender_role = msg.get("sender") or role
        msg_peer = ""
        msg_ip = ""
        if sender_role == "admin":
            if not _is_admin(websocket):
                await _respond(websocket, msg.get("id"), False, message="Unauthorized.", status=401)
                return
            msg_peer = str(msg.get("peer") or "")
            msg_ip = str(msg.get("ip") or "")
            if not msg_peer:
                msg_peer = _resolve_device_for_ip(msg_ip)
            if not msg_peer and msg_ip:
                # vẫn coi IP như peer của hội thoại
                msg_peer = msg_ip
        elif sender_role == "user":
            # User chỉ được chat trong hội thoại của chính mình (peer = device_id/ip) — chống giả mạo.
            msg_peer = str(_device_id(websocket) or "")
            msg_ip = ip
            if not msg_peer:
                msg_peer = msg_ip or "unknown"
        else:
            # sender=system hoặc không xác định: chỉ admin authed mới cho phép
            if not _is_admin(websocket):
                await _respond(websocket, msg.get("id"), False, message="Unauthorized.", status=401)
                return
            msg_peer = str(msg.get("peer") or _device_id(websocket) or ip or "unknown")
            msg_ip = ip
        if not text:
            await _respond(websocket, msg.get("id"), False, message="Nội dung không được để trống.")
            return

        saved = await asyncio.to_thread(chat_store.add_message, sender_role, msg_peer, text, msg_ip)
        if not saved:
            await _respond(websocket, msg.get("id"), False, message="Không thể lưu tin nhắn.")
            return
        # Broadcast realtime
        if sender_role == "admin":
            # gửi tới user có device_id == peer (nếu peer là device_id)
            payload = {"type": "chat_new", "message": saved, "peer": msg_peer,
                       "user_unread": chat_store.peer_unread(msg_peer)}
            await _broadcast_chat(payload, device_id=msg_peer, sender_role="admin")
        else:
            payload = {"type": "chat_new", "message": saved, "peer": msg_peer,
                       "admin_unread": await asyncio.to_thread(chat_store.admin_unread_total)}
            await _broadcast_chat(payload, device_id=msg_peer, sender_role="user")
        await _respond(websocket, msg.get("id"), True, status="success", message=saved)
        return

    if mtype in ("chat_get", "chat_init"):
        # User: luôn dùng peer = device_id/ip của chính mình (không cho tự chọn peer).
        own_peer = str(_device_id(websocket) or "")
        if role != "admin":
            if not own_peer:
                own_peer = ip or "unknown"
            rows = await asyncio.to_thread(chat_store.list_peer, own_peer)
            unread = await asyncio.to_thread(chat_store.peer_unread, own_peer)
            await _respond(websocket, msg.get("id"), True, messages=rows, unread=unread, peer=own_peer)
            return
        # Admin: trả tất cả hội thoại + unread
        own_peer = str(msg.get("peer") or "")
        if own_peer:
            rows = await asyncio.to_thread(chat_store.list_peer, own_peer)
            unread = await asyncio.to_thread(chat_store.peer_unread, own_peer)
            await _respond(websocket, msg.get("id"), True, messages=rows, unread=unread, peer=own_peer)
            return
        convs = await asyncio.to_thread(chat_store.conversations)
        total_unread = await asyncio.to_thread(chat_store.admin_unread_total)
        await _respond(websocket, msg.get("id"), True, conversations=convs,
                       total_unread=total_unread, conversations_len=len(convs))
        return

    if mtype == "chat_list":
        if not _is_admin(websocket):
            await _respond(websocket, msg.get("id"), False, message="Unauthorized.", status=401)
            return
        convs = await asyncio.to_thread(chat_store.conversations)
        total_unread = await asyncio.to_thread(chat_store.admin_unread_total)
        await _respond(websocket, msg.get("id"), True, conversations=convs, total_unread=total_unread)
        return

    if mtype == "chat_open":
        # Admin mở hội thoại với 1 peer -> đánh dấu đã đọc + trả lịch sử.
        if not _is_admin(websocket):
            await _respond(websocket, msg.get("id"), False, message="Unauthorized.", status=401)
            return
        peer = str(msg.get("peer") or "")
        if not peer:
            await _respond(websocket, msg.get("id"), False, message="Thiếu peer.")
            return
        await asyncio.to_thread(chat_store.mark_admin_read, peer)
        rows = await asyncio.to_thread(chat_store.list_peer, peer)
        total_unread = await asyncio.to_thread(chat_store.admin_unread_total)
        await _respond(websocket, msg.get("id"), True, peer=peer, messages=rows, total_unread=total_unread)
        return

    if mtype == "chat_mark_read":
        # User mở/đang xem hội thoại -> đánh dấu tin admin đã đọc.
        own_peer = str(msg.get("peer") or _device_id(websocket) or (ip if role != "admin" else ""))
        if not own_peer:
            own_peer = ip or "unknown"
        await asyncio.to_thread(chat_store.mark_user_read, own_peer)
        await _respond(websocket, msg.get("id"), True, status="success")
        return

    if mtype == "activate":
        code = str(msg.get("code") or "").strip().upper()
        device_id = str(msg.get("device_id") or _device_id(websocket) or "")

        def _do():
            d = _portal.load_data()
            return _portal.activate_voucher(ip, code, d, device_id=device_id)

        result = await asyncio.to_thread(_do)
        await _respond(websocket, msg.get("id"), result.get("status") == "success", **result)
        return

    if mtype == "qr_activate":
        token = str(msg.get("token") or "")
        device_id = str(msg.get("device_id") or _device_id(websocket) or "")

        def _do_qr():
            d = _portal.load_data()
            token_record = d.get("qr_tokens", {}).get(hash_qr_token(token))
            if not token_record or token_record.get("used") or token_record.get("expires_at", 0) < timestamp_now():
                return {"status": "error", "message": "QR không hợp lệ hoặc đã hết hạn.", "status_code": 410}
            result = _portal.activate_voucher(ip, token_record["voucher_code"], d, device_id=device_id)
            if result.get("status") == "success":
                token_record["used"] = True
                token_record["used_at"] = timestamp_now()
                token_record["used_ip"] = ip
                save_data(d)
            return result

        result = await asyncio.to_thread(_do_qr)
        await _respond(websocket, msg.get("id"), result.get("status") == "success", **result)
        return

    if mtype == "portal_info":
        from .config import CONTACT_INFO, PORTAL_NAME
        await _respond(websocket, msg.get("id"), True,
                       portal_info={"portal_name": PORTAL_NAME, "contact": CONTACT_INFO})
        return

    if mtype == "pending_get":
        device_id = str(msg.get("device_id") or _device_id(websocket) or "")
        data = await asyncio.to_thread(_portal.load_data)
        pending = await asyncio.to_thread(get_pending_activation, data, device_id=device_id, ip=ip)
        await _respond(websocket, msg.get("id"), True, pending=pending)
        return

    if mtype == "activate_pending":
        device_id = str(msg.get("device_id") or _device_id(websocket) or "")
        data = await asyncio.to_thread(_portal.load_data)
        pending = await asyncio.to_thread(get_pending_activation, data, device_id=device_id, ip=ip)
        if not pending:
            await _respond(websocket, msg.get("id"), False, message="Bạn chưa có voucher nào được admin gửi.", status_code=404)
            return
        result = await asyncio.to_thread(_portal.activate_voucher, ip, pending["voucher_code"], data, device_id=device_id)
        if result.get("status") == "success":
            await asyncio.to_thread(clear_pending_activation, data, device_id=device_id, ip=ip)

            def _sysmsg():
                d = _portal.load_data()
                return add_chat_message(d, "system", device_id,
                                        f"Voucher {pending['voucher_code']} đã được kích hoạt thành công.", ip=ip)

            await asyncio.to_thread(_sysmsg)
        await _respond(websocket, msg.get("id"), result.get("status") == "success", **result)
        return


async def _handler(websocket) -> None:
    ip = _client_ip(websocket)
    with _lock:
        _connections.setdefault(ip, set()).add(websocket)

    is_first = True
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type", "")
            # Message đầu tiên (init) thiết lập device_id + role rồi gửi status.
            if is_first or mtype == "init":
                _device_ids[websocket] = str(msg.get("device_id") or msg.get("deviceId") or "")
                _roles[websocket] = str(msg.get("role") or "user")
                is_first = False
                # Gửi status nền (không chặn xử lý các request khác)
                asyncio.get_running_loop().create_task(_send_status(websocket, ip))
                continue
            try:
                await _handle_message(websocket, msg)
            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                await _respond(websocket, msg.get("id", ""), False, message=f"Lỗi: {e}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _remove_connection(websocket)


async def _run_server(host: str, port: int) -> None:
    async with websockets.serve(_handler, host, port):
        await _push_status_loop()


_server_thread = None


def start_ws_server() -> None:
    global _server_thread
    if _server_thread is not None and _server_thread.is_alive():
        return

    def _runner():
        try:
            asyncio.run(_run_server("0.0.0.0", WS_PORT))
        except Exception as e:
            print(f"⚠️ WebSocket server lỗi: {e}")

    _server_thread = threading.Thread(target=_runner, daemon=True)
    _server_thread.start()
    print(f"🛰️ WebSocket server đang chạy trên port {WS_PORT}")
    return _server_thread
