"""Deleting drawings and tenders, and cleaning up files by the retention settings."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import CheckedJob, DeletionLog, Premeasure, Project, SystemSettings, Upload

WORKING_FIELDS = ("analysis_file", "changes")  # made again when needed


def _size(field) -> int:
    try:
        return field.size if field else 0
    except (FileNotFoundError, OSError, ValueError):
        return 0


def _drop(field) -> int:
    """Delete a stored file; returns the bytes freed."""
    n = _size(field)
    if field:
        try:
            field.delete(save=False)
        except (FileNotFoundError, OSError):
            pass
    return n


def _zoom(pm: Premeasure) -> int:
    from .tender import zoom_page_path

    path = zoom_page_path(pm)
    if path.exists():
        n = path.stat().st_size
        path.unlink()
        return n
    return 0


def premeasure_bytes(pm: Premeasure) -> int:
    from .tender import zoom_page_path

    n = sum(_size(getattr(pm, f)) for f in ("analysis_file", "preview", "output", "changes"))
    path = zoom_page_path(pm)
    return n + (path.stat().st_size if path.exists() else 0)


# --- deleting -----------------------------------------------------------------------


def delete_premeasure(pm: Premeasure, by=None, reason: str = "by hand") -> int:
    """A drawing and its files. The PDF goes too unless another page of it is
    still in use. A later revision forgets this one; an earlier one becomes
    current again. Checked Callidus jobs stay (with their figures), unlinked."""
    freed = sum(_drop(getattr(pm, f)) for f in ("analysis_file", "preview", "output", "changes")) + _zoom(pm)
    title, upload = pm.title, pm.drawing
    try:
        newer = pm.superseded_by
    except Premeasure.DoesNotExist:
        newer = None
    with transaction.atomic():
        if newer is not None:
            newer.supersedes, newer.revision_diff = None, None
            newer.save(update_fields=["supersedes", "revision_diff", "updated_at"])
            _drop(newer.changes)
        pm.delete()
        if not upload.premeasures.exists():
            freed += _drop(upload.file)
            upload.delete()
    DeletionLog.objects.create(what=f"Drawing {title} ({pm.project.code})", reason=reason, bytes_freed=freed, by=by)
    return freed


def delete_project_files(project: Project, by=None, reason: str = "by hand") -> int:
    """Every drawing, upload and Callidus file of a tender. The tender, its
    products and the figures of its checked jobs stay."""
    freed = 0
    for pm in list(project.premeasures.order_by("-created_at")):
        freed += delete_premeasure(pm, by=None, reason=reason)
    for job in project.checked_jobs.exclude(upload=None).select_related("upload"):
        up = job.upload
        job.upload = None
        job.save(update_fields=["upload"])
        if up is not None and not CheckedJob.objects.filter(upload=up).exists():
            freed += _drop(up.file)
            up.delete()
    for up in project.uploads.all():
        if not up.premeasures.exists() and not up.premeasures_started.exists():
            freed += _drop(up.file)
            up.delete()
    project.files_removed_at = timezone.now()
    project.save(update_fields=["files_removed_at"])
    DeletionLog.objects.create(what=f"Files of tender {project.code} {project.name}", reason=reason,
                               bytes_freed=freed, by=by)
    return freed


def delete_project(project: Project, by=None) -> int:
    """A whole tender and everything in it."""
    what = f"Tender {project.code} {project.name}"
    freed = delete_project_files(project, by=None, reason="tender deleted")
    for up in project.uploads.all():
        freed += _drop(up.file)
    with transaction.atomic():
        Premeasure.objects.filter(project=project).delete()
        CheckedJob.objects.filter(project=project).delete()
        Upload.objects.filter(project=project).update(background=None)
        Upload.objects.filter(project=project).delete()
        project.delete()
    DeletionLog.objects.create(what=what, reason="by hand", bytes_freed=freed, by=by)
    return freed


# --- retention ----------------------------------------------------------------------


@dataclass
class Plan:
    working: list[Premeasure] = field(default_factory=list)
    replaced: list[Premeasure] = field(default_factory=list)
    tenders: list[Project] = field(default_factory=list)
    bytes: int = 0

    @property
    def empty(self) -> bool:
        return not (self.working or self.replaced or self.tenders)


def last_activity(project: Project):
    times = [project.created_at,
             project.premeasures.aggregate(t=Max("updated_at"))["t"],
             project.checked_jobs.aggregate(t=Max("uploaded_at"))["t"],
             project.uploads.aggregate(t=Max("uploaded_at"))["t"]]
    return max(t for t in times if t is not None)


def plan_cleanup(now=None) -> Plan:
    """What the retention settings would remove now."""
    from .tender import zoom_page_path

    now = now or timezone.now()
    s = SystemSettings.get()
    plan = Plan()
    if s.tender_months:
        cutoff = now - timedelta(days=30 * s.tender_months)
        for p in Project.objects.filter(keep_files=False):
            if not (p.premeasures.exists() or p.checked_jobs.exclude(upload=None).exists()):
                continue
            if last_activity(p) < cutoff:
                plan.tenders.append(p)
                plan.bytes += project_bytes(p)
    skip = {p.pk for p in plan.tenders}
    if s.replaced_days:
        cutoff = now - timedelta(days=s.replaced_days)
        for pm in Premeasure.objects.filter(superseded_by__isnull=False).exclude(project__in=skip) \
                .exclude(project__keep_files=True).select_related("superseded_by"):
            if pm.superseded_by.created_at < cutoff:
                plan.replaced.append(pm)
                plan.bytes += premeasure_bytes(pm) + (_size(pm.drawing.file) if pm.drawing.premeasures.count() == 1 else 0)
    gone = {pm.pk for pm in plan.replaced}
    cutoff = now - timedelta(days=s.working_files_days)
    for pm in Premeasure.objects.filter(updated_at__lt=cutoff).exclude(project__in=skip).exclude(pk__in=gone):
        n = sum(_size(getattr(pm, f)) for f in WORKING_FIELDS)
        z = zoom_page_path(pm)
        n += z.stat().st_size if z.exists() else 0
        if n:
            plan.working.append(pm)
            plan.bytes += n
    return plan


def cleanup(now=None) -> int:
    """Remove what the retention settings say. Returns the bytes freed."""
    plan = plan_cleanup(now)
    freed = 0
    for p in plan.tenders:
        freed += delete_project_files(p, reason="retention: tender inactive")
    for pm in plan.replaced:
        if Premeasure.objects.filter(pk=pm.pk).exists():
            freed += delete_premeasure(pm, reason="retention: replaced revision")
    n_working, working = 0, 0
    for pm in plan.working:
        b = sum(_drop(getattr(pm, f)) for f in WORKING_FIELDS) + _zoom(pm)
        if b:
            Premeasure.objects.filter(pk=pm.pk).update(analysis_file="", changes="")
            n_working += 1
            working += b
    if n_working:
        DeletionLog.objects.create(what=f"Working files of {n_working} drawing{'s' if n_working != 1 else ''}",
                                   reason="retention: working files", bytes_freed=working)
    return freed + working


# --- how much space -------------------------------------------------------------------


def project_bytes(project: Project) -> int:
    n = sum(premeasure_bytes(pm) for pm in project.premeasures.all())
    return n + sum(_size(u.file) for u in project.uploads.all())


def folder_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def disk() -> dict:
    media = Path(settings.MEDIA_ROOT)
    media.mkdir(parents=True, exist_ok=True)
    u = shutil.disk_usage(media)
    return {"total": u.total, "used": u.used, "free": u.free, "pct": round(u.used / u.total * 100, 1),
            "media": folder_bytes(media)}
