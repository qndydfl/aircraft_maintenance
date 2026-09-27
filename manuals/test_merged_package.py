import json
import zipfile
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory, override_settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.urls import reverse

from .models import Aircraft, ManualPackage, ManualChapter, ManualPDFPage
from .merged_package import (
    build_merged_package,
    load_merged_package,
    merged_pdf_response,
)


class MergedPackageTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.package = ManualPackage.objects.create(
            aircraft=Aircraft.objects.create(name="Pilot", maker="BOEING"),
            manual_type="IPC",
            extracted_path=str(self.root),
            processed=True,
        )
        self.chapters = []
        for task in ("21-00", "11-00"):
            with fitz.open() as doc:
                for number in range(2):
                    doc.new_page().insert_text((30, 30), f"{task} sample {number}")
                doc.set_toc([[1, "Details", 2]])
                doc.save(self.root / f"{task}.pdf")
            chapter = ManualChapter.objects.create(
                package=self.package,
                task=task,
                title=task,
                pdf_relative_path=f"{task}.pdf",
            )
            ManualPDFPage.objects.create(chapter=chapter, page_number=2, text="sample")
            self.chapters.append(chapter)
        self.client.force_login(get_user_model().objects.create_user(username="pilot"))

    def test_chapter_viewer_uses_source_pdf_when_merged_file_exists(self):
        result = build_merged_package(self.package)
        self.assertEqual(result["page_count"], 4)
        with fitz.open(self.root / result["file"]) as doc:
            self.assertIn("21-00", doc[0].get_text())
            self.assertIn("11-00", doc[2].get_text())
            self.assertEqual([row[2] for row in doc.get_toc()], [1, 2, 3, 4])
        response = self.client.get(
            reverse("manual_chapter_pdf_viewer", args=[self.chapters[1].pk]),
            {"page": 2, "q": "sample"},
        )
        self.assertEqual(response.context["page_number"], 2)
        self.assertEqual(json.loads(response.context["matching_pages_json"]), [2])
        self.assertEqual(
            response.context["pdf_url"],
            reverse("manual_chapter_pdf", args=[self.chapters[1].pk]),
        )
        outline_response = self.client.get(response.context["pdf_url"] + "?outline=1")
        self.assertEqual(
            outline_response.json()["outline"],
            [{"depth": 0, "title": "Details", "pageNumber": 2}],
        )
        pdf_response = self.client.get(response.context["pdf_url"])
        self.assertEqual(pdf_response.status_code, 200)
        self.assertTrue(b"".join(pdf_response.streaming_content).startswith(b"%PDF"))
        pdf_response.close()

    def test_failed_merge_keeps_previous_manifest(self):
        original = build_merged_package(self.package)
        self.chapters[1].pdf_relative_path = "missing.pdf"
        self.chapters[1].save()
        with self.assertRaises(Exception):
            build_merged_package(self.package)
        self.assertEqual(load_merged_package(self.package), original)

    def test_ranges_and_authentication(self):
        data = build_merged_package(self.package)
        path = self.root / data["file"]
        request = RequestFactory().get(f"/?version={path.name}", HTTP_RANGE="bytes=0-9")
        response = merged_pdf_response(request, path)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b"".join(response.streaming_content), path.read_bytes()[:10])
        self.assertEqual(
            response["Cache-Control"], "private, max-age=31536000, immutable"
        )
        etag = response["ETag"]
        response.close()
        response = merged_pdf_response(
            RequestFactory().get("/", HTTP_IF_NONE_MATCH=etag), path
        )
        self.assertEqual(response.status_code, 304)
        response = merged_pdf_response(
            RequestFactory().get("/", HTTP_RANGE="bytes=999999999-"), path
        )
        self.assertEqual(response.status_code, 416)
        self.client.logout()
        self.assertEqual(
            self.client.get(
                reverse("manual_package_merged_pdf", args=[self.package.pk])
            ).status_code,
            302,
        )

    def test_initial_ipc_processing_does_not_build_merged_pdf(self):
        archive = self.root / "source.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            for chapter in self.chapters:
                zipped.write(
                    self.root / chapter.pdf_relative_path, chapter.pdf_relative_path
                )

        self.package.zip_file.storage = FileSystemStorage(location=str(self.root))
        self.package.zip_file.save("upload.zip", ContentFile(archive.read_bytes()))
        self.package.zip_file.storage = FileSystemStorage(location=str(self.root))

        from .services import process_manual_package_safely

        with override_settings(MEDIA_ROOT=str(self.root / "staging")):
            process_manual_package_safely(self.package)

        self.package.refresh_from_db()
        merged = load_merged_package(self.package)
        self.assertIsNone(merged)

    def test_reindex_failure_preserves_previous_chapters_and_pdf(self):
        from .services import process_manual_package_safely

        original = build_merged_package(self.package)
        archive = self.root / "source.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            for chapter in self.chapters:
                zipped.write(
                    self.root / chapter.pdf_relative_path, chapter.pdf_relative_path
                )
        self.package.zip_file.storage = FileSystemStorage(location=str(self.root))
        self.package.zip_file.save("upload.zip", ContentFile(archive.read_bytes()))
        self.package.zip_file.storage = FileSystemStorage(location=str(self.root))
        previous_ids = [chapter.pk for chapter in self.chapters]
        with override_settings(MEDIA_ROOT=str(self.root / "staging")):
            with patch(
                "manuals.services.index_pdf_pages_for_package",
                side_effect=RuntimeError("index failed"),
            ):
                with self.assertRaises(RuntimeError):
                    process_manual_package_safely(self.package)
        self.package.refresh_from_db()
        self.assertEqual(self.package.extracted_path, str(self.root))
        self.assertEqual(
            list(self.package.chapters.order_by("pk").values_list("pk", flat=True)),
            previous_ids,
        )
        self.assertEqual(load_merged_package(self.package), original)
