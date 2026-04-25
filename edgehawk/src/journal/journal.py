"""SQLite-backed trade journal.

The whole point: log every trade with a setup tag and let the stats
module tell you which setups actually pay you. After 50+ trades you'll
have actionable data on what to size up and what to cut.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session

from ..config import CONFIG


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    setup = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False, default="long")
    entry_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    shares = Column(Integer, nullable=False)
    entry_time = Column(DateTime, nullable=False)
    exit_time = Column(DateTime, nullable=True)
    catalyst = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    fees = Column(Float, nullable=False, default=0.0)


_engine = create_engine(f"sqlite:///{CONFIG.db_path}", future=True)
Base.metadata.create_all(_engine)


def log_entry(
    symbol: str,
    setup: str,
    entry_price: float,
    stop_price: float,
    shares: int,
    catalyst: Optional[str] = None,
    notes: Optional[str] = None,
    side: str = "long",
) -> int:
    with Session(_engine) as s:
        t = Trade(
            symbol=symbol.upper(),
            setup=setup,
            side=side,
            entry_price=entry_price,
            stop_price=stop_price,
            shares=shares,
            entry_time=datetime.now(timezone.utc),
            catalyst=catalyst,
            notes=notes,
        )
        s.add(t)
        s.commit()
        return t.id


def log_exit(trade_id: int, exit_price: float, fees: float = 0.0, notes: Optional[str] = None) -> None:
    with Session(_engine) as s:
        t = s.get(Trade, trade_id)
        if t is None:
            raise ValueError(f"Trade {trade_id} not found")
        t.exit_price = exit_price
        t.exit_time = datetime.now(timezone.utc)
        t.fees = fees
        if notes:
            t.notes = (t.notes or "") + f"\n[exit] {notes}"
        s.commit()


def open_trades() -> list[Trade]:
    with Session(_engine) as s:
        return list(s.scalars(select(Trade).where(Trade.exit_price.is_(None))))


def all_trades() -> list[Trade]:
    with Session(_engine) as s:
        return list(s.scalars(select(Trade).order_by(Trade.entry_time.desc())))


@dataclass
class TradePnL:
    trade: Trade
    pnl: float
    r_multiple: float
    pct_return: float


def trade_pnl(t: Trade) -> Optional[TradePnL]:
    if t.exit_price is None:
        return None
    direction = 1 if t.side == "long" else -1
    pnl_per_share = (t.exit_price - t.entry_price) * direction
    pnl = pnl_per_share * t.shares - t.fees
    risk_per_share = abs(t.entry_price - t.stop_price)
    r = pnl_per_share / risk_per_share if risk_per_share else 0.0
    pct = pnl_per_share / t.entry_price * 100.0 if t.entry_price else 0.0
    return TradePnL(trade=t, pnl=pnl, r_multiple=r, pct_return=pct)
