from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
import os


def get_pdf_full_path(self):
    return os.path.join(settings.MEDIA_ROOT, self.pdf_relative_path)


def validate_manual_file(value):
    ext = os.path.splitext(value.name)[1].lower()

    allowed_extensions = [".pdf", ".zip"]

    if ext not in allowed_extensions:
        raise ValidationError("PDF 또는 ZIP 파일만 업로드할 수 있습니다.")


class Aircraft(models.Model):
    MAKER_CHOICES = [
        ("BOEING", "Boeing"),
        ("AIRBUS", "Airbus"),
    ]

    maker = models.CharField(max_length=20, choices=MAKER_CHOICES)

    name = models.CharField(max_length=50)

    airbus_url = models.URLField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["maker", "name"]

    def __str__(self):
        return f"{self.maker} - {self.name}"

    def get_ordered_manuals(self):
        manuals = list(self.manuals.all())

        return sorted(manuals, key=lambda manual: manual.manual_order())

    def get_amm(self):
        return self.manuals.filter(manual_type="AMM").first()

    def get_fim(self):
        return self.manuals.filter(manual_type="FIM").first()

    def get_ipc(self):
        return self.manuals.filter(manual_type="IPC").first()

    def get_mel(self):
        return self.manuals.filter(manual_type="MEL").first()

    def get_cdl(self):
        return self.manuals.filter(manual_type="CDL").first()

    def has_manual(self, manual_type):
        return self.manuals.filter(manual_type=manual_type).exists()

    def has_amm(self):
        return self.has_manual("AMM")

    def has_fim(self):
        return self.has_manual("FIM")

    def has_ipc(self):
        return self.has_manual("IPC")

    def has_mel(self):
        return self.has_manual("MEL")

    def has_cdl(self):
        return self.has_manual("CDL")

    def get_manual_by_type(self, manual_type):
        return self.manuals.filter(manual_type=manual_type).first()

    def get_manual_status_list(self):
        manual_types = [
            "AMM",
            "FIM",
            "IPC",
            "MEL",
            "CDL",
        ]

        status_list = []

        for manual_type in manual_types:
            status_list.append(
                {
                    "type": manual_type,
                    "file": self.get_manual_by_type(manual_type),
                    "package": self.get_package_by_type(manual_type),
                }
            )

        return status_list

    def get_package_by_type(self, manual_type):
        return self.manual_packages.filter(manual_type=manual_type).first()


class ManualFile(models.Model):
    MANUAL_TYPE_CHOICES = [
        ("AMM", "AMM"),
        ("FIM", "FIM"),
        ("IPC", "IPC"),
        ("MEL", "MEL"),
        ("CDL", "CDL"),
    ]

    aircraft = models.ForeignKey(
        Aircraft, on_delete=models.CASCADE, related_name="manuals"
    )

    manual_type = models.CharField(max_length=10, choices=MANUAL_TYPE_CHOICES)

    file = models.FileField(upload_to="manuals/", validators=[validate_manual_file])

    description = models.TextField(blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    revision_no = models.CharField(max_length=50, blank=True, default="")

    revision_date_text = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["aircraft", "manual_type"]
        verbose_name = "Manual File"
        verbose_name_plural = "Manual Files"
        unique_together = [["aircraft", "manual_type"]]

    def indexed_page_count(self):
        return self.pdf_pages.count()

    def is_indexed_pdf(self):
        return self.indexed_page_count() > 0

    def manual_order(self):
        order_map = {
            "AMM": 1,
            "FIM": 2,
            "IPC": 3,
            "MEL": 4,
            "CDL": 5,
        }

        return order_map.get(self.manual_type, 99)


class AirbusManualLink(models.Model):
    MANUAL_TYPE_CHOICES = [
        ("AMM", "AMM"),
        ("FIM", "FIM"),
        ("IPC", "IPC"),
        ("MEL", "MEL"),
        ("CDL", "CDL"),
        ("OTHER", "OTHER"),
    ]

    title = models.CharField(max_length=100)

    manual_type = models.CharField(
        max_length=10, choices=MANUAL_TYPE_CHOICES, default="OTHER"
    )

    url = models.URLField()

    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["manual_type", "title"]

    def __str__(self):
        return f"{self.manual_type} - {self.title}"


class ManualPackage(models.Model):
    MANUAL_TYPE_CHOICES = [
        ("AMM", "AMM"),
        ("FIM", "FIM"),
        ("IPC", "IPC"),
    ]

    aircraft = models.ForeignKey(
        Aircraft, on_delete=models.CASCADE, related_name="manual_packages"
    )

    merged_pdf = models.FileField(upload_to="merged_manuals/", blank=True, null=True)

    manual_type = models.CharField(max_length=10, choices=MANUAL_TYPE_CHOICES)

    zip_file = models.FileField(upload_to="manual_packages/")

    extracted_path = models.CharField(max_length=500, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    processed = models.BooleanField(default=False)

    revision_no = models.CharField(max_length=50, blank=True, default="")

    revision_date_text = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["aircraft", "manual_type"]

        unique_together = [["aircraft", "manual_type"]]

    def chapter_count(self):
        return self.chapters.count()

    def page_count(self):
        return ManualPDFPage.objects.filter(chapter__package=self).count()

    def __str__(self):
        return f"{self.aircraft.name} - {self.manual_type}"


class ManualChapter(models.Model):
    package = models.ForeignKey(
        ManualPackage, on_delete=models.CASCADE, related_name="chapters"
    )

    task = models.CharField(max_length=100)

    subtask = models.CharField(max_length=100, blank=True)

    title = models.CharField(max_length=255, blank=True)

    pdf_relative_path = models.CharField(max_length=500)

    created_at = models.DateTimeField(auto_now_add=True)

    def get_pdf_full_path(self):
        if self.package and self.package.extracted_path:
            return os.path.join(
                self.package.extracted_path,
                self.pdf_relative_path,
            )

        return os.path.join(
            settings.MEDIA_ROOT,
            self.pdf_relative_path,
        )

    class Meta:
        ordering = ["task", "subtask"]

    def __str__(self):
        return f"{self.package} - {self.task} {self.subtask}"


class ManualPDFPage(models.Model):
    chapter = models.ForeignKey(
        ManualChapter, on_delete=models.CASCADE, related_name="pages"
    )

    page_number = models.PositiveIntegerField()

    text = models.TextField(blank=True)

    class Meta:
        ordering = ["chapter", "page_number"]

        unique_together = [["chapter", "page_number"]]

    def get_snippet(self, query, length=180):
        if not query:
            return self.text[:length]

        text_lower = self.text.lower()
        words = query.lower().split()

        first_index = -1

        for word in words:
            index = text_lower.find(word)

            if index != -1:
                first_index = index
                break

        if first_index == -1:
            return self.text[:length]

        start = max(first_index - 80, 0)
        end = min(first_index + length, len(self.text))

        snippet = self.text[start:end]

        if start > 0:
            snippet = "..." + snippet

        if end < len(self.text):
            snippet = snippet + "..."

        return snippet

    def __str__(self):
        return f"{self.chapter} - Page {self.page_number}"


class ManualFilePDFPage(models.Model):
    manual_file = models.ForeignKey(
        ManualFile, on_delete=models.CASCADE, related_name="pdf_pages"
    )

    page_number = models.PositiveIntegerField()

    text = models.TextField(blank=True)

    class Meta:
        ordering = [
            "manual_file",
            "page_number",
        ]

        unique_together = [["manual_file", "page_number"]]

    def get_snippet(self, query, length=180):
        if not query:
            return self.text[:length]

        text_lower = self.text.lower()
        words = query.lower().split()

        first_index = -1

        for word in words:
            index = text_lower.find(word)

            if index != -1:
                first_index = index
                break

        if first_index == -1:
            return self.text[:length]

        start = max(first_index - 80, 0)
        end = min(first_index + length, len(self.text))

        snippet = self.text[start:end]

        if start > 0:
            snippet = "..." + snippet

        if end < len(self.text):
            snippet = snippet + "..."

        return snippet

    def __str__(self):
        return f"{self.manual_file} - Page {self.page_number}"
