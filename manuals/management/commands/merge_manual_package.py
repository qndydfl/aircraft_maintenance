from django.core.management.base import BaseCommand, CommandError
from manuals.models import ManualPackage
from manuals.merged_package import build_merged_package
from manuals.merged_package import MANIFEST
from pathlib import Path


class Command(BaseCommand):
    help = "Build and activate a merged PDF for one processed IPC package."

    def add_arguments(self, parser):
        parser.add_argument("package_id", type=int)
        parser.add_argument("--disable", action="store_true", help="Return to chapter PDFs without deleting the merged file.")

    def handle(self, *args, **options):
        package = ManualPackage.objects.get(pk=options["package_id"])
        if options["disable"]:
            manifest = Path(package.extracted_path) / MANIFEST
            if manifest.exists():
                manifest.replace(manifest.with_suffix(".disabled.json"))
            self.stdout.write("Merged viewer disabled; original chapter PDFs retained.")
            return
        if package.manual_type != "IPC" or not package.processed:
            raise CommandError("Select a processed IPC package for the pilot.")
        result = build_merged_package(package, progress=self.stdout.write)
        self.stdout.write(self.style.SUCCESS(f"Merged {result['page_count']} pages"))
