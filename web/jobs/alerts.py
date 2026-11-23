"""Telling the administrators when something goes wrong: a drawing failing
with a program error, the worker stopped or stuck, the disk filling up, or the
nightly backup missed. Each problem is emailed once, again every 12 hours
while it lasts, and once more when it clears."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.utils import timezone

from .models import Alert, Premeasure, SystemSettings

REPEAT = timedelta(hours=12)
STUCK = timedelta(minutes=45)  # longer than any drawing takes
DISK_PCT = 85


def recipients() -> list[str]:
    typed = [e.strip() for e in SystemSettings.get().alert_emails.splitlines() if "@" in e]
    if typed:
        return typed
    return list(User.objects.filter(is_staff=True, is_active=True).exclude(email="").values_list("email", flat=True))


def _mail(subject: str, body: str) -> bool:
    to = recipients()
    if not (to and settings.EMAIL_HOST):
        return False
    try:
        send_mail(f"[Crean pre-measure] {subject}", body + f"\n\n{settings.SITE_URL.rstrip('/')}/settings/system/",
                  None, to)
        return True
    except Exception:
        return False  # the System page still shows it


def raise_alert(key: str, subject: str, body: str = "") -> Alert:
    """A problem is happening. Emailed now, and again every 12 hours while it lasts."""
    now = timezone.now()
    alert, created = Alert.objects.get_or_create(key=key, defaults={"subject": subject, "body": body})
    if not created and alert.resolved_at is not None:  # back again after clearing
        alert.first_at, alert.resolved_at, alert.sent_at = now, None, None
    alert.subject, alert.body = subject, body
    if alert.sent_at is None or now - alert.sent_at >= REPEAT:
        if _mail(subject, body):
            alert.sent_at = now
    alert.save()
    return alert


def clear_alert(key: str) -> None:
    alert = Alert.objects.filter(key=key, resolved_at=None).first()
    if alert is None:
        return
    alert.resolved_at = timezone.now()
    alert.save(update_fields=["resolved_at"])
    if alert.sent_at:
        _mail(f"Resolved: {alert.subject}", "This has cleared.")


def drawing_failed(pm: Premeasure) -> None:
    """A drawing failed with a program error (not a drawing the estimator can fix)."""
    from django.urls import reverse

    link = settings.SITE_URL.rstrip("/") + reverse("premeasure", args=[pm.pk])
    raise_alert(f"drawing-{pm.pk}", f"A drawing failed: {pm.title}",
                f"{pm.project} / {pm.title} could not be {'written' if pm.summary else 'read'}.\n\n{pm.error}\n\n{link}")


# --- the regular checks ---------------------------------------------------------------


def check_all(now=None) -> list[str]:
    """Run every check; returns the problems found (by subject)."""
    now = now or timezone.now()
    found = []

    stuck = Premeasure.objects.filter(status__in=[Premeasure.Status.QUEUED, Premeasure.Status.READING,
                                                  Premeasure.Status.WRITING], updated_at__lt=now - STUCK)
    if stuck.exists():
        names = ", ".join(stuck.values_list("title", flat=True)[:5])
        found.append(raise_alert("stuck", f"{stuck.count()} drawing(s) waiting for over 45 minutes",
                                 f"The worker may have stopped. Waiting: {names}.\n\nOn the server: "
                                 "docker compose ps, then docker compose restart worker.").subject)
    else:
        clear_alert("stuck")

    if not settings.JOBS_INLINE:
        try:
            from redis import Redis
            from rq import Worker

            workers = Worker.all(connection=Redis.from_url(settings.REDIS_URL))
            ok = bool(workers)
        except Exception:
            ok = False
        if not ok:
            found.append(raise_alert("worker", "The worker that reads drawings is not running",
                                     "Drawings will wait until it is back.\n\nOn the server: docker compose ps, "
                                     "then docker compose up -d worker.").subject)
        else:
            clear_alert("worker")

    from .housekeeping import disk

    d = disk()
    if d["pct"] >= DISK_PCT:
        found.append(raise_alert("disk", f"The server's disk is {d['pct']:.0f}% full",
                                 f"{d['free'] / 1e9:.1f} GB free. Clean up old files in Settings, System, or "
                                 "shorten the retention settings.").subject)
    else:
        clear_alert("disk")

    from .backup import overdue, target

    if target() is None:
        found.append(raise_alert("backup-target", "Backups are not set up",
                                 "No backup target: set BACKUP_S3_* in .env (docs/deploy.md).").subject)
    else:
        clear_alert("backup-target")
        if overdue(now):
            found.append(raise_alert("backup", "No backup in the last 26 hours",
                                     "See Settings, System for the last attempt and its error.").subject)
        else:
            clear_alert("backup")
    return found

