"""Runs the regular jobs: the nightly backup, the daily clean-up, and the
checks every 15 minutes. One small process (the scheduler service)."""

import os
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.alerts import check_all, raise_alert, clear_alert


class Command(BaseCommand):
    help = "Run backups, clean-up and checks on a schedule (runs until stopped)."

    def handle(self, *args, **opts):
        backup_hour = int(os.environ.get("BACKUP_HOUR", "2"))
        cleanup_hour = int(os.environ.get("CLEANUP_HOUR", "4"))
        done: dict[str, str] = {}
        last_check = 0.0
        self.stdout.write("Scheduler running.")
        while True:
            now = timezone.localtime()
            today = now.date().isoformat()
            if now.hour == backup_hour and done.get("backup") != today:
                done["backup"] = today
                self.run("backup", self.backup)
            if now.hour == cleanup_hour and done.get("cleanup") != today:
                done["cleanup"] = today
                self.run("cleanup", self.cleanup)
            if time.monotonic() - last_check >= 15 * 60:
                last_check = time.monotonic()
                self.run("checks", check_all)
            time.sleep(30)

    def run(self, name, fn):
        try:
            fn()
        except Exception as exc:  # keep going; the alert says what failed
            self.stderr.write(f"{name} failed: {type(exc).__name__}: {exc}")

    def backup(self):
        from jobs.backup import run_backup

        try:
            run_backup()
            clear_alert("backup-failed")
        except Exception as exc:
            raise_alert("backup-failed", "Last night's backup failed", f"{type(exc).__name__}: {exc}")
            raise

    def cleanup(self):
        from jobs.housekeeping import cleanup

        cleanup()
