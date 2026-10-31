"""One durable, consent-checked coordinator for every sync trigger."""
import asyncio
import hashlib
from pathlib import Path
import secrets

from music_acquisition.domain.models import CollectionResult, MusicConsent, MusicScopeConfig, SyncRequest, SyncRun, SyncStatus
from music_acquisition.sync.recovery import clear_pending, mark_pending
from music_acquisition.sync.retry import backoff_delay, should_retry
from music_acquisition.util.file_lock import FileLock
from music_acquisition.util.runtime import audit, local_date, parse_time


class SyncCoordinator:
    def __init__(self, host, source, snapshots, gate, *, node_id="local-node", owner_user_id="owner", retry_base=0.1):
        self._host, self._source, self._snapshots, self._gate = host, source, snapshots, gate
        self._node_id, self.owner, self.retry_base = node_id, owner_user_id, retry_base
        self._locks, self._runs = set(), {}

    def _save(self, run, request):
        self._host.local_storage.write_json(f"sync/runs/{run.run_id}.json", {**run.model_dump(mode="json"), "request": request.model_dump(mode="json")})

    def _permitted(self, cid):
        config = self._host.local_storage.read_json(f"config/scopes_{self.owner}.json")
        if config:
            self._gate.configure(MusicScopeConfig.model_validate(config))
        raw = self._host.local_storage.read_json(f"config/consent_{self.owner}.json")
        if raw:
            self._gate.set_consent(MusicConsent.model_validate(raw))
        if (self._host.local_storage.read_json(f"auth/revoked_{self.owner}.json") or {}).get("revoked"):
            return "AUTH_REVOKED"
        if not self._gate.allow_collector(self.owner, cid):
            return "SCOPE_DENIED"
        if not self._gate.allows(self.owner, collector=cid) or not self._gate.allows(self.owner, purpose="music.data.cache"):
            return "CONSENT_REQUIRED"
        consent = self._gate.consent(self.owner)
        if consent.retention_policy_id != "default":
            policy = self._host.local_storage.read_json("config/retention.json") or {}
            if consent.retention_policy_id not in policy:
                return "RETENTION_POLICY_UNKNOWN"
        return None

    async def _collect(self, cid, request, subject):
        if cid != "playlist_tracks":
            return await getattr(self._source, "get_" + cid)()
        ids = list(dict.fromkeys(request.playlist_ids))
        if not ids:
            previous = self._snapshots.load("playlists", subject)
            if previous and previous.complete:
                ids = [str(p.get("provider_playlist_id") or p.get("playlist_id")) for p in previous.records]
        if not ids:
            return CollectionResult(collection_id="no-target", collector_id=cid, subject_id=subject,
                collected_at=self._host.clock.now_iso(), status="unsupported", complete=False, warnings=["PLAYLIST_TARGET_REQUIRED"])
        records, expected, complete = [], 0, True
        for pid in ids:
            denied = self._permitted(cid)
            if denied:
                raise PermissionError(denied)
            page = await self._source.get_playlist_tracks(pid)
            records += [{**r, "parent_playlist_id": pid} for r in page.records]
            expected += page.expected_count if page.expected_count is not None else len(page.records)
            complete = complete and page.complete and page.status == "success"
        return CollectionResult(collection_id="playlists-aggregate", collector_id=cid, subject_id=subject,
            collected_at=self._host.clock.now_iso(), records=records, actual_count=len(records),
            expected_count=expected, complete=complete, status="success" if complete else "partial")

    async def sync_now(self, request: SyncRequest):
        if request.owner_user_id != self.owner:
            raise PermissionError("OWNER_DENIED")
        run = SyncRun(run_id=f"run_{secrets.token_hex(8)}", node_id=self._node_id,
            owner_user_id=self.owner, trigger=request.trigger, status="queued",
            collectors=list(request.collectors) or ["liked_songs", "playlists", "daily_songs", "playback_history"],
            started_at=self._host.clock.now_iso())
        self._runs[run.run_id] = run
        mark_pending(self._host, run.run_id)
        self._save(run, request)
        key = hashlib.sha256(f"{self._node_id}:{self.owner}:netease".encode()).hexdigest()
        lock = FileLock(Path(self._host.local_storage.root()) / "sync" / "locks" / key, timeout_sec=0)
        try:
            lock.acquire()
        except TimeoutError:
            run.status, run.error_code = "queued", "LOCK_BUSY"
            self._save(run, request)
            return run
        self._locks.add(key)
        retryable = False
        try:
            if (self._host.local_storage.read_json(f"auth/revoked_{self.owner}.json") or {}).get("revoked"):
                run.status, run.error_code = "auth_required", "AUTH_REVOKED"
                return run
            cooldown = self._host.local_storage.read_json("sync/source_cooldown.json") or {}
            if cooldown.get("not_before") and parse_time(cooldown["not_before"]) > parse_time(self._host.clock.now_iso()):
                run.status, run.error_code, run.next_retry_at = "failed", "HTTP_429", cooldown["not_before"]
                retryable = True
                return run
            run.status = "checking_auth"
            self._save(run, request)
            auth = await self._source.check_authorization()
            if auth.status == "unknown" and auth.detail == "AUTH_HEALTH_UNAVAILABLE":
                run.status, run.error_code = "failed", "NETWORK_ERROR"
                retryable = True
                return run
            if auth.status not in {"valid", "authorized"}:
                run.status, run.error_code = "auth_required", "LOGIN_REQUIRED"
                return run
            subject = auth.subject_id
            run.subject_id = subject
            if not subject:
                run.status, run.error_code = "auth_required", "ACCOUNT_ID_REQUIRED"
                return run
            capabilities = (await self._source.get_capabilities()).features
            features = {"liked_songs": "likedSongs", "playlists": "playlists", "playlist_tracks": "playlistTracks",
                "daily_songs": "dailySongs", "daily_playlists": "dailyPlaylists", "playback_history": "playbackHistory", "favorite_artists": "favoriteArtists"}
            for cid in run.collectors:
                entry = {"status": "queued", "attempts": 0}
                run.collector_results[cid] = entry
                if cid not in {"profile", *features} or (cid in features and not capabilities.get(features[cid])):
                    entry.update(status="unsupported", error_code="UNSUPPORTED")
                    continue
                denied = self._permitted(cid)
                if denied:
                    entry.update(status="denied", error_code=denied)
                    run.error_code = denied
                    continue
                consent = self._gate.consent(self.owner)
                run.consent_id, run.policy_version = consent.consent_id, consent.policy_version
                date = local_date(self._host.clock) if cid in {"daily_songs", "daily_playlists"} else None
                existing = self._snapshots.load(cid, subject, date)
                if date and existing and existing.complete and not request.force_refresh:
                    entry.update(status="success", cached=True, actual_count=existing.actual_count)
                    run.snapshot_ids.append(existing.snapshot_id)
                    continue
                for attempt in range(3):
                    entry["attempts"] += 1
                    run.status = "fetching"
                    self._save(run, request)
                    try:
                        denied = self._permitted(cid)
                        if denied:
                            raise PermissionError(denied)
                        result = await self._collect(cid, request, subject)
                        denied = self._permitted(cid)  # revoke while a request is in flight
                        if denied:
                            raise PermissionError(denied)
                        if result.subject_id != subject:
                            raise PermissionError("ACCOUNT_MISMATCH")
                        if result.status in {"failed", "unsupported"}:
                            entry.update(status=result.status, error_code="UNSUPPORTED" if result.status == "unsupported" else "SOURCE_FAILED")
                            break
                        if result.status == "partial" and attempt < 2:
                            run.retry_count += 1
                            await asyncio.sleep(backoff_delay(attempt, base=self.retry_base, max_delay=2))
                            continue
                        run.status = "validating"
                        date = local_date(self._host.clock) if cid in {"daily_songs", "daily_playlists"} else None
                        existing = self._snapshots.load(cid, subject, date)
                        if date and existing and existing.complete and not request.force_refresh:
                            snap = existing
                        else:
                            snap = self._snapshots.commit(owner_user_id=self.owner, data_type=cid, subject_id=subject,
                                records=list(result.records), complete=result.complete and result.status == "success",
                                expected_count=result.expected_count, local_date=date)
                        complete = bool(snap and snap.complete and result.complete and result.status == "success")
                        entry.update(status="success" if complete else "partial", actual_count=len(result.records), expected_count=result.expected_count)
                        if snap:
                            run.snapshot_ids.append(snap.snapshot_id)
                        audit(self._host, "collector_completed", owner=self.owner, run_id=run.run_id,
                              collector=cid, count=len(result.records), consent_id=consent.consent_id, policy_version=consent.policy_version)
                        break
                    except PermissionError as exc:
                        entry.update(status="auth_required", error_code=str(exc) if str(exc) in {"SCOPE_DENIED", "CONSENT_REQUIRED", "AUTH_REVOKED", "ACCOUNT_MISMATCH"} else "LOGIN_REQUIRED")
                        run.error_code = entry["error_code"]
                        break
                    except Exception as exc:
                        code = "NETWORK_TIMEOUT" if isinstance(exc, (TimeoutError, ConnectionError, OSError)) else "SOURCE_FORMAT_ERROR"
                        if str(exc).startswith(("HTTP_429", "HTTP_5")):
                            code = str(exc).split(":", 1)[0]
                        entry.update(status="failed", error_code=code)
                        if code == "HTTP_429":
                            from datetime import timedelta
                            delay = max(getattr(exc, "retry_after", 0), backoff_delay(attempt + 3, base=max(self.retry_base, 1)))
                            run.next_retry_at = (parse_time(self._host.clock.now_iso()) + timedelta(seconds=delay)).isoformat()
                            self._host.local_storage.write_json("sync/source_cooldown.json", {"not_before": run.next_retry_at})
                            retryable = True
                            break
                        if should_retry(code):
                            if attempt < 2:
                                run.retry_count += 1
                                await asyncio.sleep(backoff_delay(attempt, base=self.retry_base, max_delay=2))
                                continue
                            retryable = True
                        break
                self._save(run, request)
            states = [e["status"] for e in run.collector_results.values()]
            if "auth_required" in states:
                run.status = "auth_required"
            elif states and all(s == "success" for s in states):
                run.status = "success"
            elif any(s in {"success", "partial"} for s in states):
                run.status = "partial"
            elif states and all(s == "unsupported" for s in states):
                run.status = "unsupported"
            else:
                run.status = "failed"
            if not run.error_code:
                run.error_code = next((e.get("error_code") for e in run.collector_results.values() if e.get("error_code")), None)
            run.warnings = [f"{cid}:{e.get('error_code', e['status'])}" for cid, e in run.collector_results.items() if e["status"] != "success"]
            return run
        finally:
            run.completed_at = self._host.clock.now_iso()
            self._save(run, request)
            if not retryable:
                clear_pending(self._host, run.run_id)
            if request.recovery_run_id:
                previous = self._host.local_storage.read_json(f"sync/runs/{request.recovery_run_id}.json") or {}
                previous.update(status="recovered", recovered_as=run.run_id, completed_at=run.completed_at)
                self._host.local_storage.write_json(f"sync/runs/{request.recovery_run_id}.json", previous)
                clear_pending(self._host, request.recovery_run_id)
            self._locks.discard(key)
            lock.release()
            self._host.event_publisher.publish("music.sync.completed", {"run_id": run.run_id, "status": run.status})

    def get_status(self, run_id=None):
        run = self._runs.get(run_id)
        raw = None
        if run_id:
            raw = self._host.local_storage.read_json(f"sync/runs/{run_id}.json")
            run = SyncRun.model_validate(raw) if raw else None
        return SyncStatus(run_id=run_id, status=run.status, detail=run.error_code or "", next_retry_at=run.next_retry_at, recovered_as=(raw or {}).get("recovered_as")) if run else SyncStatus(status="idle" if not self._locks else "busy")
