from django.contrib import admin
from .models import DispatchReference, DispatchKeywordDictionary


@admin.register(DispatchReference)
class DispatchReferenceAdmin(admin.ModelAdmin):
    list_display = [
        "aircraft",
        "eicas_message",
        "fault_code",
        "mel_number",
        "fim_chapter",
        "amm_chapter",
    ]

    list_filter = [
        "aircraft",
    ]

    search_fields = [
        "aircraft__name",
        "eicas_message",
        "fault_code",
        "mel_number",
        "fim_chapter",
        "amm_chapter",
    ]


@admin.register(DispatchKeywordDictionary)
class DispatchKeywordDictionaryAdmin(admin.ModelAdmin):
    list_display = [
        "keyword",
        "category",
        "description",
    ]

    list_filter = [
        "category",
    ]

    search_fields = [
        "keyword",
        "related_keywords",
        "description",
    ]