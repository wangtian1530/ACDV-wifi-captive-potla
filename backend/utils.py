import re
import time
from datetime import datetime
from typing import Optional


def timestamp_now() -> int:
    return int(time.time())


def format_timestamp(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def is_valid_ipv4(address: str) -> bool:
    pattern = re.compile(r"^(25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)){3}$")
    return bool(pattern.match(address))
