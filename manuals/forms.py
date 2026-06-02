import os

from django import forms
from django.core.exceptions import ValidationError

from .models import ManualFile, Aircraft, AirbusManualLink, ManualPackage


class ManualFileForm(forms.ModelForm):

    class Meta:
        model = ManualFile

        fields = [
            "aircraft",
            "manual_type",
            "file",
            "description",
        ]

        widgets = {
            "aircraft": forms.Select(
                attrs={"class": "form-select"}
            ),
            "manual_type": forms.Select(
                attrs={"class": "form-select"}
            ),
            "file": forms.ClearableFileInput(
                attrs={"class": "form-control"}
            ),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
        }

    def clean(self):
        cleaned_data = super().clean()

        aircraft = cleaned_data.get("aircraft")
        manual_type = cleaned_data.get("manual_type")
        file = cleaned_data.get("file")

        if aircraft and manual_type:
            queryset = ManualFile.objects.filter(
                aircraft=aircraft,
                manual_type=manual_type
            )

            if self.instance.pk:
                queryset = queryset.exclude(
                    pk=self.instance.pk
                )

            if queryset.exists():
                raise ValidationError(
                    f"{aircraft.name}에는 이미 {manual_type} 파일이 등록되어 있습니다."
                )

        if not manual_type or not file:
            return cleaned_data

        ext = os.path.splitext(file.name)[1].lower()

        zip_manuals = ["AMM", "FIM", "IPC"]
        pdf_manuals = ["MEL", "CDL"]

        if manual_type in zip_manuals and ext != ".zip":
            raise ValidationError(
                "AMM / FIM / IPC는 ZIP 파일만 업로드할 수 있습니다."
            )

        if manual_type in pdf_manuals and ext != ".pdf":
            raise ValidationError(
                "MEL / CDL은 PDF 파일만 업로드할 수 있습니다."
            )

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
            "maker": forms.Select(
                attrs={"class": "form-select"}
            ),
            "name": forms.TextInput(
                attrs={"class": "form-control"}
            ),
            "airbus_url": forms.URLInput(
                attrs={"class": "form-control"}
            ),
        }


class AirbusManualLinkForm(forms.ModelForm):

    class Meta:
        model = AirbusManualLink

        fields = [
            "title",
            "manual_type",
            "url",
            "description",
        ]

        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-control"}
            ),
            "manual_type": forms.Select(
                attrs={"class": "form-select"}
            ),
            "url": forms.URLInput(
                attrs={"class": "form-control"}
            ),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
        }


class ManualPackageForm(forms.ModelForm):

    class Meta:
        model = ManualPackage

        fields = [
            "aircraft",
            "manual_type",
            "zip_file",
        ]

        widgets = {
            "aircraft": forms.Select(
                attrs={"class": "form-select"}
            ),
            "manual_type": forms.Select(
                attrs={"class": "form-select"}
            ),
            "zip_file": forms.ClearableFileInput(
                attrs={
                    "class": "form-control",
                    "accept": ".zip",
                }
            ),
        }

    def clean_zip_file(self):
        zip_file = self.cleaned_data.get("zip_file")

        if not zip_file:
            return zip_file

        ext = os.path.splitext(zip_file.name)[1].lower()

        if ext != ".zip":
            raise ValidationError(
                "AMM / FIM / IPC 패키지는 ZIP 파일만 업로드할 수 있습니다."
            )

        return zip_file