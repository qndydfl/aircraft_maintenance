from django.contrib import admin
from .models import (
    Aircraft,
    ManualFile,
    AirbusManualLink,
    ManualPackage,
    ManualPackageFile,
    ManualPDFPage,
    ManualChapter,
    ManualFilePDFPage,
    CommonManualCategory,
    CommonManualFile,
    CommonManualPDFPage,
    ReindexJob,
    ManualType,
)


@admin.register(ManualType)
class ManualTypeAdmin(admin.ModelAdmin):
    list_display = [
        "code",
        "name",
        "upload_mode",
        "sort_order",
        "is_active",
        "is_searchable",
    ]
    list_editable = ["sort_order", "is_active", "is_searchable"]
    list_filter = ["upload_mode", "is_active", "is_searchable"]
    search_fields = ["code", "name"]
    ordering = ["sort_order", "code"]
    filter_horizontal = ["aircraft"]

    def get_readonly_fields(self, request, obj=None):
        return ["code"] if obj else []

    def has_delete_permission(self, request, obj=None):
        return False


class ManualFileInline(admin.TabularInline):
    model = ManualFile
    extra = 1


@admin.register(Aircraft)
class AircraftAdmin(admin.ModelAdmin):
    list_display = ("maker", "name", "image")
    list_filter = ("maker",)
    search_fields = ("name",)
    ordering = ("maker", "name")


@admin.register(ManualFile)
class ManualFileAdmin(admin.ModelAdmin):
    list_display = [
        "aircraft",
        "manual_type",
        "file",
        "revision_no",
        "revision_date_text",
        "uploaded_at",
    ]

    list_filter = [
        "manual_type",
        "aircraft__maker",
        "aircraft",
    ]

    search_fields = [
        "aircraft__name",
        "manual_type",
    ]


@admin.register(AirbusManualLink)
class AirbusManualLinkAdmin(admin.ModelAdmin):
    list_display = (
        "aircraft",
        "manual_type",
        "title",
        "url",
    )
    list_filter = (
        "aircraft",
        "manual_type",
    )
    search_fields = (
        "aircraft__name",
        "manual_type",
        "title",
        "url",
    )


@admin.register(ManualPackage)
class ManualPackageAdmin(admin.ModelAdmin):
    list_display = [
        "aircraft",
        "manual_type",
        "zip_file",
        "revision_no",
        "revision_date_text",
        "processed",
        "uploaded_at",
    ]

    list_filter = [
        "aircraft",
        "manual_type",
        "processed",
    ]

    search_fields = [
        "aircraft__name",
        "manual_type",
    ]


@admin.register(ManualPackageFile)
class ManualPackageFileAdmin(admin.ModelAdmin):
    list_display = [
        "package",
        "relative_path",
        "extension",
        "file_size",
    ]
    list_filter = ["extension", "package__manual_type"]
    search_fields = [
        "relative_path",
        "file_name",
        "package__aircraft__name",
    ]


@admin.register(ManualChapter)
class ManualChapterAdmin(admin.ModelAdmin):
    list_display = [
        "package",
        "task",
        "subtask",
        "title",
        "pdf_relative_path",
    ]

    search_fields = [
        "task",
        "subtask",
        "title",
        "pdf_relative_path",
    ]


@admin.register(ManualPDFPage)
class ManualPDFPageAdmin(admin.ModelAdmin):
    list_display = [
        "chapter",
        "page_number",
    ]

    search_fields = [
        "chapter__task",
        "chapter__title",
        "text",
    ]


@admin.register(ManualFilePDFPage)
class ManualFilePDFPageAdmin(admin.ModelAdmin):
    list_display = [
        "manual_file",
        "page_number",
    ]

    search_fields = [
        "manual_file__aircraft__name",
        "manual_file__manual_type",
        "text",
    ]


@admin.register(CommonManualCategory)
class CommonManualCategoryAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "code",
        "order",
        "image",
    ]

    search_fields = [
        "name",
        "code",
        "description",
    ]

    ordering = [
        "order",
        "name",
    ]


@admin.register(CommonManualFile)
class CommonManualFileAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "category",
        "uploaded_at",
        "revision_no",
        "revision_date_text",
        "indexed_page_count",
    ]

    list_filter = [
        "category",
        "uploaded_at",
    ]

    search_fields = [
        "title",
        "description",
        "category__name",
    ]

    readonly_fields = [
        "uploaded_at",
    ]


@admin.register(CommonManualPDFPage)
class CommonManualPDFPageAdmin(admin.ModelAdmin):
    list_display = [
        "common_file",
        "page_number",
    ]

    list_filter = [
        "common_file__category",
    ]

    search_fields = [
        "common_file__title",
        "text",
    ]


@admin.register(ReindexJob)
class ReindexJobAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "target_type",
        "target_id",
        "status",
        "page_count",
        "created_at",
        "started_at",
        "finished_at",
    ]

    list_filter = [
        "target_type",
        "status",
        "created_at",
    ]

    search_fields = [
        "message",
        "target_type",
    ]

    readonly_fields = [
        "created_at",
        "started_at",
        "finished_at",
    ]
