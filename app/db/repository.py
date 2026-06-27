from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from app.db.database import connect, initialize_database
from app.db.models import SignalHistoryRecord, SignalRecord, WatchedMarket
from app.polymarket.schemas import MarketData


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromisoformat(text.split(".")[0])


def _watch_from_row(row: sqlite3.Row) -> WatchedMarket:
    return WatchedMarket(
        id=int(row["id"]),
        market_id=str(row["market_id"]),
        slug=str(row["slug"]),
        title=str(row["title"]),
        url=str(row["url"]),
        created_at=_parse_datetime(row["created_at"]),
        is_active=bool(row["is_active"]),
    )


class MarketRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def init_schema(self) -> None:
        initialize_database(self.database_path)

    def ping(self) -> bool:
        try:
            with connect(self.database_path) as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def watch_count(self, active_only: bool = True) -> int:
        query = "SELECT COUNT(*) AS count FROM watched_markets"
        params: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE is_active = 1"
        with connect(self.database_path) as connection:
            row = connection.execute(query, params).fetchone()
        return int(row["count"])

    def add_watch(self, market: MarketData) -> WatchedMarket:
        with connect(self.database_path) as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO watched_markets (market_id, slug, title, url, is_active)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (market.market_id, market.slug, market.title, market.url),
                )
                watch_id = int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                connection.execute(
                    """
                    UPDATE watched_markets
                    SET market_id = ?, title = ?, url = ?, is_active = 1
                    WHERE slug = ?
                    """,
                    (market.market_id, market.title, market.url, market.slug),
                )
                row = connection.execute(
                    "SELECT id FROM watched_markets WHERE slug = ?",
                    (market.slug,),
                ).fetchone()
                watch_id = int(row["id"])
            connection.commit()
            row = connection.execute(
                "SELECT * FROM watched_markets WHERE id = ?",
                (watch_id,),
            ).fetchone()
            return _watch_from_row(row)

    def remove_watch(self, watch_id: int) -> bool:
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                "UPDATE watched_markets SET is_active = 0 WHERE id = ? AND is_active = 1",
                (watch_id,),
            )
            connection.commit()
            return cursor.rowcount > 0

    def list_watches(self, active_only: bool = True) -> list[WatchedMarket]:
        query = "SELECT * FROM watched_markets"
        params: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id ASC"
        with connect(self.database_path) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_watch_from_row(row) for row in rows]

    def find_watch_by_slug(self, slug: str) -> WatchedMarket | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM watched_markets WHERE slug = ? AND is_active = 1",
                (slug,),
            ).fetchone()
        return _watch_from_row(row) if row else None

    def add_snapshot(
        self,
        watch_id: int,
        market: MarketData,
        created_at: datetime | None = None,
    ) -> int:
        raw_json = json.dumps(market.raw, default=str)
        end_date = market.end_date.isoformat() if market.end_date else None
        with connect(self.database_path) as connection:
            values = (
                watch_id,
                market.yes_price,
                market.no_price,
                market.volume,
                market.liquidity,
                market.spread,
                end_date,
                raw_json,
            )
            if created_at is None:
                cursor = connection.execute(
                    """
                    INSERT INTO market_snapshots (
                        watched_market_id, yes_price, no_price, volume, liquidity,
                        spread, end_date, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO market_snapshots (
                        watched_market_id, yes_price, no_price, volume, liquidity,
                        spread, end_date, raw_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values, created_at.isoformat()),
                )
            connection.commit()
            return int(cursor.lastrowid)

    def _snapshot_from_row(self, row: sqlite3.Row) -> MarketData:
        raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        return MarketData(
            market_id=row["market_id"],
            slug=row["slug"],
            title=row["title"],
            url=row["url"],
            description=raw.get("description"),
            yes_price=row["yes_price"],
            no_price=row["no_price"],
            volume=row["volume"],
            volume_24hr=raw.get("volume24hr"),
            liquidity=row["liquidity"],
            spread=row["spread"],
            end_date=row["end_date"],
            start_date=raw.get("startDateIso") or raw.get("startDate") or raw.get("createdAt"),
            updated_at=row["created_at"],
            active=bool(raw.get("active", True)) and not bool(raw.get("closed", False)),
            raw=raw,
        )

    def get_latest_snapshot(self, watch_id: int) -> MarketData | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT s.*, w.market_id, w.slug, w.title, w.url
                FROM market_snapshots s
                JOIN watched_markets w ON w.id = s.watched_market_id
                WHERE s.watched_market_id = ?
                ORDER BY s.created_at DESC, s.id DESC
                LIMIT 1
                """,
                (watch_id,),
            ).fetchone()
        if not row:
            return None
        return self._snapshot_from_row(row)

    def list_recent_snapshots(self, watch_id: int, limit: int = 200) -> list[MarketData]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT s.*, w.market_id, w.slug, w.title, w.url
                FROM market_snapshots s
                JOIN watched_markets w ON w.id = s.watched_market_id
                WHERE s.watched_market_id = ?
                ORDER BY s.created_at DESC, s.id DESC
                LIMIT ?
                """,
                (watch_id, limit),
            ).fetchall()
        return [self._snapshot_from_row(row) for row in rows]

    def add_signal(
        self,
        watch_id: int,
        signal_score: int,
        risk_score: int,
        verdict: str,
        reason: str,
    ) -> int:
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO signals (
                    watched_market_id, signal_score, risk_score, verdict, reason
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (watch_id, signal_score, risk_score, verdict, reason),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_signals(self, watch_id: int, limit: int = 10) -> list[SignalRecord]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM signals
                WHERE watched_market_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (watch_id, limit),
            ).fetchall()
        return [
            SignalRecord(
                id=int(row["id"]),
                watched_market_id=int(row["watched_market_id"]),
                signal_score=int(row["signal_score"]),
                risk_score=int(row["risk_score"]),
                verdict=str(row["verdict"]),
                reason=str(row["reason"]),
                created_at=_parse_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def list_recent_signals(self, limit: int = 10) -> list[SignalHistoryRecord]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    s.id,
                    s.watched_market_id,
                    s.signal_score,
                    s.risk_score,
                    s.verdict,
                    s.reason,
                    s.created_at,
                    w.title AS market_title
                FROM signals s
                JOIN watched_markets w ON w.id = s.watched_market_id
                ORDER BY s.created_at DESC, s.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            SignalHistoryRecord(
                id=int(row["id"]),
                watched_market_id=int(row["watched_market_id"]),
                market_title=str(row["market_title"]),
                signal_score=int(row["signal_score"]),
                risk_score=int(row["risk_score"]),
                verdict=str(row["verdict"]),
                reason=str(row["reason"]),
                created_at=_parse_datetime(row["created_at"]),
            )
            for row in rows
        ]
