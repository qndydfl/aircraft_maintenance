from django.db.models import Q

from .models import DispatchReference, MelDispatchItem
from manuals.models import Aircraft, ManualPDFPage, ManualFilePDFPage, CommonManualPDFPage

import csv, re, io
from datetime import datetime

from django.utils import timezone
from django.views.generic import (
    ListView,
    FormView,
    View,
    FormView,
    DetailView,
    UpdateView,
    DeleteView,
    CreateView,
    TemplateView,
)
from django.contrib import messages
from django.urls import reverse_lazy
from .forms import DispatchCSVUploadForm, DispatchReferenceForm

from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    UserPassesTestMixin,
    UserPassesTestMixin,
)
from django.http import JsonResponse, HttpResponse
from .services import (
    build_text_regex,
    build_structured_search_q,
    exclude_noisy_mel_dispatch_rows,
    recommend_package_pages,
    recommend_file_pages,
    score_package_page,
    score_file_page,
    paginate_result_group,
    parse_search_query,
    resolve_saved_package_page,
    resolve_saved_file_page,
    parse_manual_search_query,
    build_manual_text_regex,
    is_fault_code_query,
    is_message_query,
)


class DispatchListView(LoginRequiredMixin, ListView):
    model = DispatchReference
    template_name = "dispatch/dispatch_list.html"
    context_object_name = "dispatches"
    paginate_by = 30

    def get_queryset(self):
        queryset = DispatchReference.objects.select_related("aircraft").order_by(
            "aircraft__name",
            "eicas_message",
            "-created_at",
        )

        aircraft_id = self.request.GET.get("aircraft", "").strip()
        q = self.request.GET.get("q", "").strip()

        if aircraft_id:
            queryset = queryset.filter(aircraft_id=aircraft_id)

        if q:
            queryset = queryset.filter(
                Q(eicas_message__icontains=q)
                | Q(status_message__icontains=q)
                | Q(fault_code__icontains=q)
                | Q(mel_number__icontains=q)
                | Q(fim_chapter__icontains=q)
                | Q(amm_chapter__icontains=q)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["aircrafts"] = Aircraft.objects.all().order_by("maker", "name")
        context["selected_aircraft"] = self.request.GET.get("aircraft", "").strip()
        context["query"] = self.request.GET.get("q", "").strip()

        return context


class DispatchSearchView(LoginRequiredMixin, ListView):
    model = DispatchReference
    template_name = "dispatch/dispatch_search.html"
    context_object_name = "results"
    paginate_by = 20

    def get_queryset(self):
        queryset = DispatchReference.objects.select_related("aircraft").order_by(
            "aircraft__name", "eicas_message", "fault_code"
        )

        aircraft_id = self.request.GET.get("aircraft", "").strip()
        query = self.request.GET.get("q", "").strip()

        if aircraft_id:
            queryset = queryset.filter(aircraft_id=aircraft_id)

        if query:
            search_value, match_mode = parse_search_query(query)

            if search_value:
                queryset = queryset.filter(
                    build_structured_search_q(
                        [
                            "eicas_message",
                            "fault_code",
                            "mel_number",
                            "mel_search_keyword",
                            "mel_ref",
                            "fim_chapter",
                            "fim_search_keyword",
                            "fim_task_ref",
                            "amm_chapter",
                            "amm_search_keyword",
                            "amm_task_ref",
                            "description",
                            "note",
                        ],
                        search_value,
                        match_mode,
                    )
                )

        if not query and not aircraft_id:
            return DispatchReference.objects.none()

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["aircrafts"] = Aircraft.objects.filter(maker="BOEING")

        selected_aircraft = self.request.GET.get("aircraft", "")
        query = self.request.GET.get("q", "").strip()

        context["selected_aircraft"] = selected_aircraft
        context["query"] = query

        for item in context["results"]:
            context["mel_file"] = item.aircraft.get_mel()

            item.amm_query = item.amm_search_keyword or item.amm_chapter
            item.fim_query = item.fim_search_keyword or item.fim_chapter
            item.mel_query = item.mel_search_keyword or item.mel_number

            item.amm_page = resolve_saved_package_page(item, "AMM")

            item.fim_page = resolve_saved_package_page(item, "FIM")

            item.mel_file = item.aircraft.get_mel()

            item.mel_page = resolve_saved_file_page(item)

            if item.mel_file and item.mel_query:
                item.mel_page = ManualFilePDFPage.objects.filter(
                    manual_file=item.mel_file,
                    text__icontains=item.mel_query,
                ).first()

        return context


class DispatchCSVUploadView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    template_name = "dispatch/dispatch_csv_upload.html"
    form_class = DispatchCSVUploadForm
    success_url = reverse_lazy("dispatch_auto_search")

    def test_func(self):
        return self.request.user.is_staff

    def form_valid(self, form):
        csv_file = form.cleaned_data["csv_file"]

        decoded_file = csv_file.read().decode("utf-8-sig")
        io_string = io.StringIO(decoded_file)
        reader = csv.DictReader(io_string)

        reference_created_count = 0
        reference_updated_count = 0
        mel_created_count = 0
        mel_updated_count = 0
        skipped_count = 0

        def clean_value(row, key):
            return (row.get(key, "") or "").strip()

        def clean_int_value(value):
            value = (value or "").strip()
            if not value:
                return None
            try:
                return int(value)
            except ValueError:
                return None

        for row in reader:
            aircraft_name = clean_value(row, "aircraft")
            row_handled = False

            aircraft = Aircraft.objects.filter(
                name__iexact=aircraft_name, maker="BOEING"
            ).first()

            if not aircraft:
                skipped_count += 1
                continue

            eicas_message = clean_value(row, "eicas_message")
            fault_code = clean_value(row, "fault_code")

            if not eicas_message and not fault_code:
                pass
            else:
                _, created = DispatchReference.objects.update_or_create(
                    aircraft=aircraft,
                    eicas_message=eicas_message,
                    fault_code=fault_code,
                    defaults={
                        "status_message": clean_value(row, "status_message"),
                        "mel_number": clean_value(row, "mel_number"),
                        "fim_chapter": clean_value(row, "fim_chapter"),
                        "amm_chapter": clean_value(row, "amm_chapter"),
                        "mel_search_keyword": clean_value(row, "mel_search_keyword"),
                        "fim_search_keyword": clean_value(row, "fim_search_keyword"),
                        "amm_search_keyword": clean_value(row, "amm_search_keyword"),
                        "mel_ref": clean_value(row, "mel_ref"),
                        "fim_task_ref": clean_value(row, "fim_task_ref"),
                        "amm_task_ref": clean_value(row, "amm_task_ref"),
                        "description": clean_value(row, "description"),
                        "note": clean_value(row, "note"),
                        "mel_page_number": clean_int_value(row.get("mel_page_number")),
                        "fim_page_number": clean_int_value(row.get("fim_page_number")),
                        "amm_page_number": clean_int_value(row.get("amm_page_number")),
                        "mel_manual_file_id": clean_int_value(
                            row.get("mel_manual_file_id")
                        ),
                        "fim_chapter_id": clean_int_value(row.get("fim_chapter_id")),
                        "amm_chapter_id": clean_int_value(row.get("amm_chapter_id")),
                    },
                )

                if created:
                    reference_created_count += 1
                else:
                    reference_updated_count += 1
                row_handled = True

            message = clean_value(row, "message")
            level = clean_value(row, "level")
            condition = clean_value(row, "condition")
            mel_item = clean_value(row, "mel_item")
            adc = clean_value(row, "adc")
            page_number = clean_int_value(row.get("page_number"))

            if message:
                manual_file_id = clean_int_value(row.get("manual_file_id"))

                _, created = MelDispatchItem.objects.update_or_create(
                    aircraft=aircraft,
                    manual_file_id=manual_file_id,
                    message=message,
                    mel_item=mel_item,
                    page_number=page_number,
                    defaults={
                        "level": level,
                        "condition": condition,
                        "adc": adc,
                    },
                )

                if created:
                    mel_created_count += 1
                else:
                    mel_updated_count += 1
                row_handled = True

            if not row_handled:
                skipped_count += 1

        messages.success(
            self.request,
            (
                f"Dispatch Reference 신규 {reference_created_count}개, 업데이트 {reference_updated_count}개, "
                f"MEL Dispatch Item 신규 {mel_created_count}개, 업데이트 {mel_updated_count}개, "
                f"건너뜀 {skipped_count}개"
            ),
        )

        return super().form_valid(form)


class DispatchDetailView(LoginRequiredMixin, DetailView):
    model = DispatchReference
    template_name = "dispatch/dispatch_detail.html"
    context_object_name = "dispatch"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        dispatch = self.object

        query = self.request.GET.get("q", "").strip()
        selected_aircraft = self.request.GET.get("aircraft", "").strip()

        amm_query = dispatch.amm_search_keyword or dispatch.amm_chapter
        fim_query = dispatch.fim_search_keyword or dispatch.fim_chapter
        mel_query = dispatch.mel_search_keyword or dispatch.mel_number

        context["amm_page"] = resolve_saved_package_page(dispatch, "AMM")

        context["fim_page"] = resolve_saved_package_page(dispatch, "FIM")

        context["mel_file"] = dispatch.aircraft.get_mel()

        context["mel_page"] = resolve_saved_file_page(dispatch)

        context["amm_query"] = amm_query
        context["fim_query"] = fim_query
        context["mel_query"] = mel_query
        context["query"] = query
        context["selected_aircraft"] = selected_aircraft

        if context["mel_file"] and dispatch.mel_number:
            context["mel_page"] = ManualFilePDFPage.objects.filter(
                manual_file=context["mel_file"],
                text__icontains=dispatch.mel_number,
            ).first()

        if amm_query:
            context["amm_page"] = (
                ManualPDFPage.objects.select_related(
                    "chapter",
                    "chapter__package",
                    "chapter__package__aircraft",
                )
                .filter(
                    chapter__package__aircraft=dispatch.aircraft,
                    chapter__package__manual_type="AMM",
                    chapter__task__icontains=amm_query,
                )
                .first()
            )

        if fim_query:
            context["fim_page"] = (
                ManualPDFPage.objects.select_related(
                    "chapter",
                    "chapter__package",
                    "chapter__package__aircraft",
                )
                .filter(
                    chapter__package__aircraft=dispatch.aircraft,
                    chapter__package__manual_type="FIM",
                    chapter__task__icontains=fim_query,
                )
                .first()
            )

        context["manual_package_pages"] = []
        context["manual_file_pages"] = []
        context["auto_results_message"] = ""
        context["amm_results"] = []
        context["fim_results"] = []
        context["ipc_results"] = []
        context["mel_results"] = []
        context["cdl_results"] = []

        results_page = context.get("results")
        has_results = False

        if results_page is not None:
            if hasattr(results_page, "paginator"):
                has_results = results_page.paginator.count > 0
            elif hasattr(results_page, "exists"):
                has_results = results_page.exists()
            else:
                try:
                    has_results = len(results_page) > 0
                except TypeError:
                    has_results = bool(results_page)

        if query and not has_results:
            search_value, match_mode = parse_search_query(query)
            normalized_query = search_value.replace("/", " ")
            words = normalized_query.split()
            search_terms = [search_value] + words

            if selected_aircraft:
                aircraft = Aircraft.objects.filter(pk=selected_aircraft).first()

                if aircraft and search_value:
                    context["amm_results"] = recommend_package_pages(
                        aircraft,
                        "AMM",
                        search_value,
                    )
                    context["fim_results"] = recommend_package_pages(
                        aircraft,
                        "FIM",
                        search_value,
                    )
                    context["ipc_results"] = recommend_package_pages(
                        aircraft,
                        "IPC",
                        search_value,
                    )
                    context["mel_results"] = recommend_file_pages(
                        aircraft,
                        "MEL",
                        search_value,
                    )
                    context["cdl_results"] = recommend_file_pages(
                        aircraft,
                        "CDL",
                        search_value,
                    )

                    return context
            else:
                context["auto_results_message"] = (
                    "기종을 선택하면 자동 검색 결과를 표시할 수 있습니다."
                )

            package_pages = ManualPDFPage.objects.select_related(
                "chapter",
                "chapter__package",
                "chapter__package__aircraft",
            )

            file_pages = ManualFilePDFPage.objects.select_related(
                "manual_file",
                "manual_file__aircraft",
            )

            if selected_aircraft:
                package_pages = package_pages.filter(
                    chapter__package__aircraft_id=selected_aircraft
                )
                file_pages = file_pages.filter(
                    manual_file__aircraft_id=selected_aircraft
                )

            package_query = Q()
            file_query = Q()

            for term in search_terms:
                if not term:
                    continue

                if match_mode == "contains":
                    package_query |= (
                        Q(text__icontains=term)
                        | Q(chapter__task__icontains=term)
                        | Q(chapter__subtask__icontains=term)
                        | Q(chapter__title__icontains=term)
                    )

                    file_query |= (
                        Q(text__icontains=term)
                        | Q(manual_file__manual_type__icontains=term)
                        | Q(manual_file__description__icontains=term)
                    )
                else:
                    package_query |= (
                        Q(text__iexact=term)
                        | Q(chapter__task__iexact=term)
                        | Q(chapter__subtask__iexact=term)
                        | Q(chapter__title__iexact=term)
                    )

                    file_query |= (
                        Q(text__iexact=term)
                        | Q(manual_file__manual_type__iexact=term)
                        | Q(manual_file__description__iexact=term)
                    )

            package_pages = package_pages.filter(package_query)
            file_pages = file_pages.filter(file_query)

            package_pages = list(package_pages[:50])
            file_pages = list(file_pages[:50])

            for page in package_pages:
                page.snippet = page.get_snippet(query)

            for page in file_pages:
                page.snippet = page.get_snippet(query)

            context["manual_package_pages"] = package_pages
            context["manual_file_pages"] = file_pages

        return context


class StaffRequiredMixin(UserPassesTestMixin):

    def test_func(self):
        return self.request.user.is_staff


class DispatchUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = DispatchReference
    form_class = DispatchReferenceForm
    template_name = "dispatch/dispatch_form.html"
    context_object_name = "dispatch"

    def get_success_url(self):
        return reverse_lazy("dispatch_detail", kwargs={"pk": self.object.pk})


class DispatchDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = DispatchReference
    template_name = "dispatch/dispatch_confirm_delete.html"
    context_object_name = "dispatch"
    success_url = reverse_lazy("dispatch_auto_search")


class DispatchCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = DispatchReference
    form_class = DispatchReferenceForm
    template_name = "dispatch/dispatch_form.html"

    def get_initial(self):
        initial = super().get_initial()

        aircraft_id = self.request.GET.get("aircraft")

        search_type = self.request.GET.get("search_type", "").strip()
        search_text = self.request.GET.get("search_text", "").strip()

        eicas_message = self.request.GET.get("eicas_message", "").strip()
        status_message = self.request.GET.get("status_message", "").strip()
        fault_code = self.request.GET.get("fault_code", "").strip()

        mel_number = self.request.GET.get("mel_number", "").strip()
        fim_chapter = self.request.GET.get("fim_chapter", "").strip()
        amm_chapter = self.request.GET.get("amm_chapter", "").strip()

        mel_keyword = self.request.GET.get("mel_keyword", "").strip()
        fim_keyword = self.request.GET.get("fim_keyword", "").strip()
        amm_keyword = self.request.GET.get("amm_keyword", "").strip()

        mel_page_id = self.request.GET.get("mel_page_id")
        fim_page_id = self.request.GET.get("fim_page_id")
        amm_page_id = self.request.GET.get("amm_page_id")

        if aircraft_id:
            initial["aircraft"] = aircraft_id

        if search_text:
            if search_type == "message":
                initial["eicas_message"] = search_text
            elif search_type == "fault":
                initial["fault_code"] = search_text
            else:
                initial["eicas_message"] = search_text

        if eicas_message:
            initial["eicas_message"] = eicas_message

        if status_message:
            initial["status_message"] = status_message

        if fault_code:
            initial["fault_code"] = fault_code

        if mel_number:
            initial["mel_number"] = mel_number

        if fim_chapter:
            initial["fim_chapter"] = fim_chapter

        if amm_chapter:
            initial["amm_chapter"] = amm_chapter

        if mel_keyword:
            initial["mel_search_keyword"] = mel_keyword

        if fim_keyword:
            initial["fim_search_keyword"] = fim_keyword

        if amm_keyword:
            initial["amm_search_keyword"] = amm_keyword

        reference_text = search_text or eicas_message or status_message or fault_code

        if mel_page_id:
            mel_page = (
                ManualFilePDFPage.objects.select_related("manual_file")
                .filter(pk=mel_page_id)
                .first()
            )

            if mel_page:
                initial["mel_number"] = mel_number or reference_text or ""
                initial["mel_search_keyword"] = reference_text or ""
                initial["mel_page_number"] = mel_page.page_number
                initial["mel_manual_file_id"] = mel_page.manual_file.id
                initial["mel_ref"] = reference_text or ""

        if fim_page_id:
            fim_page = (
                ManualPDFPage.objects.select_related("chapter")
                .filter(pk=fim_page_id)
                .first()
            )

            if fim_page:
                initial["fim_chapter"] = fim_page.chapter.task
                initial["fim_search_keyword"] = reference_text or fim_page.chapter.task
                initial["fim_page_number"] = fim_page.page_number
                initial["fim_chapter_id"] = fim_page.chapter.id
                initial["fim_task_ref"] = fim_page.chapter.task

        if amm_page_id:
            amm_page = (
                ManualPDFPage.objects.select_related("chapter")
                .filter(pk=amm_page_id)
                .first()
            )

            if amm_page:
                initial["amm_chapter"] = amm_page.chapter.task
                initial["amm_search_keyword"] = reference_text or amm_page.chapter.task
                initial["amm_page_number"] = amm_page.page_number
                initial["amm_chapter_id"] = amm_page.chapter.id
                initial["amm_task_ref"] = amm_page.chapter.task

        return initial

    def get_success_url(self):
        return reverse_lazy("dispatch_detail", kwargs={"pk": self.object.pk})


class DispatchAutocompleteView(LoginRequiredMixin, View):

    def get(self, request, *args, **kwargs):
        query = request.GET.get("q", "").strip()

        results = []

        if query:
            queryset = DispatchReference.objects.filter(
                Q(eicas_message__icontains=query) | Q(fault_code__icontains=query)
            ).order_by("eicas_message", "fault_code")[:20]

            for item in queryset:
                if item.eicas_message:
                    results.append(item.eicas_message)

                if item.fault_code:
                    results.append(item.fault_code)

        return JsonResponse({"results": list(dict.fromkeys(results))})


class DispatchCSVDownloadView(LoginRequiredMixin, View):

    def get(self, request, *args, **kwargs):
        aircraft_id = request.GET.get("aircraft", "").strip()
        query = request.GET.get("q", "").strip()

        queryset = DispatchReference.objects.select_related("aircraft").order_by(
            "aircraft__name", "eicas_message", "fault_code"
        )

        if aircraft_id:
            queryset = queryset.filter(aircraft_id=aircraft_id)

        if query:
            words = query.split()

            for word in words:
                queryset = queryset.filter(
                    Q(eicas_message__icontains=word)
                    | Q(fault_code__icontains=word)
                    | Q(mel_number__icontains=word)
                    | Q(mel_search_keyword__icontains=word)
                    | Q(mel_ref__icontains=word)
                    | Q(fim_chapter__icontains=word)
                    | Q(fim_search_keyword__icontains=word)
                    | Q(fim_task_ref__icontains=word)
                    | Q(amm_chapter__icontains=word)
                    | Q(amm_search_keyword__icontains=word)
                    | Q(amm_task_ref__icontains=word)
                    | Q(description__icontains=word)
                    | Q(note__icontains=word)
                )

        if not aircraft_id and not query:
            queryset = DispatchReference.objects.none()

        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")

        response["Content-Disposition"] = 'attachment; filename="dispatch_results.csv"'

        response.write("\ufeff")

        writer = csv.writer(response)

        writer.writerow(
            [
                "aircraft",
                "eicas_message",
                "fault_code",
                "mel_number",
                "fim_chapter",
                "amm_chapter",
                "description",
                "note",
            ]
        )

        for item in queryset:
            writer.writerow(
                [
                    item.aircraft.name,
                    item.eicas_message,
                    item.fault_code,
                    item.mel_number,
                    item.fim_chapter,
                    item.amm_chapter,
                    item.description,
                    item.note,
                ]
            )

        return response


class DispatchPrintView(LoginRequiredMixin, TemplateView):
    template_name = "dispatch/dispatch_print.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        aircraft_id = self.request.GET.get("aircraft", "").strip()
        query = self.request.GET.get("q", "").strip()

        queryset = DispatchReference.objects.select_related("aircraft").order_by(
            "aircraft__name", "eicas_message", "fault_code"
        )

        if aircraft_id:
            queryset = queryset.filter(aircraft_id=aircraft_id)

        if query:
            words = query.split()

            for word in words:
                queryset = queryset.filter(
                    Q(eicas_message__icontains=word)
                    | Q(fault_code__icontains=word)
                    | Q(mel_number__icontains=word)
                    | Q(mel_search_keyword__icontains=word)
                    | Q(mel_ref__icontains=word)
                    | Q(fim_chapter__icontains=word)
                    | Q(fim_search_keyword__icontains=word)
                    | Q(fim_task_ref__icontains=word)
                    | Q(amm_chapter__icontains=word)
                    | Q(amm_search_keyword__icontains=word)
                    | Q(amm_task_ref__icontains=word)
                    | Q(description__icontains=word)
                    | Q(note__icontains=word)
                )

        if not aircraft_id and not query:
            queryset = DispatchReference.objects.none()

        context["results"] = queryset
        context["query"] = query
        context["selected_aircraft"] = aircraft_id

        return context


class DispatchDetailPrintView(LoginRequiredMixin, DetailView):
    model = DispatchReference
    template_name = "dispatch/dispatch_detail_print.html"
    context_object_name = "dispatch"


def format_fml_date(value):
    if value:
        try:
            parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return value.upper()
    else:
        parsed_date = timezone.localdate()

    return parsed_date.strftime("%d%b%y").upper()


def clean_fml_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def extract_first_match(pattern, text):
    match = re.search(pattern, text or "", re.IGNORECASE)
    return clean_fml_text(match.group(1) if match and match.groups() else match.group(0) if match else "")


def extract_ata_code(*values):
    for value in values:
        match = re.search(r"\b(\d{2})[-\s]?(\d{2})(?:[-\s]?\d{2})?", value or "")

        if match:
            return f"{match.group(1)} {match.group(2)}"

    return ""


def extract_reference(prefix, text):
    pattern = rf"\b({prefix}\.?\s*\d{{2}}-\d{{2}}-\d{{2}}-\d{{3}}-\d{{3}}[A-Z0-9-]*)"
    return extract_first_match(pattern, text)


def extract_ata_prefix(maint_msg):
    match = re.search(r"\b(\d{2})", maint_msg or "")
    return match.group(1) if match else ""


def find_nearest_reference(text, ata_prefix, reference_prefix=None, anchor=None):
    if not text or not ata_prefix:
        return ""

    if reference_prefix:
        pattern = (
            rf"\b({reference_prefix}\.?\s*{ata_prefix}-"
            r"\d{2}-\d{2}-\d{3}-\d{3}[A-Z0-9-]*)"
        )
    else:
        pattern = rf"\b({ata_prefix})[-\s]?(\d{{2}})(?:[-\s]?\d{{2}})?(?:[-\s]?\d{{3}})?(?:[-\s]?\d{{3}})?[A-Z0-9-]*"

    matches = list(re.finditer(pattern, text, re.IGNORECASE))

    if not matches:
        return ""

    anchor_index = -1

    if anchor:
        anchor_index = text.lower().find(anchor.lower())

    if anchor_index >= 0:
        selected = min(matches, key=lambda item: abs(item.start() - anchor_index))
    else:
        selected = matches[0]

    if reference_prefix:
        return clean_fml_text(selected.group(1)).upper()

    return f"{selected.group(1)} {selected.group(2)}"


def collect_ata_candidates(text, ata_prefix, keyword="", limit=60):
    if not text or not ata_prefix:
        return []

    candidates = []
    seen = set()
    scan_limit = max(limit * 3, 24)

    def add_candidate(suffix, description, include_group=False):
        if suffix.endswith("0") and not include_group:
            return None

        code = f"{ata_prefix} {suffix}"
        compact_code = f"{ata_prefix}{suffix}"
        clean_description = clean_fml_text(description).upper()

        if not clean_description or clean_description.isdigit():
            return None

        if suffix == ata_prefix and (
            "ATA CODE" in clean_description
            or "CONTINUED" in clean_description
        ):
            return None

        if code in seen:
            return None

        seen.add(code)
        candidate = {
            "value": code,
            "compact": compact_code,
            "description": clean_description,
            "label": f"{compact_code} - {clean_description}",
        }
        candidates.append(candidate)
        return candidate

    def append_candidate_description(candidate, text):
        if not candidate:
            return

        clean_description = clean_fml_text(text).upper()

        if not clean_description:
            return

        candidate["description"] = clean_fml_text(
            f"{candidate['description']} {clean_description}"
        ).upper()
        candidate["label"] = f"{candidate['compact']} - {candidate['description']}"

    section_active = False
    current_candidate = None

    for raw_line in text.splitlines():
        line = clean_fml_text(raw_line)
        if not line:
            continue

        section_match = re.match(
            rf"^{ata_prefix}\s*[A-Z][A-Z0-9 /&().,-]*$",
            line,
            re.IGNORECASE,
        )
        if section_match:
            section_active = True
            current_candidate = None
            continue

        prefixed_match = re.match(
            rf"^{ata_prefix}\s*(\d{{2}})\s*([A-Z][A-Z0-9 /&().,-]+)$",
            line,
            re.IGNORECASE,
        )
        if prefixed_match:
            section_active = True
            current_candidate = add_candidate(
                prefixed_match.group(1),
                prefixed_match.group(2),
                include_group=True,
            )
        elif section_active:
            child_match = re.match(r"^(\d{2})\s*([A-Z][A-Z0-9 /&().,-]+)$", line, re.IGNORECASE)
            if child_match:
                current_candidate = add_candidate(child_match.group(1), child_match.group(2))
            elif re.match(r"^[A-Z][A-Z0-9 /&().,-]+$", line, re.IGNORECASE):
                append_candidate_description(current_candidate, line)
            elif re.match(r"^\d{2}\s*[A-Z]", line, re.IGNORECASE):
                section_active = False
                current_candidate = None

        if len(candidates) >= scan_limit:
            break

    inline_pattern = rf"\b{ata_prefix}\s*(\d{{2}})\s+([A-Z][A-Z0-9 /&().,-]{{2,70}})"

    for match in re.finditer(inline_pattern, text, re.IGNORECASE):
        if len(candidates) >= scan_limit:
            break

        add_candidate(match.group(1), match.group(2))

        if len(candidates) >= scan_limit:
            break

    flat_text = clean_fml_text(text).upper()
    section_start = re.search(
        rf"\b{ata_prefix}\s*[A-Z][A-Z0-9 /&().,-]*?\s+(?={ata_prefix}\s*[1-9]0\b)",
        flat_text,
        re.IGNORECASE,
    )

    if section_start and len(candidates) < scan_limit:
        section_text = flat_text[section_start.end():]
        next_chapter = None

        for chapter_match in re.finditer(
            r"\b(\d{2})\s+[A-Z][A-Z0-9 /&().,-]{3,80}?\s+\1\s*10\b",
            section_text,
            re.IGNORECASE,
        ):
            if chapter_match.group(1) != ata_prefix:
                next_chapter = chapter_match.start()
                break

        if next_chapter is not None:
            section_text = section_text[:next_chapter]

        markers = list(
            re.finditer(
                rf"\b{ata_prefix}\s*([1-9]0)\s+|\b([1-9]\d)\s+",
                section_text,
                re.IGNORECASE,
            )
        )
        current_group = ""

        for index, marker in enumerate(markers):
            if len(candidates) >= scan_limit:
                break

            next_start = markers[index + 1].start() if index + 1 < len(markers) else len(section_text)

            if marker.group(1):
                current_group = marker.group(1)
                description = section_text[marker.end():next_start]
                add_candidate(current_group, description, include_group=True)
                continue

            suffix = marker.group(2)

            if not current_group or suffix[0] != current_group[0]:
                continue

            description = section_text[marker.end():next_start]
            add_candidate(suffix, description)

    keyword_value = clean_fml_text(keyword).replace("*", "").upper()

    if keyword_value:
        keyword_parts = [
            part
            for part in re.split(r"\s+", keyword_value)
            if len(part) >= 3
        ]

        if keyword_parts:
            related = [
                candidate
                for candidate in candidates
                if any(part in candidate["description"] for part in keyword_parts)
            ]
            unrelated = [
                candidate
                for candidate in candidates
                if candidate not in related
            ]
            candidates = related + unrelated

    return candidates[:limit]


def candidate_values(candidates):
    return [candidate["value"] for candidate in candidates]


def get_fml_aircraft(aircraft_id, row=None, page=None):
    if row:
        return row.aircraft

    if page:
        return page.manual_file.aircraft

    if aircraft_id:
        return Aircraft.objects.filter(pk=aircraft_id).first()

    return None


def find_mel_page_by_maint_msg(aircraft, maint_msg, ata_prefix):
    if not aircraft:
        return None

    queryset = ManualFilePDFPage.objects.select_related(
        "manual_file",
        "manual_file__aircraft",
    ).filter(
        manual_file__aircraft=aircraft,
        manual_file__manual_type="MEL",
    )

    if maint_msg:
        page = queryset.filter(text__icontains=maint_msg).order_by("page_number").first()

        if page:
            return page

    if ata_prefix:
        return queryset.filter(text__icontains=f"{ata_prefix}-").order_by("page_number").first()

    return None


def find_fim_reference_from_upload(aircraft, ata_prefix, maint_msg="", source_text=""):
    if not aircraft or aircraft.maker != "BOEING" or not ata_prefix:
        return ""

    fim_task_ref = find_fim_task_reference_by_maint_msg(
        aircraft,
        ata_prefix,
        maint_msg,
    )

    if fim_task_ref:
        return fim_task_ref

    source_fim_ref = find_nearest_reference(source_text, ata_prefix, reference_prefix="FIM", anchor=maint_msg)

    if source_fim_ref:
        return source_fim_ref

    return find_package_reference_from_upload(
        aircraft,
        "FIM",
        ata_prefix,
        maint_msg=maint_msg,
    )


def normalize_fim_maint_msg(value):
    normalized = normalize_maint_msg(value).replace(" ", "")
    match = re.search(r"\b(\d{2})[-\s]?(\d{5})\b", normalized)

    if match:
        return f"{match.group(1)}-{match.group(2)}"

    return normalized


def extract_fim_task_reference_from_text(text, maint_msg, ata_prefix):
    normalized_maint_msg = normalize_fim_maint_msg(maint_msg)

    if not text or not normalized_maint_msg:
        return ""

    escaped_msg = re.escape(normalized_maint_msg)
    pattern = (
        rf"\b{escaped_msg}\b\s+"
        rf"({ata_prefix}-\d{{2}})\s+TASK\s+(\d{{3,4}})"
    )
    match = re.search(pattern, clean_fml_text(text).upper(), re.IGNORECASE)

    if match:
        return f"FIM {match.group(1)} TASK {match.group(2)}"

    loose_msg = normalized_maint_msg.replace("-", r"[-\s]?")
    loose_pattern = (
        rf"\b{loose_msg}\b\s+"
        rf"({ata_prefix}[-\s]\d{{2}})\s+TASK\s+(\d{{3,4}})"
    )
    loose_match = re.search(loose_pattern, clean_fml_text(text).upper(), re.IGNORECASE)

    if loose_match:
        task = loose_match.group(1).replace(" ", "-")
        return f"FIM {task} TASK {loose_match.group(2)}"

    return ""


def find_fim_task_reference_by_maint_msg(aircraft, ata_prefix, maint_msg):
    normalized_maint_msg = normalize_fim_maint_msg(maint_msg)

    if not aircraft or not ata_prefix or not normalized_maint_msg:
        return ""

    queryset = ManualPDFPage.objects.select_related(
        "chapter",
        "chapter__package",
        "chapter__package__aircraft",
    ).filter(
        chapter__package__aircraft=aircraft,
        chapter__package__manual_type="FIM",
    )

    maint_msg_variants = {
        normalized_maint_msg,
        normalized_maint_msg.replace("-", " "),
        normalized_maint_msg.replace("-", ""),
    }

    page_filter = Q()

    for variant in maint_msg_variants:
        if variant:
            page_filter |= Q(text__icontains=variant)

    for page in queryset.filter(page_filter).order_by("chapter__task", "page_number")[:20]:
        reference = extract_fim_task_reference_from_text(
            page.text,
            normalized_maint_msg,
            ata_prefix,
        )

        if reference:
            return reference

    return ""


def build_chapter_reference(page, manual_type):
    if not page or not page.chapter:
        return ""

    task = clean_fml_text(page.chapter.task)
    subtask = clean_fml_text(page.chapter.subtask)

    if not task:
        return ""

    if subtask:
        return f"{manual_type} {task}-{subtask}".upper()

    return f"{manual_type} {task}".upper()


def find_package_reference_from_upload(aircraft, manual_type, ata_prefix, maint_msg=""):
    if not aircraft or not ata_prefix:
        return ""

    queryset = ManualPDFPage.objects.select_related(
        "chapter",
        "chapter__package",
        "chapter__package__aircraft",
    ).filter(
        chapter__package__aircraft=aircraft,
        chapter__package__manual_type=manual_type,
    )

    if maint_msg:
        page = queryset.filter(text__icontains=maint_msg).order_by("page_number").first()

        if page:
            reference = find_nearest_reference(
                page.text,
                ata_prefix,
                reference_prefix=manual_type,
                anchor=maint_msg,
            )

            if reference:
                return reference

            chapter_ref = build_chapter_reference(page, manual_type)

            if chapter_ref:
                return chapter_ref

    page = queryset.filter(
        Q(text__icontains=f"{ata_prefix}-")
        | Q(chapter__task__startswith=ata_prefix)
    ).order_by(
        "chapter__task",
        "chapter__subtask",
        "page_number",
    ).first()

    if not page:
        return ""

    reference = find_nearest_reference(
        page.text,
        ata_prefix,
        reference_prefix=manual_type,
        anchor=maint_msg,
    )

    return reference or build_chapter_reference(page, manual_type)


def find_amm_reference_from_upload(aircraft, ata_prefix, maint_msg="", source_text=""):
    source_amm_ref = find_nearest_reference(source_text, ata_prefix, reference_prefix="AMM", anchor=maint_msg)

    if re.search(r"\bAMM\s+TASK\s+\d{2}-\d{2}-\d{2}-\d{3}-\d{3}\b", source_amm_ref, re.IGNORECASE):
        return source_amm_ref

    amm_ref = find_package_reference_from_upload(
        aircraft,
        "AMM",
        ata_prefix,
        maint_msg=maint_msg,
    )

    if re.search(r"\bAMM\s+TASK\s+\d{2}-\d{2}-\d{2}-\d{3}-\d{3}\b", amm_ref, re.IGNORECASE):
        return amm_ref

    return ""


def parse_fim_task_reference(fim_ref):
    match = re.search(
        r"\bFIM\s+(\d{2}-\d{2})\s+TASK\s+(\d{3,4})\b",
        fim_ref or "",
        re.IGNORECASE,
    )

    if not match:
        return "", ""

    return match.group(1), match.group(2)


def build_performed_test_text(task_title, amm_task):
    clean_title = clean_fml_text(task_title).replace(" - ", " ")
    clean_title = re.sub(r"\s*-\s*", " ", clean_title)
    clean_title = clean_fml_text(clean_title).upper()
    clean_amm_task = clean_fml_text(amm_task).upper()

    if (
        clean_amm_task.startswith("AMM TASK 45-")
        and "RECIRCULATION FAN SYSTEM" in clean_title
    ):
        clean_title = "RECIRCULATION FAN SYSTEM TEST"
        clean_amm_task = "AMM TASK 21-25-00-710-801"

    if not clean_title or not clean_amm_task:
        return ""

    return f"PERFORMED {clean_title} NORMAL PER {clean_amm_task}"


def extract_amm_task_reference(text):
    match = re.search(
        r"\b(AMM\s+TASK\s+\d{2}-\d{2}-\d{2}-\d{3}-\d{3})\b",
        text or "",
        re.IGNORECASE,
    )

    if match:
        return clean_fml_text(match.group(1)).upper()

    return ""


def extract_fim_task_block(task_text, task_number):
    normalized_text = clean_fml_text(task_text)

    if not normalized_text or not task_number:
        return normalized_text

    start_match = re.search(
        rf"\b{re.escape(task_number)}\.\s+",
        normalized_text,
        re.IGNORECASE,
    )

    if not start_match:
        return normalized_text

    task_block = normalized_text[start_match.start():]
    end_match = re.search(r"\bEND OF TASK\b", task_block, re.IGNORECASE)

    if end_match:
        return task_block[: end_match.end()]

    next_task_match = re.search(
        rf"\b(?!{re.escape(task_number)}\.)\d{{3,4}}\.\s+",
        task_block[start_match.end() - start_match.start():],
        re.IGNORECASE,
    )

    if next_task_match:
        return task_block[: start_match.end() - start_match.start() + next_task_match.start()]

    return task_block


def extract_fim_test_action_text(task_text, task_number=""):
    if not task_text:
        return ""

    normalized_text = extract_fim_task_block(task_text, task_number)
    section_match = re.search(
        r"\bFault Isolation Procedure\b(?!\s+below\b)"
        r"(?:\s*-\s*[A-Z][A-Za-z /-]*)?"
        r"(.*?)(?:\bEND OF TASK\b)",
        normalized_text,
        re.IGNORECASE,
    )
    search_text = section_match.group(1) if section_match else ""

    if not search_text:
        return ""

    wdm_match = re.search(r"\bWDM\b", search_text, re.IGNORECASE)

    if wdm_match:
        search_text = search_text[: wdm_match.start()]

    task_match = re.search(
        r"This is the task:\s*([^.,]*?\btest\b[^.,]*?),\s*"
        r"(AMM\s+TASK\s+\d{2}-\d{2}-\d{2}-\d{3}-\d{3})",
        search_text,
        re.IGNORECASE,
    )

    if task_match:
        return build_performed_test_text(task_match.group(1), task_match.group(2))

    task_match = re.search(
        r"(?:Do|Perform|Test|Check)?\s*(?:the\s+)?"
        r"([A-Z0-9 /()-]*?\btest\b[A-Z0-9 /()-]*?)\.\s*"
        r"This is the task:\s*([^.,]*?\btest\b[^.,]*?),\s*"
        r"(AMM\s+TASK\s+\d{2}-\d{2}-\d{2}-\d{3}-\d{3})",
        search_text,
        re.IGNORECASE,
    )

    if task_match:
        return build_performed_test_text(task_match.group(2), task_match.group(3))

    ground_test_match = re.search(
        r"Do this Ground Test on the MAT:\s*(.*?)\s*"
        r"\([^)]*?(AMM\s+TASK\s+\d{2}-\d{2}-\d{2}-\d{3}-\d{3})\)",
        search_text,
        re.IGNORECASE,
    )

    if ground_test_match:
        return build_performed_test_text(
            ground_test_match.group(1),
            ground_test_match.group(2),
        )

    task_match = re.search(
        r"([^.;:]*?\btest\b[^.;:]*?),\s*"
        r"(AMM\s+TASK\s+\d{2}-\d{2}-\d{2}-\d{3}-\d{3})",
        search_text,
        re.IGNORECASE,
    )

    if task_match:
        return build_performed_test_text(task_match.group(1), task_match.group(2))

    return ""


def is_fim_task_body_text(text, task_number, maint_msg=""):
    normalized_text = clean_fml_text(text).upper()

    if not re.search(rf"\b{re.escape(task_number)}\.\s+", normalized_text):
        return False

    if maint_msg:
        normalized_maint_msg = normalize_fim_maint_msg(maint_msg)

        if normalized_maint_msg and normalized_maint_msg not in normalized_text:
            return False

    return bool(re.search(r"\bTHIS TASK IS FOR MAINTENANCE MESSAGES?\b", normalized_text))


def find_fim_test_action_text(aircraft, fim_ref, maint_msg=""):
    task_chapter, task_number = parse_fim_task_reference(fim_ref)

    if not aircraft or not task_chapter or not task_number:
        return ""

    queryset = ManualPDFPage.objects.select_related(
        "chapter",
        "chapter__package",
        "chapter__package__aircraft",
    ).filter(
        chapter__package__aircraft=aircraft,
        chapter__package__manual_type="FIM",
        chapter__task__startswith=task_chapter.split("-")[0],
    )
    normalized_maint_msg = normalize_fim_maint_msg(maint_msg)
    task_heading = f"{task_number}."

    start_page = None

    if normalized_maint_msg:
        task_pages = (
            queryset.filter(text__icontains=normalized_maint_msg)
            .filter(text__icontains=task_heading)
            .order_by("chapter__task", "page_number")[:30]
        )

        for page in task_pages:
            if is_fim_task_body_text(page.text, task_number, normalized_maint_msg):
                start_page = page
                break

    if not start_page and normalized_maint_msg:
        task_ref_text = f"{task_chapter} TASK {task_number}"
        index_page = (
            queryset.filter(text__icontains=normalized_maint_msg)
            .filter(text__icontains=task_ref_text)
            .order_by("chapter__task", "page_number")
            .first()
        )

        if index_page:
            body_pages = (
                ManualPDFPage.objects.filter(
                    chapter=index_page.chapter,
                    page_number__gte=index_page.page_number,
                )
                .order_by("page_number")[:900]
            )

            for page in body_pages:
                if is_fim_task_body_text(page.text, task_number, normalized_maint_msg):
                    start_page = page
                    break

    if not start_page:
        for page in queryset.filter(text__icontains=task_heading).order_by("chapter__task", "page_number")[:50]:
            if is_fim_task_body_text(page.text, task_number):
                start_page = page
                break

    if not start_page:
        return ""

    pages = (
        ManualPDFPage.objects.filter(
            chapter=start_page.chapter,
            page_number__gte=start_page.page_number,
            page_number__lte=start_page.page_number + 10,
        )
        .order_by("page_number")
        .values_list("text", flat=True)
    )
    task_text_parts = []

    for text in pages:
        task_text_parts.append(text)
        action_text = extract_fim_test_action_text(
            " ".join(task_text_parts),
            task_number=task_number,
        )

        if action_text:
            return action_text

    return ""


def extract_maint_msg(text):
    return extract_first_match(r"\b(MAINT\s+MSG\s+[A-Z0-9,\s-]+)", text)


def normalize_maint_msg(value):
    return re.sub(
        r"^\s*MAINT\s+MSG\s*",
        "",
        clean_fml_text(value),
        flags=re.IGNORECASE,
    ).upper()


def build_defect_text(query, row=None, page=None):
    if query:
        return clean_fml_text(query).upper()

    if row and row.message:
        return clean_fml_text(row.message).upper()

    page_text = page.text if page else ""
    query_words = [word for word in re.split(r"\s+", query or "") if word]

    for raw_line in re.split(r"[\r\n]+", page_text):
        line = clean_fml_text(raw_line)

        if not line:
            continue

        lowered_line = line.lower()

        if query_words and all(word.lower().rstrip("*") in lowered_line for word in query_words):
            return line.upper()

    return clean_fml_text(query).upper()


def extract_defer_reason(*values):
    source_text = clean_fml_text(" ".join(value or "" for value in values))

    if not source_text:
        return "TIME CONSRT"

    match = re.search(
        r"\b(REPETITIVE\s+MAINTENANCE\s+ACTION\s+REQUIRED"
        r"(?:\s+(?:AT\s+)?(?:BEFORE|AFTER|EVERY|EACH|WITHIN|INTERVAL|ON|PRIOR|NEXT)\b"
        r"[^.;:\n]*)?)",
        source_text,
        re.IGNORECASE,
    )

    if match:
        return clean_fml_text(match.group(1)).upper()

    return "TIME CONSRT"


def has_m_procedure(*values):
    source_text = " ".join(value or "" for value in values)
    return bool(re.search(r"\(\s*M\s*\)", source_text, re.IGNORECASE))


def find_mel_item_source_text(row):
    if not row or not row.manual_file_id or not row.mel_item:
        return ""

    mel_item = clean_fml_text(row.mel_item)
    queryset = ManualFilePDFPage.objects.filter(
        manual_file_id=row.manual_file_id,
        text__icontains=mel_item,
    ).order_by("page_number")

    m_page = queryset.filter(text__icontains="(M)").first()

    if m_page:
        return m_page.text

    item_page = queryset.first()

    if item_page:
        return item_page.text

    return ""


def build_action_taken(
    query,
    maint_msg,
    row=None,
    page=None,
    aircraft=None,
    fim_ref="",
    amm_ref="",
    fim_action_text="",
    defer_action=False,
    defer_no="",
):
    page_text = page.text if page else ""
    row_text = row.condition if row else ""
    mel_item_text = find_mel_item_source_text(row)
    source_text = clean_fml_text(" ".join([page_text, row_text]))

    selected_maint_msg = normalize_maint_msg(maint_msg)

    ata_prefix = extract_ata_prefix(selected_maint_msg)
    tsm_ref = extract_reference("TSM", source_text)
    according_ref = fim_ref or tsm_ref
    condition = clean_fml_text(row_text).upper()

    if defer_action:
        defer_reason = extract_defer_reason(row_text, page_text, mel_item_text)

        if has_m_procedure(row_text, page_text, mel_item_text):
            return (
                "DEFERRED AND PERFORMED M PROCEDURE "
                f"<REASON: {defer_reason}>"
            )

        return f"DEFERRED <REASON: {defer_reason}>"

    action_parts = []

    if selected_maint_msg:
        opening = f"MAINT MSG {selected_maint_msg}"

        if according_ref:
            opening += f", ACCORDING TO {according_ref.upper()},"
        else:
            opening += ","

        action_parts.append(opening)

    if fim_action_text:
        action_parts.append(clean_fml_text(fim_action_text).upper())
    elif fim_ref:
        action_parts.append(f"CHECK THE APPLICABLE {fim_ref.upper()}")
    elif condition:
        action_parts.append(condition)

    if fim_action_text and amm_ref and "PER AMM" not in " ".join(action_parts).upper():
        action_parts.append(f"PER {amm_ref.upper()}")

    if not action_parts and query:
        action_parts.append(clean_fml_text(query).upper())

    return "\n".join(action_parts)


def build_fml_source_from_request(request):
    row = None
    page = None
    query = request.GET.get("q", "").strip()
    aircraft_id = request.GET.get("aircraft", "").strip()
    row_id = request.GET.get("row_id", "").strip()
    page_id = request.GET.get("page_id", "").strip()

    if row_id:
        row = (
            MelDispatchItem.objects.select_related("aircraft", "manual_file")
            .filter(pk=row_id)
            .first()
        )

        if row:
            query = query or row.message
            aircraft_id = aircraft_id or str(row.aircraft_id)

    if page_id:
        page = (
            ManualFilePDFPage.objects.select_related("manual_file", "manual_file__aircraft")
            .filter(pk=page_id)
            .first()
        )

        if page:
            aircraft_id = aircraft_id or str(page.manual_file.aircraft_id)

    if not page and query:
        aircraft_qs = Aircraft.objects.all()

        if aircraft_id:
            aircraft_qs = aircraft_qs.filter(pk=aircraft_id)

        search_value, match_mode = parse_manual_search_query(query)

        if search_value:
            text_regex = build_manual_text_regex(search_value, match_mode)

            if match_mode == "contains":
                page_filter = Q(text__icontains=search_value)
            else:
                page_filter = Q(text__iregex=text_regex)

            page = (
                ManualFilePDFPage.objects.select_related(
                    "manual_file",
                    "manual_file__aircraft",
                )
                .filter(
                    manual_file__aircraft__in=aircraft_qs,
                    manual_file__manual_type="MEL",
                )
                .filter(page_filter)
                .order_by("manual_file__aircraft__name", "page_number")
                .first()
            )

    if not row and query:
        aircraft_qs = Aircraft.objects.all()

        if aircraft_id:
            aircraft_qs = aircraft_qs.filter(pk=aircraft_id)

        search_value, match_mode = parse_search_query(query)

        if search_value:
            row = (
                exclude_noisy_mel_dispatch_rows(
                    MelDispatchItem.objects.select_related("aircraft", "manual_file")
                    .filter(aircraft__in=aircraft_qs)
                )
                .filter(
                    build_structured_search_q(
                        ["message", "condition", "mel_item"],
                        search_value,
                        match_mode,
                    )
                )
                .order_by("aircraft__name", "message", "page_number")
                .first()
            )

    return query, aircraft_id, row, page


class DispatchFmlGeneratorView(LoginRequiredMixin, TemplateView):
    template_name = "dispatch/dispatch_fml_generator.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        query, aircraft_id, row, page = build_fml_source_from_request(self.request)
        maint_msg = self.request.GET.get("maint_msg", "").strip()
        station = self.request.GET.get("station", "").strip().upper()
        work_order = self.request.GET.get("work_order", "").strip()
        date_value = self.request.GET.get("date", "").strip()
        selected_ata_code = self.request.GET.get("ata_code", "").strip()
        defer_action = self.request.GET.get("defer_action") == "1"
        defer_no = self.request.GET.get("defer_no", "").strip()
        aircraft = get_fml_aircraft(aircraft_id, row=row, page=page)
        ata_prefix = extract_ata_prefix(maint_msg)

        if maint_msg and not page:
            page = find_mel_page_by_maint_msg(aircraft, maint_msg, ata_prefix)

        defect_text = build_defect_text(query, row=row, page=page)
        defer_no_display = defer_no or (row.mel_item if row else "")
        source_text = " ".join(
            [
                row.mel_item if row else "",
                row.condition if row else "",
                page.text if page else "",
            ]
        )

        if not ata_prefix:
            ata_prefix = extract_ata_prefix(row.mel_item if row else "") or extract_ata_prefix(source_text)

        ata_candidates = collect_ata_candidates(
            source_text,
            ata_prefix,
            keyword=query,
        )

        if ata_prefix:
            common_pages = (
                CommonManualPDFPage.objects.select_related(
                    "common_file",
                    "common_file__category",
                )
                .filter(
                    Q(text__icontains=f"{ata_prefix}-")
                    | Q(text__icontains=ata_prefix)
                    | Q(common_file__title__icontains="ATA")
                    | Q(common_file__description__icontains="ATA")
                    | Q(common_file__category__name__icontains="ATA")
                    | Q(common_file__category__code__icontains="ATA")
                )
                .order_by("common_file__category__order", "common_file__title", "page_number")
                .values_list("text", flat=True)[:30]
            )

            common_candidates = []

            for common_text in common_pages:
                for candidate in collect_ata_candidates(
                    common_text,
                    ata_prefix,
                    keyword=query,
                ):
                    if candidate["value"] not in candidate_values(common_candidates):
                        common_candidates.append(candidate)

                    if len(common_candidates) >= 60:
                        break

                if len(common_candidates) >= 60:
                    break

            if common_candidates:
                ata_candidates = common_candidates

        if len(ata_candidates) < 4 and aircraft and ata_prefix:
            extra_pages = (
                ManualFilePDFPage.objects.filter(
                    Q(text__icontains=f"{ata_prefix}-")
                    | Q(text__icontains=ata_prefix),
                    manual_file__aircraft=aircraft,
                )
                .filter(
                    Q(manual_file__manual_type="MEL")
                    | Q(manual_file__manual_type="OTHER")
                    | Q(manual_file__description__icontains="ATA CODE")
                )
                .order_by("page_number")
                .values_list("text", flat=True)[:20]
            )

            for extra_text in extra_pages:
                for candidate in collect_ata_candidates(
                    extra_text,
                    ata_prefix,
                    keyword=query,
                ):
                    if candidate["value"] not in candidate_values(ata_candidates):
                        ata_candidates.append(candidate)

                    if len(ata_candidates) >= 60:
                        break

                if len(ata_candidates) >= 60:
                    break

        auto_ata_code = find_nearest_reference(
            source_text,
            ata_prefix,
            anchor=maint_msg,
        ) or extract_ata_code(row.mel_item if row else "", source_text)
        ata_candidate_values = candidate_values(ata_candidates)

        if selected_ata_code:
            ata_code = selected_ata_code
        elif len(ata_candidates) == 1:
            ata_code = ata_candidates[0]["value"]
        elif auto_ata_code in ata_candidate_values:
            ata_code = auto_ata_code
        elif not ata_candidates:
            ata_code = auto_ata_code
        else:
            ata_code = ""

        if selected_ata_code and selected_ata_code not in ata_candidate_values:
            ata_candidates.insert(
                0,
                {
                    "value": selected_ata_code,
                    "compact": selected_ata_code.replace(" ", ""),
                    "description": "SELECTED",
                    "label": f"{selected_ata_code.replace(' ', '')} - SELECTED",
                },
            )
        elif selected_ata_code and ata_candidates:
            ata_candidates = sorted(
                ata_candidates,
                key=lambda candidate: candidate["value"] != selected_ata_code,
            )

        fim_ref = find_fim_reference_from_upload(
            aircraft,
            ata_prefix,
            maint_msg=maint_msg,
            source_text=source_text,
        )
        amm_ref = find_amm_reference_from_upload(
            aircraft,
            ata_prefix,
            maint_msg=maint_msg,
            source_text=source_text,
        )
        fim_action_text = find_fim_test_action_text(
            aircraft,
            fim_ref,
            maint_msg=maint_msg,
        )
        fim_action_amm_ref = extract_amm_task_reference(fim_action_text)

        if fim_action_amm_ref:
            amm_ref = fim_action_amm_ref

        action_taken = build_action_taken(
            query,
            maint_msg,
            row=row,
            page=page,
            aircraft=aircraft,
            fim_ref=fim_ref,
            amm_ref=amm_ref,
            fim_action_text=fim_action_text,
            defer_action=defer_action,
            defer_no=defer_no,
        )

        context.update(
            {
                "aircrafts": Aircraft.objects.all().order_by("maker", "name"),
                "selected_aircraft": str(aircraft.pk) if aircraft else aircraft_id,
                "query": query,
                "station": station,
                "work_order": work_order,
                "defer_action": defer_action,
                "defer_no": defer_no,
                "defer_no_display": defer_no_display,
                "date_input": date_value or timezone.localdate().isoformat(),
                "fml_date": format_fml_date(date_value),
                "item_no": self.request.GET.get("item_no", "1").strip() or "1",
                "leg": self.request.GET.get("leg", "").strip(),
                "maint_msg": maint_msg,
                "source_row": row,
                "source_page": page,
                "defect_text": defect_text,
                "ata_code": ata_code,
                "ata_candidates": ata_candidates,
                "fim_ref": fim_ref,
                "amm_ref": amm_ref,
                "fim_action_text": fim_action_text,
                "action_taken": action_taken,
            }
        )

        return context


class DispatchAutoSearchView(LoginRequiredMixin, TemplateView):
    template_name = "dispatch/dispatch_auto_search.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        aircraft_id = self.request.GET.get("aircraft", "").strip()
        query = self.request.GET.get("q", "").strip()
        search_type = self.request.GET.get("search_type", "all").strip().lower()

        if search_type in ["keyword", "all", "전체"]:
            search_type = "all"
        elif search_type in ["message", "msg"]:
            search_type = "message"
        elif search_type in ["fault", "fault_code", "faultcode"]:
            search_type = "fault"

        context["aircrafts"] = Aircraft.objects.all().order_by("maker", "name")
        context["selected_aircraft"] = aircraft_id
        context["query"] = query
        context["search_type"] = search_type
        context["message_mode"] = search_type == "message"

        context["saved_dispatch_results"] = []
        context["mel_dispatch_rows"] = []

        context["amm_results"] = []
        context["fim_results"] = []
        context["ipc_results"] = []
        context["mel_results"] = []
        context["cdl_results"] = []

        context["amm_page_data"] = paginate_result_group(self.request, [], "amm")
        context["fim_page_data"] = paginate_result_group(self.request, [], "fim")
        context["ipc_page_data"] = paginate_result_group(self.request, [], "ipc")
        context["mel_page_data"] = paginate_result_group(self.request, [], "mel")
        context["cdl_page_data"] = paginate_result_group(self.request, [], "cdl")

        if not query:
            return context

        aircraft_qs = Aircraft.objects.all().order_by("maker", "name")

        if aircraft_id:
            aircraft_qs = aircraft_qs.filter(pk=aircraft_id)

        if aircraft_id and not aircraft_qs.exists():
            return context

        search_value, match_mode = parse_search_query(query)

        if not search_value:
            return context

        # 1. MEL Dispatch Table 결과
        if search_type == "fault":
            mel_dispatch_rows = MelDispatchItem.objects.none()

        elif search_type == "message":
            mel_dispatch_rows = exclude_noisy_mel_dispatch_rows(
                MelDispatchItem.objects.filter(aircraft__in=aircraft_qs)
            ).filter(
                build_structured_search_q(
                    ["message", "condition"],
                    search_value,
                    match_mode,
                )
            )
            mel_dispatch_rows = mel_dispatch_rows.order_by(
                "aircraft__name", "message", "page_number"
            )[:100]

        else:
            mel_dispatch_rows = exclude_noisy_mel_dispatch_rows(
                MelDispatchItem.objects.filter(aircraft__in=aircraft_qs)
            ).filter(
                build_structured_search_q(
                    ["message", "condition", "mel_item"],
                    search_value,
                    match_mode,
                )
            )
            mel_dispatch_rows = mel_dispatch_rows.order_by(
                "aircraft__name", "message", "page_number"
            )[:100]

        context["mel_dispatch_rows"] = mel_dispatch_rows

        # 2. 저장된 Dispatch Reference 결과
        saved_refs = DispatchReference.objects.filter(
            aircraft__in=aircraft_qs
        ).select_related("aircraft")

        if search_type == "message":
            saved_refs = saved_refs.filter(
                build_structured_search_q(
                    [
                        "eicas_message",
                        "status_message",
                        "mel_search_keyword",
                        "fim_search_keyword",
                        "amm_search_keyword",
                        "description",
                        "note",
                    ],
                    search_value,
                    match_mode,
                )
            )

        elif search_type == "fault":
            saved_refs = saved_refs.filter(
                build_structured_search_q(
                    ["fault_code"],
                    search_value,
                    match_mode,
                )
            )

        else:
            saved_refs = saved_refs.filter(
                build_structured_search_q(
                    [
                        "eicas_message",
                        "status_message",
                        "fault_code",
                        "mel_number",
                        "mel_search_keyword",
                        "mel_ref",
                        "fim_chapter",
                        "fim_search_keyword",
                        "fim_task_ref",
                        "amm_chapter",
                        "amm_search_keyword",
                        "amm_task_ref",
                        "description",
                        "note",
                    ],
                    search_value,
                    match_mode,
                )
            )

        saved_dispatch_results = list(saved_refs[:20])

        for item in saved_dispatch_results:
            item.mel_file = item.aircraft.get_mel()
            item.amm_page = resolve_saved_package_page(item, "AMM")
            item.fim_page = resolve_saved_package_page(item, "FIM")
            item.mel_page = resolve_saved_file_page(item)

        context["saved_dispatch_results"] = saved_dispatch_results

        # 3. Manual PDF 검색
        search_value, match_mode = parse_manual_search_query(query)

        if not search_value:
            return context

        package_base = ManualPDFPage.objects.select_related(
            "chapter",
            "chapter__package",
            "chapter__package__aircraft",
        ).filter(chapter__package__aircraft__in=aircraft_qs)

        file_base = ManualFilePDFPage.objects.select_related(
            "manual_file",
            "manual_file__aircraft",
        ).filter(manual_file__aircraft__in=aircraft_qs)

        text_regex = build_manual_text_regex(search_value, match_mode)

        if match_mode == "contains":
            package_filter = Q(text__icontains=search_value)
            file_filter = Q(text__icontains=search_value)
        else:
            package_filter = Q(text__iregex=text_regex)
            file_filter = Q(text__iregex=text_regex)

        # AMM
        context["amm_results"] = list(
            package_base.filter(chapter__package__manual_type="AMM")
            .filter(package_filter)
            .order_by("chapter__task", "chapter__subtask", "page_number")
        )

        # FIM
        context["fim_results"] = list(
            package_base.filter(chapter__package__manual_type="FIM")
            .filter(package_filter)
            .order_by("chapter__task", "chapter__subtask", "page_number")
        )

        # IPC
        context["ipc_results"] = list(
            package_base.filter(chapter__package__manual_type="IPC")
            .filter(package_filter)
            .order_by("chapter__task", "chapter__subtask", "page_number")
        )

        # MEL - 점수 계산 후 점수순 정렬
        mel_results = list(
            file_base.filter(manual_file__manual_type="MEL").filter(file_filter)
        )

        for page in mel_results:
            page.recommend_score = score_file_page(page, search_value)
            page.snippet = page.get_snippet(search_value)

        mel_results.sort(
            key=lambda page: (
                page.manual_file_id,
                page.page_number,
            ),
        )

        context["mel_results"] = mel_results

        # CDL
        context["cdl_results"] = list(
            file_base.filter(manual_file__manual_type="CDL")
            .filter(file_filter)
            .order_by("manual_file__description", "page_number")
        )

        # AMM / FIM / IPC 점수 및 snippet
        for result_group in [
            context["amm_results"],
            context["fim_results"],
            context["ipc_results"],
        ]:
            for page in result_group:
                page.recommend_score = score_package_page(page, search_value)
                page.snippet = page.get_snippet(search_value)

        # CDL 점수 및 snippet
        for page in context["cdl_results"]:
            page.recommend_score = score_file_page(page, search_value)
            page.snippet = page.get_snippet(search_value)

        context["amm_page_data"] = paginate_result_group(
            self.request, context["amm_results"], "amm"
        )
        context["fim_page_data"] = paginate_result_group(
            self.request, context["fim_results"], "fim"
        )
        context["ipc_page_data"] = paginate_result_group(
            self.request, context["ipc_results"], "ipc"
        )
        context["mel_page_data"] = paginate_result_group(
            self.request, context["mel_results"], "mel"
        )
        context["cdl_page_data"] = paginate_result_group(
            self.request, context["cdl_results"], "cdl"
        )

        return context
