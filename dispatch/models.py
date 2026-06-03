from django.db import models
from manuals.models import Aircraft, ManualFile


class DispatchReference(models.Model):
    aircraft = models.ForeignKey(Aircraft, on_delete=models.CASCADE)

    eicas_message = models.CharField(max_length=255, blank=True, default="")
    status_message = models.CharField(max_length=255, blank=True, default="")
    fault_code = models.CharField(max_length=255, blank=True, default="")

    mel_number = models.CharField(max_length=255, blank=True, default="")
    mel_search_keyword = models.CharField(max_length=255, blank=True, default="")
    mel_ref = models.CharField(max_length=255, blank=True, default="")

    fim_chapter = models.CharField(max_length=255, blank=True, default="")
    fim_search_keyword = models.CharField(max_length=255, blank=True, default="")
    fim_task_ref = models.CharField(max_length=255, blank=True, default="")

    amm_chapter = models.CharField(max_length=255, blank=True, default="")
    amm_search_keyword = models.CharField(max_length=255, blank=True, default="")
    amm_task_ref = models.CharField(max_length=255, blank=True, default="")

    description = models.TextField(blank=True, default="")
    note = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    mel_page_number = models.PositiveIntegerField(null=True, blank=True)

    fim_page_number = models.PositiveIntegerField(null=True, blank=True)

    amm_page_number = models.PositiveIntegerField(null=True, blank=True)

    mel_manual_file_id = models.PositiveIntegerField(null=True, blank=True)

    fim_chapter_id = models.PositiveIntegerField(null=True, blank=True)

    amm_chapter_id = models.PositiveIntegerField(null=True, blank=True)

    amm_task_ref = models.CharField(max_length=100, blank=True)

    fim_task_ref = models.CharField(max_length=100, blank=True)

    mel_ref = models.CharField(max_length=100, blank=True)

    status_message = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = [
            "aircraft",
            "eicas_message",
            "fault_code",
        ]

        unique_together = [
            [
                "aircraft",
                "eicas_message",
                "fault_code",
            ]
        ]

    def __str__(self):
        if self.eicas_message:
            return f"{self.aircraft} - {self.eicas_message}"
        return f"{self.aircraft} - {self.fault_code}"


class DispatchKeywordDictionary(models.Model):
    CATEGORY_CHOICES = [
        ("EICAS", "EICAS Message"),
        ("FAULT", "Fault Code"),
        ("SYSTEM", "System"),
        ("SYNONYM", "Synonym"),
    ]

    keyword = models.CharField(max_length=255)
    related_keywords = models.TextField(
        blank=True, help_text="쉼표로 구분: PACK, AIR CONDITIONING, BLEED"
    )

    category = models.CharField(
        max_length=20, choices=CATEGORY_CHOICES, default="EICAS"
    )

    description = models.TextField(blank=True)

    def get_related_list(self):
        return [
            item.strip() for item in self.related_keywords.split(",") if item.strip()
        ]

    def __str__(self):
        return self.keyword


class MelDispatchItem(models.Model):

    aircraft = models.ForeignKey(
        Aircraft,
        on_delete=models.CASCADE,
        related_name="mel_dispatch_items",
    )

    manual_file = models.ForeignKey(
        ManualFile,
        on_delete=models.CASCADE,
        related_name="mel_dispatch_items",
        null=True,
        blank=True,
    )

    message = models.CharField(max_length=255, db_index=True)

    level = models.CharField(
        max_length=10,
        blank=True,
        default="",
    )

    condition = models.TextField(
        blank=True,
        default="",
    )

    mel_item = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
    )

    adc = models.CharField(
        max_length=50,
        blank=True,
        default="",
    )

    page_number = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "message",
            "mel_item",
            "page_number",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "aircraft",
                    "manual_file",
                    "message",
                    "level",
                    "condition",
                    "mel_item",
                    "adc",
                    "page_number",
                ],
                name="unique_mel_dispatch_full_row",
            )
        ]

    def __str__(self):
        return f"{self.message} ({self.mel_item})"
