#!/usr/bin/env python3
"""Unified media lineage and publication catalog for the Super* video skills."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
CONTRACT_VERSION = "supermedia.lineage/v1"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv"}
TERMINAL_PUBLICATION_STATES = {
    "draft_saved_unverified",
    "draft_saved_verified",
    "scheduled",
    "published",
    "status_unknown",
}
ACTIVE_PUBLICATION_STATES = TERMINAL_PUBLICATION_STATES | {"reserved", "uploading"}
ID_HEADERS = ("短码", "短码或媒体ID", "媒体ID", "media_id", "id")
URL_HEADERS = ("视频地址", "规范地址", "canonical_url", "url")
TITLE_HEADERS = ("标题", "title")
FILE_HEADERS = ("文件名", "filename", "file")
STATUS_HEADERS = ("状态", "status")
CREATOR_EXCLUDES = {"quarantine", "catalog", ".supermedia", "__pycache__"}
NAMESPACE = uuid.UUID("2e2fa026-2f0e-49c7-a95f-b8b2f1ae50f7")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, material: str) -> str:
    return f"{prefix}_{uuid.uuid5(NAMESPACE, material).hex[:24]}"


def normalize_platform(value: str) -> str:
    aliases = {
        "ig": "instagram",
        "yt": "youtube",
        "youtube-short": "youtube",
        "tt": "tiktok",
        "wechat": "tencent",
        "wechat-channels": "tencent",
        "视频号": "tencent",
    }
    normalized = value.strip().lower().replace(" ", "-")
    return aliases.get(normalized, normalized)


def creator_key(platform: str, creator: str) -> str:
    return f"{normalize_platform(platform)}:{creator.strip().lower()}"


def source_key(platform: str, native_id: str) -> str:
    return f"{normalize_platform(platform)}:{native_id.strip()}"


def instagram_id_from_filename(filename: str) -> str | None:
    """Recover the 11-character legacy Reel shortcode suffix without slug bleed."""
    stem = Path(filename).stem
    lowered = stem.lower()
    cut_positions = sorted({
        position
        for marker in (
            "__h264-aac", "__h264", "__fdash", "__dash", "__repair",
            "_h264-aac", "_h264", "_fdash", "_dash", "_repair",
        )
        if (position := lowered.rfind(marker)) >= 0
    }, reverse=True)
    cut_positions.append(len(stem))
    for position in cut_positions:
        prefix = stem[:position]
        if len(prefix) < 11:
            continue
        candidate = prefix[-11:]
        if re.fullmatch(r"D[A-Za-z0-9_-]{10}", candidate):
            return candidate
    return None


def first_value(row: dict[str, str], headers: Iterable[str]) -> str:
    for header in headers:
        value = row.get(header)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


class Catalog:
    def __init__(self, root: Path, db_path: Path | None = None):
        self.root = root.expanduser().resolve()
        self.state_dir = self.root / ".supermedia"
        self.db_path = (db_path or self.state_dir / "media_catalog.sqlite").expanduser().resolve()
        self.events_path = self.state_dir / "publication_events.jsonl"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS creators (
                creator_key TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                canonical_creator TEXT NOT NULL,
                profile_url TEXT,
                media_directory TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_posts (
                source_key TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                native_id TEXT NOT NULL,
                creator_key TEXT NOT NULL REFERENCES creators(creator_key),
                canonical_url TEXT,
                title TEXT,
                source_status TEXT,
                published_at TEXT,
                discovered_at TEXT,
                downloaded_at TEXT,
                metadata_path TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(platform, native_id)
            );
            CREATE TABLE IF NOT EXISTS creator_source_inventory (
                creator_key TEXT NOT NULL REFERENCES creators(creator_key),
                source_key TEXT NOT NULL REFERENCES source_posts(source_key),
                metadata_path TEXT NOT NULL,
                declared_filename TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY(creator_key, source_key)
            );
            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS asset_locations (
                path TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                role TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                present INTEGER NOT NULL DEFAULT 1,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS asset_lineage (
                asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                source_key TEXT NOT NULL REFERENCES source_posts(source_key),
                parent_asset_id TEXT REFERENCES assets(asset_id),
                relation TEXT NOT NULL,
                confidence REAL NOT NULL,
                lineage_status TEXT NOT NULL,
                batch_id TEXT,
                agent TEXT,
                receipt_path TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(asset_id, source_key)
            );
            CREATE TABLE IF NOT EXISTS transform_runs (
                transform_id TEXT PRIMARY KEY,
                job_id TEXT,
                batch_id TEXT,
                input_asset_id TEXT REFERENCES assets(asset_id),
                output_asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                source_key TEXT REFERENCES source_posts(source_key),
                operation TEXT,
                recipe_id TEXT,
                agent TEXT,
                status TEXT NOT NULL,
                receipt_path TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS target_accounts (
                account_key TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                account_name TEXT NOT NULL,
                UNIQUE(platform, account_name)
            );
            CREATE TABLE IF NOT EXISTS publications (
                publication_id TEXT PRIMARY KEY,
                plan_item_id TEXT,
                asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                source_key TEXT REFERENCES source_posts(source_key),
                account_key TEXT NOT NULL REFERENCES target_accounts(account_key),
                title TEXT,
                status TEXT NOT NULL,
                verification TEXT,
                manifest_path TEXT,
                first_recorded_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_publications_account_asset
                ON publications(account_key, asset_id, status);
            CREATE INDEX IF NOT EXISTS idx_publications_account_source
                ON publications(account_key, source_key, status);
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                account_key TEXT NOT NULL REFERENCES target_accounts(account_key),
                plan_item_id TEXT,
                manifest_path TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                released_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_active_reservation
                ON reservations(asset_id, account_key)
                WHERE status = 'active';
            CREATE TABLE IF NOT EXISTS warnings (
                warning_id TEXT PRIMARY KEY,
                severity TEXT NOT NULL,
                code TEXT NOT NULL,
                asset_id TEXT,
                source_key TEXT,
                account_key TEXT,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sync_runs (
                sync_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                platform TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                stats_json TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES('contract_version', ?)",
            (CONTRACT_VERSION,),
        )
        self.connection.commit()

    def event(self, event_type: str, payload: dict[str, Any]) -> None:
        event_id = stable_id("evt", f"{event_type}:{utc_now()}:{canonical_json(payload)}")
        self.connection.execute(
            "INSERT INTO events(event_id, event_type, occurred_at, payload_json) VALUES(?,?,?,?)",
            (event_id, event_type, utc_now(), canonical_json(payload)),
        )

    def refresh_event_log(self) -> None:
        rows = self.connection.execute(
            "SELECT event_id, event_type, occurred_at, payload_json FROM events ORDER BY occurred_at, event_id"
        ).fetchall()
        fd, temporary_name = tempfile.mkstemp(
            dir=self.state_dir, prefix=".publication-events.", suffix=".jsonl"
        )
        os.close(fd)
        temporary = Path(temporary_name)
        with temporary.open("w", encoding="utf-8") as handle:
            for row in rows:
                payload = {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "occurred_at": row["occurred_at"],
                    "payload": json.loads(row["payload_json"]),
                }
                handle.write(canonical_json(payload) + "\n")
        os.replace(temporary, self.events_path)

    def _asset_for_path(self, path: Path, role: str, *, force_hash: bool = False) -> str:
        path = path.expanduser().resolve()
        stat = path.stat()
        existing = self.connection.execute(
            "SELECT asset_id, size_bytes, mtime_ns FROM asset_locations WHERE path = ?",
            (str(path),),
        ).fetchone()
        if (
            existing
            and not force_hash
            and existing["size_bytes"] == stat.st_size
            and existing["mtime_ns"] == stat.st_mtime_ns
        ):
            self.connection.execute(
                "UPDATE asset_locations SET present=1, role=?, last_seen_at=? WHERE path=?",
                (role, utc_now(), str(path)),
            )
            return str(existing["asset_id"])
        digest = sha256_file(path)
        asset_id = f"asset_{digest[:24]}"
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO assets(asset_id, sha256, size_bytes, media_type, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(sha256) DO UPDATE SET
                size_bytes=excluded.size_bytes,
                updated_at=excluded.updated_at
            """,
            (asset_id, digest, stat.st_size, "video", now, now),
        )
        self.connection.execute(
            """
            INSERT INTO asset_locations(path, asset_id, role, size_bytes, mtime_ns, present, last_seen_at)
            VALUES(?,?,?,?,?,1,?)
            ON CONFLICT(path) DO UPDATE SET
                asset_id=excluded.asset_id,
                role=excluded.role,
                size_bytes=excluded.size_bytes,
                mtime_ns=excluded.mtime_ns,
                present=1,
                last_seen_at=excluded.last_seen_at
            """,
            (str(path), asset_id, role, stat.st_size, stat.st_mtime_ns, now),
        )
        return asset_id

    def resolve_asset(self, path: Path, *, register: bool = True) -> str | None:
        resolved = path.expanduser().resolve()
        row = self.connection.execute(
            "SELECT asset_id FROM asset_locations WHERE path=? AND present=1",
            (str(resolved),),
        ).fetchone()
        if row:
            stat = resolved.stat()
            cached = self.connection.execute(
                "SELECT size_bytes, mtime_ns FROM asset_locations WHERE path=?",
                (str(resolved),),
            ).fetchone()
            if cached and cached["size_bytes"] == stat.st_size and cached["mtime_ns"] == stat.st_mtime_ns:
                return str(row["asset_id"])
        if not register or not resolved.is_file():
            return None
        return self._asset_for_path(resolved, "untracked")

    def upsert_account(self, platform: str, account: str) -> str:
        platform = normalize_platform(platform)
        key = f"{platform}:{account.strip()}"
        self.connection.execute(
            """
            INSERT INTO target_accounts(account_key, platform, account_name)
            VALUES(?,?,?)
            ON CONFLICT(account_key) DO NOTHING
            """,
            (key, platform, account.strip()),
        )
        return key

    def source_keys_for_asset(self, asset_id: str) -> list[str]:
        return [
            str(row["source_key"])
            for row in self.connection.execute(
                "SELECT source_key FROM asset_lineage WHERE asset_id=? ORDER BY confidence DESC, source_key",
                (asset_id,),
            )
        ]

    def sync_platform(self, platform: str, *, include_derivatives: bool = True) -> dict[str, int]:
        platform = normalize_platform(platform)
        channel_root = self.root / platform
        if not channel_root.is_dir():
            raise ValueError(f"渠道目录不存在: {channel_root}")
        sync_id = stable_id("sync", f"{platform}:{utc_now()}")
        started = utc_now()
        self.connection.execute(
            "INSERT INTO sync_runs(sync_id, kind, platform, started_at, status) VALUES(?,?,?,?,?)",
            (sync_id, "full_catalog_sync", platform, started, "running"),
        )
        stats = {
            "creators": 0,
            "sources": 0,
            "source_assets": 0,
            "derivative_assets": 0,
            "lineage_exact_or_inferred": 0,
            "lineage_hold": 0,
        }
        self.connection.execute(
            "UPDATE asset_locations SET present=0 WHERE path LIKE ?",
            (str(channel_root.resolve()) + os.sep + "%",),
        )
        self.connection.execute(
            """
            DELETE FROM creator_source_inventory
            WHERE creator_key IN (SELECT creator_key FROM creators WHERE platform=?)
            """,
            (platform,),
        )
        self.connection.execute(
            """
            DELETE FROM asset_lineage
            WHERE agent IN ('superdown88','catalog-scanner')
              AND source_key IN (SELECT source_key FROM source_posts WHERE platform=?)
            """,
            (platform,),
        )
        self.connection.execute(
            """
            DELETE FROM warnings
            WHERE account_key IS NULL
              AND code IN ('HOLD_LINEAGE_UNKNOWN','HOLD_LINEAGE_AMBIGUOUS')
            """
        )
        for creator_dir in sorted(path for path in channel_root.iterdir() if path.is_dir()):
            if creator_dir.name in CREATOR_EXCLUDES or creator_dir.name.startswith("."):
                continue
            metadata_path = creator_dir / "metadata.tsv"
            if not metadata_path.is_file():
                continue
            stats["creators"] += 1
            ckey = creator_key(platform, creator_dir.name)
            self.connection.execute(
                """
                INSERT INTO creators(creator_key, platform, canonical_creator, media_directory, last_seen_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(creator_key) DO UPDATE SET
                    media_directory=excluded.media_directory,
                    last_seen_at=excluded.last_seen_at
                """,
                (ckey, platform, creator_dir.name, str(creator_dir.resolve()), utc_now()),
            )
            rows: list[dict[str, str]] = []
            with metadata_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                rows = [dict(row) for row in reader]
            native_ids: list[str] = []
            file_to_source: dict[str, str] = {}
            for row in rows:
                native_id = first_value(row, ID_HEADERS)
                filename = first_value(row, FILE_HEADERS)
                if platform == "instagram" and len(native_id) != 11 and filename:
                    repaired_id = instagram_id_from_filename(filename)
                    if repaired_id:
                        native_id = repaired_id
                if not native_id:
                    continue
                skey = source_key(platform, native_id)
                native_ids.append(native_id)
                if filename:
                    file_to_source[filename] = skey
                self.connection.execute(
                    """
                    INSERT INTO source_posts(
                        source_key, platform, native_id, creator_key, canonical_url, title,
                        source_status, published_at, discovered_at, downloaded_at, metadata_path, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_key) DO UPDATE SET
                        canonical_url=COALESCE(excluded.canonical_url, source_posts.canonical_url),
                        title=COALESCE(excluded.title, source_posts.title),
                        source_status=COALESCE(excluded.source_status, source_posts.source_status),
                        published_at=COALESCE(excluded.published_at, source_posts.published_at),
                        discovered_at=COALESCE(excluded.discovered_at, source_posts.discovered_at),
                        downloaded_at=COALESCE(excluded.downloaded_at, source_posts.downloaded_at),
                        metadata_path=excluded.metadata_path,
                        updated_at=excluded.updated_at
                    """,
                    (
                        skey,
                        platform,
                        native_id,
                        ckey,
                        first_value(row, URL_HEADERS) or None,
                        first_value(row, TITLE_HEADERS) or None,
                        first_value(row, STATUS_HEADERS) or None,
                        row.get("发布日期") or None,
                        row.get("发现时间") or None,
                        row.get("下载时间") or None,
                        str(metadata_path.resolve()),
                        utc_now(),
                    ),
                )
                now = utc_now()
                self.connection.execute(
                    """
                    INSERT INTO creator_source_inventory(
                        creator_key, source_key, metadata_path, declared_filename,
                        first_seen_at, last_seen_at
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(creator_key, source_key) DO UPDATE SET
                        metadata_path=excluded.metadata_path,
                        declared_filename=COALESCE(excluded.declared_filename, creator_source_inventory.declared_filename),
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        ckey,
                        skey,
                        str(metadata_path.resolve()),
                        filename or None,
                        now,
                        now,
                    ),
                )
                stats["sources"] += 1
            native_ids = sorted(set(native_ids), key=len, reverse=True)
            for path in sorted(
                item for item in creator_dir.iterdir()
                if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS
            ):
                skey = file_to_source.get(path.name)
                if skey is None:
                    matches = [native_id for native_id in native_ids if native_id in path.name]
                    if len(matches) == 1:
                        skey = source_key(platform, matches[0])
                asset_id = self._asset_for_path(path, "source")
                stats["source_assets"] += 1
                if skey:
                    self.connection.execute(
                        """
                        INSERT INTO asset_lineage(
                            asset_id, source_key, parent_asset_id, relation, confidence,
                            lineage_status, batch_id, agent, receipt_path, updated_at
                        ) VALUES(?,?,NULL,'source',1.0,'exact',NULL,'superdown88',?,?)
                        ON CONFLICT(asset_id, source_key) DO UPDATE SET
                            confidence=1.0, lineage_status='exact', updated_at=excluded.updated_at
                        """,
                        (asset_id, skey, str(metadata_path.resolve()), utc_now()),
                    )
            if include_derivatives:
                for path in sorted(creator_dir.rglob("*")):
                    if (
                        not path.is_file()
                        or path.parent == creator_dir
                        or path.suffix.lower() not in VIDEO_EXTENSIONS
                        or any(part.startswith(".") for part in path.relative_to(creator_dir).parts)
                        or "quarantine" in path.parts
                        or ".partial" in path.name
                        or ".tmp" in path.name
                    ):
                        continue
                    asset_id = self._asset_for_path(path, "derivative")
                    stats["derivative_assets"] += 1
                    relative = path.relative_to(creator_dir)
                    material = str(relative)
                    matches = sorted(
                        {native_id for native_id in native_ids if native_id in material},
                        key=len,
                        reverse=True,
                    )
                    batch_id = relative.parts[0] if len(relative.parts) > 1 else None
                    if len(matches) == 1:
                        skey = source_key(platform, matches[0])
                        parent = self.connection.execute(
                            """
                            SELECT al.asset_id
                            FROM asset_lineage al
                            JOIN asset_locations loc ON loc.asset_id=al.asset_id
                            WHERE al.source_key=? AND loc.role='source'
                            ORDER BY loc.path LIMIT 1
                            """,
                            (skey,),
                        ).fetchone()
                        self.connection.execute(
                            """
                            INSERT INTO asset_lineage(
                                asset_id, source_key, parent_asset_id, relation, confidence,
                                lineage_status, batch_id, agent, receipt_path, updated_at
                            ) VALUES(?,?,?,'derived',0.95,'inferred_filename',?,'catalog-scanner',NULL,?)
                            ON CONFLICT(asset_id, source_key) DO UPDATE SET
                                parent_asset_id=COALESCE(asset_lineage.parent_asset_id, excluded.parent_asset_id),
                                batch_id=COALESCE(asset_lineage.batch_id, excluded.batch_id),
                                updated_at=excluded.updated_at
                            """,
                            (
                                asset_id,
                                skey,
                                str(parent["asset_id"]) if parent else None,
                                batch_id,
                                utc_now(),
                            ),
                        )
                        stats["lineage_exact_or_inferred"] += 1
                    else:
                        reviewed = self.connection.execute(
                            """
                            SELECT 1 FROM asset_lineage
                            WHERE asset_id=? AND lineage_status IN ('manual_reviewed','receipt_verified')
                            LIMIT 1
                            """,
                            (asset_id,),
                        ).fetchone()
                        if reviewed:
                            stats["lineage_exact_or_inferred"] += 1
                            continue
                        stats["lineage_hold"] += 1
                        warning_id = stable_id("warn", f"lineage:{asset_id}:{material}")
                        self.connection.execute(
                            """
                            INSERT OR IGNORE INTO warnings(
                                warning_id, severity, code, asset_id, source_key, account_key,
                                message, status, created_at
                            ) VALUES(?,?,?,?,NULL,NULL,?,'open',?)
                            """,
                            (
                                warning_id,
                                "block",
                                "HOLD_LINEAGE_UNKNOWN" if not matches else "HOLD_LINEAGE_AMBIGUOUS",
                                asset_id,
                                f"无法唯一关联源作品: {path}",
                                utc_now(),
                            ),
                        )
        self.connection.execute(
            """
            UPDATE publications
            SET source_key=(
                SELECT al.source_key FROM asset_lineage al
                WHERE al.asset_id=publications.asset_id
                ORDER BY
                    CASE al.lineage_status
                        WHEN 'manual_reviewed' THEN 0
                        WHEN 'receipt_verified' THEN 1
                        WHEN 'exact' THEN 2
                        ELSE 3
                    END,
                    al.confidence DESC
                LIMIT 1
            )
            WHERE EXISTS(
                SELECT 1 FROM asset_lineage al
                WHERE al.asset_id=publications.asset_id
            )
            """
        )
        self.connection.execute(
            """
            DELETE FROM source_posts
            WHERE platform=?
              AND NOT EXISTS(
                  SELECT 1 FROM creator_source_inventory csi
                  WHERE csi.source_key=source_posts.source_key
              )
              AND NOT EXISTS(
                  SELECT 1 FROM asset_lineage al
                  WHERE al.source_key=source_posts.source_key
              )
              AND NOT EXISTS(
                  SELECT 1 FROM publications p
                  WHERE p.source_key=source_posts.source_key
              )
              AND NOT EXISTS(
                  SELECT 1 FROM transform_runs tr
                  WHERE tr.source_key=source_posts.source_key
              )
            """,
            (platform,),
        )
        completed = utc_now()
        self.connection.execute(
            "UPDATE sync_runs SET completed_at=?, status='completed', stats_json=? WHERE sync_id=?",
            (completed, canonical_json(stats), sync_id),
        )
        self.event("catalog.sync.completed", {"sync_id": sync_id, "platform": platform, "stats": stats})
        self.connection.commit()
        self.refresh_event_log()
        return stats

    def ingest_receipt(self, receipt_path: Path) -> dict[str, Any]:
        receipt_path = receipt_path.expanduser().resolve()
        raw = json.loads(receipt_path.read_text(encoding="utf-8"))
        lineage = raw.get("lineage") or {}
        output = raw.get("output") or {}
        output_path = Path(output.get("path") or raw.get("output_file") or "")
        if not output_path.is_file():
            raise ValueError(f"回执输出不存在: {output_path}")
        skey = lineage.get("source_key") or raw.get("source_key")
        if not skey or self.connection.execute(
            "SELECT 1 FROM source_posts WHERE source_key=?", (skey,)
        ).fetchone() is None:
            raise ValueError(f"回执缺少已登记的 source_key: {skey!r}")
        asset_id = self._asset_for_path(output_path, "derivative", force_hash=True)
        parent_asset_id = lineage.get("parent_asset_id") or raw.get("parent_asset_id")
        status = str(raw.get("status") or "verified")
        confidence = 1.0 if status == "verified" else 0.8
        self.connection.execute(
            """
            INSERT INTO asset_lineage(
                asset_id, source_key, parent_asset_id, relation, confidence, lineage_status,
                batch_id, agent, receipt_path, updated_at
            ) VALUES(?,?,?,'derived',?,'receipt_verified',?,?,?,?)
            ON CONFLICT(asset_id, source_key) DO UPDATE SET
                parent_asset_id=COALESCE(excluded.parent_asset_id, asset_lineage.parent_asset_id),
                confidence=excluded.confidence,
                lineage_status=excluded.lineage_status,
                batch_id=COALESCE(excluded.batch_id, asset_lineage.batch_id),
                agent=COALESCE(excluded.agent, asset_lineage.agent),
                receipt_path=excluded.receipt_path,
                updated_at=excluded.updated_at
            """,
            (
                asset_id,
                skey,
                parent_asset_id,
                confidence,
                lineage.get("batch_id") or raw.get("batch_id"),
                lineage.get("agent") or raw.get("agent") or "unknown-agent",
                str(receipt_path),
                utc_now(),
            ),
        )
        transform_id = stable_id(
            "transform",
            f"{raw.get('job_id')}:{asset_id}:{raw.get('plan_hash')}:{receipt_path}",
        )
        self.connection.execute(
            """
            INSERT OR REPLACE INTO transform_runs(
                transform_id, job_id, batch_id, input_asset_id, output_asset_id,
                source_key, operation, recipe_id, agent, status, receipt_path,
                completed_at, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                transform_id,
                raw.get("job_id"),
                lineage.get("batch_id") or raw.get("batch_id"),
                parent_asset_id,
                asset_id,
                skey,
                lineage.get("operation") or "video-transform",
                lineage.get("recipe_id"),
                lineage.get("agent") or "unknown-agent",
                status,
                str(receipt_path),
                raw.get("completed_at") or utc_now(),
                utc_now(),
            ),
        )
        self.event(
            "transform.receipt.ingested",
            {"transform_id": transform_id, "asset_id": asset_id, "source_key": skey},
        )
        self.connection.commit()
        self.refresh_event_log()
        return {"transform_id": transform_id, "asset_id": asset_id, "source_key": skey}

    def link_asset(
        self,
        path: Path,
        skey: str,
        *,
        batch_id: str | None,
        evidence: str,
        confidence: float,
    ) -> dict[str, Any]:
        if not 0 < confidence <= 1:
            raise ValueError("confidence 必须满足 0 < value <= 1")
        if self.connection.execute(
            "SELECT 1 FROM source_posts WHERE source_key=?", (skey,)
        ).fetchone() is None:
            raise ValueError(f"source_key 未登记: {skey}")
        asset_id = self.resolve_asset(path)
        if not asset_id:
            raise ValueError(f"文件不存在或无法登记: {path}")
        parent = self.connection.execute(
            """
            SELECT al.asset_id
            FROM asset_lineage al
            JOIN asset_locations loc ON loc.asset_id=al.asset_id
            WHERE al.source_key=? AND loc.role='source'
            ORDER BY loc.path LIMIT 1
            """,
            (skey,),
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO asset_lineage(
                asset_id, source_key, parent_asset_id, relation, confidence,
                lineage_status, batch_id, agent, receipt_path, updated_at
            ) VALUES(?,?,?,'derived',?,'manual_reviewed',?,'catalog-repair',?,?)
            ON CONFLICT(asset_id, source_key) DO UPDATE SET
                parent_asset_id=COALESCE(excluded.parent_asset_id, asset_lineage.parent_asset_id),
                confidence=excluded.confidence,
                lineage_status='manual_reviewed',
                batch_id=COALESCE(excluded.batch_id, asset_lineage.batch_id),
                agent='catalog-repair',
                receipt_path=excluded.receipt_path,
                updated_at=excluded.updated_at
            """,
            (
                asset_id,
                skey,
                str(parent["asset_id"]) if parent else None,
                confidence,
                batch_id,
                evidence,
                utc_now(),
            ),
        )
        self.connection.execute(
            """
            UPDATE warnings SET status='resolved', resolved_at=?
            WHERE asset_id=? AND status='open'
              AND code IN ('HOLD_LINEAGE_UNKNOWN','HOLD_LINEAGE_AMBIGUOUS')
            """,
            (utc_now(), asset_id),
        )
        self.event(
            "asset.lineage.manually_linked",
            {
                "asset_id": asset_id,
                "source_key": skey,
                "confidence": confidence,
                "evidence": evidence,
            },
        )
        self.connection.commit()
        self.refresh_event_log()
        return {"asset_id": asset_id, "source_key": skey, "lineage_status": "manual_reviewed"}

    def preflight_file(
        self, path: Path, target_platform: str, target_account: str
    ) -> dict[str, Any]:
        asset_id = self.resolve_asset(path)
        account_key_value = self.upsert_account(target_platform, target_account)
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        sources = self.source_keys_for_asset(asset_id) if asset_id else []
        if not asset_id:
            blockers.append({"code": "FILE_NOT_REGISTERED", "message": f"文件未登记: {path}"})
        if len(sources) != 1:
            blockers.append(
                {
                    "code": "HOLD_LINEAGE_UNKNOWN" if not sources else "HOLD_LINEAGE_AMBIGUOUS",
                    "message": f"文件必须唯一关联一个源作品，当前关联数={len(sources)}",
                }
            )
        exact_rows = self.connection.execute(
            """
            SELECT status, title FROM publications
            WHERE account_key=? AND asset_id=? AND status IN ({})
            """.format(",".join("?" for _ in ACTIVE_PUBLICATION_STATES)),
            (account_key_value, asset_id, *sorted(ACTIVE_PUBLICATION_STATES)),
        ).fetchall() if asset_id else []
        if exact_rows:
            blockers.append(
                {
                    "code": "DUPLICATE_ASSET_SAME_ACCOUNT",
                    "message": f"相同成品已存在于目标账号: {exact_rows[0]['status']}",
                }
            )
        reservation = self.connection.execute(
            "SELECT reservation_id FROM reservations WHERE account_key=? AND asset_id=? AND status='active'",
            (account_key_value, asset_id),
        ).fetchone() if asset_id else None
        if reservation:
            blockers.append(
                {"code": "ACTIVE_RESERVATION", "message": "相同成品已被另一活动计划预约"}
            )
        if len(sources) == 1:
            sibling = self.connection.execute(
                """
                SELECT asset_id, status, title FROM publications
                WHERE account_key=? AND source_key=? AND asset_id<>?
                  AND status IN ({})
                ORDER BY updated_at DESC LIMIT 1
                """.format(",".join("?" for _ in ACTIVE_PUBLICATION_STATES)),
                (
                    account_key_value,
                    sources[0],
                    asset_id,
                    *sorted(ACTIVE_PUBLICATION_STATES),
                ),
            ).fetchone()
            if sibling:
                warnings.append(
                    {
                        "code": "SAME_SOURCE_SIBLING_SAME_ACCOUNT",
                        "message": f"同一源作品的其他版本已在该账号: {sibling['status']}",
                    }
                )
        other_account = self.connection.execute(
            """
            SELECT account_key, status FROM publications
            WHERE asset_id=? AND account_key<>? AND status IN ({})
            ORDER BY updated_at DESC LIMIT 1
            """.format(",".join("?" for _ in ACTIVE_PUBLICATION_STATES)),
            (asset_id, account_key_value, *sorted(ACTIVE_PUBLICATION_STATES)),
        ).fetchone() if asset_id else None
        if other_account:
            warnings.append(
                {
                    "code": "ASSET_USED_OTHER_ACCOUNT",
                    "message": f"该成品已用于其他账号: {other_account['account_key']}",
                }
            )
        return {
            "file": str(path.expanduser().resolve()),
            "asset_id": asset_id,
            "source_keys": sources,
            "account_key": account_key_value,
            "blockers": blockers,
            "warnings": warnings,
            "decision": "block" if blockers else "review" if warnings else "allow",
        }

    def preflight_manifest(
        self,
        manifest_path: Path,
        target_platform: str,
        target_account: str,
    ) -> dict[str, Any]:
        manifest_path = manifest_path.expanduser().resolve()
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("发布清单必须是非空 JSON 数组")
        results = []
        seen_assets: set[str] = set()
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict) or not item.get("file"):
                raise ValueError(f"发布清单第 {index} 项缺少 file")
            path = Path(str(item["file"])).expanduser()
            if not path.is_absolute():
                path = manifest_path.parent / path
            result = self.preflight_file(path, target_platform, target_account)
            if result["asset_id"] and result["asset_id"] in seen_assets:
                result["blockers"].append(
                    {"code": "DUPLICATE_WITHIN_MANIFEST", "message": "同一成品在清单中重复出现"}
                )
                result["decision"] = "block"
            if result["asset_id"]:
                seen_assets.add(result["asset_id"])
            result["index"] = index
            result["plan_item_id"] = str(item.get("plan_item_id") or item.get("plan_order") or index)
            results.append(result)
        return {
            "schema": CONTRACT_VERSION,
            "manifest": str(manifest_path),
            "target": {
                "platform": normalize_platform(target_platform),
                "account": target_account,
            },
            "summary": {
                "items": len(results),
                "allow": sum(item["decision"] == "allow" for item in results),
                "review": sum(item["decision"] == "review" for item in results),
                "block": sum(item["decision"] == "block" for item in results),
            },
            "items": results,
        }

    def reserve_manifest(
        self,
        manifest_path: Path,
        target_platform: str,
        target_account: str,
        *,
        allow_source_repeat: bool = False,
    ) -> dict[str, Any]:
        report = self.preflight_manifest(manifest_path, target_platform, target_account)
        if report["summary"]["block"]:
            raise ValueError("发布前检查存在阻止项，未创建预约")
        if report["summary"]["review"] and not allow_source_repeat:
            raise ValueError("发布前检查存在同源/跨账号提醒；需明确 --allow-source-repeat")
        now = utc_now()
        for item in report["items"]:
            reservation_id = stable_id(
                "reserve",
                f"{item['account_key']}:{item['asset_id']}:{report['manifest']}:{item['plan_item_id']}",
            )
            self.connection.execute(
                """
                INSERT INTO reservations(
                    reservation_id, asset_id, account_key, plan_item_id,
                    manifest_path, status, created_at
                ) VALUES(?,?,?,?,?,'active',?)
                """,
                (
                    reservation_id,
                    item["asset_id"],
                    item["account_key"],
                    item["plan_item_id"],
                    report["manifest"],
                    now,
                ),
            )
            item["reservation_id"] = reservation_id
        self.event(
            "publication.manifest.reserved",
            {
                "manifest": report["manifest"],
                "target": report["target"],
                "count": len(report["items"]),
            },
        )
        self.connection.commit()
        self.refresh_event_log()
        return report

    def complete_manifest(
        self,
        manifest_path: Path,
        target_platform: str,
        target_account: str,
        status: str,
        verification: str,
    ) -> dict[str, int]:
        if status not in TERMINAL_PUBLICATION_STATES | {"failed", "cancelled"}:
            raise ValueError(f"不支持的发布状态: {status}")
        manifest_path = manifest_path.expanduser().resolve()
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        account_key_value = self.upsert_account(target_platform, target_account)
        recorded = 0
        unresolved = 0
        now = utc_now()
        for index, item in enumerate(raw, start=1):
            path = Path(str(item.get("file") or "")).expanduser()
            if not path.is_absolute():
                path = manifest_path.parent / path
            asset_id = self.resolve_asset(path)
            sources = self.source_keys_for_asset(asset_id) if asset_id else []
            if not asset_id or len(sources) != 1:
                unresolved += 1
                continue
            plan_item_id = str(item.get("plan_item_id") or item.get("plan_order") or index)
            publication_id = stable_id(
                "pub",
                f"{account_key_value}:{asset_id}:{plan_item_id}:{manifest_path}",
            )
            self.connection.execute(
                """
                INSERT INTO publications(
                    publication_id, plan_item_id, asset_id, source_key, account_key,
                    title, status, verification, manifest_path, first_recorded_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(publication_id) DO UPDATE SET
                    title=COALESCE(excluded.title, publications.title),
                    status=excluded.status,
                    verification=excluded.verification,
                    updated_at=excluded.updated_at
                """,
                (
                    publication_id,
                    plan_item_id,
                    asset_id,
                    sources[0],
                    account_key_value,
                    str(item.get("title") or ""),
                    status,
                    verification,
                    str(manifest_path),
                    now,
                    now,
                ),
            )
            self.connection.execute(
                """
                UPDATE reservations SET status='released', released_at=?
                WHERE asset_id=? AND account_key=? AND status='active'
                """,
                (now, asset_id, account_key_value),
            )
            self.event(
                "publication.status.recorded",
                {
                    "publication_id": publication_id,
                    "asset_id": asset_id,
                    "source_key": sources[0],
                    "account_key": account_key_value,
                    "status": status,
                    "verification": verification,
                },
            )
            recorded += 1
        self.connection.commit()
        self.refresh_event_log()
        return {"recorded": recorded, "unresolved": unresolved}

    def summary(self) -> dict[str, Any]:
        counts = {}
        for table in (
            "creators",
            "source_posts",
            "creator_source_inventory",
            "assets",
            "asset_locations",
            "asset_lineage",
            "transform_runs",
            "publications",
            "reservations",
            "warnings",
            "events",
        ):
            counts[table] = self.connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        # `assets` is an immutable historical ledger: a deleted file may still be
        # needed to explain an old publication.  Keep it, but expose the live
        # inventory separately so a dashboard never mistakes history for disk state.
        counts["present_assets"] = self.connection.execute(
            "SELECT COUNT(DISTINCT asset_id) AS n FROM asset_locations WHERE present=1"
        ).fetchone()["n"]
        counts["missing_asset_locations"] = self.connection.execute(
            "SELECT COUNT(*) AS n FROM asset_locations WHERE present=0"
        ).fetchone()["n"]
        by_account = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT account_key, status, COUNT(*) AS count
                FROM publications GROUP BY account_key, status
                ORDER BY account_key, status
                """
            )
        ]
        lineage = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT lineage_status, COUNT(DISTINCT asset_id) AS count
                FROM asset_lineage GROUP BY lineage_status ORDER BY lineage_status
                """
            )
        ]
        holds = self.connection.execute(
            "SELECT COUNT(*) AS n FROM warnings WHERE status='open' AND severity='block'"
        ).fetchone()["n"]
        return {
            "schema": CONTRACT_VERSION,
            "database": str(self.db_path),
            "root": str(self.root),
            "counts": counts,
            "lineage": lineage,
            "publications_by_account": by_account,
            "open_blocking_warnings": holds,
        }

    def export_reports(self, output_dir: Path | None = None) -> dict[str, str]:
        output_dir = (
            output_dir.expanduser().resolve()
            if output_dir
            else self.state_dir / "reports"
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        def export_tsv(name: str, query: str, params: tuple[Any, ...] = ()) -> Path:
            path = output_dir / name
            rows = self.connection.execute(query, params).fetchall()
            columns = [item[0] for item in self.connection.execute(query, params).description]
            fd, temporary_name = tempfile.mkstemp(
                dir=output_dir, prefix=f".{name}.", suffix=".tmp"
            )
            os.close(fd)
            temporary = Path(temporary_name)
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter="\t")
                writer.writerow(columns)
                for row in rows:
                    writer.writerow([row[column] for column in columns])
            os.replace(temporary, path)
            return path

        creator_report = export_tsv(
            "creator_summary.tsv",
            """
            SELECT
                c.platform,
                c.canonical_creator AS creator,
                COUNT(DISTINCT csi.source_key) AS source_inventory,
                COUNT(DISTINCT CASE WHEN loc.role='source' THEN loc.asset_id END) AS source_assets,
                COUNT(DISTINCT CASE WHEN loc.role='derivative' THEN loc.asset_id END) AS derivative_assets,
                COUNT(DISTINCT CASE WHEN p.status='draft_saved_verified' THEN p.publication_id END) AS draft_verified,
                COUNT(DISTINCT CASE WHEN p.status='draft_saved_unverified' THEN p.publication_id END) AS draft_unverified,
                COUNT(DISTINCT CASE WHEN p.status='published' THEN p.publication_id END) AS published,
                c.last_seen_at
            FROM creators c
            LEFT JOIN creator_source_inventory csi ON csi.creator_key=c.creator_key
            LEFT JOIN asset_lineage al ON al.source_key=csi.source_key
            LEFT JOIN asset_locations loc ON loc.asset_id=al.asset_id AND loc.present=1
            LEFT JOIN publications p ON p.source_key=csi.source_key
            GROUP BY c.creator_key
            ORDER BY c.platform, c.canonical_creator
            """,
        )
        asset_report = export_tsv(
            "asset_inventory.tsv",
            """
            SELECT
                c.platform,
                c.canonical_creator AS creator,
                csi.source_key,
                a.asset_id,
                a.sha256,
                loc.role,
                loc.path,
                al.relation,
                al.lineage_status,
                al.confidence,
                al.batch_id,
                GROUP_CONCAT(DISTINCT p.account_key || ':' || p.status) AS publication_history
            FROM creator_source_inventory csi
            JOIN creators c ON c.creator_key=csi.creator_key
            LEFT JOIN asset_lineage al ON al.source_key=csi.source_key
            LEFT JOIN assets a ON a.asset_id=al.asset_id
            LEFT JOIN asset_locations loc ON loc.asset_id=a.asset_id AND loc.present=1
            LEFT JOIN publications p ON p.asset_id=a.asset_id
            GROUP BY c.creator_key, csi.source_key, a.asset_id, loc.path
            ORDER BY c.platform, c.canonical_creator, csi.source_key, loc.role, loc.path
            """,
        )
        publication_report = export_tsv(
            "publication_history.tsv",
            """
            SELECT
                p.publication_id,
                p.plan_item_id,
                p.source_key,
                p.asset_id,
                p.account_key,
                p.title,
                p.status,
                p.verification,
                p.manifest_path,
                p.first_recorded_at,
                p.updated_at
            FROM publications p
            ORDER BY p.account_key, p.updated_at, p.plan_item_id
            """,
        )
        warning_report = export_tsv(
            "warnings.tsv",
            """
            SELECT warning_id, severity, code, asset_id, source_key, account_key,
                   message, status, created_at, resolved_at
            FROM warnings ORDER BY status, severity, code, created_at
            """,
        )
        summary_path = output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(self.summary(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {
            "creator_summary": str(creator_report),
            "asset_inventory": str(asset_report),
            "publication_history": str(publication_report),
            "warnings": str(warning_report),
            "summary": str(summary_path),
        }

    def audit(self) -> dict[str, Any]:
        integrity = self.connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [dict(row) for row in self.connection.execute("PRAGMA foreign_key_check")]
        missing_locations = [
            str(row["path"])
            for row in self.connection.execute("SELECT path FROM asset_locations WHERE present=1")
            if not Path(row["path"]).is_file()
        ]
        return {
            "integrity": integrity,
            "foreign_key_errors": foreign_keys,
            "missing_present_locations": missing_locations,
            "status": "ok" if integrity == "ok" and not foreign_keys and not missing_locations else "error",
        }


def write_report(path: Path | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path:
        path = path.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统一视频资产、血缘和发布状态账本")
    parser.add_argument("--root", required=True, type=Path, help="Video_Download 根目录")
    parser.add_argument("--db", type=Path, help="SQLite 路径；默认 ROOT/.supermedia/media_catalog.sqlite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init")

    sync = subparsers.add_parser("sync")
    sync.add_argument("--platform", required=True)
    sync.add_argument("--sources-only", action="store_true")
    sync.add_argument("--report", type=Path)

    receipt = subparsers.add_parser("ingest-receipt")
    receipt.add_argument("receipt", type=Path)

    link = subparsers.add_parser("link-asset")
    link.add_argument("--file", required=True, type=Path)
    link.add_argument("--source-key", required=True)
    link.add_argument("--batch-id")
    link.add_argument("--evidence", required=True)
    link.add_argument("--confidence", type=float, default=1.0)

    preflight = subparsers.add_parser("preflight-manifest")
    preflight.add_argument("--manifest", required=True, type=Path)
    preflight.add_argument("--target-platform", required=True)
    preflight.add_argument("--target-account", required=True)
    preflight.add_argument("--report", type=Path)

    reserve = subparsers.add_parser("reserve-manifest")
    reserve.add_argument("--manifest", required=True, type=Path)
    reserve.add_argument("--target-platform", required=True)
    reserve.add_argument("--target-account", required=True)
    reserve.add_argument("--allow-source-repeat", action="store_true")
    reserve.add_argument("--report", type=Path)

    complete = subparsers.add_parser("complete-manifest")
    complete.add_argument("--manifest", required=True, type=Path)
    complete.add_argument("--target-platform", required=True)
    complete.add_argument("--target-account", required=True)
    complete.add_argument("--status", required=True)
    complete.add_argument("--verification", required=True)

    subparsers.add_parser("summary")
    subparsers.add_parser("audit")
    export = subparsers.add_parser("export-reports")
    export.add_argument("--output-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog = Catalog(args.root, args.db)
    try:
        if args.command == "init":
            write_report(None, catalog.summary())
            return 0
        if args.command == "sync":
            stats = catalog.sync_platform(
                args.platform, include_derivatives=not args.sources_only
            )
            write_report(args.report, {"status": "ok", "stats": stats})
            return 0
        if args.command == "ingest-receipt":
            write_report(None, {"status": "ok", **catalog.ingest_receipt(args.receipt)})
            return 0
        if args.command == "link-asset":
            write_report(
                None,
                {
                    "status": "ok",
                    **catalog.link_asset(
                        args.file,
                        args.source_key,
                        batch_id=args.batch_id,
                        evidence=args.evidence,
                        confidence=args.confidence,
                    ),
                },
            )
            return 0
        if args.command == "preflight-manifest":
            report = catalog.preflight_manifest(
                args.manifest, args.target_platform, args.target_account
            )
            write_report(args.report, report)
            return 4 if report["summary"]["block"] else 3 if report["summary"]["review"] else 0
        if args.command == "reserve-manifest":
            report = catalog.reserve_manifest(
                args.manifest,
                args.target_platform,
                args.target_account,
                allow_source_repeat=args.allow_source_repeat,
            )
            write_report(args.report, report)
            return 0
        if args.command == "complete-manifest":
            write_report(
                None,
                {
                    "status": "ok",
                    **catalog.complete_manifest(
                        args.manifest,
                        args.target_platform,
                        args.target_account,
                        args.status,
                        args.verification,
                    ),
                },
            )
            return 0
        if args.command == "summary":
            write_report(None, catalog.summary())
            return 0
        if args.command == "audit":
            report = catalog.audit()
            write_report(None, report)
            return 0 if report["status"] == "ok" else 5
        if args.command == "export-reports":
            write_report(None, {"status": "ok", "reports": catalog.export_reports(args.output_dir)})
            return 0
        raise AssertionError(args.command)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"catalog error: {exc}", file=sys.stderr)
        return 2
    finally:
        catalog.close()


if __name__ == "__main__":
    raise SystemExit(main())
