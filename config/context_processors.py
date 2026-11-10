from django.conf import settings


SIDEBAR_EXCLUDED_URL_NAMES = {
    "login",
    "logout",
    "dispatch_detail_print",
    "service_worker",
}


def site_dates(request):
    return {
        "site_created_date": getattr(settings, "SITE_CREATED_DATE", ""),
        "site_revision_date": getattr(settings, "SITE_REVISION_DATE", ""),
        "site_revision_no": getattr(settings, "SITE_REVISION_NO", ""),
    }


def app_sidebar(request):
    """Show the shared navigation shell on authenticated application pages."""
    resolver_match = getattr(request, "resolver_match", None)
    url_name = getattr(resolver_match, "url_name", "")
    user = getattr(request, "user", None)
    route_kwargs = getattr(resolver_match, "kwargs", {}) or {}
    active_section = ""

    if url_name == "home":
        active_section = "home"
    elif url_name == "manual_search":
        active_section = "search"
    elif url_name == "calculator":
        active_section = "calculator"
    elif url_name.startswith("dispatch_"):
        active_section = "dispatch"
    elif url_name in {
        "aircraft_manage",
        "aircraft_create",
        "aircraft_update",
        "aircraft_delete",
    }:
        active_section = "management"
    elif url_name.startswith(("manual_", "aircraft_", "common_", "other_")):
        active_section = "manuals"

    show_sidebar = bool(
        user
        and user.is_authenticated
        and url_name not in SIDEBAR_EXCLUDED_URL_NAMES
        and request.GET.get("embedded", "").lower()
        not in {"1", "true", "yes"}
    )
    manual_groups = []
    common_groups = []
    sidebar_all_manuals_active = url_name == "manual_home"
    sidebar_common_active = False

    if show_sidebar:
        from django.urls import reverse

        from manuals.models import (
            Aircraft,
            CommonManualCategory,
            ManualChapter,
            ManualFile,
            ManualPackage,
            OtherManualFile,
        )

        active_aircraft_id = None
        active_package_id = None
        active_manual_file_id = None
        active_other_category_id = None
        active_other_file_id = None
        active_common_category_id = None
        active_common_file_id = None

        if url_name == "aircraft_manual_detail":
            active_aircraft_id = route_kwargs.get("pk")
        elif url_name in {"manual_package_open", "manual_package_update"}:
            active_package_id = route_kwargs.get("pk")
        elif url_name == "manual_chapter_list":
            active_package_id = route_kwargs.get("package_pk")
        elif url_name == "manual_chapter_pdf_viewer":
            active_package_id = (
                ManualChapter.objects.filter(pk=route_kwargs.get("pk"))
                .values_list("package_id", flat=True)
                .first()
            )
        elif url_name in {"manual_file_pdf_viewer", "manual_file_update"}:
            active_manual_file_id = route_kwargs.get("pk")
        elif url_name == "other_manual_category_detail":
            active_other_category_id = route_kwargs.get("pk")
        elif url_name == "other_manual_file_pdf_viewer":
            active_other_file_id = route_kwargs.get("pk")
        elif url_name == "common_manual_category_detail":
            active_common_category_id = route_kwargs.get("pk")
        elif url_name == "common_manual_file_pdf_viewer":
            active_common_file_id = route_kwargs.get("pk")

        if active_package_id and active_aircraft_id is None:
            active_aircraft_id = (
                ManualPackage.objects.filter(pk=active_package_id)
                .values_list("aircraft_id", flat=True)
                .first()
            )
        if active_manual_file_id and active_aircraft_id is None:
            active_aircraft_id = (
                ManualFile.objects.filter(pk=active_manual_file_id)
                .values_list("aircraft_id", flat=True)
                .first()
            )
        if active_other_file_id:
            active_other_category_id = (
                OtherManualFile.objects.filter(pk=active_other_file_id)
                .values_list("category_id", flat=True)
                .first()
            )

        aircrafts = Aircraft.objects.order_by("maker", "name").prefetch_related(
            "manual_packages",
            "manuals",
            "other_categories",
        )
        grouped_aircrafts = {}

        for aircraft in aircrafts:
            manual_links = {}
            aircraft_is_active = aircraft.pk == active_aircraft_id

            for package in aircraft.manual_packages.all():
                package_is_active = package.pk == active_package_id
                manual_links[package.manual_type] = {
                    "label": package.manual_type,
                    "mode": "ZIP",
                    "url": reverse("manual_package_open", args=[package.pk]),
                    "is_active": package_is_active,
                }
                aircraft_is_active = aircraft_is_active or package_is_active

            for manual_file in aircraft.manuals.all():
                manual_file_is_active = manual_file.pk == active_manual_file_id
                manual_links.setdefault(
                    manual_file.manual_type,
                    {
                        "label": manual_file.manual_type,
                        "mode": "PDF",
                        "url": reverse(
                            "manual_file_pdf_viewer",
                            args=[manual_file.pk],
                        ),
                        "is_active": manual_file_is_active,
                    },
                )
                aircraft_is_active = aircraft_is_active or manual_file_is_active

            for category in aircraft.other_categories.all():
                category_is_active = category.pk == active_other_category_id
                manual_links.setdefault(
                    category.manual_type,
                    {
                        "label": category.manual_type,
                        "mode": "FOLDER",
                        "url": reverse(
                            "other_manual_category_detail",
                            args=[category.pk],
                        ),
                        "is_active": category_is_active,
                    },
                )
                aircraft_is_active = aircraft_is_active or category_is_active

            grouped_aircrafts.setdefault(aircraft.maker.title(), []).append(
                {
                    "label": aircraft.name,
                    "url": reverse("aircraft_manual_detail", args=[aircraft.pk]),
                    "is_active": aircraft_is_active,
                    "overview_active": (
                        url_name == "aircraft_manual_detail"
                        and aircraft.pk == active_aircraft_id
                    ),
                    "manuals": sorted(
                        manual_links.values(),
                        key=lambda item: item["label"],
                    ),
                }
            )

        manual_groups = [
            {
                "label": maker,
                "aircrafts": maker_aircrafts,
                "is_active": any(
                    aircraft["is_active"] for aircraft in maker_aircrafts
                ),
            }
            for maker, maker_aircrafts in grouped_aircrafts.items()
        ]

        common_categories = CommonManualCategory.objects.order_by(
            "name"
        ).prefetch_related("files")
        for category in common_categories:
            files = [
                {
                    "label": common_file.title,
                    "url": reverse(
                        "common_manual_file_pdf_viewer",
                        args=[common_file.pk],
                    ),
                    "is_active": common_file.pk == active_common_file_id,
                }
                for common_file in category.files.all()
            ]
            category_is_active = (
                category.pk == active_common_category_id
                or any(file["is_active"] for file in files)
            )
            common_groups.append(
                {
                    "label": category.name,
                    "url": reverse(
                        "common_manual_category_detail",
                        args=[category.pk],
                    ),
                    "is_active": category_is_active,
                    "overview_active": category.pk == active_common_category_id,
                    "files": files,
                }
            )
        sidebar_common_active = any(
            category["is_active"] for category in common_groups
        )

    return {
        "show_app_sidebar": show_sidebar,
        "sidebar_active_section": active_section,
        "sidebar_manual_groups": manual_groups,
        "sidebar_common_groups": common_groups,
        "sidebar_all_manuals_active": sidebar_all_manuals_active,
        "sidebar_common_active": sidebar_common_active,
    }
