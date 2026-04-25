"""IBKR Flex Web Service auto-importer.

Setup (in IBKR Account Management → Performance & Reports → Flex Queries):

  1. Create a Flex Query with these sections enabled:
       Trades  →  Order Type, Symbol, Quantity, TradePrice, TradeDate,
                  TradeTime, Buy/Sell, Commission, IBOrderID
     Output format: XML
     Period: "Today" or "Last 7 Days"

  2. Note the Query ID (number).
  3. Generate a Flex Web Service token under Configuration → Flex Web Service.
  4. Put both in .env:
       IBKR_FLEX_TOKEN=...
       IBKR_FLEX_QUERY_ID=...

Run:  python -m src.cli ibkr-import
This pulls today's fills and inserts any not already in the journal.
Buy fills become long entries; Sell fills close them. Multi-leg fills are
combined per IBOrderID.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import CONFIG
from .journal import Trade, _engine

REQUEST_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
GET_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"


@dataclass
class FlexFill:
    order_id: str
    symbol: str
    side: str            # 'BOT' | 'SLD'
    quantity: int
    price: float
    commission: float
    fill_time: datetime


def _request_statement() -> Optional[str]:
    if not CONFIG.ibkr_flex_token or not CONFIG.ibkr_flex_query_id:
        raise RuntimeError("IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID must be set in .env")
    r = requests.get(REQUEST_URL, params={
        "v": 3,
        "t": CONFIG.ibkr_flex_token,
        "q": CONFIG.ibkr_flex_query_id,
    }, timeout=20)
    root = ET.fromstring(r.text)
    if root.findtext("Status") != "Success":
        raise RuntimeError(f"IBKR Flex request failed: {r.text[:300]}")
    return root.findtext("ReferenceCode")


def _retrieve_statement(reference: str, retries: int = 6) -> str:
    for i in range(retries):
        r = requests.get(GET_URL, params={
            "v": 3,
            "t": CONFIG.ibkr_flex_token,
            "q": reference,
        }, timeout=30)
        if r.text.startswith("<FlexStatement") or "<FlexStatements" in r.text:
            return r.text
        # Often returns "Statement generation in progress" — retry
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"Statement {reference} not ready after {retries} retries")


def _parse_fills(xml_text: str) -> list[FlexFill]:
    root = ET.fromstring(xml_text)
    fills: list[FlexFill] = []
    for trade in root.iter("Trade"):
        order_id = trade.get("ibOrderID") or trade.get("orderID") or ""
        symbol = trade.get("symbol", "")
        side = trade.get("buySell", "")
        try:
            qty = int(float(trade.get("quantity", "0")))
            price = float(trade.get("tradePrice", "0"))
            commission = abs(float(trade.get("ibCommission", "0") or 0))
        except (TypeError, ValueError):
            continue
        date_s = trade.get("tradeDate", "")
        time_s = trade.get("tradeTime", "")
        try:
            fill_time = datetime.strptime(f"{date_s} {time_s}", "%Y%m%d %H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            fill_time = datetime.now(timezone.utc)
        fills.append(FlexFill(order_id, symbol, side, abs(qty), price, commission, fill_time))
    return fills


def _existing_order_ids() -> set[str]:
    with Session(_engine) as s:
        rows = s.scalars(select(Trade.notes)).all()
    out: set[str] = set()
    for n in rows:
        if not n:
            continue
        for line in n.splitlines():
            if line.startswith("[ibkr_order_id]"):
                out.add(line.split("=", 1)[-1].strip())
    return out


def import_today() -> dict:
    reference = _request_statement()
    xml_text = _retrieve_statement(reference)
    fills = _parse_fills(xml_text)

    seen = _existing_order_ids()
    inserted = closed = skipped = 0

    with Session(_engine) as s:
        for f in fills:
            if f.order_id and f.order_id in seen:
                skipped += 1
                continue
            if f.side == "BOT":
                t = Trade(
                    symbol=f.symbol.upper(),
                    setup="ibkr_imported",
                    side="long",
                    entry_price=f.price,
                    stop_price=round(f.price * 0.95, 2),  # placeholder — user can edit
                    shares=f.quantity,
                    entry_time=f.fill_time,
                    fees=f.commission,
                    notes=f"[ibkr_order_id]={f.order_id}",
                )
                s.add(t)
                inserted += 1
            elif f.side == "SLD":
                # Find latest open trade for this symbol
                open_t = s.scalars(
                    select(Trade)
                    .where(Trade.symbol == f.symbol.upper())
                    .where(Trade.exit_price.is_(None))
                    .order_by(Trade.entry_time.desc())
                ).first()
                if open_t is None:
                    skipped += 1
                    continue
                open_t.exit_price = f.price
                open_t.exit_time = f.fill_time
                open_t.fees = (open_t.fees or 0) + f.commission
                open_t.notes = (open_t.notes or "") + f"\n[ibkr_order_id_exit]={f.order_id}"
                closed += 1
        s.commit()
    return {"inserted": inserted, "closed": closed, "skipped": skipped, "total_fills": len(fills)}
