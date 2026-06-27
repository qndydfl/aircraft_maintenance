from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check the active file storage backend by saving and deleting a tiny test file."

    def handle(self, *args, **options):
        self.stdout.write(f"USE_R2={getattr(settings, 'USE_R2', None)}")
        self.stdout.write(f"default_storage={default_storage.__class__.__module__}.{default_storage.__class__.__name__}")
        self.stdout.write(f"bucket_set={bool(getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None))}")
        self.stdout.write(f"endpoint_set={bool(getattr(settings, 'AWS_S3_ENDPOINT_URL', None))}")

        test_name = f"diagnostics/storage-check-{uuid4().hex}.txt"
        saved_name = default_storage.save(
            test_name,
            ContentFile(b"manualportal storage check\n"),
        )

        self.stdout.write(f"saved_name={saved_name}")
        self.stdout.write(f"exists_after_save={default_storage.exists(saved_name)}")

        default_storage.delete(saved_name)

        self.stdout.write(f"exists_after_delete={default_storage.exists(saved_name)}")

        if default_storage.exists(saved_name):
            self.stderr.write(
                self.style.ERROR(
                    "Storage delete check failed. The test object still exists."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS("Storage save/delete check passed."))
