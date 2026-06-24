from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from .forms import CommonManualCategoryForm
from .models import Aircraft, ManualFile, CommonManualCategory, CommonManualFile


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
