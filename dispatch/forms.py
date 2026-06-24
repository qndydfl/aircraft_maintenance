from django import forms
from .models import DispatchReference


class DispatchCSVUploadForm(forms.Form):
    csv_file = forms.FileField(
        label="Dispatch CSV File",
        help_text="DispatchReference 컬럼과 message/condition/mel_item 컬럼을 함께 업로드할 수 있습니다.",
    )


class DispatchReferenceForm(forms.ModelForm):
    class Meta:
        model = DispatchReference

        fields = [
            "aircraft",
            "eicas_message",
            "status_message",
            "fault_code",
            "mel_number",
            "fim_chapter",
            "amm_chapter",
            "description",
            "note",
            "amm_search_keyword",
            "fim_search_keyword",
            "mel_search_keyword",
            "mel_page_number",
            "fim_page_number",
            "amm_page_number",
            "mel_manual_file_id",
            "fim_chapter_id",
            "amm_chapter_id",
            "amm_task_ref",
            "fim_task_ref",
            "mel_ref",
        ]

        widgets = {
            "aircraft": forms.Select(attrs={"class": "form-select"}),
            "eicas_message": forms.TextInput(attrs={"class": "form-control"}),
            "status_message": forms.TextInput(attrs={"class": "form-control"}),
            "fault_code": forms.TextInput(attrs={"class": "form-control"}),
            "mel_number": forms.TextInput(attrs={"class": "form-control"}),
            "fim_chapter": forms.TextInput(attrs={"class": "form-control"}),
            "amm_chapter": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            "note": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            "amm_search_keyword": forms.TextInput(attrs={"class": "form-control"}),
            "fim_search_keyword": forms.TextInput(attrs={"class": "form-control"}),
            "mel_search_keyword": forms.TextInput(attrs={"class": "form-control"}),
            "mel_page_number": forms.HiddenInput(),
            "fim_page_number": forms.HiddenInput(),
            "amm_page_number": forms.HiddenInput(),
            "mel_manual_file_id": forms.HiddenInput(),
            "fim_chapter_id": forms.HiddenInput(),
            "amm_chapter_id": forms.HiddenInput(),
            "amm_task_ref": forms.HiddenInput(),
            "fim_task_ref": forms.HiddenInput(),
            "mel_ref": forms.HiddenInput(),
        }