from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from .forms import CommonManualCategoryForm
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
