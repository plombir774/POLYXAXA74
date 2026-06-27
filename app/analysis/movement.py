from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.polymarket.schemas import MarketData


@dataclass(frozen=True)
class SnapshotMovement:
    change_1h: float | None
    change_6h: float | None
    change_24h: float | None

    @property
    def has_history(self) -> bool:
        return any(value is not None for value in (self.change_1h, self.change_6h, self.change_24h))


def calculate_snapshot_movement(
    current: MarketData,
    historical_snapshots: list[MarketData],
    *,
    now: datetime | None = None,
) -> SnapshotMovement:
    if current.yes_price is None:
        return SnapshotMovement(None, None, None)
    reference_time = now or datetime.now(UTC)
    reference_time = reference_time if reference_time.tzinfo else reference_time.replace(tzinfo=UTC)
    snapshots = [
        snapshot
        for snapshot in historical_snapshots
        if snapshot.yes_price is not None and snapshot.updated_at is not None
    ]
    return SnapshotMovement(
        change_1h=_change_since(current, snapshots, reference_time, hours=1),
        change_6h=_change_since(current, snapshots, reference_time, hours=6),
        change_24h=_change_since(current, snapshots, reference_time, hours=24),
    )


def _change_since(
    current: MarketData,
    snapshots: list[MarketData],
    reference_time: datetime,
    *,
    hours: int,
) -> float | None:
    cutoff = reference_time - timedelta(hours=hours)
    eligible = []
    for snapshot in snapshots:
        updated_at = snapshot.updated_at
        if updated_at is None:
            continue
        updated_at = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC)
        if updated_at <= cutoff:
            eligible.append(snapshot)
    if not eligible:
        return None
    baseline = max(
        eligible,
        key=lambda snapshot: snapshot.updated_at
        if snapshot.updated_at and snapshot.updated_at.tzinfo
        else snapshot.updated_at.replace(tzinfo=UTC),  # type: ignore[union-attr]
    )
    if baseline.yes_price is None or current.yes_price is None:
        return None
    return current.yes_price - baseline.yes_price


def format_snapshot_movement(movement: SnapshotMovement) -> str:
    if not movement.has_history:
        return "Not enough history yet."
    parts = []
    for label, value in (
        ("1h", movement.change_1h),
        ("6h", movement.change_6h),
        ("24h", movement.change_24h),
    ):
        if value is not None:
            parts.append(f"{label}: {value:+.1%}")
    return " | ".join(parts)
