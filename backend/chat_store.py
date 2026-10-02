"""Kho lưu trữ tin nhắn chat riêng (tách khỏi acdv_data.json).
- File riêng: acdv_chat.json
- Mỗi hội thoại tổ chức theo "peer" (device_id của khách; admin là phía bên kia).
- Có khóa luồng + ghi atomic chống hỏng file.
- Theo dõi trạng thái "đã đọc" để gửi thông báo tin chưa đọc cho admin & user.
"""
import json
import os
import threading

from .config import CHAT_FILE
from .utils import timestamp_now

_lock = threading.RLock()


def _defaults() -> dict:
    return {"next_id": 1, "messages": []}


def _load() -> dict:
    with _lock:
        if not os.path.exists(CHAT_FILE):
            store = _defaults()
            _save(store)
            return store
        try:
            with open(CHAT_FILE, "r", encoding="utf-8") as f:
                store = json.load(f)
        except (json.JSONDecodeError, OSError):
            store = _defaults()
            _save(store)
            return store
        store.setdefault("next_id", 1)
        store.setdefault("messages", [])
        return store


def _save(store: dict) -> None:
    with _lock:
        tmp = CHAT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CHAT_FILE)


def add_message(sender: str, peer: str, text: str, ip: str = "") -> dict:
    """Thêm 1 tin nhắn. peer là device_id/ip của khách.
    sender: user/admin/system."""
    text = (text or "").strip()
    if not text:
        return {}
    with _lock:
        store = _load()
        msg = {
            "id": store["next_id"],
            "sender": sender,
            "peer": str(peer or "unknown"),
            "ip": str(ip or ""),
            "text": text,
            "ts": timestamp_now(),
            "read_by_admin": sender == "admin",
            "read_by_user": sender == "user",
        }
        store["next_id"] += 1
        store["messages"].append(msg)
        _save(store)
        return msg


def list_peer(peer: str) -> list:
    with _lock:
        store = _load()
    return [m for m in store["messages"] if m.get("peer") == peer]


def list_all() -> list:
    with _lock:
        store = _load()
    return list(store["messages"])


def conversations() -> list:
    """Nhóm tin nhắn theo peer cho admin."""
    with _lock:
        store = _load()
    groups = {}
    order = []
    for m in store["messages"]:
        p = m.get("peer") or "unknown"
        if p not in groups:
            groups[p] = {"peer": p, "ip": m.get("ip", ""), "last_text": "",
                         "last_ts": 0, "unread": 0, "messages": []}
            order.append(p)
        g = groups[p]
        g["messages"].append(m)
        if m.get("sender") == "user" and not m.get("read_by_admin"):
            g["unread"] += 1
        if not g["last_text"] or m.get("ts", 0) >= g.get("last_ts", 0):
            g["last_text"] = m.get("text", "")
            g["last_ts"] = m.get("ts", 0)
    result = [groups[p] for p in order]
    result.sort(key=lambda g: g.get("last_ts", 0), reverse=True)
    return result


def admin_unread_total() -> int:
    with _lock:
        store = _load()
    return sum(1 for m in store["messages"] if m.get("sender") == "user" and not m.get("read_by_admin"))


def peer_unread(peer: str) -> int:
    with _lock:
        store = _load()
    return sum(1 for m in store["messages"] if m.get("peer") == peer and m.get("sender") == "admin" and not m.get("read_by_user"))


def mark_admin_read(peer: str) -> None:
    with _lock:
        store = _load()
        if not peer:
            return
        changed = False
        for m in store["messages"]:
            if m.get("peer") == peer and m.get("sender") == "user" and not m.get("read_by_admin"):
                m["read_by_admin"] = True
                changed = True
        if changed:
            _save(store)


def mark_all_admin_read() -> None:
    with _lock:
        store = _load()
        changed = False
        for m in store["messages"]:
            if m.get("sender") == "user" and not m.get("read_by_admin"):
                m["read_by_admin"] = True
                changed = True
        if changed:
            _save(store)


def mark_user_read(peer: str) -> None:
    with _lock:
        store = _load()
        if not peer:
            return
        changed = False
        for m in store["messages"]:
            if m.get("peer") == peer and m.get("sender") == "admin" and not m.get("read_by_user"):
                m["read_by_user"] = True
                changed = True
        if changed:
            _save(store)
