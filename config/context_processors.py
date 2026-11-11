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
    other_groups = []
    sidebar_all_manuals_active = url_name == "manual_home"
    sidebar_common_active = False
    sidebar_other_active = False

    if show_sidebar:
        from urllib.parse import urlencode

        from django.db.models import Count, Prefetch
        from django.urls import reverse

        from manuals.models import (
            Aircraft,
            CommonManualCategory,
            ManualChapter,
            ManualFile,
            ManualPackage,
            OtherManualCategory,
            OtherManualFile,
        )

        active_aircraft_id = None
        active_package_id = None
        active_manual_file_id = None
        active_other_category_id = None
        active_other_file_id = None
        active_other_folder_path = ""
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
        elif url_name == "other_manual_folder_browse":
            active_other_category_id = route_kwargs.get("pk")
            active_other_folder_path = (
                request.GET.get("path", "").replace("\\", "/").strip("/")
            )
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
            active_other_file = (
                OtherManualFile.objects.filter(pk=active_other_file_id)
                .values("category_id", "folder_path")
                .first()
            )
            if active_other_file:
                active_other_category_id = active_other_file["category_id"]
                active_other_folder_path = (
                    active_other_file["folder_path"] or ""
                ).strip("/")

        package_queryset = ManualPackage.objects.annotate(
            sidebar_file_count=Count("package_files", distinct=True),
            sidebar_chapter_count=Count("chapters", distinct=True),
        )
        other_category_queryset = OtherManualCategory.objects.prefetch_related(
            "folders",
            "files",
        )
        aircrafts = Aircraft.objects.order_by("maker", "name").prefetch_related(
            Prefetch("manual_packages", queryset=package_queryset),
            "manuals",
            Prefetch("other_categories", queryset=other_category_queryset),
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
                    "count": (
                        package.sidebar_file_count
                        or package.sidebar_chapter_count
                    ),
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
                        "count": 1,
                        "url": reverse(
                            "manual_file_pdf_viewer",
                            args=[manual_file.pk],
                        ),
                        "is_active": manual_file_is_active,
                    },
                )
                aircraft_is_active = aircraft_is_active or manual_file_is_active

            other_links = []
            other_is_active = False

            for category in aircraft.other_categories.all():
                category_is_active = category.pk == active_other_category_id
                category_files = list(category.files.all())
                folder_paths = set()

                def add_folder_with_ancestors(raw_path):
                    parts = [
                        part
                        for part in str(raw_path or "")
                        .replace("\\", "/")
                        .strip("/")
                        .split("/")
                        if part
                    ]
                    for depth in range(1, len(parts) + 1):
                        folder_paths.add("/".join(parts[:depth]))

                for registered_folder in category.folders.all():
                    add_folder_with_ancestors(registered_folder.path)
                for other_file in category_files:
                    add_folder_with_ancestors(other_file.folder_path)

                folder_nodes = {}
                folders = []

                for folder_path in sorted(
                    folder_paths,
                    key=lambda path: (path.count("/"), path.lower()),
                ):
                    parent_path, _, folder_name = folder_path.rpartition("/")
                    folder_node = {
                        "label": folder_name,
                        "path": folder_path,
                        "count": 0,
                        "is_active": bool(
                            category_is_active
                            and active_other_folder_path == folder_path
                        ),
                        "is_open": bool(
                            category_is_active
                            and (
                                active_other_folder_path == folder_path
                                or active_other_folder_path.startswith(
                                    f"{folder_path}/"
                                )
                            )
                        ),
                        "url": (
                            reverse(
                                "other_manual_folder_browse",
                                args=[category.pk],
                            )
                            + "?"
                            + urlencode({"path": folder_path})
                        ),
                        "folders": [],
                        "files": [],
                    }
                    folder_nodes[folder_path] = folder_node
                    if parent_path:
                        folder_nodes[parent_path]["folders"].append(folder_node)
                    else:
                        folders.append(folder_node)

                direct_files = []
                for other_file in sorted(
                    category_files,
                    key=lambda item: (item.folder_path.lower(), item.title.lower()),
                ):
                    normalized_folder_path = (
                        other_file.folder_path or ""
                    ).replace("\\", "/").strip("/")
                    file_is_active = other_file.pk == active_other_file_id
                    file_item = {
                        "label": other_file.title,
                        "count": 1,
                        "is_active": file_is_active,
                        "url": (
                            reverse(
                                "other_manual_file_pdf_viewer",
                                args=[other_file.pk],
                            )
                            if other_file.is_pdf
                            else (
                                other_file.file.url
                                if other_file.file
                                else reverse(
                                    "other_manual_category_detail",
                                    args=[category.pk],
                                )
                            )
                        ),
                    }
                    if normalized_folder_path:
                        folder_nodes[normalized_folder_path]["files"].append(
                            file_item
                        )
                    else:
                        direct_files.append(file_item)

                def update_folder_counts(folder_node):
                    child_count = sum(
                        update_folder_counts(child)
                        for child in folder_node["folders"]
                    )
                    folder_node["count"] = (
                        len(folder_node["files"]) + child_count
                    )
                    return folder_node["count"]

                for folder in folders:
                    update_folder_counts(folder)

                other_links.append(
                    {
                        "label": category.manual_type,
                        "count": len(category_files),
                        "url": reverse(
                            "other_manual_category_detail",
                            args=[category.pk],
                        ),
                        "is_active": category_is_active,
                        "overview_active": (
                            category_is_active
                            and not active_other_folder_path
                            and not active_other_file_id
                        ),
                        "folders": folders,
                        "direct_files": direct_files,
                    }
                )
                other_is_active = other_is_active or category_is_active

            if other_links:
                other_groups.append(
                    {
                        "label": aircraft.name,
                        "is_active": other_is_active,
                        "categories": sorted(
                            other_links, key=lambda item: item["label"]
                        ),
                    }
                )

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
                    "count": 1,
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
        sidebar_other_active = any(
            group["is_active"] for group in other_groups
        )

    return {
        "show_app_sidebar": show_sidebar,
        "sidebar_active_section": active_section,
        "sidebar_manual_groups": manual_groups,
        "sidebar_common_groups": common_groups,
        "sidebar_all_manuals_active": sidebar_all_manuals_active,
        "sidebar_common_active": sidebar_common_active,
        "sidebar_other_groups": other_groups,
        "sidebar_other_active": sidebar_other_active,
    }
