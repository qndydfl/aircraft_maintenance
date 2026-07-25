import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dispatch.models import DispatchReference, MelDispatchItem
from dispatch.services import (
    clean_condition,
    extract_adc,
    extract_airbus_mel_dispatch_blocks,
    extract_level,
    extract_mel_item,
    merge_mel_row_with_context,
)
from manuals.models import Aircraft, ManualFile, ManualFilePDFPage
from manuals.services import build_manual_text_regex, parse_manual_search_query


class DispatchSearchViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tester",
            password="password123",
        )
        self.client.force_login(self.user)
        self.aircraft = Aircraft.objects.create(name="B737-800", maker="BOEING")
        self.url = reverse("dispatch_search")

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

    def test_mel_row_search_matches_exact_phrase_inside_merged_message(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message=(
                "OUTFLOW VALVE AFT OUTFLOW VALVE AFT "
                "OUTFLOW VALVE FWD OUTFLOW VALVE FWD"
            ),
            level="ASAS",
            condition="Outflow Valves (FWD and AFT) - Passenger",
            mel_item="21-31-03-02",
            adc="RTG",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message=(
                "OUTFLOW VALVE AFT OUTFLOW VALVE AFT "
                "OUTFLOW VALVE FWD OUTFLOW VALVE FWD"
            ),
            level="ASAS",
            condition="Outflow Valves (FWD and AFT) - All",
            mel_item="21-31-03-04",
            adc="RTG",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="OUTFLOW VALVE FWD",
            level="A",
            condition="Different row",
            mel_item="21-31-03-01",
            adc="RTG",
        )

        response = self.client.get(
            self.url,
            {"q": "outflow valve aft", "search_type": "all"},
        )

        mel_items = [
            row.mel_item
            for row in response.context["mel_dispatch_rows"]
        ]

        self.assertEqual(mel_items, ["21-31-03-02", "21-31-03-04"])

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

    def test_airbus_dispatch_message_finds_referenced_mel_item_and_candidates(self):
        airbus = Aircraft.objects.create(name="A350", maker="AIRBUS")
        mel_file = ManualFile.objects.create(
            aircraft=airbus,
            manual_type="MEL",
            file="manuals/a350-mel.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=mel_file,
            page_number=375,
            text=(
                "Dispatch Message: NAV ADR 1(2)(3) REJECTED BY PRIMs "
                "Ident.: ME-DM-NAV-00021423.0001001 / 10 APR 17 "
                "Applicable to: ALL AIRCRAFT STATUS CONDITION OF DISPATCH "
                "Data from ADR 1(2)(3) have been rejected by the PRIMs. "
                "Refer to Item 34-12-01 ADR"
            ),
        )

        response = self.client.get(
            self.url,
            {
                "aircraft": airbus.pk,
                "q": "nav adr * rejected by*",
                "search_type": "all",
            },
        )

        rows = response.context["mel_dispatch_rows"]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].mel_item, "34-12-01")
        self.assertEqual(response.context["related_manual_queries"], ["34-12-01"])
        self.assertEqual(response.context["mel_page_data"]["total"], 1)
        self.assertEqual(
            response.context["mel_page_data"]["item"].viewer_query,
            "nav adr * rejected by*",
        )

    def test_airbus_wildcard_crosses_message_words_and_parentheses(self):
        airbus = Aircraft.objects.create(name="A350", maker="AIRBUS")
        mel_file = ManualFile.objects.create(
            aircraft=airbus,
            manual_type="MEL",
            file="manuals/a350-mel.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=mel_file,
            page_number=379,
            text=(
                "Dispatch Message: NAV GNSS 1(2)(1+2) REJECTED BY IRs "
                "Ident.: ME-DM-NAV-00023698.0001001 / 07 FEB 25 "
                "Applicable to: ALL AIRCRAFT STATUS CONDITION OF DISPATCH "
                "The GNSS position is detected failed by the ADIRS. "
                "Refer to Item 34-50-06 GNSS monitoring by IRs"
            ),
        )
        ManualFilePDFPage.objects.create(
            manual_file=mel_file,
            page_number=499,
            text=(
                "MEL ITEMS PRELIMINARY PAGES - TABLE OF CONTENTS "
                "34-50-06 GNSS monitoring by IRs"
            ),
        )
        ManualFilePDFPage.objects.create(
            manual_file=mel_file,
            page_number=1259,
            text="34-50-06 GNSS monitoring by IRs One GNSS rejected by IRs",
        )

        response = self.client.get(
            self.url,
            {
                "aircraft": airbus.pk,
                "q": "nav gnss**rejected by*",
                "search_type": "all",
            },
        )

        rows = response.context["mel_dispatch_rows"]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].mel_item, "34-50-06")
        self.assertEqual(response.context["related_manual_queries"], ["34-50-06"])
        self.assertEqual(response.context["mel_page_data"]["total"], 1)

        viewer_response = self.client.get(
            reverse("manual_file_pdf_viewer", kwargs={"pk": mel_file.pk}),
            {
                "page": 379,
                "q": "nav gnss**rejected by*",
                "from_search_page": 1,
            },
        )

        self.assertEqual(viewer_response.context["match_count"], 1)
        self.assertEqual(response.context["ipc_results"], [])
        self.assertEqual(response.context["cdl_results"], [])


class DispatchServiceTests(TestCase):
    def test_extract_airbus_mel_dispatch_block(self):
        rows = extract_airbus_mel_dispatch_blocks(
            "Dispatch Message: NAV ADR 1(2)(3) REJECTED BY PRIMs "
            "Ident.: ME-DM-NAV-00021423.0001001 / 10 APR 17 "
            "AIRCRAFT STATUS CONDITION OF DISPATCH "
            "Data from ADR have been rejected by the PRIMs. "
            "Refer to Item 34-12-01 ADR"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"], "NAV ADR 1(2)(3) REJECTED BY PRIMs")
        self.assertEqual(rows[0]["mel_item"], "34-12-01")

    def test_maintenance_message_regex_matches_number_in_comma_list(self):
        search_value, match_mode = parse_manual_search_query(
            "maintenance messages: 28-19424"
        )
        pattern = build_manual_text_regex(search_value, match_mode)

        self.assertIsNotNone(
            re.search(
                pattern,
                "MAINTENANCE MESSAGES: 28-19423, 28-19424",
                re.IGNORECASE,
            )
        )
        self.assertIsNone(
            re.search(
                pattern,
                "MAINTENANCE MESSAGES: 28-19423",
                re.IGNORECASE,
            )
        )

    def test_extract_level_keeps_mel_m_level(self):
        self.assertEqual(extract_level("M"), "M")

    def test_extract_mel_item_preserves_na_value(self):
        self.assertEqual(extract_mel_item("N/A"), "N/A")

    def test_extract_adc_preserves_combined_rtg_or_cd_value(self):
        self.assertEqual(extract_adc("RTG or CD"), "RTG or CD")
        self.assertEqual(extract_adc("RTG / CD"), "RTG or CD")

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
