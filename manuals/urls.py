from django.urls import path

from .views import HomeView
from .views import (
    AircraftManualDetailView,
    ManualFileDetailView,
    ManualFileCreateView,
    ManualFileUpdateView,
    ManualFileDeleteView,
    AircraftManageListView,
    AircraftCreateView,
    AircraftUpdateView,
    AircraftDeleteView,
    AircraftManualPrintView,
    ManualPackageCreateView,
    ManualChapterListView,
    ManualChapterPDFView,
    ManualSearchView,
    ManualChapterPDFViewerView,
    ManualFilePDFView,
    ManualFilePDFViewerView,
    ManualPackageUpdateView,
    ManualPackageReindexView,
    ManualPackageOpenView,
    ManualFileReindexView,
    ManualPackageReuploadView,
    ManualPackageDeleteView,
    ExtractMelDispatchItemsView,
)

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path(
        "aircraft/<int:pk>/manuals/",
        AircraftManualDetailView.as_view(),
        name="aircraft_manual_detail",
    ),
    path("manual/<int:pk>/", ManualFileDetailView.as_view(), name="manual_detail"),
    path("manuals/create/", ManualFileCreateView.as_view(), name="manual_file_create"),
    path(
        "manuals/<int:pk>/edit/",
        ManualFileUpdateView.as_view(),
        name="manual_file_update",
    ),
    path(
        "manuals/<int:pk>/delete/",
        ManualFileDeleteView.as_view(),
        name="manual_file_delete",
    ),
    path("aircraft/manage/", AircraftManageListView.as_view(), name="aircraft_manage"),
    path("aircraft/create/", AircraftCreateView.as_view(), name="aircraft_create"),
    path(
        "aircraft/<int:pk>/edit/", AircraftUpdateView.as_view(), name="aircraft_update"
    ),
    path(
        "aircraft/<int:pk>/delete/",
        AircraftDeleteView.as_view(),
        name="aircraft_delete",
    ),
    path(
        "aircraft/<int:pk>/manuals/print/",
        AircraftManualPrintView.as_view(),
        name="aircraft_manual_print",
    ),
    path(
        "manual-packages/create/",
        ManualPackageCreateView.as_view(),
        name="manual_package_create",
    ),
    path(
        "manual-packages/<int:package_pk>/chapters/",
        ManualChapterListView.as_view(),
        name="manual_chapter_list",
    ),
    path(
        "manual-chapters/<int:pk>/pdf/",
        ManualChapterPDFView.as_view(),
        name="manual_chapter_pdf",
    ),
    path("manual-search/", ManualSearchView.as_view(), name="manual_search"),
    path(
        "manual-chapters/<int:pk>/pdf/viewer/",
        ManualChapterPDFViewerView.as_view(),
        name="manual_chapter_pdf_viewer",
    ),
    path(
        "manual-files/<int:pk>/pdf/",
        ManualFilePDFView.as_view(),
        name="manual_file_pdf",
    ),
    path(
        "manual-files/<int:pk>/pdf/viewer/",
        ManualFilePDFViewerView.as_view(),
        name="manual_file_pdf_viewer",
    ),
    path(
        "manual-packages/<int:pk>/edit/",
        ManualPackageUpdateView.as_view(),
        name="manual_package_update",
    ),
    path(
        "manual-packages/<int:pk>/reindex/",
        ManualPackageReindexView.as_view(),
        name="manual_package_reindex",
    ),
    path(
        "manual-packages/<int:pk>/delete/",
        ManualPackageDeleteView.as_view(),
        name="manual_package_delete",
    ),
    path(
        "manual-packages/<int:pk>/open/",
        ManualPackageOpenView.as_view(),
        name="manual_package_open",
    ),
    path(
        "manual-files/<int:pk>/reindex/",
        ManualFileReindexView.as_view(),
        name="manual_file_reindex",
    ),
    path(
        "manual-packages/<int:package_pk>/reupload/",
        ManualPackageReuploadView.as_view(),
        name="manual_package_reupload",
    ),
    path(
        "manual-files/<int:pk>/extract-mel-items/",
        ExtractMelDispatchItemsView.as_view(),
        name="extract_mel_dispatch_items",
    ),
]
