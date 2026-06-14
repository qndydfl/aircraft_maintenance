const config = window.PDF_VIEWER_CONFIG || {};

const matchingPages = Array.isArray(config.matchingPages)
    ? config.matchingPages
    : [];

const viewMode = config.viewMode || "single";

const pdfUrl = config.pdfUrl;
const initialPage = config.initialPage || 1;
const searchQuery = config.searchQuery || "";
const viewerType = config.viewerType || "file";

pdfjsLib.GlobalWorkerOptions.workerSrc = config.workerSrc;

let pdfDoc = null;
let currentPage = initialPage || 1;
let pageRendering = false;
let pagePending = null;

const canvas = document.getElementById("pdf-canvas");
const ctx = canvas.getContext("2d");
const pageNumberInput = document.getElementById("page-number");
const pageCountSpan = document.getElementById("page-count");
const textLayer = document.getElementById("text-layer");
const outlineContainer = document.getElementById("outline-container");
const pdfContainer = document.querySelector(".pdf-viewer-scroll");
const zoomLevelSpan = document.getElementById("zoom-level");
const zoomInBtn = document.getElementById("zoom-in");
const zoomOutBtn = document.getElementById("zoom-out");
const fitWidthBtn = document.getElementById("fit-width");
const fitPageBtn = document.getElementById("fit-page");
const outlinePanel = document.getElementById("outline-panel");
const toggleOutlineBtn = document.getElementById("toggle-outline");
const manualPdfLayout = document.getElementById("manual-pdf-layout");
const outlineResizer = document.getElementById("outline-resizer");
const outlineStorageKey = "manualPdfOutlineHiddenV3";
const singlePageViewer = document.getElementById("single-page-viewer");
const multiPageViewer = document.getElementById("multi-page-viewer");
const viewerSearchInput = document.getElementById("viewer-search-input");
const viewerSearchBtn = document.getElementById("viewer-search-btn");
const viewerSearchPrevBtn = document.getElementById("viewer-search-prev");
const viewerSearchNextBtn = document.getElementById("viewer-search-next");
const viewerSearchCount = document.getElementById("viewer-search-count");

let viewerSearchQuery = searchQuery || "";
let viewerSearchMatches = [];
let viewerSearchIndex = -1;

let scale = 1.0;

function isMobileViewport() {
    return window.innerWidth < 768;
}

// 모바일은 기본적으로 가로폭 맞춤으로 시작
let scaleMode = isMobileViewport() ? "width" : "page";

function parseViewerSearchQuery(query) {
    if (!query) {
        return {
            value: "",
            mode: "exact",
            words: [],
            regex: null,
        };
    }

    const trimmed = query.trim().toLowerCase();

    if (trimmed.includes("*")) {
        const words = trimmed
            .split("*")
            .map(function (word) {
                return word.trim();
            })
            .filter(Boolean);

        if (!words.length) {
            return {
                value: "",
                mode: "exact",
                words: [],
                regex: null,
            };
        }

        const regexText = words
            .map(function (word) {
                return escapeRegExp(word);
            })
            .join(".*");

        return {
            value: trimmed,
            mode: "wildcard",
            words: words,
            regex: new RegExp(regexText, "i"),
        };
    }

    return {
        value: trimmed,
        mode: "exact",
        words: [trimmed],
        regex: null,
    };
}

let parsedSearch = parseViewerSearchQuery(searchQuery);

function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function clearTextLayer() {
    textLayer.innerHTML = "";
}

function normalizeText(value) {
    return String(value || "")
        .toLowerCase()
        .replace(/\s+/g, " ")
        .trim();
}

function isMatchedText(text) {
    if (!parsedSearch.value) {
        return false;
    }

    const normalizedText = normalizeText(text);

    if (parsedSearch.mode === "wildcard") {
        return parsedSearch.words.some(function (word) {
            return normalizedText.includes(word);
        });
    }

    const escaped = escapeRegExp(parsedSearch.value);
    const regex = new RegExp("\\b" + escaped + "\\b", "i");

    return regex.test(normalizedText);
}

function renderTextLayer(page, viewport) {
    clearTextLayer();

    textLayer.style.width = viewport.width + "px";
    textLayer.style.height = viewport.height + "px";

    page.getTextContent().then(function (textContent) {
        textContent.items.forEach(function (item) {
            const span = document.createElement("span");
            const text = item.str || "";

            span.textContent = text;

            const transform = pdfjsLib.Util.transform(
                viewport.transform,
                item.transform,
            );

            const x = transform[4];
            const y = transform[5];

            span.style.left = x + "px";
            span.style.top = y + "px";
            span.style.fontSize = Math.abs(transform[0]) + "px";

            span.style.transform =
                "scaleX(" + transform[0] / Math.abs(transform[0] || 1) + ")";

            if (isMatchedText(text)) {
                span.classList.add("highlight-text");
            }

            textLayer.appendChild(span);
        });
    });
}

// [수정] 모바일 해상도를 고려하여 안전 여백 및 모드 자동 전환 로직 보완
function getFitScale(page) {
    const viewport = page.getViewport({ scale: 1 });

    const padding = window.innerWidth < 768 ? 4 : 16;
    const containerWidth = Math.max(
        Math.min(pdfContainer.clientWidth, window.innerWidth) - padding,
        1,
    );
    const containerHeight = Math.max(pdfContainer.clientHeight - padding, 1);

    // 모바일이거나 가로 맞춤 모드일 때는 화면 너비 기준으로 스케일 계산
    if (scaleMode === "width") {
        return containerWidth / viewport.width;
    }

    if (scaleMode === "page") {
        return Math.min(
            containerWidth / viewport.width,
            containerHeight / viewport.height,
        );
    }

    return scale;
}

function updateZoomLabel(value) {
    zoomLevelSpan.textContent = Math.round(value * 100) + "%";
}

function renderPage(pageNumber) {
    pageRendering = true;
    clearTextLayer();

    pdfDoc.getPage(pageNumber).then(function (page) {
        const pageScale = getFitScale(page);
        const viewport = page.getViewport({ scale: pageScale });

        canvas.height = viewport.height;
        canvas.width = viewport.width;

        const renderContext = {
            canvasContext: ctx,
            viewport: viewport,
        };

        const renderTask = page.render(renderContext);

        renderTask.promise.then(function () {
            renderTextLayer(page, viewport);
            updateZoomLabel(pageScale);

            pageRendering = false;

            if (pagePending !== null) {
                renderPage(pagePending);
                pagePending = null;
            }
        });
    });

    pageNumberInput.value = pageNumber;
}

function queueRenderPage(pageNumber) {
    if (pageRendering) {
        pagePending = pageNumber;
    } else {
        renderPage(pageNumber);
    }
}

function goToPage(pageNumber) {
    if (!pdfDoc) {
        return;
    }

    if (pageNumber < 1 || pageNumber > pdfDoc.numPages) {
        return;
    }

    currentPage = pageNumber;
    queueRenderPage(currentPage);
}

function resolveDestination(dest) {
    if (!dest) {
        return Promise.resolve(null);
    }

    if (typeof dest === "string") {
        return pdfDoc.getDestination(dest);
    }

    return Promise.resolve(dest);
}

function goToDestination(dest) {
    if (!dest || !dest.length) {
        return;
    }

    const pageRef = dest[0];

    if (typeof pageRef === "number") {
        goToPage(pageRef + 1);
        return;
    }

    pdfDoc.getPageIndex(pageRef).then(function (pageIndex) {
        goToPage(pageIndex + 1);
    });
}

function createOutlineTree(items, depth = 0) {
    const ul = document.createElement("ul");
    ul.className = "outline-list depth-" + depth;

    items.forEach(function (item) {
        const li = document.createElement("li");
        li.className = "outline-item";

        const row = document.createElement("div");
        row.className =
            "outline-row depth-row-" +
            depth +
            " d-flex align-items-center gap-2";

        const hasChildren = item.items && item.items.length > 0;
        let childUl = null;

        if (hasChildren) {
            const toggleBtn = document.createElement("button");
            toggleBtn.type = "button";
            toggleBtn.className = "outline-toggle-btn";

            // ➕ [초기 상태] 하위 항목이 접혀 있으므로 [+] 기호 배치
            toggleBtn.innerHTML = '<span class="toggle-icon">[+]</span>';

            row.appendChild(toggleBtn);

            // 토글 버튼 클릭 시 서브 태스크 열고 닫기 이벤트
            toggleBtn.addEventListener("click", function (event) {
                event.preventDefault();
                event.stopPropagation();
                toggleFolder();
            });

            function toggleFolder() {
                const isCollapsed = childUl.classList.contains(
                    "outline-children-collapsed",
                );
                const iconSpan = toggleBtn.querySelector(".toggle-icon");

                if (isCollapsed) {
                    // ➖ [열림 상태] 서브 태스크가 펼쳐질 때
                    childUl.classList.remove("outline-children-collapsed");
                    toggleBtn.classList.add("is-open");
                    iconSpan.innerHTML = "[-]";
                } else {
                    // ➕ [닫힘 상태] 서브 태스크를 숨길 때
                    childUl.classList.add("outline-children-collapsed");
                    toggleBtn.classList.remove("is-open");
                    iconSpan.innerHTML = "[+]";
                }
            }
        } else {
            // 하위 Subtask가 없는 최종 단락 노드 (문서 표시용 점선 공백 또는 단순 기호)
            const docIcon = document.createElement("span");
            docIcon.className = "outline-doc-icon";
            docIcon.innerHTML = "▱"; // 기존 포맷 유지
            row.appendChild(docIcon);
        }

        const link = document.createElement("a");
        link.className = "outline-tree-link";
        link.href = "javascript:void(0)";
        link.innerHTML = `<span class="tree-title">${item.title || "Untitled"}</span>`;

        link.addEventListener("click", function (event) {
            event.preventDefault();

            document
                .querySelectorAll(".outline-row")
                .forEach((r) => r.classList.remove("is-active"));
            row.classList.add("is-active");

            resolveDestination(item.dest).then(function (dest) {
                goToDestination(dest);
            });
        });

        row.appendChild(link);
        li.appendChild(row);

        if (hasChildren) {
            childUl = createOutlineTree(item.items, depth + 1);
            childUl.classList.add("outline-children-collapsed"); // 기본 접힘 상태 유지
            li.appendChild(childUl);
        }

        ul.appendChild(li);
    });

    return ul;
}

function outlineHasChildren(items) {
    return items.some(function (item) {
        return item.items && item.items.length > 0;
    });
}

function extractAtaParts(title) {
    if (!title) {
        return null;
    }

    const cleaned = title.replace(/^\s*(ATA\s*)?/i, "").trim();
    const match = cleaned.match(
        /^(\d{2})(?:[-\.\s]*(\d{2}))?(?:[-\.\s]*(\d{2}))?\b/,
    );

    if (!match) {
        return null;
    }

    const ata = match[1];
    const subAta = match[2] || "";
    const subject = cleaned
        .slice(match[0].length)
        .replace(/^[-: ]+/, "")
        .trim();

    return {
        ata: ata,
        subAta: subAta,
        subject: subject,
    };
}

function buildAtaOutline(items) {
    if (!items || items.length === 0) {
        return items;
    }

    if (outlineHasChildren(items)) {
        return items;
    }

    const groupMap = new Map();
    const unknownGroup = { title: "Other", items: [] };

    function ensureAtaGroup(ata) {
        if (!groupMap.has(ata)) {
            groupMap.set(ata, { title: "ATA " + ata, items: [] });
        }

        return groupMap.get(ata);
    }

    function ensureSubGroup(parent, ata, subAta) {
        const key = ata + "-" + subAta;
        let existing = parent.items.find(function (item) {
            return item.__key === key;
        });

        if (!existing) {
            existing = {
                title: "ATA " + key,
                items: [],
                __key: key,
            };
            parent.items.push(existing);
        }

        return existing;
    }

    items.forEach(function (item) {
        const title = item.title || "";
        const parts = extractAtaParts(title);

        if (!parts) {
            unknownGroup.items.push(item);
            return;
        }

        const ataGroup = ensureAtaGroup(parts.ata);
        const subjectTitle = parts.subject || title;

        if (parts.subAta) {
            const subGroup = ensureSubGroup(ataGroup, parts.ata, parts.subAta);
            subGroup.items.push({
                title: subjectTitle,
                dest: item.dest,
                items: item.items || [],
            });
        } else {
            ataGroup.items.push({
                title: subjectTitle,
                dest: item.dest,
                items: item.items || [],
            });
        }
    });

    const ordered = Array.from(groupMap.entries())
        .sort(function (a, b) {
            return parseInt(a[0], 10) - parseInt(b[0], 10);
        })
        .map(function (entry) {
            return entry[1];
        });

    if (unknownGroup.items.length > 0) {
        ordered.push(unknownGroup);
    }

    return ordered;
}

function loadOutline() {
    outlineContainer.innerHTML =
        '<div class="outline-empty">Loading contents...</div>';

    if (!pdfDoc) {
        outlineContainer.innerHTML =
            '<div class="outline-empty">PDF가 아직 로드되지 않았습니다.</div>';
        return;
    }

    pdfDoc
        .getOutline()
        .then(function (outline) {
            outlineContainer.innerHTML = "";

            if (!outline || outline.length === 0) {
                outlineContainer.innerHTML =
                    '<div class="outline-empty">이 PDF에는 Bookmark / Contents 정보가 없습니다.</div>';
                return;
            }

            const outlineItems = buildAtaOutline(outline);
            outlineContainer.appendChild(createOutlineTree(outlineItems));
        })
        .catch(function (error) {
            console.error("PDF Outline Load Error:", error);

            outlineContainer.innerHTML =
                '<div class="outline-empty">Bookmark 정보를 불러오지 못했습니다.</div>';
        });
}

document.getElementById("prev-page").addEventListener("click", function () {
    goToPage(currentPage - 1);
});

document.getElementById("next-page").addEventListener("click", function () {
    goToPage(currentPage + 1);
});

pageNumberInput.addEventListener("change", function () {
    goToPage(parseInt(this.value, 10));
});

zoomInBtn.addEventListener("click", function () {
    scaleMode = "custom";
    scale = Math.min(scale + 0.1, 4);
    queueRenderPage(currentPage);
});

zoomOutBtn.addEventListener("click", function () {
    scaleMode = "custom";
    scale = Math.max(scale - 0.1, 0.5);
    queueRenderPage(currentPage);
});

fitWidthBtn.addEventListener("click", function () {
    scaleMode = "width";
    queueRenderPage(currentPage);
});

fitPageBtn.addEventListener("click", function () {
    scaleMode = "page";
    queueRenderPage(currentPage);
});

window.addEventListener("resize", function () {
    if (isMobileViewport() && scaleMode === "page") {
        scaleMode = "width";
    }

    if (scaleMode !== "custom") {
        queueRenderPage(currentPage);
    }
});

// ── Outline panel resizer ───────────────────────────────────────────────────
const OUTLINE_WIDTH_KEY = "manualPdfOutlineWidthV1";

(function initOutlineResizer() {
    if (!outlineResizer || !outlinePanel || isMobileViewport()) {
        return;
    }

    // Restore saved width
    const savedWidth = localStorage.getItem(OUTLINE_WIDTH_KEY);
    if (savedWidth) {
        outlinePanel.style.width = savedWidth + "px";
    }

    let dragging = false;
    let startX = 0;
    let startWidth = 0;

    outlineResizer.addEventListener("mousedown", function (event) {
        event.preventDefault();
        dragging = true;
        startX = event.clientX;
        startWidth = outlinePanel.getBoundingClientRect().width;
        outlineResizer.classList.add("is-dragging");
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
    });

    document.addEventListener("mousemove", function (event) {
        if (!dragging) return;
        const delta = event.clientX - startX;
        const newWidth = Math.min(
            Math.max(startWidth + delta, 160),
            window.innerWidth * 0.6,
        );
        outlinePanel.style.width = newWidth + "px";
    });

    document.addEventListener("mouseup", function () {
        if (!dragging) return;
        dragging = false;
        outlineResizer.classList.remove("is-dragging");
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        localStorage.setItem(
            OUTLINE_WIDTH_KEY,
            Math.round(outlinePanel.getBoundingClientRect().width),
        );
        // Re-render so PDF fits the new container width
        if (scaleMode !== "custom") {
            queueRenderPage(currentPage);
        }
    });
})();
// ────────────────────────────────────────────────────────────────────────────

function applyOutlineState() {
    const storedValue = sessionStorage.getItem(outlineStorageKey);

    let isHidden;

    if (storedValue === null) {
        isHidden = window.innerWidth < 768;
        sessionStorage.setItem(outlineStorageKey, isHidden ? "true" : "false");
    } else {
        isHidden = storedValue === "true";
    }

    if (isHidden) {
        manualPdfLayout.classList.add("outline-hidden");
    } else {
        manualPdfLayout.classList.remove("outline-hidden");
    }
}

applyOutlineState();

toggleOutlineBtn.addEventListener("click", function () {
    manualPdfLayout.classList.toggle("outline-hidden");

    const isHidden = manualPdfLayout.classList.contains("outline-hidden");

    sessionStorage.setItem(outlineStorageKey, isHidden ? "true" : "false");
});

if (!pdfUrl) {
    outlineContainer.innerHTML =
        '<div class="outline-empty">PDF를 불러올 수 없습니다.</div>';
} else {
    pdfjsLib
        .getDocument(pdfUrl)
        .promise.then(function (pdfDoc_) {
            pdfDoc = pdfDoc_;
            pageCountSpan.textContent = pdfDoc.numPages;

            if (currentPage > pdfDoc.numPages) {
                currentPage = 1;
            }

            if (viewMode === "matches" && matchingPages.length > 0) {
                renderAllMatches();
            } else {
                singlePageViewer.classList.remove("d-none");
                multiPageViewer.classList.add("d-none");

                // 초기 렌더는 기기 폭 기준으로 자동 선택
                scaleMode = isMobileViewport() ? "width" : "page";
                renderPage(currentPage);
            }

            loadOutline();
        })
        .catch(function (error) {
            console.error("PDF Load Error:", error);
            outlineContainer.innerHTML =
                '<div class="outline-empty">PDF를 불러오지 못했습니다.</div>';
        });
}

function createTextLayerForPage(page, viewport, wrapper) {
    const layer = document.createElement("div");
    layer.className = "text-layer dynamic-text-layer";

    layer.style.position = "absolute";
    layer.style.left = "0";
    layer.style.top = "0";
    layer.style.width = viewport.width + "px";
    layer.style.height = viewport.height + "px";
    layer.style.zIndex = "2";
    layer.style.opacity = "0.25";
    layer.style.lineHeight = "1";

    wrapper.appendChild(layer);

    page.getTextContent().then(function (textContent) {
        textContent.items.forEach(function (item) {
            const span = document.createElement("span");
            const text = item.str || "";

            span.textContent = text;

            const transform = pdfjsLib.Util.transform(
                viewport.transform,
                item.transform,
            );

            span.style.position = "absolute";
            span.style.whiteSpace = "pre";
            span.style.transformOrigin = "0% 0%";
            span.style.color = "transparent";
            span.style.left = transform[4] + "px";
            span.style.top = transform[5] + "px";
            span.style.fontSize = Math.abs(transform[0]) + "px";
            span.style.transform =
                "scaleX(" + transform[0] / Math.abs(transform[0] || 1) + ")";

            if (isMatchedText(text)) {
                span.classList.add("highlight-text");
            }

            layer.appendChild(span);
        });
    });
}

function renderMatchPage(pageNumber) {
    return pdfDoc.getPage(pageNumber).then(function (page) {
        const pageScale = getFitScale(page);
        const viewport = page.getViewport({ scale: pageScale });

        const pageBox = document.createElement("div");
        pageBox.className = "match-page-box mb-4";

        const label = document.createElement("div");
        label.className = "match-page-label text-start fw-bold mb-2";
        label.textContent = "Page " + pageNumber;

        const wrapper = document.createElement("div");
        wrapper.className = "pdf-viewer-wrap";
        wrapper.style.position = "relative";
        wrapper.style.display = "inline-block";

        const pageCanvas = document.createElement("canvas");
        pageCanvas.height = viewport.height;
        pageCanvas.width = viewport.width;

        wrapper.appendChild(pageCanvas);

        pageBox.appendChild(label);
        pageBox.appendChild(wrapper);
        multiPageViewer.appendChild(pageBox);

        return page
            .render({
                canvasContext: pageCanvas.getContext("2d"),
                viewport: viewport,
            })
            .promise.then(function () {
                createTextLayerForPage(page, viewport, wrapper);
            });
    });
}

function renderAllMatches() {
    singlePageViewer.classList.add("d-none");
    multiPageViewer.classList.remove("d-none");
    multiPageViewer.innerHTML = "";

    if (!matchingPages.length) {
        multiPageViewer.innerHTML =
            '<div class="alert alert-secondary">검색 결과 페이지가 없습니다.</div>';
        return;
    }

    matchingPages.forEach(function (pageNumber) {
        renderMatchPage(pageNumber);
    });
}

async function searchPdfPages(query) {
    const parsed = parseViewerSearchQuery(query);

    if (!parsed.value || !pdfDoc) {
        return [];
    }

    const results = [];

    for (let pageNumber = 1; pageNumber <= pdfDoc.numPages; pageNumber++) {
        const page = await pdfDoc.getPage(pageNumber);
        const textContent = await page.getTextContent();

        const pageText = textContent.items
            .map(function (item) {
                return item.str || "";
            })
            .join(" ");

        if (isTextMatchedByParsedSearch(pageText, parsed)) {
            results.push(pageNumber);
        }
    }

    return results;
}

function isTextMatchedByParsedSearch(text, parsed) {
    if (!parsed.value) {
        return false;
    }

    const normalizedText = normalizeText(text);

    if (parsed.mode === "wildcard") {
        if (parsed.regex) {
            return parsed.regex.test(normalizedText);
        }

        return parsed.words.every(function (word) {
            return normalizedText.includes(word);
        });
    }

    const escaped = escapeRegExp(parsed.value);
    const regex = new RegExp("\\b" + escaped + "\\b", "i");

    return regex.test(normalizedText);
}

function updateViewerSearchCount() {
    if (!viewerSearchMatches.length) {
        viewerSearchCount.textContent = "0 / 0";
        return;
    }

    viewerSearchCount.textContent =
        viewerSearchIndex + 1 + " / " + viewerSearchMatches.length;
}

async function runViewerSearch() {
    const query = viewerSearchInput.value.trim();

    viewerSearchQuery = query;
    parsedSearch = parseViewerSearchQuery(query);

    viewerSearchCount.textContent = "Searching...";

    viewerSearchMatches = await searchPdfPages(query);
    viewerSearchIndex = 0;

    if (!viewerSearchMatches.length) {
        updateViewerSearchCount();
        return;
    }

    updateViewerSearchCount();
    goToPage(viewerSearchMatches[viewerSearchIndex]);
}

function goToSearchMatch(index) {
    if (!viewerSearchMatches.length) {
        return;
    }

    if (index < 0) {
        index = viewerSearchMatches.length - 1;
    }

    if (index >= viewerSearchMatches.length) {
        index = 0;
    }

    viewerSearchIndex = index;
    updateViewerSearchCount();
    goToPage(viewerSearchMatches[viewerSearchIndex]);
}

if (viewerSearchBtn) {
    viewerSearchBtn.addEventListener("click", function () {
        runViewerSearch();
    });
}

if (viewerSearchInput) {
    viewerSearchInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            runViewerSearch();
        }
    });
}

if (viewerSearchPrevBtn) {
    viewerSearchPrevBtn.addEventListener("click", function () {
        goToSearchMatch(viewerSearchIndex - 1);
    });
}

if (viewerSearchNextBtn) {
    viewerSearchNextBtn.addEventListener("click", function () {
        goToSearchMatch(viewerSearchIndex + 1);
    });
}

let wheelLock = false;

pdfContainer.addEventListener(
    "wheel",
    function (event) {
        if (!pdfDoc) {
            return;
        }

        // [수정] 모바일 화면 크기일 때는 휠 가로채기를 차단하고 브라우저 기본 터치 스크롤을 허용합니다.
        if (window.innerWidth < 768) {
            return;
        }

        // Ctrl + Wheel = PDF만 확대/축소
        if (event.ctrlKey) {
            event.preventDefault();
            event.stopPropagation();

            scaleMode = "custom";

            if (event.deltaY < 0) {
                scale = Math.min(scale + 0.1, 4);
            } else {
                scale = Math.max(scale - 0.1, 0.5);
            }

            queueRenderPage(currentPage);
            return;
        }

        // 일반 Wheel = 이전/다음 페이지
        if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
            return;
        }

        event.preventDefault();
        event.stopPropagation();

        if (wheelLock) {
            return;
        }

        wheelLock = true;

        if (event.deltaY > 0) {
            goToPage(currentPage + 1);
        } else if (event.deltaY < 0) {
            goToPage(currentPage - 1);
        }

        setTimeout(function () {
            wheelLock = false;
        }, 350);
    },
    {
        passive: false,
    },
);
