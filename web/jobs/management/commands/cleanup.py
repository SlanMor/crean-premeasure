from django.core.management.base import BaseCommand

from jobs.housekeeping import cleanup, plan_cleanup


class Command(BaseCommand):
    help = "Remove files by the retention settings (Settings, System). --dry-run shows what would go."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        if opts["dry_run"]:
            p = plan_cleanup()
            self.stdout.write(f"Would remove: {len(p.tenders)} tenders' files, {len(p.replaced)} replaced revisions, "
                              f"working files of {len(p.working)} drawings; {p.bytes / 1e6:.1f} MB.")
            return
        self.stdout.write(f"Freed {cleanup() / 1e6:.1f} MB.")
