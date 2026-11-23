from django.core.management.base import BaseCommand

from jobs.backup import list_dumps, restore


class Command(BaseCommand):
    help = ("Restore from the backup: the latest database dump (or --dump KEY) into an empty, migrated database, "
            "and the files. --list shows the dumps kept.")

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true")
        parser.add_argument("--dump", default=None)
        parser.add_argument("--no-files", action="store_true")

    def handle(self, *args, **opts):
        if opts["list"]:
            for k in list_dumps():
                self.stdout.write(k)
            return
        key = restore(opts["dump"], files=not opts["no_files"])
        self.stdout.write(f"Restored {key}.")
