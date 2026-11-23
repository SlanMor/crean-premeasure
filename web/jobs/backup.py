"""Nightly backups off the server.

What is kept, and where:
- The database, as a compressed Django dump (independent of the Postgres
  version, restored with ``manage.py restore_backup``): one a day for 14 days,
  then the first of each month for 12 months. Under ``database/``.
- The uploaded drawings, Callidus files and outputs, copied file by file
  (only new or changed files each night). Working files the platform can make
  again are left out. A file deleted on the server is removed from the backup
  30 days later. Under ``media/``.

The target is S3-compatible object storage in the EU (Hetzner Object Storage),
set by BACKUP_S3_* in .env; BACKUP_DIR (a folder) is for tests and local use.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from .models import BackupRun

DAILY = 14
MONTHLY = 12
DELETED_GRACE_DAYS = 30
SKIP = ("premeasure/work/", "premeasure/zoom/", "premeasure/changes/", "premeasure/preview/")
EXCLUDE = ["contenttypes", "auth.permission", "sessions", "admin.logentry"]


# --- where backups go -----------------------------------------------------------------


class Target:
    def put(self, key: str, path: Path) -> None: ...
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def get_file(self, key: str, path: Path) -> None: ...
    def list(self, prefix: str) -> dict[str, int]: ...  # key -> size
    def delete(self, key: str) -> None: ...
    def describe(self) -> str: ...


class FolderTarget(Target):
    def __init__(self, root: str):
        self.root = Path(root)

    def _p(self, key: str) -> Path:
        return self.root / key

    def put(self, key, path):
        dest = self._p(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(path).read_bytes())

    def put_bytes(self, key, data):
        dest = self._p(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def get_bytes(self, key):
        p = self._p(key)
        return p.read_bytes() if p.exists() else None

    def get_file(self, key, path):
        Path(path).write_bytes(self._p(key).read_bytes())

    def list(self, prefix):
        base = self._p(prefix)
        out = {}
        if base.exists():
            for p in base.rglob("*"):
                if p.is_file():
                    out[p.relative_to(self.root).as_posix()] = p.stat().st_size
        return out

    def delete(self, key):
        self._p(key).unlink(missing_ok=True)

    def describe(self):
        return f"folder {self.root}"


class S3Target(Target):
    def __init__(self, endpoint, bucket, key_id, secret, region):
        import boto3

        self.bucket = bucket
        self.endpoint = endpoint
        self.s3 = boto3.client("s3", endpoint_url=endpoint or None, aws_access_key_id=key_id,
                               aws_secret_access_key=secret, region_name=region or None)

    def put(self, key, path):
        self.s3.upload_file(str(path), self.bucket, key)

    def put_bytes(self, key, data):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_bytes(self, key):
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None
        except Exception as exc:  # some S3 services report a missing key differently
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                return None
            raise

    def get_file(self, key, path):
        self.s3.download_file(self.bucket, key, str(path))

    def list(self, prefix):
        out = {}
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for o in page.get("Contents", []):
                out[o["Key"]] = o["Size"]
        return out

    def delete(self, key):
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def describe(self):
        return f"bucket {self.bucket} at {self.endpoint}"


def target() -> Target | None:
    e = os.environ
    if e.get("BACKUP_S3_BUCKET"):
        return S3Target(e.get("BACKUP_S3_ENDPOINT", ""), e["BACKUP_S3_BUCKET"], e.get("BACKUP_S3_ACCESS_KEY"),
                        e.get("BACKUP_S3_SECRET_KEY"), e.get("BACKUP_S3_REGION", ""))
    if e.get("BACKUP_DIR"):
        return FolderTarget(e["BACKUP_DIR"])
    return None


# --- the backup -----------------------------------------------------------------------


def dump_database() -> bytes:
    buf = io.StringIO()
    call_command("dumpdata", natural_foreign=True, natural_primary=True, exclude=EXCLUDE, indent=None, stdout=buf)
    return gzip.compress(buf.getvalue().encode("utf-8"))


def _keep(keys: list[str], today: date) -> set[str]:
    """Database dumps to keep: the last DAILY days, and the first of each month for MONTHLY months."""
    keep = set()
    dated = []
    for k in keys:
        try:
            d = datetime.strptime(Path(k).name[:10], "%Y-%m-%d").date()
        except ValueError:
            keep.add(k)  # not ours: leave it
            continue
        dated.append((d, k))
    months: dict[tuple[int, int], tuple[date, str]] = {}
    for d, k in dated:
        if (today - d).days < DAILY:
            keep.add(k)
        m = (d.year, d.month)
        if m not in months or d < months[m][0]:
            months[m] = (d, k)
    for (y, m), (d, k) in sorted(months.items(), reverse=True)[:MONTHLY]:
        keep.add(k)
    return keep


def run_backup(now: datetime | None = None) -> BackupRun:
    """Back up the database and new or changed files. Raises if there is
    nowhere to back up to or the backup fails (after recording it)."""
    now = now or timezone.now()
    run = BackupRun.objects.create()
    t = target()
    try:
        if t is None:
            raise RuntimeError("No backup target: set BACKUP_S3_BUCKET (and its keys) in .env.")
        run.target = t.describe()
        stamp = timezone.localtime(now).strftime("%Y-%m-%d-%H%M")
        data = dump_database()
        t.put_bytes(f"database/{stamp}.json.gz", data)
        run.database_bytes = len(data)
        dumps = list(t.list("database/"))
        for k in dumps:
            if k not in _keep(dumps, timezone.localtime(now).date()):
                t.delete(k)
        _backup_media(t, run, now)
        run.ok = True
    except Exception as exc:
        run.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        run.finished_at = timezone.now()
        run.save()
    return run


def _backup_media(t: Target, run: BackupRun, now: datetime) -> None:
    media = Path(settings.MEDIA_ROOT)
    have = t.list("media/")
    manifest = json.loads(t.get_bytes("media-manifest.json") or b"{}")
    missing = manifest.get("missing_since", {})
    local = {}
    for p in media.rglob("*") if media.exists() else []:
        if not p.is_file():
            continue
        rel = p.relative_to(media).as_posix()
        if rel.startswith(SKIP):
            continue
        local[f"media/{rel}"] = p
    for key, p in local.items():
        size = p.stat().st_size
        if have.get(key) != size:
            t.put(key, p)
            run.files_sent += 1
            run.bytes_sent += size
        missing.pop(key, None)
    today = timezone.localtime(now).date().isoformat()
    for key in have:
        if key in local:
            continue
        since = missing.setdefault(key, today)
        if (date.fromisoformat(today) - date.fromisoformat(since)).days >= DELETED_GRACE_DAYS:
            t.delete(key)
            missing.pop(key, None)
    run.files_total = len(local)
    t.put_bytes("media-manifest.json", json.dumps({"updated": now.isoformat(), "missing_since": missing}).encode())


# --- restoring ------------------------------------------------------------------------


def list_dumps() -> list[str]:
    t = target()
    return sorted(t.list("database/")) if t else []


def restore(dump_key: str | None = None, files: bool = True) -> str:
    """Load a database dump (the latest by default) into an empty, migrated
    database, and copy the files back. For a new server after a loss."""
    t = target()
    if t is None:
        raise RuntimeError("No backup target set.")
    dumps = sorted(t.list("database/"))
    if not dumps:
        raise RuntimeError("No database dumps in the backup.")
    key = dump_key or dumps[-1]
    data = gzip.decompress(t.get_bytes(key))
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        call_command("loaddata", path, verbosity=0)
    finally:
        os.unlink(path)
    if files:
        media = Path(settings.MEDIA_ROOT)
        for k in t.list("media/"):
            dest = media / k[len("media/"):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            t.get_file(k, dest)
    return key


def last_good():
    return BackupRun.objects.filter(ok=True).first()


def overdue(now=None) -> bool:
    """No successful backup in the last 26 hours."""
    now = now or timezone.now()
    good = last_good()
    return good is None or good.started_at < now - timedelta(hours=26)
