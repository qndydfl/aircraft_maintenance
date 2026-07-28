import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dispatch.models import DispatchReference, MelDispatchItem
from dispatch.services import (
    clean_condition,
    extract_airbus_mel_dispatch_blocks,
    extract_mel_item,
    extract_mel_table_fields,
    extract_mel_table_records,
    is_eicas_message_page,
)
from manuals.models import (
    Aircraft,
    ManualChapter,
    ManualFile,
    ManualFilePDFPage,
    ManualPackage,
    ManualPDFPage,
)
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
            condition="Valid condition",
            mel_item="21-25-01-01",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="LEFT RECIRC FANS OFF",
            condition="Another condition",
            mel_item="21-25-01-02",
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
            condition="Valid condition",
            mel_item="21-25-01-01",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="LEFT RECIRC FANS OFF",
            condition="Another condition",
            mel_item="21-25-01-02",
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
            condition="Valid condition",
            mel_item="21-25-01-01",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="RECIRC FAN ON",
            condition="Another condition",
            mel_item="21-25-01-02",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="RECIRCULATION FAN OFF",
            condition="Different condition",
            mel_item="21-25-01-03",
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

    def test_all_search_does_not_match_condition_only(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="PACK OFF",
            condition="UNIQUE CONDITION TEXT",
            mel_item="21-51-01",
        )

        response = self.client.get(
            self.url,
            {"q": "UNIQUE CONDITION TEXT", "search_type": "all"},
        )

        self.assertEqual(list(response.context["mel_dispatch_rows"]), [])

    def test_all_search_does_not_match_mel_item_only(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="PACK OFF",
            condition="Air Conditioning Pack",
            mel_item="21-51-99",
        )

        response = self.client.get(
            self.url,
            {"q": "21-51-99", "search_type": "all"},
        )

        self.assertEqual(list(response.context["mel_dispatch_rows"]), [])

    def test_boeing_mel_results_only_use_eicas_message_pages(self):
        manual = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file="manuals/b737-mel.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=manual,
            page_number=40,
            text="737 MEL\nEICAS Message\nPREAMBLE Section 1\nADIRU",
        )
        ManualFilePDFPage.objects.create(
            manual_file=manual,
            page_number=900,
            text="737 MEL\nATA 34 Navigation Section 2\nADIRU procedure",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            manual_file=manual,
            message="ADIRU",
            condition="Air Data Inertial Reference Unit",
            mel_item="34-21-01",
            page_number=40,
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            manual_file=manual,
            message="ADIRU",
            condition="Procedure text incorrectly indexed as a message",
            mel_item="34-21-02",
            page_number=900,
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            manual_file=manual,
            message="ADIRU ALIGN MODE",
            condition="",
            mel_item="N/A",
            page_number=40,
        )

        response = self.client.get(
            self.url,
            {"q": "adiru", "search_type": "all"},
        )

        rows = list(response.context["mel_dispatch_rows"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].page_number, 40)
        self.assertEqual(rows[0].mel_item, "34-21-01")
        self.assertEqual(rows[0].message, "ADIRU")

    def test_mel_row_search_matches_exact_phrase_inside_merged_message(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message=(
                "OUTFLOW VALVE AFT OUTFLOW VALVE AFT "
                "OUTFLOW VALVE FWD OUTFLOW VALVE FWD"
            ),
            condition="Outflow Valves (FWD and AFT) - Passenger",
            mel_item="21-31-03-02",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message=(
                "OUTFLOW VALVE AFT OUTFLOW VALVE AFT "
                "OUTFLOW VALVE FWD OUTFLOW VALVE FWD"
            ),
            condition="Outflow Valves (FWD and AFT) - All",
            mel_item="21-31-03-04",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="OUTFLOW VALVE FWD",
            condition="Different row",
            mel_item="21-31-03-01",
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
            condition="m",
            mel_item="",
        )

        response = self.client.get(
            self.url,
            {"q": "BAD ROW", "search_type": "message"},
        )

        self.assertEqual(list(response.context["mel_dispatch_rows"]), [])

    def test_empty_mel_columns_render_as_dashes(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="PACK OFF",
            condition="",
            mel_item="21-51-01",
        )

        response = self.client.get(
            self.url,
            {"q": "PACK OFF", "search_type": "message"},
        )

        self.assertContains(response, '<td class="mel-condition">-</td>', html=True)

    def test_none_dispatch_row_uses_label_without_numeric_related_query(self):
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            message="OVERHEAT ENG L",
            condition="None(No Dispatch)",
            mel_item="N/A",
        )

        response = self.client.get(
            self.url,
            {"q": "OVERHEAT ENG L", "search_type": "message"},
        )
        row = response.context["mel_dispatch_rows"][0]

        self.assertEqual(row.mel_item, "None(No Dispatch)")
        self.assertEqual(row.condition, "")
        self.assertFalse(row.has_numeric_mel_item)
        self.assertEqual(response.context["related_manual_queries"], [])
        self.assertEqual(response.context["mel_page_data"]["total"], 0)

    def test_na_mel_item_opens_the_original_eicas_message_page(self):
        mel_file = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file="manuals/b737-mel.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=mel_file,
            page_number=3,
            text="N/A INDEX PAGE",
        )
        ManualFilePDFPage.objects.create(
            manual_file=mel_file,
            page_number=70,
            text=(
                "737 MEL\nEICAS Message\nPREAMBLE Section 1\n"
                "NO SMOKING S Passenger Signs"
            ),
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            manual_file=mel_file,
            message="NO SMOKING",
            condition="Passenger Signs",
            mel_item="N/A",
            page_number=70,
        )

        response = self.client.get(
            self.url,
            {"q": "NO SMOKING", "search_type": "message"},
        )
        row = response.context["mel_dispatch_rows"][0]

        self.assertEqual(row.viewer_page_number, 70)
        self.assertEqual(row.viewer_query, "NO SMOKING")
        self.assertFalse(row.has_numeric_mel_item)

    def test_manual_results_remain_when_eicas_message_does_not_exist(self):
        query = "NO SMOKING SIGN"
        mel_file = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file="manuals/b737-mel.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=mel_file,
            page_number=12,
            text=query,
        )

        for manual_type in ["FIM", "AMM"]:
            package = ManualPackage.objects.create(
                aircraft=self.aircraft,
                manual_type=manual_type,
                zip_file=f"manual_packages/{manual_type.lower()}.zip",
                processed=True,
            )
            chapter = ManualChapter.objects.create(
                package=package,
                task="00-00",
                title=f"{manual_type} Search Result",
                pdf_relative_path=f"{manual_type.lower()}.pdf",
            )
            ManualPDFPage.objects.create(
                chapter=chapter,
                page_number=1,
                text=query,
            )

        response = self.client.get(
            self.url,
            {"q": query, "search_type": "message"},
        )

        self.assertEqual(response.context["mel_dispatch_rows"], [])
        self.assertEqual(response.context["mel_page_data"]["total"], 1)
        self.assertEqual(response.context["fim_page_data"]["total"], 1)
        self.assertEqual(response.context["amm_page_data"]["total"], 1)
        self.assertContains(
            response,
            "MEL Dispatch Results에 해당 EICAS Message가 없습니다.",
        )

    def test_none_dispatch_keeps_condition_empty_and_manual_results(self):
        query = "OIL PRESS SENSORS L"
        mel_file = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file="manuals/b737-mel.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=mel_file,
            page_number=87,
            text=(
                "737 MEL\nEICAS Message\nPREAMBLE Section 1\n"
                f"{query} S None(No Dispatch)"
            ),
        )

        for manual_type in ["FIM", "AMM"]:
            package = ManualPackage.objects.create(
                aircraft=self.aircraft,
                manual_type=manual_type,
                zip_file=f"manual_packages/oil-{manual_type.lower()}.zip",
                processed=True,
            )
            chapter = ManualChapter.objects.create(
                package=package,
                task="79-00",
                title=f"{manual_type} Oil Pressure",
                pdf_relative_path=f"oil-{manual_type.lower()}.pdf",
            )
            ManualPDFPage.objects.create(
                chapter=chapter,
                page_number=1,
                text=query,
            )

        response = self.client.get(
            self.url,
            {"q": query, "search_type": "message"},
        )
        row = response.context["mel_dispatch_rows"][0]

        self.assertEqual(row.condition, "")
        self.assertEqual(row.mel_item, "None(No Dispatch)")
        self.assertEqual(row.viewer_page_number, 87)
        self.assertEqual(response.context["mel_page_data"]["total"], 1)
        self.assertEqual(response.context["fim_page_data"]["total"], 1)
        self.assertEqual(response.context["amm_page_data"]["total"], 1)
        self.assertContains(response, '<td class="mel-condition">-</td>', html=True)

    def test_no_search_results_show_clear_message(self):
        response = self.client.get(
            self.url,
            {"q": "MESSAGE THAT DOES NOT EXIST", "search_type": "message"},
        )

        self.assertContains(
            response,
            "입력한 검색어에 해당하는 내용이 없습니다.",
        )

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

    def test_related_manuals_follow_single_dispatch_aircraft(self):
        other_aircraft = Aircraft.objects.create(name="A320", maker="AIRBUS")
        selected_manual = ManualFile.objects.create(
            aircraft=self.aircraft,
            manual_type="MEL",
            file="manuals/b737-mel.pdf",
        )
        other_manual = ManualFile.objects.create(
            aircraft=other_aircraft,
            manual_type="MEL",
            file="manuals/a320-mel.pdf",
        )
        ManualFilePDFPage.objects.create(
            manual_file=selected_manual,
            page_number=12,
            text=(
                "737 MEL\nEICAS Message\nPREAMBLE Section 1\n"
                "NO SMOKING ON"
            ),
        )
        ManualFilePDFPage.objects.create(
            manual_file=other_manual,
            page_number=24,
            text="NO SMOKING ON",
        )
        MelDispatchItem.objects.create(
            aircraft=self.aircraft,
            manual_file=selected_manual,
            message="NO SMOKING ON",
            condition="",
            mel_item="N/A",
            page_number=12,
        )

        response = self.client.get(
            self.url,
            {"q": "no smoking on", "search_type": "all"},
        )

        self.assertEqual(response.context["mel_page_data"]["total"], 1)
        self.assertEqual(
            response.context["mel_page_data"]["item"].manual_file_id,
            selected_manual.pk,
        )

        viewer_response = self.client.get(
            reverse(
                "manual_file_pdf_viewer",
                kwargs={"pk": selected_manual.pk},
            ),
            {
                "page": 12,
                "q": "no smoking on",
                "from_search_page": 1,
            },
        )

        self.assertEqual(viewer_response.context["match_count"], 1)


class DispatchServiceTests(TestCase):
    def test_eicas_message_page_detection_only_checks_header(self):
        self.assertTrue(
            is_eicas_message_page(
                "777 MEL\nEICAS Message\nPREAMBLE Section 1\nADIRU"
            )
        )
        self.assertFalse(
            is_eicas_message_page(
                "777 MEL\nATA 34 Navigation Section 2\nRefer to EICAS Message ADIRU"
            )
        )

    def test_mel_table_fields_prefer_item_column_over_condition_reference(self):
        fields = extract_mel_table_fields(
            [
                None,
                "C",
                (
                    "Flap/Slat Control Lanes - For single coil(lane) failure "
                    "of PCV, refer to TIB-777-27-014"
                ),
                "y\np\n27-03-01",
                "RTG",
            ]
        )

        self.assertEqual(
            fields,
            (
                "",
                (
                    "Flap/Slat Control Lanes - For single coil(lane) failure "
                    "of PCV, refer to TIB-777-27-014"
                ),
                "27-03-01",
            ),
        )

    def test_radio_alt_merged_rows_create_one_result_per_message(self):
        records = extract_mel_table_records(
            [
                [
                    "RADIO ALT C\nRADIO ALT MONITOR C",
                    "S",
                    "Radio Altimeter Systems",
                    "34-33-01",
                    "CD",
                ],
                ["RADIO ALT L\nRADIO ALT MONITOR L", "S", None, None, None],
                ["RADIO ALT R\nRADIO ALT MONITOR R", "S", None, None, None],
            ]
        )

        self.assertEqual(
            [record["message"] for record in records],
            [
                "RADIO ALT C",
                "RADIO ALT MONITOR C",
                "RADIO ALT L",
                "RADIO ALT MONITOR L",
                "RADIO ALT R",
                "RADIO ALT MONITOR R",
            ],
        )
        self.assertTrue(
            all(record["condition"] == "Radio Altimeter Systems" for record in records)
        )
        self.assertTrue(all(record["mel_item"] == "34-33-01" for record in records))

    def test_pseu_message_group_receives_each_detail_row(self):
        records = extract_mel_table_records(
            [
                [
                    "n\nU\nPSEU 1 CHANNEL\nPSEU 2 CHANNEL",
                    "c\nS\nS",
                    "PSEU Channels - One Inoperative - PSEU 1",
                    "32-08-01A",
                    "CD",
                ],
                [
                    None,
                    None,
                    "PSEU Channels - One Inoperative - PSEU 2",
                    "32-08-01B",
                    "CD",
                ],
                [
                    None,
                    None,
                    "PSEU Channels - Two Inoperative",
                    "32-08-01C",
                    "RTG",
                ],
            ]
        )

        self.assertEqual(len(records), 6)
        self.assertEqual(
            [
                record["mel_item"]
                for record in records
                if record["message"] == "PSEU 1 CHANNEL"
            ],
            ["32-08-01A", "32-08-01B", "32-08-01C"],
        )
        self.assertEqual(
            [
                record["mel_item"]
                for record in records
                if record["message"] == "PSEU 2 CHANNEL"
            ],
            ["32-08-01A", "32-08-01B", "32-08-01C"],
        )

    def test_single_message_with_detail_subrow_creates_two_results(self):
        records = extract_mel_table_records(
            [
                ["SLATS PRIMARY FAIL", "C", "", "None(No Dispatch)", ""],
                [
                    None,
                    "C",
                    "Flap/Slat Control Lanes",
                    "27-03-01",
                    "RTG",
                ],
            ]
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["mel_item"], "None(No Dispatch)")
        self.assertEqual(records[1]["mel_item"], "27-03-01")

    def test_noisy_na_starts_a_new_message_context(self):
        records = extract_mel_table_records(
            [
                [
                    "FUEL CROSSFEED AFT",
                    "A",
                    "Crossfeed Valves",
                    "28-22-03",
                    "RTG",
                ],
                ["FUEL DIAGREE", "A", "", "C\nN/A", ""],
            ]
        )

        fuel_diagree = records[-1]
        self.assertEqual(fuel_diagree["message"], "FUEL DIAGREE")
        self.assertEqual(fuel_diagree["condition"], "")
        self.assertEqual(fuel_diagree["mel_item"], "N/A")

    def test_apu_start_sys_ocr_noise_is_normalized(self):
        records = extract_mel_table_records(
            [
                [
                    "n\nAPU STUART SYS",
                    "c\nS",
                    "APU Starting System - APU Required",
                    "49-42-01A",
                    "RTG",
                ],
                [
                    None,
                    None,
                    "APU Starting System - Procedures Do Not Require APU",
                    "49-42-01B",
                    "CD",
                ],
            ]
        )

        self.assertEqual(
            [record["message"] for record in records],
            ["APU START SYS", "APU START SYS"],
        )
        self.assertEqual(
            [record["mel_item"] for record in records],
            ["49-42-01A", "49-42-01B"],
        )

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

    def test_extract_airbus_none_dispatch_uses_clear_mel_item_label(self):
        rows = extract_airbus_mel_dispatch_blocks(
            "Dispatch Message: ENG OIL PRESS LOW "
            "Ident.: ME-DM-ENG-0001 "
            "AIRCRAFT STATUS NO DISPATCH"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["condition"], "")
        self.assertEqual(rows[0]["mel_item"], "None(No Dispatch)")
        self.assertTrue(rows[0]["is_none_dispatch"])

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

    def test_extract_mel_item_preserves_na_value(self):
        self.assertEqual(extract_mel_item("N/A"), "N/A")
        self.assertEqual(extract_mel_item("C\nN/A"), "N/A")

    def test_extract_mel_item_preserves_none_dispatch_value(self):
        self.assertEqual(
            extract_mel_item("None(No Dispatch)"),
            "None(No Dispatch)",
        )

    def test_clean_condition_removes_single_letter_noise(self):
        self.assertEqual(clean_condition("r"), "")
        self.assertEqual(
            clean_condition("t\nn\nProximity Sensor Electronic Unit"),
            "Proximity Sensor Electronic Unit",
        )
