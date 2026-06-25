from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dispatch.models import DispatchReference, MelDispatchItem
from dispatch.services import (
    clean_condition,
    extract_level,
    extract_mel_item,
    merge_mel_row_with_context,
)
from manuals.models import Aircraft


class DispatchAutoSearchViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tester",
            password="password123",
        )
        self.client.force_login(self.user)
        self.aircraft = Aircraft.objects.create(name="B737-800", maker="BOEING")
        self.url = reverse("dispatch_auto_search")

    def test_message_search_is_exact_without_wildcards_for_mel_rows(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="RECIRC FANS OFF",
            level="S",
            condition="Valid condition",
            mel_item="21-25-01-01",
            adc="RTG",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="LEFT RECIRC FANS OFF",
            level="S",
            condition="Another condition",
            mel_item="21-25-01-02",
            adc="RTG",
        )

        response = self.client.get(
            self.url,
            {"q": "RECIRC FANS OFF", "search_type": "message"},
        )

        rows = list(response.context["mel_dispatch_rows"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].message, "RECIRC FANS OFF")

    def test_message_search_uses_contains_when_wrapped_with_wildcards(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="RECIRC FANS OFF",
            level="S",
            condition="Valid condition",
            mel_item="21-25-01-01",
            adc="RTG",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="LEFT RECIRC FANS OFF",
            level="S",
            condition="Another condition",
            mel_item="21-25-01-02",
            adc="RTG",
        )

        response = self.client.get(
            self.url,
            {"q": "*RECIRC FANS OFF*", "search_type": "message"},
        )

        rows = list(response.context["mel_dispatch_rows"])

        self.assertEqual(len(rows), 2)

    def test_message_search_uses_connected_wildcard_for_mel_rows(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="RECIRC FANS OFF",
            level="S",
            condition="Valid condition",
            mel_item="21-25-01-01",
            adc="RTG",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="RECIRC FAN ON",
            level="S",
            condition="Another condition",
            mel_item="21-25-01-02",
            adc="RTG",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="RECIRCULATION FAN OFF",
            level="S",
            condition="Different condition",
            mel_item="21-25-01-03",
            adc="RTG",
        )

        response = self.client.get(
            self.url,
            {"q": "recirc fan*", "search_type": "message"},
        )

        messages = [
            row.message
            for row in response.context["mel_dispatch_rows"]
        ]

        self.assertIn("RECIRC FANS OFF", messages)
        self.assertIn("RECIRC FAN ON", messages)
        self.assertNotIn("RECIRCULATION FAN OFF", messages)

    def test_message_search_excludes_noisy_mel_rows(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="BAD ROW",
            level="M",
            condition="m",
            mel_item="",
            adc="",
        )

        response = self.client.get(
            self.url,
            {"q": "BAD ROW", "search_type": "message"},
        )

        self.assertEqual(list(response.context["mel_dispatch_rows"]), [])

    def test_saved_dispatch_message_search_is_exact_without_wildcards(self):
        DispatchReference.objects.create(
            aircraft=self.aircraft,
            eicas_message="PACK OFF",
            status_message="STATUS",
        )
        DispatchReference.objects.create(
            aircraft=self.aircraft,
            eicas_message="LEFT PACK OFF",
            status_message="STATUS",
        )

        response = self.client.get(
            self.url,
            {"q": "PACK OFF", "search_type": "message"},
        )

        results = response.context["saved_dispatch_results"]

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].eicas_message, "PACK OFF")


class DispatchServiceTests(TestCase):
    def test_extract_level_keeps_mel_m_level(self):
        self.assertEqual(extract_level("M"), "M")

    def test_extract_mel_item_preserves_na_value(self):
        self.assertEqual(extract_mel_item("N/A"), "N/A")

    def test_clean_condition_removes_single_letter_noise(self):
        self.assertEqual(clean_condition("r"), "")

    def test_new_message_row_does_not_inherit_previous_mel_context(self):
        current_row = {
            "message": "RECIRC FAN",
            "level": "S",
            "condition": "Recirculation Fans (Upper, Lower) - Passenger",
            "mel_item": "21-25-01-01",
            "adc": "RTG",
        }

        message, level, condition, mel_item, adc, current_row = (
            merge_mel_row_with_context(
                "RECIRC FANS OFF",
                "",
                "",
                "",
                "",
                current_row,
            )
        )

        self.assertEqual(message, "RECIRC FANS OFF")
        self.assertEqual(level, "")
        self.assertEqual(condition, "")
        self.assertEqual(mel_item, "")
        self.assertEqual(adc, "")
        self.assertEqual(current_row["message"], "RECIRC FANS OFF")

    def test_blank_message_row_inherits_previous_mel_context(self):
        current_row = {
            "message": "RECIRC FAN",
            "level": "S",
            "condition": "Recirculation Fans (Upper, Lower) - Passenger",
            "mel_item": "21-25-01-01",
            "adc": "RTG",
        }

        message, level, condition, mel_item, adc, current_row = (
            merge_mel_row_with_context(
                "",
                "",
                "",
                "",
                "",
                current_row,
            )
        )

        self.assertEqual(message, "RECIRC FAN")
        self.assertEqual(level, "S")
        self.assertEqual(condition, "Recirculation Fans (Upper, Lower) - Passenger")
        self.assertEqual(mel_item, "21-25-01-01")
        self.assertEqual(adc, "RTG")
