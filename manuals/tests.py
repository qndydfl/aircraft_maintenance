from datetime import timedelta
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from .forms import CommonManualCategoryForm, ManualPackageForm
from .services import (
    extract_ipc_chapter_titles,
    index_manual_package_files,
    save_package_revision_info,
)
from .models import (
    Aircraft,
    ManualChapter,
    ManualFile,
    ManualFilePDFPage,
    ManualPackage,
    ManualPackageFile,
    ManualPDFPage,
    CommonManualCategory,
    CommonManualFile,
    OtherManualCategory,
    OtherManualFile,
    OtherManualPDFPage,
    OtherManualFolder,
    ReindexJob,
    ManualType,
)


class ManualPackageFileTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="package-file-user",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.aircraft = Aircraft.objects.create(maker="BOEING", name="B737")
        self.package = ManualPackage.objects.create(
            aircraft=self.aircraft,
            manual_type="AMM",
        )

    def test_indexes_original_zip_files_and_keeps_folder_paths(self):
        with TemporaryDirectory(dir=Path.cwd() / "tmp") as temp_dir:
            root = Path(temp_dir)
            nested = root / "ATA 21" / "Forms"
            nested.mkdir(parents=True)
            (root / "index.html").write_text("index", encoding="utf-8")
            (nested / "procedure.pdf").write_bytes(b"pdf")
            (nested / "inspection.xlsx").write_bytes(b"xlsx")
            (nested / "notes.docx").write_bytes(b"docx")

            def fake_convert(input_path, output_dir):
                output_path = Path(output_dir) / (Path(input_path).stem + ".pdf")
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"preview")
                return str(output_path)

            with patch(
                "manuals.services.convert_office_to_pdf",
                side_effect=fake_convert,
            ):
                count = index_manual_package_files(self.package, str(root))

            self.assertEqual(count, 4)
            self.assertEqual(
                set(
                    self.package.package_files.values_list(
                        "relative_path",
                        flat=True,
                    )
                ),
                {
                    "index.html",
                    "ATA 21/Forms/procedure.pdf",
                    "ATA 21/Forms/inspection.xlsx",
                    "ATA 21/Forms/notes.docx",
                },
            )
            office_file = self.package.package_files.get(
                file_name="inspection.xlsx"
            )
            self.assertEqual(office_file.folder_path, "ATA 21/Forms")
            self.assertTrue(office_file.preview_pdf_relative_path)

    def test_office_conversion_failure_keeps_original_file(self):
        with TemporaryDirectory(dir=Path.cwd() / "tmp") as temp_dir:
            root = Path(temp_dir)
            (root / "inspection.xlsx").write_bytes(b"xlsx")

            with patch(
                "manuals.services.convert_office_to_pdf",
                side_effect=RuntimeError("LibreOffice unavailable"),
            ):
                index_manual_package_files(self.package, str(root))

            package_file = self.package.package_files.get()
            self.assertEqual(package_file.relative_path, "inspection.xlsx")
            self.assertEqual(package_file.preview_pdf_relative_path, "")
            self.assertFalse(package_file.can_preview)

    def test_download_and_preview_serve_registered_original(self):
        with TemporaryDirectory(dir=Path.cwd() / "tmp") as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "ATA 21" / "procedure.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"test-pdf")
            self.package.extracted_path = str(root)
            self.package.save(update_fields=["extracted_path"])
            package_file = ManualPackageFile.objects.create(
                package=self.package,
                relative_path="ATA 21/procedure.pdf",
                folder_path="ATA 21",
                file_name="procedure.pdf",
                extension=".pdf",
                file_size=8,
            )

            download_response = self.client.get(
                reverse("manual_package_file_download", args=[package_file.pk])
            )
            preview_response = self.client.get(
                reverse("manual_package_file_preview", args=[package_file.pk])
            )

            self.assertEqual(download_response.status_code, 200)
            self.assertIn(
                "attachment",
                download_response.headers["Content-Disposition"],
            )
            self.assertEqual(b"".join(download_response.streaming_content), b"test-pdf")
            download_response.close()
            self.assertEqual(preview_response.status_code, 200)
            self.assertEqual(preview_response.headers["Content-Type"], "application/pdf")
            b"".join(preview_response.streaming_content)
            preview_response.close()

    def test_file_endpoint_rejects_path_outside_extracted_directory(self):
        with TemporaryDirectory(dir=Path.cwd() / "tmp") as temp_dir:
            root = Path(temp_dir)
            self.package.extracted_path = str(root)
            self.package.save(update_fields=["extracted_path"])
            package_file = ManualPackageFile.objects.create(
                package=self.package,
                relative_path="../outside.pdf",
                file_name="outside.pdf",
                extension=".pdf",
            )

            response = self.client.get(
                reverse("manual_package_file_download", args=[package_file.pk])
            )

            self.assertEqual(response.status_code, 404)

    def test_chapter_workspace_lists_files_by_folder(self):
        ManualPackageFile.objects.create(
            package=self.package,
            relative_path="ATA 21/Forms/inspection.xlsx",
            folder_path="ATA 21/Forms",
            file_name="inspection.xlsx",
            extension=".xlsx",
        )

        response = self.client.get(
            reverse("manual_chapter_list", args=[self.package.pk])
        )

        self.assertContains(response, "Package files")
        self.assertContains(response, "ATA 21")
        self.assertContains(response, "Forms")
        self.assertContains(response, "inspection.xlsx")

    def test_package_upload_form_supports_folder_selection(self):
        response = self.client.get(reverse("manual_package_create"))

        self.assertContains(response, "폴더 선택")
        self.assertContains(response, "개별 파일 선택")
        self.assertContains(response, "webkitdirectory")
        self.assertContains(response, "data-manual-folder-input")
        self.assertContains(response, "data-manual-files-input")
        self.assertContains(response, "manual_folder_upload.js")


class ManualTypeAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="manual-type-admin",
            password="test-password",
            email="manual-type-admin@example.com",
        )
        self.client.force_login(self.user)
        self.manual_type = ManualType.objects.create(
            code="ADMIN_TEST",
            name="Admin Test",
            upload_mode=ManualType.UPLOAD_PDF,
        )

    def test_manual_type_can_be_deleted_from_admin(self):
        delete_url = reverse(
            "admin:manuals_manualtype_delete",
            args=[self.manual_type.pk],
        )
        confirmation = self.client.get(delete_url)

        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Are you sure")

        response = self.client.post(delete_url, {"post": "yes"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ManualType.objects.filter(pk=self.manual_type.pk).exists()
        )


class OtherManualFolderUploadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="other-folder-user",
            password="test-password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.aircraft = Aircraft.objects.create(maker="BOEING", name="B777")
        self.category, _ = OtherManualCategory.objects.get_or_create(
            aircraft=self.aircraft,
            manual_type="OTHER",
            defaults={
                "name": "OTHER",
                "code": "OTHER",
            },
        )

    def test_existing_and_new_aircraft_have_other_manual_categories(self):
        other_aircraft = Aircraft.objects.create(maker="AIRBUS", name="A320")

        self.assertTrue(
            OtherManualCategory.objects.filter(
                aircraft=other_aircraft,
                manual_type="OTHER",
            ).exists()
        )

        response = self.client.get(reverse("manual_home"))
        self.assertContains(response, "Other Manuals")
        self.assertContains(response, "A320")
        self.assertContains(response, "Open OTHER Folder")

        detail_response = self.client.get(
            reverse("aircraft_manual_detail", args=[other_aircraft.pk])
        )
        other_item = next(
            item
            for item in detail_response.context["manual_items"]
            if item["type"] == "OTHER"
        )
        self.assertIsNotNone(other_item["folder"])

    def test_deleting_other_category_redirects_to_manual_library(self):
        response = self.client.post(
            reverse("other_manual_category_delete", args=[self.category.pk])
        )

        self.assertRedirects(response, reverse("manual_home"))
        self.assertFalse(
            OtherManualCategory.objects.filter(pk=self.category.pk).exists()
        )

    def test_create_empty_nested_folders(self):
        create_url = reverse("other_manual_folder_create")
        root_response = self.client.post(
            f"{create_url}?category={self.category.pk}",
            {
                "category": self.category.pk,
                "folder_name": "Documents",
                "parent_path": "",
            },
        )
        self.assertEqual(root_response.status_code, 302)
        self.assertTrue(
            OtherManualFolder.objects.filter(
                category=self.category,
                path="Documents",
            ).exists()
        )

        child_response = self.client.post(
            f"{create_url}?category={self.category.pk}&path=Documents",
            {
                "category": self.category.pk,
                "folder_name": "Forms",
                "parent_path": "Documents",
            },
        )
        self.assertEqual(child_response.status_code, 302)
        self.assertTrue(
            OtherManualFolder.objects.filter(
                category=self.category,
                path="Documents/Forms",
            ).exists()
        )

        browse_response = self.client.get(
            reverse(
                "other_manual_folder_browse",
                args=[self.category.pk],
            ),
            {"path": "Documents/Forms"},
        )
        self.assertEqual(browse_response.status_code, 200)
        self.assertContains(browse_response, "등록된 파일이 없습니다.")

    def test_upload_file_directly_into_nested_folder(self):
        folder = OtherManualFolder.objects.create(
            category=self.category,
            path="Documents/Forms",
        )

        with TemporaryDirectory(dir=Path.cwd() / "tmp") as media_root:
            with override_settings(MEDIA_ROOT=media_root, REINDEX_INLINE=False):
                response = self.client.post(
                    reverse("other_manual_file_create")
                    + f"?category={self.category.pk}&path={folder.path}",
                    {
                        "category": self.category.pk,
                        "folder_path": folder.path,
                        "title": "Checklist",
                        "file": SimpleUploadedFile(
                            "checklist.pdf",
                            b"%PDF-1.4 test",
                            content_type="application/pdf",
                        ),
                        "description": "",
                        "revision_no": "",
                        "revision_date_text": "",
                    },
                )

        self.assertEqual(response.status_code, 302)
        uploaded_file = self.category.files.get(title="Checklist")
        self.assertEqual(uploaded_file.folder_path, "Documents/Forms")
        self.assertEqual(
            uploaded_file.relative_path,
            "Documents/Forms/checklist.pdf",
        )

        browse_response = self.client.get(
            reverse(
                "other_manual_folder_browse",
                args=[self.category.pk],
            ),
            {"path": "Documents/Forms"},
        )
        self.assertContains(browse_response, "Checklist")

    def test_other_folder_form_offers_folder_selection(self):
        response = self.client.get(
            reverse("other_manual_folder_upload")
            + f"?category={self.category.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload OTHER Manuals")
        self.assertContains(response, "개별 파일 선택")
        self.assertContains(response, "webkitdirectory")
        self.assertContains(response, "data-manual-folder-input")
        self.assertContains(response, "data-manual-files-input")

    def test_upload_preserves_nested_other_folder_paths(self):
        archive = io.BytesIO()

        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("Maintenance/Forms/checklist.docx", b"word")
            zip_file.writestr("Maintenance/Data/limits.xlsx", b"excel")
            zip_file.writestr("Maintenance/readme.txt", b"skip")

        archive.seek(0)

        with TemporaryDirectory(dir=Path.cwd() / "tmp") as media_root:
            with override_settings(MEDIA_ROOT=media_root, REINDEX_INLINE=False):
                response = self.client.post(
                    reverse("other_manual_folder_upload")
                    + f"?category={self.category.pk}",
                    {
                        "category": self.category.pk,
                        "folder_package": SimpleUploadedFile(
                            "Maintenance.zip",
                            archive.getvalue(),
                            content_type="application/zip",
                        ),
                        "revision_no": "R1",
                    },
                )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.category.files.count(), 2)
                checklist = self.category.files.get(title="checklist")
                limits = self.category.files.get(title="limits")
                self.assertEqual(
                    checklist.relative_path,
                    "Maintenance/Forms/checklist.docx",
                )
                self.assertEqual(checklist.folder_path, "Maintenance/Forms")
                self.assertEqual(limits.folder_path, "Maintenance/Data")
                self.assertEqual(checklist.revision_no, "R1")
                self.assertEqual(
                    ReindexJob.objects.filter(
                        target_type=ReindexJob.TARGET_OTHER_FILE
                    ).count(),
                    2,
                )

                detail_response = self.client.get(
                    reverse(
                        "other_manual_category_detail",
                        args=[self.category.pk],
                    )
                )
                self.assertContains(detail_response, "Maintenance")
                self.assertContains(detail_response, "OTHER FOLDER")
                self.assertContains(detail_response, "Open")

                maintenance_response = self.client.get(
                    reverse(
                        "other_manual_folder_browse",
                        args=[self.category.pk],
                    ),
                    {"path": "Maintenance"},
                )
                self.assertContains(maintenance_response, "Forms")
                self.assertContains(maintenance_response, "Data")

                forms_response = self.client.get(
                    reverse(
                        "other_manual_folder_browse",
                        args=[self.category.pk],
                    ),
                    {"path": "Maintenance/Forms"},
                )
                self.assertContains(forms_response, "checklist")
                self.assertContains(forms_response, "Download")

                data_response = self.client.get(
                    reverse(
                        "other_manual_folder_browse",
                        args=[self.category.pk],
                    ),
                    {"path": "Maintenance/Data"},
                )
                self.assertContains(data_response, "limits")

    def test_folder_browser_rejects_parent_path(self):
        response = self.client.get(
            reverse("other_manual_folder_browse", args=[self.category.pk]),
            {"path": "../outside"},
        )

        self.assertEqual(response.status_code, 404)


class IPCIndexNavigationTests(TestCase):
    def test_extracts_original_boeing_chapter_headings(self):
        html = """
        <ul>
            <li class="tocHead">CHAPTER 11 - PLACARDS AND MARKINGS
                <ul><li><a href="11-00.pdf">SECTION 11-00</a></li></ul>
            </li>
            <li class="tocHead">CHAPTER 21 - AIR CONDITIONING
                <ul><li><a href="21-fm.pdf">FRONT MATTER</a></li></ul>
            </li>
        </ul>
        """

        with TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "index.html"
            index_path.write_text(html, encoding="utf-8")
            chapter_titles = extract_ipc_chapter_titles(str(index_path))

        self.assertEqual(
            chapter_titles["11"],
            "CHAPTER 11 - PLACARDS AND MARKINGS",
        )
        self.assertEqual(
            chapter_titles["21"],
            "CHAPTER 21 - AIR CONDITIONING",
        )


class AircraftManualAccessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tester",
            password="testpass123",
        )

    def test_airbus_aircraft_detail_page_is_accessible(self):
        aircraft = Aircraft.objects.create(name="A320", maker="AIRBUS")

        self.client.login(username="tester", password="testpass123")

        response = self.client.get(
            reverse("aircraft_manual_detail", kwargs={"pk": aircraft.pk})
        )

        self.assertEqual(response.status_code, 200)

    def test_aircraft_detail_includes_wdm_and_ssm_zip_slots(self):
        aircraft = Aircraft.objects.create(name="B787", maker="BOEING")
        self.client.login(username="tester", password="testpass123")

        response = self.client.get(
            reverse("aircraft_manual_detail", kwargs={"pk": aircraft.pk})
        )

        manual_types = [item["type"] for item in response.context["manual_items"]]
        self.assertIn("WDM", manual_types)
        self.assertIn("SSM", manual_types)

    def test_wdm_and_ssm_are_valid_zip_package_types(self):
        aircraft = Aircraft.objects.create(name="B777", maker="BOEING")

        for manual_type in ("WDM", "SSM"):
            form = ManualPackageForm(
                data={
                    "aircraft": aircraft.pk,
                    "manual_type": manual_type,
                    "revision_no": "",
                    "revision_date_text": "",
                },
                files={
                    "zip_file": SimpleUploadedFile(
                        f"{manual_type.lower()}.zip",
                        b"zip-content",
                        content_type="application/zip",
                    )
                },
            )

            self.assertTrue(form.is_valid(), form.errors)

    def test_new_zip_manual_type_is_available_without_code_changes(self):
        aircraft = Aircraft.objects.create(name="B767", maker="BOEING")
        ManualType.objects.create(
            code="SRM",
            name="SRM",
            upload_mode=ManualType.UPLOAD_ZIP,
            sort_order=55,
        )
        self.client.login(username="tester", password="testpass123")

        response = self.client.get(
            reverse("aircraft_manual_detail", kwargs={"pk": aircraft.pk})
        )

        manual_types = [item["type"] for item in response.context["manual_items"]]
        self.assertIn("SRM", manual_types)

        form = ManualPackageForm(instance=ManualPackage(aircraft=aircraft))
        self.assertIn("SRM", dict(form.fields["manual_type"].choices))


class ManualEditingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="manual-editor",
            password="testpass123",
            email="editor@example.com",
        )
        self.aircraft = Aircraft.objects.create(name="B777", maker="BOEING")
        self.package = ManualPackage.objects.create(
            aircraft=self.aircraft,
            manual_type="WDM",
            zip_file="manual_packages/b777-wdm.zip",
            processed=True,
            revision_no="R1",
        )
        self.client.force_login(self.user)

    def test_package_metadata_can_be_edited_without_reupload(self):
        response = self.client.post(
            reverse("manual_package_update", kwargs={"pk": self.package.pk}),
            {
                "aircraft": self.aircraft.pk,
                "manual_type": "WDM",
                "revision_no": "R2",
                "revision_date_text": "27 OCT 2026",
            },
        )

        self.assertRedirects(
            response,
            reverse("aircraft_manual_detail", kwargs={"pk": self.aircraft.pk}),
        )
        self.package.refresh_from_db()
        self.assertEqual(self.package.revision_no, "R2")
        self.assertEqual(self.package.zip_file.name, "manual_packages/b777-wdm.zip")
        self.assertTrue(self.package.processed)
        self.assertFalse(
            ReindexJob.objects.filter(
                target_type=ReindexJob.TARGET_MANUAL_PACKAGE,
                target_id=self.package.pk,
            ).exists()
        )

    def test_package_card_links_to_edit_view(self):
        response = self.client.get(
            reverse("aircraft_manual_detail", kwargs={"pk": self.aircraft.pk})
        )

        self.assertContains(
            response,
            reverse("manual_package_update", kwargs={"pk": self.package.pk}),
        )


class AircraftManageTabTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="manager",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )
        self.aircraft = Aircraft.objects.create(name="B787", maker="BOEING")
        self.category = CommonManualCategory.objects.create(
            name="Flight Deck",
            code="FLIGHT_DECK",
        )
        self.client.force_login(self.user)

    def test_aircraft_tab_is_the_default(self):
        response = self.client.get(reverse("aircraft_manage"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "aircraft")
        self.assertContains(response, self.aircraft.name)
        self.assertNotContains(response, self.category.name)
        self.assertContains(response, "Add Aircraft")
        self.assertNotContains(response, "Add Common Manual")

    def test_common_tab_shows_common_manual_categories(self):
        response = self.client.get(reverse("aircraft_manage"), {"tab": "common"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "common")
        self.assertContains(response, self.category.name)
        self.assertNotContains(response, self.aircraft.name)
        self.assertContains(response, "Add Common Manual")
        self.assertContains(
            response,
            f"{reverse('aircraft_manage')}?tab=common",
        )

    def test_unknown_tab_falls_back_to_aircraft(self):
        response = self.client.get(reverse("aircraft_manage"), {"tab": "unknown"})

        self.assertEqual(response.context["active_tab"], "aircraft")
        self.assertContains(response, self.aircraft.name)

    def test_common_category_create_returns_to_common_tab(self):
        response = self.client.post(
            reverse("common_manual_category_create"),
            {
                "name": "Cabin Operations",
                "description": "Shared cabin documents",
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('aircraft_manage')}?tab=common",
            fetch_redirect_response=False,
        )


class CalculatorClockLayoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="calculator-user",
            password="testpass123",
        )
        self.client.force_login(self.user)

    def test_utc_and_korea_clocks_are_separate_with_date_and_time(self):
        response = self.client.get(reverse("calculator"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="date-clock-grid"')
        self.assertContains(response, 'id="utc-time"')
        self.assertContains(response, 'id="local-time"')
        self.assertContains(response, "date-clock-date-part", count=2)
        self.assertContains(response, "date-clock-time-part", count=2)
        self.assertContains(response, "KST")

    def test_calculator_notes_do_not_render_initial_formulas(self):
        response = self.client.get(reverse("calculator"))

        self.assertNotContains(response, "0:00 + 0:00 기준")
        self.assertNotContains(response, "0.000 g/cm3 기준")
        self.assertContains(response, "Base time과 Add time을 입력하세요")
        self.assertContains(response, "Density g/cm3 값을 입력하세요")
        self.assertContains(response, 'class="consumption-rate-unit"')

class ManualFilePDFEndpointTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="pdfuser",
            password="testpass123",
        )
        self.aircraft = Aircraft.objects.create(name="A320", maker="AIRBUS")

    def test_manual_file_pdf_endpoint_serves_pdf(self):
        manual = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file=SimpleUploadedFile(
                "sample.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
                content_type="application/pdf",
            ),
        )

        self.client.login(username="pdfuser", password="testpass123")

        response = self.client.get(reverse("manual_file_pdf", kwargs={"pk": manual.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_manual_file_pdf_viewer_uses_manual_file_pdf_endpoint(self):
        manual = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file=SimpleUploadedFile(
                "sample.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
                content_type="application/pdf",
            ),
        )

        self.client.login(username="pdfuser", password="testpass123")

        response = self.client.get(
            reverse("manual_file_pdf_viewer", kwargs={"pk": manual.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("manual_file_pdf", kwargs={"pk": manual.pk}),
        )


class ManualSearchContentPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="searchuser",
            password="testpass123",
        )
        self.aircraft = Aircraft.objects.create(name="A350", maker="AIRBUS")
        self.manual = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file="manuals/a350-mel.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=self.manual,
            page_number=116,
            text=(
                "MEL ENTRIES PRELIMINARY PAGES - TABLE OF CONTENTS "
                "NAV GNSS 1(2)(1+2) REJECTED BY IRs"
            ),
        )
        ManualFilePDFPage.objects.create(
            manual_file=self.manual,
            page_number=379,
            text=(
                "Dispatch Message: NAV GNSS 1(2)(1+2) REJECTED BY IRs "
                "CONDITION OF DISPATCH Refer to Item 34-50-06"
            ),
        )
        self.client.force_login(self.user)

    def test_manual_search_prefers_content_page_over_table_of_contents(self):
        response = self.client.get(
            reverse("manual_search"),
            {
                "aircraft": self.aircraft.pk,
                "q": "nav gnss * rejected by*",
            },
        )

        groups = response.context["result_groups"]

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["match_count"], 1)
        self.assertEqual(groups[0]["first_page"].page_number, 379)

    def test_manual_search_accepts_multiple_manual_types(self):
        cdl = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="CDL",
            file="manuals/a350-cdl.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=cdl,
            page_number=42,
            text="NAV GNSS 1 REJECTED BY IR CDU MESSAGE",
        )

        response = self.client.get(
            reverse("manual_search"),
            {
                "aircraft": self.aircraft.pk,
                "manual_type": ["MEL", "CDL"],
                "q": "nav gnss * rejected by*",
            },
        )

        groups = response.context["result_groups"]

        self.assertEqual(
            {group["manual"] for group in groups},
            {"MEL", "CDL"},
        )
        self.assertEqual(response.context["selected_manual_types"], ["MEL", "CDL"])

    def test_all_aircraft_options_show_manual_counts_and_filter_by_aircraft(self):
        other_aircraft = Aircraft.objects.create(name="B737", maker="BOEING")
        manual_type, _ = ManualType.objects.get_or_create(
            code="MEL",
            defaults={
                "name": "MEL",
                "upload_mode": ManualType.UPLOAD_PDF,
            },
        )
        manual_type.aircraft.set([self.aircraft, other_aircraft])
        other_manual = ManualFile.objects.create(
            aircraft=other_aircraft,
            manual_type="MEL",
            file="manuals/b737-mel.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=other_manual,
            page_number=12,
            text="NAV GNSS 1 REJECTED BY IR CDU MESSAGE",
        )

        response = self.client.get(
            reverse("manual_search"),
            {
                "manual_type": f"AIRCRAFT:{self.aircraft.pk}",
                "q": "nav gnss * rejected by*",
            },
        )

        options = {
            option["value"]: option
            for option in response.context["manual_type_options"]
        }
        self.assertEqual(
            options[f"AIRCRAFT:{self.aircraft.pk}"]["type"],
            "AIRCRAFT",
        )
        self.assertIn("manuals", options[f"AIRCRAFT:{self.aircraft.pk}"]["label"])
        self.assertEqual(
            options[f"AIRCRAFT:{other_aircraft.pk}"]["type"],
            "AIRCRAFT",
        )
        group_options = {
            group["label"]: group["options"]
            for group in response.context["manual_type_groups"]
        }
        self.assertIn("AIRBUS - A350", group_options)
        self.assertIn("BOEING - B737", group_options)
        self.assertEqual(len(group_options["AIRBUS - A350"]), 1)
        self.assertIn("manuals", group_options["AIRBUS - A350"][0]["label"])
        self.assertEqual(
            {group["aircraft"] for group in response.context["result_groups"]},
            {"A350"},
        )

    def test_selecting_aircraft_shows_its_types_without_default_selection(self):
        other_aircraft = Aircraft.objects.create(name="B737", maker="BOEING")
        definitions = list(
            ManualType.objects.filter(is_active=True, is_searchable=True)
        )
        for definition in definitions:
            definition.aircraft.set([other_aircraft])

        for code in ("MEL", "CDL", "OTHER"):
            definition = ManualType.objects.get(code=code)
            definition.aircraft.set([self.aircraft])

        response = self.client.get(
            reverse("manual_search"),
            {"aircraft": self.aircraft.pk},
        )

        self.assertEqual(
            {
                option["type"]
                for option in response.context["manual_type_options"]
            },
            {"MEL", "CDL", "COMMON"},
        )
        self.assertNotIn(
            "Other",
            {group["label"] for group in response.context["manual_type_groups"]},
        )
        self.assertEqual(response.context["selected_manual_type_values"], [])
        self.assertEqual(response.context["selected_manual_types"], [])

    def test_empty_other_folder_is_not_available_in_search(self):
        category, _ = OtherManualCategory.objects.get_or_create(
            aircraft=self.aircraft,
            name="OTHER",
            manual_type="OTHER",
        )
        OtherManualFolder.objects.create(
            category=category,
            path="B747 documents",
        )

        response = self.client.get(
            reverse("manual_search"),
            {"aircraft": self.aircraft.pk},
        )

        self.assertNotIn(
            "OTHER",
            {option["value"] for option in response.context["manual_type_options"]},
        )
        self.assertNotIn(
            "Other",
            {group["label"] for group in response.context["manual_type_groups"]},
        )

    def test_other_manual_is_available_and_searchable_for_aircraft(self):
        other_aircraft = Aircraft.objects.create(name="B737", maker="BOEING")
        other_definition = ManualType.objects.get(code="OTHER")
        other_definition.aircraft.set([other_aircraft])

        category, _ = OtherManualCategory.objects.get_or_create(
            aircraft=self.aircraft,
            name="OTHER",
            manual_type="OTHER",
        )
        other_file = OtherManualFile.objects.create(
            category=category,
            title="Hydraulic Procedure",
            file="manuals/other/hydraulic-procedure.pdf",
        )
        OtherManualPDFPage.objects.create(
            other_file=other_file,
            page_number=3,
            text="HYDRAULIC PUMP PRESSURE CHECK",
        )

        response = self.client.get(
            reverse("manual_search"),
            {
                "aircraft": self.aircraft.pk,
                "manual_type": "OTHER",
                "q": "hydraulic pump pressure",
            },
        )

        options = response.context["manual_type_options"]
        self.assertIn("OTHER", {option["value"] for option in options})
        other_group = next(
            group
            for group in response.context["manual_type_groups"]
            if group["label"] == "Other"
        )
        self.assertEqual(other_group["options"][0]["value"], "OTHER")
        self.assertEqual(len(response.context["result_groups"]), 1)
        self.assertEqual(
            response.context["result_groups"][0]["manual"],
            "OTHER",
        )

    def test_manual_search_filters_wdm_package_content(self):
        package = ManualPackage.objects.create(
            aircraft=self.aircraft,
            manual_type="WDM",
            zip_file="manual_packages/a350-wdm.zip",
            processed=True,
        )
        chapter = ManualChapter.objects.create(
            package=package,
            task="WDM 20-10-00",
            title="Wiring Diagram",
            pdf_relative_path="wdm-20-10-00.pdf",
        )
        ManualPDFPage.objects.create(
            chapter=chapter,
            page_number=7,
            text="WIRE ROUTING CONNECTOR IDENTIFICATION",
        )

        response = self.client.get(
            reverse("manual_search"),
            {
                "aircraft": self.aircraft.pk,
                "manual_type": "WDM",
                "q": "connector identification",
            },
        )

        groups = response.context["result_groups"]
        self.assertEqual(response.context["selected_manual_types"], ["WDM"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["manual"], "WDM")

    def test_manual_search_single_manual_type_excludes_unselected_types(self):
        cdl = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="CDL",
            file="manuals/a350-cdl.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=cdl,
            page_number=42,
            text="NAV GNSS 1 REJECTED BY IR CDU MESSAGE",
        )

        response = self.client.get(
            reverse("manual_search"),
            {
                "aircraft": self.aircraft.pk,
                "manual_type": "MEL",
                "q": "nav gnss * rejected by*",
            },
        )

        groups = response.context["result_groups"]

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["manual"], "MEL")

    def test_manual_viewer_match_navigation_excludes_table_of_contents(self):
        response = self.client.get(
            reverse("manual_file_pdf_viewer", kwargs={"pk": self.manual.pk}),
            {
                "page": 379,
                "q": "nav gnss * rejected by*",
                "from_search_page": 1,
            },
        )

        self.assertEqual(response.context["match_count"], 1)
        self.assertEqual(response.context["matching_pages_json"], "[379]")

    def test_manual_viewer_contains_search_uses_result_page_count(self):
        response = self.client.get(
            reverse("manual_file_pdf_viewer", kwargs={"pk": self.manual.pk}),
            {
                "page": 379,
                "q": "NAV GNSS 1(2)(1+2) REJECTED BY IRs",
                "from_search_page": 1,
            },
        )

        self.assertEqual(response.context["match_count"], 1)
        self.assertEqual(response.context["matching_pages_json"], "[379]")


class ManualPackageViewerMatchCountTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="packageuser",
            password="testpass123",
        )
        self.aircraft = Aircraft.objects.create(name="B777", maker="BOEING")
        self.package = ManualPackage.objects.create(
            aircraft=self.aircraft,
            manual_type="FIM",
            zip_file="manual_packages/fim.zip",
        )
        self.index_chapter = ManualChapter.objects.create(
            package=self.package,
            task="FM",
            title="Front Matter",
            pdf_relative_path="front-matter.pdf",
        )
        self.content_chapter = ManualChapter.objects.create(
            package=self.package,
            task="21-25",
            title="Air Conditioning",
            pdf_relative_path="21-25.pdf",
        )
        ManualPDFPage.objects.create(
            chapter=self.index_chapter,
            page_number=10,
            text="TABLE OF CONTENTS OIL PRESS SENSORS L",
        )
        ManualPDFPage.objects.create(
            chapter=self.content_chapter,
            page_number=25,
            text="FAULT ISOLATION OIL PRESS SENSORS L",
        )
        self.client.force_login(self.user)

    def test_package_viewer_uses_manual_search_result_page_count(self):
        search_response = self.client.get(
            reverse("manual_search"),
            {
                "aircraft": self.aircraft.pk,
                "q": "oil press sensors l",
            },
        )
        viewer_response = self.client.get(
            reverse(
                "manual_chapter_pdf_viewer",
                kwargs={"pk": self.content_chapter.pk},
            ),
            {
                "page": 25,
                "q": "oil press sensors l",
                "from_search_page": 1,
            },
        )

        groups = search_response.context["result_groups"]
        self.assertEqual(groups[0]["match_count"], 1)
        self.assertEqual(viewer_response.context["match_count"], 1)

    def test_chapter_list_starts_without_selected_viewer(self):
        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            ),
            {"q": "oil press sensors l"},
        )

        self.assertEqual(response.context["initial_viewer_url"], "")
        self.assertIsNone(response.context["initial_chapter"])
        self.assertEqual(len(response.context["matching_chapters"]), 1)

    def test_chapter_list_search_results_expose_valid_pk_for_viewer_links(self):
        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            ),
            {"q": "oil press sensors l"},
        )

        self.assertTrue(response.context["matching_chapters"])
        for item in response.context["matching_chapters"]:
            self.assertTrue(item.get("pk"))
            self.assertIsNotNone(item.get("pk"))

    def test_chapter_list_renders_regular_chapter_links_without_chapter_id(self):
        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["chapter_bookmarks"])

    def test_chapter_list_keeps_zero_section_inside_ipc_group(self):
        self.package.manual_type = "IPC"
        self.package.save(update_fields=["manual_type"])

        front_matter = ManualChapter.objects.create(
            package=self.package,
            task="21-fm___116",
            title="CHAPTER 21, FRONT MATTER",
            pdf_relative_path="21-fm.pdf",
        )
        zero_section = ManualChapter.objects.create(
            package=self.package,
            task="21-00___116",
            title="General",
            pdf_relative_path="21-00.pdf",
        )
        ManualChapter.objects.create(
            package=self.package,
            task="21-31___116",
            title="Outflow Valve",
            pdf_relative_path="21-31.pdf",
        )

        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            )
        )
        ata_21_group = next(
            group
            for group in response.context["chapter_groups"]
            if group["key"] == "21"
        )

        self.assertTrue(ata_21_group["is_group"])
        self.assertIsNone(ata_21_group["parent"])
        self.assertEqual(ata_21_group["items"][0], front_matter)
        self.assertEqual(ata_21_group["items"][1], zero_section)
        self.assertEqual(len(ata_21_group["items"]), 4)

    def test_ipc_group_uses_same_aircraft_amm_chapter_title(self):
        self.package.manual_type = "IPC"
        self.package.save(update_fields=["manual_type"])
        amm_package = ManualPackage.objects.create(
            aircraft=self.aircraft,
            manual_type="AMM",
            zip_file="manual_packages/amm.zip",
        )
        ManualChapter.objects.create(
            package=amm_package,
            task="21___107",
            title="CHAPTER 21 - AIR CONDITIONING",
            pdf_relative_path="21.pdf",
        )
        ipc_section = ManualChapter.objects.create(
            package=self.package,
            task="21-31___116",
            title="SECTION 21-31, PRESSURIZATION",
            pdf_relative_path="21-31.pdf",
        )

        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            )
        )
        ata_21_group = next(
            group
            for group in response.context["chapter_groups"]
            if group["key"] == "21"
        )

        self.assertEqual(
            ata_21_group["label"],
            "CHAPTER 21 - AIR CONDITIONING",
        )
        self.assertIn(ipc_section, ata_21_group["items"])

    def test_ipc_group_uses_section_title_when_amm_title_is_unavailable(self):
        self.package.manual_type = "IPC"
        self.package.save(update_fields=["manual_type"])
        ipc_section = ManualChapter.objects.create(
            package=self.package,
            task="11-00___001",
            title="SECTION 11-00, PLACARDS AND MARKINGS",
            pdf_relative_path="11-00.pdf",
        )

        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            )
        )
        ata_11_group = next(
            group
            for group in response.context["chapter_groups"]
            if group["key"] == "11"
        )

        self.assertEqual(
            ata_11_group["label"],
            "CHAPTER 11 - PLACARDS AND MARKINGS",
        )
        self.assertIn(ipc_section, ata_11_group["items"])

    def test_chapter_list_groups_ipc_tasks_by_ata_section_and_earliest_subsection(self):
        self.package.manual_type = "IPC"
        self.package.save(update_fields=["manual_type"])

        ManualChapter.objects.create(
            package=self.package,
            task="22-32___116",
            title="Hydraulics",
            pdf_relative_path="22-32.pdf",
        )
        representative = ManualChapter.objects.create(
            package=self.package,
            task="22-31___116",
            title="Fuel",
            pdf_relative_path="22-31.pdf",
        )
        ManualChapter.objects.create(
            package=self.package,
            task="22-33___116",
            title="Pneumatics",
            pdf_relative_path="22-33.pdf",
        )

        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            )
        )

        ata_22_group = next(
            group
            for group in response.context["chapter_groups"]
            if group["key"] == "22"
        )

        self.assertTrue(ata_22_group["is_group"])
        self.assertIsNone(ata_22_group["parent"])
        self.assertEqual(ata_22_group["items"][0], representative)
        self.assertEqual(len(ata_22_group["items"]), 3)

    def test_chapter_list_keeps_non_ipc_hyphen_task_direct(self):
        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            )
        )

        content_entry = next(
            entry
            for entry in response.context["chapter_groups"]
            if entry["item"] == self.content_chapter
        )

        self.assertFalse(content_entry["is_group"])

    def test_ipc_search_group_renders_section_viewer_links(self):
        self.package.manual_type = "IPC"
        self.package.save(update_fields=["manual_type"])
        zero_section = ManualChapter.objects.create(
            package=self.package,
            task="21-00___116",
            title="General",
            pdf_relative_path="21-00.pdf",
        )
        detail_section = ManualChapter.objects.create(
            package=self.package,
            task="21-31___116",
            title="Outflow Valve",
            pdf_relative_path="21-31.pdf",
        )
        ManualPDFPage.objects.create(
            chapter=zero_section,
            page_number=3,
            text="IPC GROUP SEARCH",
        )
        ManualPDFPage.objects.create(
            chapter=detail_section,
            page_number=8,
            text="IPC GROUP SEARCH",
        )

        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            ),
            {"q": "ipc group search"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("manual_chapter_pdf_viewer", kwargs={"pk": zero_section.pk}),
        )
        self.assertContains(
            response,
            reverse("manual_chapter_pdf_viewer", kwargs={"pk": detail_section.pk}),
        )

    def test_chapter_list_keeps_non_section_task_without_parent(self):
        direct_chapter = ManualChapter.objects.create(
            package=self.package,
            task="06___107",
            title="Dimensions and Areas",
            pdf_relative_path="06.pdf",
        )

        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            )
        )
        direct_entry = next(
            entry
            for entry in response.context["chapter_groups"]
            if not entry["is_group"] and entry["item"] == direct_chapter
        )

        self.assertFalse(direct_entry["is_group"])

    def test_chapter_list_restores_requested_embedded_viewer(self):
        response = self.client.get(
            reverse(
                "manual_chapter_list",
                kwargs={"package_pk": self.package.pk},
            ),
            {
                "chapter": self.content_chapter.pk,
                "page": 25,
            },
        )

        initial_viewer_url = response.context["initial_viewer_url"]

        self.assertIn(
            reverse(
                "manual_chapter_pdf_viewer",
                kwargs={"pk": self.content_chapter.pk},
            ),
            initial_viewer_url,
        )
        self.assertIn("embedded=1", initial_viewer_url)
        self.assertIn("page=25", initial_viewer_url)

    def test_chapter_viewer_preserves_embedded_mode(self):
        response = self.client.get(
            reverse(
                "manual_chapter_pdf_viewer",
                kwargs={"pk": self.content_chapter.pk},
            ),
            {
                "page": 25,
                "q": "oil press sensors l",
                "scope": "chapter",
                "embedded": 1,
            },
        )

        self.assertTrue(response.context["embedded"])
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("embedded=1", response.context["package_chapters_json"])


class CommonManualCategoryFormTests(TestCase):
    def test_category_form_accepts_rendered_fields_only(self):
        form = CommonManualCategoryForm(
            data={
                "name": "Flight Deck",
                "description": "Common flight deck manuals",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)

        category = form.save()

        self.assertEqual(category.name, "Flight Deck")
        self.assertEqual(category.code, "FLIGHT_DECK")
        self.assertEqual(category.order, 0)

    def test_category_form_generates_unique_code_suffix(self):
        CommonManualCategory.objects.create(
            name="Flight Deck",
            code="FLIGHT_DECK",
        )

        form = CommonManualCategoryForm(
            data={
                "name": "Flight Deck",
                "description": "Duplicate name",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)

        category = form.save()

        self.assertEqual(category.code, "FLIGHT_DECK_2")


class ManualRevisionTests(TestCase):
    def setUp(self):
        self.aircraft = Aircraft.objects.create(name="B747", maker="BOEING")

    def test_manual_file_prefers_manually_entered_revision(self):
        manual = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file="manuals/B747_MEL_R10_01JAN2026.pdf",
            revision_no="R11",
            revision_date_text="15 FEB 2026",
        )

        self.assertEqual(manual.display_revision_no, "R11")
        self.assertEqual(manual.display_revision_date_text, "15 FEB 2026")

    def test_package_revision_is_inherited_by_each_chapter(self):
        package = ManualPackage.objects.create(
            aircraft=self.aircraft,
            manual_type="AMM",
            zip_file="manual_packages/B747_AMM_R20_05MAY2026.zip",
            revision_no="R21",
            revision_date_text="10 JUN 2026",
        )
        chapter = ManualChapter.objects.create(
            package=package,
            task="21-00",
            title="Air Conditioning",
            pdf_relative_path="21/21-00.pdf",
        )

        self.assertEqual(chapter.display_revision_no, "R21")
        self.assertEqual(chapter.display_revision_date_text, "10 JUN 2026")

    def test_package_reindex_preserves_manually_entered_revision(self):
        package = ManualPackage.objects.create(
            aircraft=self.aircraft,
            manual_type="FIM",
            zip_file="manual_packages/B747_FIM_R30_01JUL2026.zip",
            revision_no="R31",
            revision_date_text="05 AUG 2026",
        )

        save_package_revision_info(package, index_html_path=None)
        package.refresh_from_db()

        self.assertEqual(package.revision_no, "R31")
        self.assertEqual(package.revision_date_text, "05 AUG 2026")

    def test_package_uses_filename_when_revision_is_blank(self):
        package = ManualPackage.objects.create(
            aircraft=self.aircraft,
            manual_type="IPC",
            zip_file="manual_packages/B747_IPC_R40_05AUG2026.zip",
        )

        save_package_revision_info(package, index_html_path=None)
        package.refresh_from_db()

        self.assertEqual(package.revision_no, "R40")
        self.assertEqual(package.revision_date_text, "05AUG2026")


class CommonManualFileDeleteViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="commonuser",
            password="testpass123",
            is_staff=True,
        )
        self.category = CommonManualCategory.objects.create(
            name="Flight Deck",
            code="FLIGHT_DECK",
        )
        self.common_file = CommonManualFile.objects.create(
            category=self.category,
            title="Checklist",
            file=SimpleUploadedFile(
                "checklist.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
                content_type="application/pdf",
            ),
        )

    def test_delete_page_is_accessible_for_common_manual_file(self):
        self.client.login(username="commonuser", password="testpass123")

        response = self.client.get(
            reverse(
                "common_manual_file_delete",
                kwargs={"pk": self.common_file.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Checklist")
        self.assertContains(
            response,
            reverse(
                "common_manual_category_detail",
                kwargs={"pk": self.category.pk},
            ),
        )


@override_settings(REINDEX_INLINE=False)
class UploadedAtRefreshTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="uploadadmin",
            password="testpass123",
            email="upload@example.com",
        )
        self.aircraft = Aircraft.objects.create(name="B777", maker="BOEING")
        self.category = CommonManualCategory.objects.create(
            name="Common",
            code="COMMON",
        )
        self.client.force_login(self.user)
        self.old_uploaded_at = timezone.now() - timedelta(days=7)

    @staticmethod
    def pdf_file(name):
        return SimpleUploadedFile(
            name,
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
            content_type="application/pdf",
        )

    def test_manual_pdf_reupload_refreshes_uploaded_at(self):
        manual = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file=self.pdf_file("old-mel.pdf"),
        )
        ManualFile.objects.filter(pk=manual.pk).update(uploaded_at=self.old_uploaded_at)

        response = self.client.post(
            reverse("manual_file_update", kwargs={"pk": manual.pk}),
            {
                "aircraft": self.aircraft.pk,
                "manual_type": "MEL",
                "description": "Re-uploaded",
                "revision_no": "",
                "revision_date_text": "",
                "file": self.pdf_file("new-mel.pdf"),
            },
        )

        manual.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertGreater(manual.uploaded_at, self.old_uploaded_at)

    def test_package_reupload_refreshes_uploaded_at(self):
        package = ManualPackage.objects.create(
            aircraft=self.aircraft,
            manual_type="AMM",
            zip_file=SimpleUploadedFile(
                "old-amm.zip",
                b"old zip",
                content_type="application/zip",
            ),
        )
        ManualPackage.objects.filter(pk=package.pk).update(
            uploaded_at=self.old_uploaded_at
        )

        response = self.client.post(
            reverse(
                "manual_package_reupload",
                kwargs={"package_pk": package.pk},
            ),
            {
                "aircraft": self.aircraft.pk,
                "manual_type": "AMM",
                "revision_no": "",
                "revision_date_text": "",
                "zip_file": SimpleUploadedFile(
                    "new-amm.zip",
                    b"new zip",
                    content_type="application/zip",
                ),
            },
        )

        package.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertGreater(package.uploaded_at, self.old_uploaded_at)

    def test_common_manual_file_edit_refreshes_uploaded_at(self):
        common_file = CommonManualFile.objects.create(
            category=self.category,
            title="Common Manual",
            file=self.pdf_file("old-common.pdf"),
        )
        CommonManualFile.objects.filter(pk=common_file.pk).update(
            uploaded_at=self.old_uploaded_at
        )

        response = self.client.post(
            reverse("common_manual_file_update", kwargs={"pk": common_file.pk}),
            {
                "category": self.category.pk,
                "title": "Common Manual",
                "description": "Updated file",
                "revision_no": "",
                "revision_date_text": "",
                "file": self.pdf_file("new-common.pdf"),
            },
        )

        common_file.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertGreater(common_file.uploaded_at, self.old_uploaded_at)

    def test_common_manual_metadata_edit_keeps_uploaded_at(self):
        common_file = CommonManualFile.objects.create(
            category=self.category,
            title="Common Manual",
            file=self.pdf_file("common.pdf"),
        )
        CommonManualFile.objects.filter(pk=common_file.pk).update(
            uploaded_at=self.old_uploaded_at
        )

        response = self.client.post(
            reverse("common_manual_file_update", kwargs={"pk": common_file.pk}),
            {
                "category": self.category.pk,
                "title": "Renamed Common Manual",
                "description": "Metadata only",
                "revision_no": "",
                "revision_date_text": "",
            },
        )

        common_file.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(common_file.uploaded_at, self.old_uploaded_at)
