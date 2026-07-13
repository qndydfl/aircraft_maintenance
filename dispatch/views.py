from django.db.models import Q
from types import SimpleNamespace

from .models import DispatchReference, MelDispatchItem
from manuals.models import Aircraft, ManualPDFPage, ManualFilePDFPage

import csv, re, io
from django.views.generic import (
    ListView,
    FormView,
    View,
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
)
from django.http import JsonResponse, HttpResponse
from .services import (
    build_structured_search_q,
    build_mel_dispatch_search_q,
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


class DispatchCSVUploadView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    template_name = "dispatch/dispatch_csv_upload.html"
    form_class = DispatchCSVUploadForm
    success_url = reverse_lazy("dispatch_search")

    def test_func(self):
        return self.request.user.is_superuser

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
    success_url = reverse_lazy("dispatch_search")


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


class DispatchSearchView(LoginRequiredMixin, TemplateView):
    template_name = "dispatch/dispatch_search.html"

    def build_manual_pdf_query(self, query):
        query = (query or "").strip()

        if re.fullmatch(r"\d{2}-\d{3,5}", query):
            return f"maintenance messages: {query}"

        return query

    def build_mel_dispatch_viewer_query(self, row, fallback_query):
        if getattr(row, "is_none_dispatch", False):
            return (getattr(row, "message", "") or fallback_query).strip()

        mel_item = (getattr(row, "mel_item", "") or "").strip()
        message = (getattr(row, "message", "") or "").strip()

        if mel_item and mel_item.upper() not in {"N/A", "NA", "-"}:
            return mel_item

        if message:
            return message

        return fallback_query

    def find_mel_dispatch_viewer_page(self, row):
        if getattr(row, "is_none_dispatch", False):
            return getattr(row, "page_number", None)

        manual_file = getattr(row, "manual_file", None)
        mel_item = (getattr(row, "mel_item", "") or "").strip()

        if not manual_file or not mel_item:
            return getattr(row, "page_number", None)

        item_pattern = re.compile(
            rf"(?<![0-9A-Za-z/_-]){re.escape(mel_item)}(?![0-9A-Za-z/_-])",
            re.IGNORECASE,
        )
        candidates = []

        pages = (
            ManualFilePDFPage.objects.filter(
                manual_file=manual_file,
                text__icontains=mel_item,
            )
            .only("page_number", "text")
            .order_by("page_number")
        )

        for page in pages:
            match = item_pattern.search(page.text or "")

            if match:
                candidates.append(
                    {
                        "page_number": page.page_number,
                        "position": match.start(),
                        "score": self.score_mel_item_page(
                            page.text,
                            match.start(),
                        ),
                    }
                )

        if candidates:
            candidates.sort(
                key=lambda item: (
                    -item["score"],
                    item["position"],
                    item["page_number"],
                )
            )
            return candidates[0]["page_number"]

        return getattr(row, "page_number", None)

    def score_mel_item_page(self, text, match_position):
        text = text or ""
        lower_text = text.lower()
        before_text = lower_text[max(match_position - 500, 0) : match_position]
        after_text = lower_text[match_position : match_position + 900]
        nearby_text = lower_text[max(match_position - 120, 0) : match_position + 220]

        score = 0

        if re.search(r"\binterval\s+installed\s+required\b", after_text):
            score += 120

        if re.search(r"\brepair\s+interval\b", after_text):
            score += 80

        if re.search(r"\bitem\s*$", before_text[-80:]):
            score += 20

        if "mel item - index" in lower_text[:500]:
            score -= 120

        if "eicas message - index" in lower_text[:500]:
            score -= 120

        if "list of effective" in lower_text[:500]:
            score -= 120

        if re.search(r"\buse\s+mel\s+item\b", nearby_text):
            score -= 90

        if re.search(r"\brefer\s+to\s+(?:mel\s+)?item\b", nearby_text):
            score -= 60

        return score

    def build_sibling_message_regex(self, query):
        words = [
            word.upper()
            for word in re.findall(r"[0-9A-Za-z]+", query or "")
            if word.strip()
        ]

        if len(words) < 2:
            return None

        last_word = words[-1]

        if not re.fullmatch(r"(?:[A-Z]+\d+|[A-Z])", last_word):
            return None

        stem_words = words[:-1]
        if re.fullmatch(r"[A-Z]+\d+", last_word):
            suffix_pattern = r"[A-Z]\d+"
        else:
            suffix_pattern = r"[A-Z]"
        token_chars = r"0-9A-Za-z가-힣"
        token_patterns = [
            rf"(?<![{token_chars}]){re.escape(word)}(?![{token_chars}])"
            for word in stem_words
        ]
        token_patterns.append(
            rf"(?<![{token_chars}]){suffix_pattern}(?![{token_chars}])"
        )

        return re.compile(
            r"\s+".join(token_patterns),
            re.IGNORECASE,
        )

    def find_sibling_mel_rows(self, aircraft_qs, query):
        sibling_regex = self.build_sibling_message_regex(query)

        if not sibling_regex:
            return []

        candidates = exclude_noisy_mel_dispatch_rows(
            MelDispatchItem.objects.filter(aircraft__in=aircraft_qs)
        )
        matched_rows = []

        for row in candidates.only(
            "id",
            "aircraft_id",
            "manual_file_id",
            "message",
            "level",
            "condition",
            "mel_item",
            "adc",
            "page_number",
        ):
            if sibling_regex.search(row.message or ""):
                row.display_message = query.upper()
                matched_rows.append(row)

        return matched_rows

    def build_message_block_regex(self, query):
        words = [
            word.upper()
            for word in re.findall(r"[0-9A-Za-z]+", query or "")
            if word.strip()
        ]

        if not words:
            return None, None

        token_chars = r"0-9A-Za-z가-힣"
        token_patterns = [
            rf"(?<![{token_chars}]){re.escape(word)}(?![{token_chars}])"
            for word in words
        ]

        return (
            re.compile(r"[\s:：]+".join(token_patterns), re.IGNORECASE),
            re.compile(
                rf"^\s*(?<![{token_chars}]){re.escape(words[0])}(?![{token_chars}])",
                re.IGNORECASE,
            ),
        )

    def find_message_block_mel_rows(self, aircraft_qs, query):
        block_regex, starts_with_first_word = self.build_message_block_regex(query)

        if not block_regex or not starts_with_first_word:
            return []

        candidates = exclude_noisy_mel_dispatch_rows(
            MelDispatchItem.objects.filter(aircraft__in=aircraft_qs)
        )
        matched_rows = []

        for row in candidates.only(
            "id",
            "aircraft_id",
            "manual_file_id",
            "message",
            "level",
            "condition",
            "mel_item",
            "adc",
            "page_number",
        ):
            message = row.message or ""

            if starts_with_first_word.search(message) and block_regex.search(message):
                row.display_message = query.upper()
                matched_rows.append(row)

        return matched_rows

    def build_none_dispatch_regex(self, query):
        words = [
            word.upper()
            for word in re.findall(r"[0-9A-Za-z]+", query or "")
            if word.strip()
        ]

        if not words:
            return None

        token_chars = r"0-9A-Za-z가-힣"
        token_patterns = [
            rf"(?<![{token_chars}]){re.escape(word)}(?![{token_chars}])"
            for word in words
        ]
        message_pattern = r"[\s:：]+".join(token_patterns)

        return re.compile(
            rf"{message_pattern}\s+(?P<level>[A-Z])\s+None\s*\(\s*No\s+Dispatch\s*\)",
            re.IGNORECASE,
        )

    def find_none_dispatch_rows(self, aircraft_qs, query):
        none_dispatch_regex = self.build_none_dispatch_regex(query)

        if not none_dispatch_regex:
            return []

        pages = (
            ManualFilePDFPage.objects.filter(
                manual_file__aircraft__in=aircraft_qs,
                manual_file__manual_type="MEL",
                text__icontains="None",
            )
            .select_related("manual_file", "manual_file__aircraft")
            .only("page_number", "text", "manual_file", "manual_file__aircraft")
            .order_by("manual_file_id", "page_number")
        )
        matched_rows = []

        for page in pages:
            match = none_dispatch_regex.search(page.text or "")

            if not match:
                continue

            matched_rows.append(
                SimpleNamespace(
                    pk=f"none-dispatch:{page.manual_file_id}:{page.page_number}:{query}",
                    aircraft=page.manual_file.aircraft,
                    aircraft_id=page.manual_file.aircraft_id,
                    manual_file=page.manual_file,
                    manual_file_id=page.manual_file_id,
                    message=query.upper(),
                    display_message=query.upper(),
                    level=match.group("level"),
                    condition="None(No Dispatch)",
                    mel_item="None(No Dispatch)",
                    adc="",
                    page_number=page.page_number,
                    is_none_dispatch=True,
                )
            )

        return matched_rows

    def apply_display_message(self, row, query):
        if getattr(row, "display_message", None):
            return

        message = row.message or ""
        block_regex, starts_with_first_word = self.build_message_block_regex(query)

        if not message or not block_regex or not starts_with_first_word:
            return

        normalized_message = " ".join(message.upper().split())
        normalized_query = " ".join((query or "").upper().split())

        if (
            normalized_query
            and normalized_message != normalized_query
            and starts_with_first_word.search(message)
            and block_regex.search(message)
        ):
            row.display_message = normalized_query

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
        context["manual_pdf_query"] = self.build_manual_pdf_query(query)
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
                build_mel_dispatch_search_q(
                    ["message", "condition"],
                    search_value,
                    match_mode,
                )
            )
            mel_dispatch_rows = mel_dispatch_rows.order_by(
                "aircraft__name", "message", "page_number", "mel_item"
            )[:100]

        else:
            mel_dispatch_rows = exclude_noisy_mel_dispatch_rows(
                MelDispatchItem.objects.filter(aircraft__in=aircraft_qs)
            ).filter(
                build_mel_dispatch_search_q(
                    ["message", "condition", "mel_item"],
                    search_value,
                    match_mode,
                )
            )
            mel_dispatch_rows = mel_dispatch_rows.order_by(
                "aircraft__name", "message", "page_number", "mel_item"
            )[:100]

        mel_dispatch_rows = list(mel_dispatch_rows)

        rows_by_id = {row.pk: row for row in mel_dispatch_rows}

        if not rows_by_id:
            for row in self.find_message_block_mel_rows(aircraft_qs, query):
                rows_by_id.setdefault(row.pk, row)

        if not rows_by_id:
            for row in self.find_none_dispatch_rows(aircraft_qs, query):
                rows_by_id.setdefault(row.pk, row)

        if not rows_by_id:
            for row in self.find_sibling_mel_rows(aircraft_qs, query):
                rows_by_id.setdefault(row.pk, row)

        mel_dispatch_rows = sorted(
            rows_by_id.values(),
            key=lambda row: (
                row.aircraft.name if row.aircraft_id else "",
                row.page_number or 0,
                row.mel_item or "",
                row.message or "",
            ),
        )

        for row in mel_dispatch_rows:
            self.apply_display_message(row, query)
            row.viewer_query = self.build_mel_dispatch_viewer_query(row, query)
            row.viewer_page_number = self.find_mel_dispatch_viewer_page(row)

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
        manual_pdf_query = context["manual_pdf_query"]
        search_value, match_mode = parse_manual_search_query(manual_pdf_query)

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
