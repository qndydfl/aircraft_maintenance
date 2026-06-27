import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dispatch.services import extract_mel_dispatch_items_from_pdf
from manuals.models import (
    CommonManualFile,
    ManualFile,
    ManualPackage,
    OtherManualFile,
    ReindexJob,
)
from manuals.services import (
    index_pdf_pages_for_common_manual_file_safely,
    index_pdf_pages_for_manual_file_safely,
    index_pdf_pages_for_other_manual_file_safely,
    process_manual_package_safely,
)


class Command(BaseCommand):
    help = "Process queued manual re-index jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process the current queue once and exit.",
        )
        parser.add_argument(
            "--sleep",
            type=int,
            default=5,
            help="Seconds to wait between queue checks.",
        )
        parser.add_argument(
            "--stale-minutes",
            type=int,
            default=120,
            help="Mark processing jobs older than this as failed before claiming the next job.",
        )

    def handle(self, *args, **options):
        run_once = options["once"]
        sleep_seconds = options["sleep"]
        stale_minutes = options["stale_minutes"]

        while True:
            self.fail_stale_processing_jobs(stale_minutes)
            job = self.claim_next_job()

            if job:
                self.process_job(job)
                continue

            if run_once:
                break

            time.sleep(sleep_seconds)

    def fail_stale_processing_jobs(self, stale_minutes):
        if stale_minutes <= 0:
            return

        cutoff = timezone.now() - timedelta(minutes=stale_minutes)
        stale_jobs = ReindexJob.objects.filter(
            status=ReindexJob.STATUS_PROCESSING,
            started_at__lt=cutoff,
            finished_at__isnull=True,
        )

        failed_count = stale_jobs.update(
            status=ReindexJob.STATUS_FAILED,
            message="Processing job timed out. Please run re-index again.",
            finished_at=timezone.now(),
        )

        if failed_count:
            self.stderr.write(
                self.style.WARNING(
                    f"Marked {failed_count} stale processing job(s) as failed."
                )
            )

    def claim_next_job(self):
        with transaction.atomic():
            job = (
                ReindexJob.objects.select_for_update()
                .filter(status=ReindexJob.STATUS_QUEUED)
                .order_by("created_at")
                .first()
            )

            if not job:
                return None

            job.status = ReindexJob.STATUS_PROCESSING
            job.started_at = timezone.now()
            job.message = "Processing re-index job..."
            job.save(update_fields=["status", "started_at", "message"])

            return job

    def process_job(self, job):
        try:
            page_count, message = self.run_job(job)

            job.status = ReindexJob.STATUS_DONE
            job.page_count = page_count
            job.message = message
            job.finished_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "page_count",
                    "message",
                    "finished_at",
                ]
            )

            self.stdout.write(self.style.SUCCESS(f"Job {job.pk}: {message}"))

        except Exception as error:
            job.status = ReindexJob.STATUS_FAILED
            job.message = str(error)[:255]
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "message", "finished_at"])

            self.stderr.write(self.style.ERROR(f"Job {job.pk} failed: {error}"))

    def run_job(self, job):
        if job.target_type == ReindexJob.TARGET_MANUAL_PACKAGE:
            package = ManualPackage.objects.get(pk=job.target_id)
            result = process_manual_package_safely(package)
            return (
                result["page_count"],
                f"{package.manual_type} re-index complete: {result['page_count']} pages",
            )

        if job.target_type == ReindexJob.TARGET_MANUAL_FILE:
            manual = ManualFile.objects.get(pk=job.target_id)
            page_count = index_pdf_pages_for_manual_file_safely(manual)

            if manual.manual_type == "MEL":
                mel_count = extract_mel_dispatch_items_from_pdf(manual)
                return (
                    page_count,
                    f"{manual.manual_type} re-index complete: {page_count} pages, {mel_count} MEL messages",
                )

            return (
                page_count,
                f"{manual.manual_type} re-index complete: {page_count} pages",
            )

        if job.target_type == ReindexJob.TARGET_COMMON_FILE:
            common_file = CommonManualFile.objects.get(pk=job.target_id)
            page_count = index_pdf_pages_for_common_manual_file_safely(common_file)
            return (
                page_count,
                f"Common manual re-index complete: {page_count} pages",
            )

        if job.target_type == ReindexJob.TARGET_OTHER_FILE:
            other_file = OtherManualFile.objects.get(pk=job.target_id)
            page_count = index_pdf_pages_for_other_manual_file_safely(other_file)
            return (
                page_count,
                f"OTHER file re-index complete: {page_count} pages",
            )

        raise ValueError(f"Unsupported job target type: {job.target_type}")
