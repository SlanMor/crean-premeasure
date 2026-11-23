from django.core.management.base import BaseCommand

from jobs.backup import run_backup


class Command(BaseCommand):
    help = "Back up the database and files to the backup target now."

    def handle(self, *args, **opts):
        run = run_backup()
        self.stdout.write(f"Backed up to {run.target}: database {run.database_bytes / 1e6:.1f} MB, "
                          f"{run.files_sent} new or changed files ({run.bytes_sent / 1e6:.1f} MB) of {run.files_total}.")
