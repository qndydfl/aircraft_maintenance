from django.contrib import admin
from .models import (
    Aircraft,
    ManualFile,
    AirbusManualLink,
    ManualPackage,
    ManualPDFPage,
    ManualChapter,
    ManualFilePDFPage,
)


class ManualFileInline(admin.TabularInline):
    model = ManualFile
    extra = 1


@admin.register(Aircraft)
class AircraftAdmin(admin.ModelAdmin):
    list_display = [
        "maker",
        "name",
        "airbus_url",
        "created_at",
    ]

    list_filter = [
        "maker",
    ]

    search_fields = [
        "name",
        "maker",
    ]

    inlines = [
        ManualFileInline,
    ]


@admin.register(ManualFile)
class ManualFileAdmin(admin.ModelAdmin):
    list_display = [
        "aircraft",
        "manual_type",
        "file",
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
    list_display = [
        "manual_type",
        "title",
        "url",
        "created_at",
    ]

    list_filter = [
        "manual_type",
    ]

    search_fields = [
        "title",
        "url",
        "description",
    ]


@admin.register(ManualPackage)
class ManualPackageAdmin(admin.ModelAdmin):
    list_display = [
        "aircraft",
        "manual_type",
        "zip_file",
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
