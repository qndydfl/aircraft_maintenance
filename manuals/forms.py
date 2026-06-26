import os

from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .models import (
    ManualFile,
    Aircraft,
    ManualPackage,
    CommonManualCategory,
    CommonManualFile,
    OtherManualCategory,
    OtherManualFile,
)

IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"]
DOCUMENT_EXTENSIONS = [".pdf", ".doc", ".docx", ".xls", ".xlsx"]
UPLOAD_ACCEPT_EXTENSIONS = ",".join(DOCUMENT_EXTENSIONS + IMAGE_EXTENSIONS)


class ManualFileForm(forms.ModelForm):

    class Meta:
        model = ManualFile

        fields = [
            "aircraft",
            "manual_type",
            "file",
            "description",
            "revision_no",
            "revision_date_text",
        ]

        widgets = {
            "aircraft": forms.Select(attrs={"class": "form-select"}),
            "manual_type": forms.Select(attrs={"class": "form-select"}),
            "file": forms.ClearableFileInput(
                attrs={
                    "class": "form-control",
                    "accept": ".pdf",
                }
            ),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "revision_no": forms.TextInput(attrs={"class": "form-control"}),
            "revision_date_text": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean(self):
        cleaned_data = super().clean()

        aircraft = cleaned_data.get("aircraft")
        manual_type = cleaned_data.get("manual_type")
        file = cleaned_data.get("file")

        if manual_type == "OTHER":
            raise ValidationError(
                "OTHER는 PDF 단일 업로드가 아니라 ZIP 폴더 업로드를 사용하세요."
            )

        if aircraft and manual_type:
            queryset = ManualFile.objects.filter(
                aircraft=aircraft,
                manual_type=manual_type,
            )

            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)

            if queryset.exists():
                raise ValidationError(
                    f"{aircraft.name}에는 이미 {manual_type} 파일이 등록되어 있습니다."
                )

        if not manual_type or not file:
            return cleaned_data

        ext = os.path.splitext(file.name)[1].lower()

        pdf_manuals = ["MEL", "CDL"]

        if manual_type in pdf_manuals and ext != ".pdf":
            raise ValidationError("MEL / CDL은 PDF 파일만 업로드할 수 있습니다.")

        return cleaned_data


class AircraftForm(forms.ModelForm):

    class Meta:
        model = Aircraft

        fields = [
            "maker",
            "name",
            "airbus_url",
        ]

        widgets = {
            "maker": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "airbus_url": forms.URLInput(attrs={"class": "form-control"}),
        }


class ManualPackageForm(forms.ModelForm):

    class Meta:
        model = ManualPackage

        fields = [
            "aircraft",
            "manual_type",
            "zip_file",
            "revision_no",
        ]

        widgets = {
            "aircraft": forms.Select(attrs={"class": "form-select"}),
            "manual_type": forms.Select(attrs={"class": "form-select"}),
            "zip_file": forms.ClearableFileInput(
                attrs={
                    "class": "form-control",
                    "accept": ".zip",
                }
            ),
            "revision_no": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean(self):
        cleaned_data = super().clean()

        aircraft = cleaned_data.get("aircraft")
        manual_type = cleaned_data.get("manual_type")

        if aircraft and manual_type:
            queryset = ManualPackage.objects.filter(
                aircraft=aircraft,
                manual_type=manual_type,
            )

            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)

            if queryset.exists():
                raise ValidationError(
                    f"{aircraft.name}에는 이미 {manual_type} ZIP 패키지가 등록되어 있습니다."
                )

        return cleaned_data

    def clean_zip_file(self):
        zip_file = self.cleaned_data.get("zip_file")

        if not zip_file:
            return zip_file

        ext = os.path.splitext(zip_file.name)[1].lower()

        if ext != ".zip":
            raise ValidationError(
                "AMM / FIM / IPC / OTHER는 ZIP 파일만 업로드할 수 있습니다."
            )

        return zip_file


class CommonManualCategoryForm(forms.ModelForm):

    def _build_unique_code(self):
        base_code = slugify(self.cleaned_data.get("name", ""), allow_unicode=False)
        base_code = base_code.replace("-", "_").upper() or "CATEGORY"
        candidate = base_code[:50]
        suffix = 2

        existing_codes = CommonManualCategory.objects.all()
        if self.instance.pk:
            existing_codes = existing_codes.exclude(pk=self.instance.pk)

        while existing_codes.filter(code=candidate).exists():
            suffix_text = f"_{suffix}"
            candidate = f"{base_code[:50 - len(suffix_text)]}{suffix_text}"
            suffix += 1

        return candidate

    class Meta:
        model = CommonManualCategory

        fields = [
            "name",
            "description",
        ]

        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)

        if not instance.code:
            instance.code = self._build_unique_code()

        if commit:
            instance.save()

        return instance


class CommonManualFileForm(forms.ModelForm):

    class Meta:
        model = CommonManualFile

        fields = [
            "category",
            "title",
            "file",
            "description",
            "revision_no",
            "revision_date_text",
        ]

        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "file": forms.ClearableFileInput(
                attrs={
                    "class": "form-control",
                    "accept": UPLOAD_ACCEPT_EXTENSIONS,
                }
            ),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "revision_no": forms.TextInput(attrs={"class": "form-control"}),
            "revision_date_text": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean_file(self):
        file = self.cleaned_data.get("file")

        if not file:
            return file

        ext = os.path.splitext(file.name)[1].lower()

        allowed_extensions = DOCUMENT_EXTENSIONS + IMAGE_EXTENSIONS

        if ext not in allowed_extensions:
            raise ValidationError(
                "공통 매뉴얼은 PDF, Word, Excel, 이미지 파일만 업로드할 수 있습니다."
            )

        return file

class OtherManualCategoryForm(forms.ModelForm):

    class Meta:
        model = OtherManualCategory

        fields = [
            "aircraft",
            "description",
        ]

        widgets = {
            "aircraft": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def clean(self):
        cleaned_data = super().clean()

        aircraft = cleaned_data.get("aircraft")

        if aircraft:
            queryset = OtherManualCategory.objects.filter(
                aircraft=aircraft,
            )

            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)

            if queryset.exists():
                raise ValidationError(
                    f"{aircraft.name}에는 이미 OTHER 폴더가 생성되어 있습니다."
                )

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        instance.name = "OTHER"
        instance.code = "OTHER"

        if commit:
            instance.save()

        return instance


class OtherManualFileForm(forms.ModelForm):

    class Meta:
        model = OtherManualFile

        fields = [
            "category",
            "title",
            "file",
            "description",
            "revision_no",
            "revision_date_text",
        ]

        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "file": forms.ClearableFileInput(
                attrs={
                    "class": "form-control",
                    "accept": UPLOAD_ACCEPT_EXTENSIONS,
                }
            ),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "revision_no": forms.TextInput(attrs={"class": "form-control"}),
            "revision_date_text": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean_file(self):
        file = self.cleaned_data.get("file")

        if not file:
            return file

        ext = os.path.splitext(file.name)[1].lower()

        allowed_extensions = DOCUMENT_EXTENSIONS + IMAGE_EXTENSIONS

        if ext not in allowed_extensions:
            raise ValidationError(
                "OTHER 매뉴얼은 PDF, Word, Excel, 이미지 파일만 업로드할 수 있습니다."
            )

        return file
