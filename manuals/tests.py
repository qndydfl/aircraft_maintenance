from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from .forms import CommonManualCategoryForm
from .services import extract_ipc_chapter_titles, save_package_revision_info
from .models import (
    Aircraft,
    ManualChapter,
    ManualFile,
    ManualFilePDFPage,
    ManualPackage,
    ManualPDFPage,
    CommonManualCategory,
    CommonManualFile,
)


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

    def test_airbus_aircraft_print_page_is_accessible(self):
        aircraft = Aircraft.objects.create(name="A320", maker="AIRBUS")

        self.client.login(username="tester", password="testpass123")

        response = self.client.get(
            reverse("aircraft_manual_print", kwargs={"pk": aircraft.pk})
        )

        self.assertEqual(response.status_code, 200)


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
