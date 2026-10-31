# -*- coding: utf-8 -*-
"""Canonical Music Model + Engine API payloads (schema_version stable)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "music.canonical.v1"


class MusicArtistRef(BaseModel):
    provider_artist_id: str
    name: str


class MusicAlbumRef(BaseModel):
    provider_album_id: str = ""
    name: str = ""


class MusicTrack(BaseModel):
    track_id: str
    provider: str = "netease"
    provider_track_id: str
    title: str
    artists: list[MusicArtistRef] = Field(default_factory=list)
    album: MusicAlbumRef | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class MusicArtist(BaseModel):
    artist_id: str
    provider: str = "netease"
    provider_artist_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MusicPlaylist(BaseModel):
    playlist_id: str
    provider: str = "netease"
    provider_playlist_id: str
    name: str
    owner_id: str | None = None
    track_count: int | None = None
    visibility: str | None = None
    updated_at: str | None = None


class MusicProfile(BaseModel):
    subject_id: str
    display_name: str = ""
    avatar_url: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class CollectionResult(BaseModel):
    collection_id: str
    collector_id: str
    provider: str = "netease"
    subject_id: str
    collected_at: str
    status: Literal["success", "partial", "failed", "unsupported"] = "success"
    complete: bool = True
    records: list[Any] = Field(default_factory=list)
    expected_count: int | None = None
    actual_count: int = 0
    cursor: str | None = None
    warnings: list[str] = Field(default_factory=list)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class MusicSnapshot(BaseModel):
    snapshot_id: str
    owner_user_id: str
    provider: str = "netease"
    data_type: str
    subject_id: str
    local_date: str | None = None
    timezone: str = "UTC"
    fetched_at: str
    expires_at: str | None = None
    status: Literal["complete", "partial", "invalid", "expired"] = "complete"
    complete: bool = True
    expected_count: int | None = None
    actual_count: int = 0
    snapshot_hash: str = ""
    schema_version: str = SCHEMA_VERSION
    records: list[Any] = Field(default_factory=list)


class MusicDiffItem(BaseModel):
    change_type: str
    entity_type: str = "track"
    provider_entity_id: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class MusicDiff(BaseModel):
    diff_id: str
    subject_id: str
    data_type: str
    previous_snapshot_id: str
    current_snapshot_id: str
    previous_hash: str
    current_hash: str
    changes: list[MusicDiffItem] = Field(default_factory=list)
    generated_at: str


class AuthorizationRequest(BaseModel):
    owner_user_id: str
    node_id: str = "local-node"
    provider: str = "netease"
    scopes: list[str] = Field(default_factory=list)
    mode: Literal["qr", "config_bind"] = "qr"


class AuthorizationChallenge(BaseModel):
    session_id: str
    provider: str = "netease"
    qr_key: str = ""
    qr_url: str = ""
    expires_at: str = ""
    status: str = "waiting_scan"
    state_nonce: str = ""
    qr_image_data: str | None = None


class AuthorizationSession(BaseModel):
    session_id: str
    provider: str = "netease"
    node_id: str
    user_id: str
    state_nonce: str
    status: Literal[
        "created",
        "waiting_scan",
        "waiting_confirm",
        "authorized",
        "expired",
        "failed",
        "cancelled",
    ] = "created"
    created_at: str
    expires_at: str
    authorized_subject_id: str | None = None


class AuthorizationStatus(BaseModel):
    session_id: str | None = None
    status: str
    subject_id: str | None = None
    detail: str = ""
    scopes: list[str] = Field(default_factory=list)


class MusicScopeConfig(BaseModel):
    owner_user_id: str
    scopes: list[str] = Field(default_factory=list)
    purposes: list[str] = Field(default_factory=list)
    policy_version: str = "music.consent.v1"


class MusicConsent(BaseModel):
    consent_id: str
    owner_user_id: str
    node_id: str
    provider: str = "netease"
    scopes: list[str] = Field(default_factory=list)
    purposes: list[str] = Field(default_factory=list)
    retention_policy_id: str = "default"
    granted_at: str
    expires_at: str | None = None
    status: Literal["active", "expired", "revoked"] = "active"
    policy_version: str = "music.consent.v1"


class SyncRequest(BaseModel):
    owner_user_id: str
    collectors: list[str] = Field(default_factory=list)
    trigger: Literal[
        "scheduled",
        "manual",
        "startup_recovery",
        "auth_recovered",
        "scope_changed",
        "retry",
    ] = "manual"
    force_refresh: bool = False
    playlist_ids: list[str] = Field(default_factory=list)
    recovery_run_id: str | None = None


class SyncRun(BaseModel):
    run_id: str
    node_id: str = "local-node"
    owner_user_id: str
    provider: str = "netease"
    trigger: str = "manual"
    status: str = "queued"
    collectors: list[str] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    retry_count: int = 0
    error_code: str | None = None
    warnings: list[str] = Field(default_factory=list)
    snapshot_ids: list[str] = Field(default_factory=list)
    collector_results: dict[str, Any] = Field(default_factory=dict)
    consent_id: str | None = None
    policy_version: str | None = None
    subject_id: str | None = None
    next_retry_at: str | None = None


class SyncStatus(BaseModel):
    run_id: str | None = None
    status: str
    detail: str = ""
    active_locks: list[str] = Field(default_factory=list)
    next_retry_at: str | None = None
    recovered_as: str | None = None


class SourceCapabilities(BaseModel):
    provider: str = "netease"
    features: dict[str, bool] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    verified_at: str = ""
    verification_status: str = "implemented_unverified"


class EngineStatus(BaseModel):
    ok: bool
    version: str
    provider: str = "netease"
    auth_status: str = "unbound"
    initialized: bool = False
    host: str = "unknown"
    data_root: str = ""
    capabilities: SourceCapabilities | None = None
