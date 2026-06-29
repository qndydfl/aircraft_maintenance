from django.urls import path

from .views import (
    DispatchCSVUploadView,
    DispatchDetailView,
    DispatchCreateView,
    DispatchUpdateView,
    DispatchDeleteView,
    DispatchFmlGeneratorView,
    DispatchAutocompleteView,
    DispatchCSVDownloadView,
    DispatchPrintView,
    DispatchDetailPrintView,
    DispatchAutoSearchView,
    DispatchListView,
)

urlpatterns = [
    path("", DispatchAutoSearchView.as_view(), name="dispatch_auto_search"),
    path("fml/", DispatchFmlGeneratorView.as_view(), name="dispatch_fml_generator"),
    path(
        "autocomplete/",
        DispatchAutocompleteView.as_view(),
        name="dispatch_autocomplete",
    ),
    path("download/", DispatchCSVDownloadView.as_view(), name="dispatch_csv_download"),
    path("upload/", DispatchCSVUploadView.as_view(), name="dispatch_csv_upload"),
    path("create/", DispatchCreateView.as_view(), name="dispatch_create"),
    path("print/", DispatchPrintView.as_view(), name="dispatch_print"),
    path(
        "<int:pk>/print/",
        DispatchDetailPrintView.as_view(),
        name="dispatch_detail_print",
    ),
    path("<int:pk>/", DispatchDetailView.as_view(), name="dispatch_detail"),
    path("<int:pk>/edit/", DispatchUpdateView.as_view(), name="dispatch_update"),
    path("<int:pk>/delete/", DispatchDeleteView.as_view(), name="dispatch_delete"),
    path("dispatch/list/", DispatchListView.as_view(), name="dispatch_list"),
]
