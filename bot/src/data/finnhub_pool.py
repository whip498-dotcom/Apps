"""Round-robin pool of Finnhub clients.

Each free Finnhub API key gets 60 calls/min. Holding several keys and
rotating through them lets the scanner check catalysts/short interest
on the whole universe per scan iteration without tripping rate limits
on the back half of the alphabet.

Usage:
    client = next_client()
    if client is None:
        return  # no keys configured
    client.company_news(...)
"""
from __future__ import annotations

import itertools
import threading
from typing import Optional

import finnhub

from ..config import CONFIG

_lock = threading.Lock()
_clients: list[finnhub.Client] = [finnhub.Client(api_key=k) for k in CONFIG.finnhub_keys]
_cycle = itertools.cycle(_clients) if _clients else None


def has_keys() -> bool:
    return bool(_clients)


def key_count() -> int:
    return len(_clients)


def next_client() -> Optional[finnhub.Client]:
    """Return the next client in the rotation, or None if no keys configured."""
    if _cycle is None:
        return None
    with _lock:
        return next(_cycle)
