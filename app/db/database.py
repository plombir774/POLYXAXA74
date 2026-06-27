from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS watched_markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watched_market_id INTEGER NOT NULL,
    yes_price REAL NULL,
    no_price REAL NULL,
    volume REAL NULL,
    liquidity REAL NULL,
    spread REAL NULL,
    end_date DATETIME NULL,
    raw_json TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (watched_market_id) REFERENCES watched_markets(id)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watched_market_id INTEGER NOT NULL,
    signal_score INTEGER NOT NULL,
    risk_score INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (watched_market_id) REFERENCES watched_markets(id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_market_created
ON market_snapshots(watched_market_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_signals_market_created
ON signals(watched_market_id, created_at DESC);

CREATE TABLE IF NOT EXISTS opportunity_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    question TEXT NOT NULL,
    category TEXT NOT NULL,
    prediction_timestamp DATETIME NOT NULL,
    market_probability REAL NOT NULL,
    fair_probability_mid REAL NOT NULL,
    edge_estimate REAL NOT NULL,
    quality_score INTEGER NOT NULL,
    confidence_score INTEGER NOT NULL,
    risk_score INTEGER NOT NULL,
    resolution_timestamp DATETIME NULL,
    actual_outcome TEXT NOT NULL DEFAULT 'UNKNOWN'
);

CREATE INDEX IF NOT EXISTS idx_opportunity_history_category
ON opportunity_history(category, prediction_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_opportunity_history_outcome
ON opportunity_history(actual_outcome, prediction_timestamp DESC);
"""


def connect(database_path: str) -> sqlite3.Connection:
    if database_path != ":memory:":
        Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: str) -> None:
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
        connection.commit()
