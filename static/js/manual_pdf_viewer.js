const config = window.PDF_VIEWER_CONFIG || {};

const matchingPages = Array.isArray(config.matchingPages)
    ? config.matchingPages
    : [];
const matchItems = Array.isArray(config.matchItems)
    ? config.matchItems
    : [];

const viewMode = config.viewMode || "single";

const pdfUrl = config.pdfUrl;
const initialPage = config.initialPage || 1;
const searchQuery = config.searchQuery || "";
const viewerType = config.viewerType || "file";
const initialServerMatchIndex = Number.isFinite(config.currentMatchIndex)
    ? config.currentMatchIndex
    : parseInt(config.currentMatchIndex || "0", 10);
const serverMatchTotal = Number.isFinite(config.matchCount)
    ? config.matchCount
    : parseInt(config.matchCount || matchingPages.length || "0", 10);

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
const singlePageViewer = document.getElementById("single-page-viewer");
const multiPageViewer = document.getElementById("multi-page-viewer");
const viewerLoading = document.getElementById("viewer-loading");
const viewerSearchInput = document.getElementById("viewer-search-input");
const viewerSearchBtn = document.getElementById("viewer-search-btn");
const viewerSearchPrevBtn = document.getElementById("viewer-search-prev");
const viewerSearchNextBtn = document.getElementById("viewer-search-next");
const viewerSearchCount = document.getElementById("viewer-search-count");
const viewerBackLink = document.getElementById("viewer-back-link");
const viewerMatchLinks = document.querySelectorAll("[data-viewer-match-link]");
const serverMatchCount = document.getElementById("server-match-count");

const outlineStorageKey = "manualPdfOutlineHiddenV3";
const OUTLINE_WIDTH_KEY = "manualPdfOutlineWidthV1";

let viewerSearchQuery = searchQuery || "";
let viewerSearchMatches = [];
let viewerSearchIndex = -1;
let serverMatchIndex = Number.isFinite(initialServerMatchIndex)
    ? initialServerMatchIndex
    : 0;


let scale = 1.0;
let scaleMode = isMobileViewport() ? "width" : "page";
let renderRequestId = 0;
let skipRenderLoadingOnce = false;
const pdfPageCache = new Map();
const textContentCache = new Map();
const prefetchingPages = new Set();

function isMobileViewport() {
    return window.innerWidth < 768;
}

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
    const tokenChars = "0-9a-z가-힣";
    const tokenBody = "[" + tokenChars + "/_-]*";

    function buildWildcardTokenRegex(token) {
        const parts = token.split("*").map(function (part) {
            return escapeRegExp(part);
        });

        let regexText = parts.join(tokenBody);

        if (!token.startsWith("*")) {
            regexText = "(?<![" + tokenChars + "])" + regexText;
        }

        if (!token.endsWith("*")) {
            regexText = regexText + "(?![" + tokenChars + "])";
        }

        return regexText;
    }

    function splitQueryTokens(value) {
        return value
            .split(/\s+/)
            .map(function (word) {
                return word.trim();
            })
            .filter(Boolean);
    }

    if (trimmed.includes("*")) {
        const tokens = splitQueryTokens(trimmed);
        const words = trimmed
            .split(/[\s*]+/)
            .map(function (word) {
                return word.trim();
            })
            .filter(Boolean);

        if (!tokens.length) {
            return {
                value: "",
                mode: "exact",
                words: [],
                regex: null,
            };
        }

        const regexText = tokens
            .map(function (token) {
                return buildWildcardTokenRegex(token);
            })
            .join("\\s+");

        return {
            value: trimmed,
            mode: "wildcard",
            words: words,
            regex: new RegExp(regexText, "i"),
        };
    }

    const exactWords = splitQueryTokens(trimmed);

    const exactRegexText = exactWords
        .map(function (word) {
            return "(?<![0-9a-z가-힣])" +
                escapeRegExp(word) +
                "(?![0-9a-z가-힣])";
        })
        .join("\\s+");

    return {
        value: trimmed,
        mode: "exact",
        words: exactWords,
        regex: exactRegexText ? new RegExp(exactRegexText, "i") : null,
    };
}

let parsedSearch = parseViewerSearchQuery(searchQuery);

function showViewerLoading(message) {
    if (!viewerLoading) {
        return;
    }

    const title = viewerLoading.querySelector(".viewer-loading-title");

    if (title && message) {
        title.textContent = message;
    }

    viewerLoading.classList.remove("d-none");
}

function hideViewerLoading() {
    if (!viewerLoading) {
        return;
    }

    viewerLoading.classList.add("d-none");
}

if (viewerBackLink) {
    const fallbackUrl = viewerBackLink.dataset.fallbackUrl || viewerBackLink.href;

    viewerBackLink.addEventListener("click", function (event) {
        event.preventDefault();
        const globalLoading = document.getElementById("global-loading");

        if (globalLoading) {
            globalLoading.classList.add("d-none");
        }

        const hasSameSiteReferrer =
            document.referrer &&
            new URL(document.referrer).origin === window.location.origin;

        if (window.history.length > 1 && hasSameSiteReferrer) {
            window.history.back();
            return;
        }

        window.location.href = fallbackUrl || "/";
    });
}

viewerMatchLinks.forEach(function (link) {
    link.addEventListener("click", function (event) {
        const href = link.getAttribute("href");

        if (!href) {
            return;
        }

        const targetUrl = new URL(href, window.location.href);
        const isSameViewer = targetUrl.pathname === window.location.pathname;
        const direction = link.dataset.viewerMatchDirection;

        event.preventDefault();
        const globalLoading = document.getElementById("global-loading");

        if (globalLoading) {
            globalLoading.classList.add("d-none");
        }

        if (matchItems.length && serverMatchIndex > 0 && direction) {
            let nextServerIndex = direction === "prev"
                ? serverMatchIndex - 2
                : serverMatchIndex;

            if (nextServerIndex < 0) {
                nextServerIndex = matchItems.length - 1;
            } else if (nextServerIndex >= matchItems.length) {
                nextServerIndex = 0;
            }

            const nextItem = matchItems[nextServerIndex];

            if (nextItem && nextItem.viewerUrl) {
                const nextUrl = new URL(nextItem.viewerUrl, window.location.href);
                const nextPage = parseInt(
                    nextUrl.searchParams.get("page") ||
                    nextItem.pageNumber ||
                    "",
                    10
                );

                if (
                    nextUrl.pathname === window.location.pathname &&
                    Number.isFinite(nextPage)
                ) {
                    window.history.replaceState({}, "", nextUrl.href);
                    updateServerMatchCountByDirection(direction);
                    goToPage(nextPage, { silent: true });
                    return;
                }

                showViewerLoading("Loading next match...");
                window.location.replace(nextUrl.href);
                return;
            }
        }

        const currentMatchPageIndex = getCurrentMatchingPageIndex();
        const canUseLocalMatchNavigation = (
            isSameViewer &&
            direction &&
            matchingPages.length > 0 &&
            currentMatchPageIndex !== -1 &&
            (
                !serverMatchTotal ||
                serverMatchTotal === matchingPages.length
            )
        );

        if (canUseLocalMatchNavigation) {
            goToRelativeMatch(direction);
            return;
        }

        showViewerLoading("Loading next match...");
        window.location.replace(targetUrl.href);
    });
});

function getCurrentMatchingPageIndex() {
    if (!matchingPages.length) {
        return -1;
    }

    let index = matchingPages.indexOf(currentPage);

    if (index !== -1) {
        return index;
    }

    for (let i = 0; i < matchingPages.length; i += 1) {
        if (matchingPages[i] >= currentPage) {
            return i;
        }
    }

    return matchingPages.length - 1;
}

function updateServerMatchCountByDirection(direction) {
    if (!serverMatchCount || !serverMatchTotal || serverMatchIndex <= 0) {
        return;
    }

    if (direction === "prev") {
        serverMatchIndex -= 1;
    } else if (direction === "next") {
        serverMatchIndex += 1;
    }

    if (serverMatchIndex < 1) {
        serverMatchIndex = serverMatchTotal;
    } else if (serverMatchIndex > serverMatchTotal) {
        serverMatchIndex = 1;
    }

    serverMatchCount.textContent =
        serverMatchIndex + " / " + serverMatchTotal;
}

function goToRelativeMatch(direction) {
    const currentIndex = getCurrentMatchingPageIndex();

    if (currentIndex === -1) {
        return;
    }

    let nextIndex = direction === "prev"
        ? currentIndex - 1
        : currentIndex + 1;

    if (nextIndex < 0) {
        nextIndex = matchingPages.length - 1;
    } else if (nextIndex >= matchingPages.length) {
        nextIndex = 0;
    }

    const pageNumber = matchingPages[nextIndex];
    const url = new URL(window.location.href);
    url.searchParams.set("page", pageNumber);
    window.history.replaceState({}, "", url.href);

    updateServerMatchCountByDirection(direction);
    goToPage(pageNumber, { silent: true });
}

function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function clearTextLayer() {
    if (!textLayer) {
        return;
    }

    textLayer.innerHTML = "";
    textLayer.style.width = "0px";
    textLayer.style.height = "0px";
}

function normalizeText(value) {
    return String(value || "")
        .toLowerCase()
        .replace(/\s+/g, " ")
        .trim();
}

function isSearchTokenChar(char) {
    return /[0-9a-z가-힣]/i.test(char || "");
}

function containsExactSearchValue(text, searchValue) {
    if (!text || !searchValue) {
        return false;
    }

    let startIndex = text.indexOf(searchValue);

    while (startIndex !== -1) {
        const before = text[startIndex - 1] || "";
        const after = text[startIndex + searchValue.length] || "";

        if (!isSearchTokenChar(before) && !isSearchTokenChar(after)) {
            return true;
        }

        startIndex = text.indexOf(searchValue, startIndex + 1);
    }

    return false;
}

function containsExactSearchWord(text, searchWord) {
    return containsExactSearchValue(text, searchWord);
}

function containsWildcardSearchWord(text, searchWord) {
    if (!text || !searchWord) {
        return false;
    }

    const regex = new RegExp(
        "(^|[^0-9a-z가-힣])" + escapeRegExp(searchWord) + "[0-9a-z가-힣/_-]*",
        "i"
    );

    return regex.test(text);
}

function isMatchedText(text) {
    if (!parsedSearch.value) {
        return false;
    }

    const normalizedText = normalizeText(text);

    if (parsedSearch.mode === "wildcard") {
        return parsedSearch.regex
            ? parsedSearch.regex.test(normalizedText)
            : false;
    }

    return parsedSearch.regex
        ? parsedSearch.regex.test(normalizedText)
        : containsExactSearchValue(normalizedText, parsedSearch.value);
}

function buildTextItemMatchIndexes(textItems, parsed) {
    if (!parsed || !parsed.regex || !textItems.length) {
        return new Set();
    }

    const ranges = [];
    let pageText = "";

    textItems.forEach(function (item, index) {
        const itemText = normalizeText(item.str || "");

        if (!itemText) {
            return;
        }

        if (pageText) {
            pageText += " ";
        }

        const start = pageText.length;
        pageText += itemText;
        const end = pageText.length;

        ranges.push({
            index: index,
            start: start,
            end: end,
        });
    });

    const matchedIndexes = new Set();
    const regexFlags = parsed.regex.flags.includes("g")
        ? parsed.regex.flags
        : parsed.regex.flags + "g";
    const regex = new RegExp(parsed.regex.source, regexFlags);
    let match;

    while ((match = regex.exec(pageText)) !== null) {
        const matchStart = match.index;
        const matchEnd = match.index + match[0].length;

        ranges.forEach(function (range) {
            if (range.end > matchStart && range.start < matchEnd) {
                matchedIndexes.add(range.index);
            }
        });

        if (match.index === regex.lastIndex) {
            regex.lastIndex += 1;
        }
    }

    return matchedIndexes;
}

function getFitScale(page) {
    const viewport = page.getViewport({ scale: 1 });

    const padding = isMobileViewport() ? 4 : 16;

    const containerWidth = Math.max(
        pdfContainer.clientWidth - padding,
        1
    );

    const containerHeight = Math.max(
        pdfContainer.clientHeight - padding,
        1
    );

    if (scaleMode === "width") {
        return containerWidth / viewport.width;
    }

    if (scaleMode === "page") {
        return Math.min(
            containerWidth / viewport.width,
            containerHeight / viewport.height
        );
    }

    return scale;
}

function updateZoomLabel(value) {
    zoomLevelSpan.textContent = Math.round(value * 100) + "%";
}

function getPdfPage(pageNumber) {
    if (pdfPageCache.has(pageNumber)) {
        return Promise.resolve(pdfPageCache.get(pageNumber));
    }

    return pdfDoc.getPage(pageNumber).then(function (page) {
        pdfPageCache.set(pageNumber, page);
        return page;
    });
}

function getTextContentForPage(pageNumber, page) {
    if (textContentCache.has(pageNumber)) {
        return Promise.resolve(textContentCache.get(pageNumber));
    }

    const pagePromise = page
        ? Promise.resolve(page)
        : getPdfPage(pageNumber);

    return pagePromise.then(function (resolvedPage) {
        return resolvedPage.getTextContent().then(function (textContent) {
            textContentCache.set(pageNumber, textContent);
            return textContent;
        });
    });
}

function getPrefetchMatchPages() {
    const pages = [];
    const seen = new Set();

    function addPage(pageNumber) {
        const parsedPage = parseInt(pageNumber, 10);

        if (
            Number.isFinite(parsedPage) &&
            parsedPage >= 1 &&
            pdfDoc &&
            parsedPage <= pdfDoc.numPages &&
            parsedPage !== currentPage &&
            !seen.has(parsedPage)
        ) {
            seen.add(parsedPage);
            pages.push(parsedPage);
        }
    }

    if (matchItems.length && serverMatchIndex > 0) {
        for (let offset = 1; offset <= 3; offset += 1) {
            addPage(matchItems[(serverMatchIndex - 1 + offset) % matchItems.length].pageNumber);
        }

        addPage(matchItems[(serverMatchIndex - 2 + matchItems.length) % matchItems.length].pageNumber);
    } else if (matchingPages.length) {
        const currentIndex = getCurrentMatchingPageIndex();

        if (currentIndex !== -1) {
            for (let offset = 1; offset <= 3; offset += 1) {
                addPage(matchingPages[(currentIndex + offset) % matchingPages.length]);
            }

            addPage(matchingPages[(currentIndex - 1 + matchingPages.length) % matchingPages.length]);
        }
    }

    return pages;
}

function prefetchMatchPages() {
    if (!pdfDoc || !parsedSearch.value) {
        return;
    }

    getPrefetchMatchPages().forEach(function (pageNumber) {
        if (prefetchingPages.has(pageNumber)) {
            return;
        }

        prefetchingPages.add(pageNumber);

        getPdfPage(pageNumber)
            .then(function (page) {
                return getTextContentForPage(pageNumber, page);
            })
            .catch(function () {
                // Prefetch is only a speed boost; normal rendering can still recover.
            })
            .finally(function () {
                prefetchingPages.delete(pageNumber);
            });
    });
}

function renderTextLayer(pageNumber, page, viewport, requestId) {
    if (!textLayer || requestId !== renderRequestId) {
        return;
    }

    if (!parsedSearch.value) {
        clearTextLayer();
        return;
    }

    textLayer.innerHTML = "";
    textLayer.style.width = viewport.width + "px";
    textLayer.style.height = viewport.height + "px";
    textLayer.style.left = "0";
    textLayer.style.top = "0";

    getTextContentForPage(pageNumber, page).then(function (textContent) {
        if (requestId !== renderRequestId) {
            return;
        }

        const matchedItemIndexes = buildTextItemMatchIndexes(
            textContent.items,
            parsedSearch
        );

        textContent.items.forEach(function (item, index) {
            const span = document.createElement("span");
            const text = item.str || "";

            span.textContent = text;

            const transform = pdfjsLib.Util.transform(
                viewport.transform,
                item.transform
            );

            const fontHeight = Math.hypot(transform[2], transform[3]);
            const fontWidthScale =
                Math.hypot(transform[0], transform[1]) / Math.max(fontHeight, 1);

            span.style.left = transform[4] + "px";
            span.style.top = transform[5] - fontHeight + "px";
            span.style.fontSize = fontHeight + "px";
            span.style.transform = "scaleX(" + fontWidthScale + ")";

            if (isMatchedText(text) || matchedItemIndexes.has(index)) {
                span.classList.add("highlight-text");
            }

            textLayer.appendChild(span);
        });
    });
}

function renderPage(pageNumber) {
    pageRendering = true;

    const requestId = ++renderRequestId;
    const showRenderLoading = !skipRenderLoadingOnce;
    skipRenderLoadingOnce = false;

    clearTextLayer();

    if (showRenderLoading) {
        showViewerLoading("Rendering page...");
    }

    getPdfPage(pageNumber)
        .then(function (page) {
            if (requestId !== renderRequestId) {
                return;
            }

            const pageScale = getFitScale(page);
            const viewport = page.getViewport({ scale: pageScale });

            canvas.height = viewport.height;
            canvas.width = viewport.width;

            canvas.style.width = viewport.width + "px";
            canvas.style.height = viewport.height + "px";

            singlePageViewer.style.width = viewport.width + "px";
            singlePageViewer.style.height = viewport.height + "px";

            textLayer.style.width = viewport.width + "px";
            textLayer.style.height = viewport.height + "px";

            const renderTask = page.render({
                canvasContext: ctx,
                viewport: viewport,
            });

            return renderTask.promise.then(function () {
                if (requestId !== renderRequestId) {
                    return;
                }

                renderTextLayer(pageNumber, page, viewport, requestId);
                updateZoomLabel(pageScale);
                hideViewerLoading();
                prefetchMatchPages();

                pageRendering = false;

                if (pagePending !== null) {
                    const pendingPage = pagePending;
                    pagePending = null;
                    renderPage(pendingPage);
                }
            });
        })
        .catch(function (error) {
            console.error("PDF Page Render Error:", error);
            hideViewerLoading();
            pageRendering = false;
        });

    pageNumberInput.value = pageNumber;

    const mobilePageCurrent = document.getElementById("mobile-page-current");

    if (mobilePageCurrent) {
        mobilePageCurrent.textContent = pageNumber;
    }
}

function queueRenderPage(pageNumber) {
    if (pageRendering) {
        pagePending = pageNumber;
    } else {
        renderPage(pageNumber);
    }
}

function goToPage(pageNumber, options) {
    options = options || {};

    if (!pdfDoc) {
        return;
    }

    if (pageNumber < 1 || pageNumber > pdfDoc.numPages) {
        return;
    }

    currentPage = pageNumber;
    skipRenderLoadingOnce = Boolean(options.silent);

    if (
        isMobileViewport() &&
        !manualPdfLayout.classList.contains("outline-hidden")
    ) {
        manualPdfLayout.classList.add("outline-hidden");
        sessionStorage.setItem(outlineStorageKey, "true");
    }

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
            toggleBtn.innerHTML = '<span class="toggle-icon">[+]</span>';

            row.appendChild(toggleBtn);

            toggleBtn.addEventListener("click", function (event) {
                event.preventDefault();
                event.stopPropagation();
                toggleFolder();
            });

            function toggleFolder() {
                const isCollapsed = childUl.classList.contains(
                    "outline-children-collapsed"
                );
                const iconSpan = toggleBtn.querySelector(".toggle-icon");

                if (isCollapsed) {
                    childUl.classList.remove("outline-children-collapsed");
                    toggleBtn.classList.add("is-open");
                    iconSpan.innerHTML = "[-]";
                } else {
                    childUl.classList.add("outline-children-collapsed");
                    toggleBtn.classList.remove("is-open");
                    iconSpan.innerHTML = "[+]";
                }
            }
        } else {
            const docIcon = document.createElement("span");
            docIcon.className = "outline-doc-icon";
            docIcon.innerHTML = "▱";
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
                .forEach(function (r) {
                    r.classList.remove("is-active");
                });

            row.classList.add("is-active");

            if (window.innerWidth <= 567 && manualPdfLayout) {
                manualPdfLayout.classList.add("outline-hidden");
                sessionStorage.setItem(outlineStorageKey, "true");
            }

            resolveDestination(item.dest).then(function (dest) {
                goToDestination(dest);
            });
        });

        row.appendChild(link);
        li.appendChild(row);

        if (hasChildren) {
            childUl = createOutlineTree(item.items, depth + 1);
            childUl.classList.add("outline-children-collapsed");
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
        /^(\d{2})(?:[-\.\s]*(\d{2}))?(?:[-\.\s]*(\d{2}))?\b/
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

    requestAnimationFrame(function () {
        queueRenderPage(currentPage);
    });
});

fitPageBtn.addEventListener("click", function () {
    scaleMode = "page";

    requestAnimationFrame(function () {
        queueRenderPage(currentPage);
    });
});

function rerenderOnResize() {
    if (!pdfDoc) {
        return;
    }

    if (isMobileViewport() && scaleMode === "page") {
        scaleMode = "width";
    }

    if (scaleMode !== "custom") {
        queueRenderPage(currentPage);
    }
}

if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", rerenderOnResize);
}

window.addEventListener("resize", rerenderOnResize);

(function initOutlineResizer() {
    if (!outlineResizer || !outlinePanel || !manualPdfLayout) {
        return;
    }

    const savedWidth = localStorage.getItem(OUTLINE_WIDTH_KEY);

    if (savedWidth && window.innerWidth > 567) {
        outlinePanel.style.width = savedWidth + "px";
        outlinePanel.style.flexBasis = savedWidth + "px";
    }

    let dragging = false;
    let activePointerId = null;
    let resizeRenderTimer = null;

    outlineResizer.addEventListener("pointerdown", function (event) {
        event.preventDefault();

        dragging = true;
        activePointerId = event.pointerId;

        outlineResizer.setPointerCapture(event.pointerId);
        outlineResizer.classList.add("is-dragging");

        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
    });

    outlineResizer.addEventListener("pointermove", function (event) {
        if (!dragging || event.pointerId !== activePointerId) {
            return;
        }

        event.preventDefault();

        const layoutRect = manualPdfLayout.getBoundingClientRect();
        let newWidth = event.clientX - layoutRect.left;

        const minWidth = window.innerWidth <= 991 ? 170 : 220;
        const maxWidth = window.innerWidth <= 991
            ? window.innerWidth * 0.82
            : window.innerWidth * 0.6;

        newWidth = Math.max(minWidth, Math.min(newWidth, maxWidth));

        outlinePanel.style.width = newWidth + "px";
        outlinePanel.style.flexBasis = newWidth + "px";

        if (resizeRenderTimer) {
            clearTimeout(resizeRenderTimer);
        }

        resizeRenderTimer = setTimeout(function () {
            if (scaleMode !== "custom") {
                queueRenderPage(currentPage);
            }
        }, 80);
    });

    outlineResizer.addEventListener("pointerup", function (event) {
        if (!dragging || event.pointerId !== activePointerId) {
            return;
        }

        dragging = false;
        activePointerId = null;

        outlineResizer.releasePointerCapture(event.pointerId);
        outlineResizer.classList.remove("is-dragging");

        document.body.style.cursor = "";
        document.body.style.userSelect = "";

        localStorage.setItem(
            OUTLINE_WIDTH_KEY,
            Math.round(outlinePanel.getBoundingClientRect().width)
        );

        if (scaleMode !== "custom") {
            queueRenderPage(currentPage);
        }
    });

    outlineResizer.addEventListener("pointercancel", function () {
        dragging = false;
        activePointerId = null;

        outlineResizer.classList.remove("is-dragging");

        document.body.style.cursor = "";
        document.body.style.userSelect = "";
    });
})();

function applyOutlineState() {
    const storedValue = sessionStorage.getItem(outlineStorageKey);

    let isHidden;

    if (storedValue === null) {
        isHidden = isMobileViewport();
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

    sessionStorage.setItem(
        outlineStorageKey,
        isHidden ? "true" : "false"
    );

    setTimeout(function () {
        if (scaleMode !== "custom") {
            queueRenderPage(currentPage);
        }
    }, 180);
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

            const mobilePageTotal =
                document.getElementById("mobile-page-total");

            if (mobilePageTotal) {
                mobilePageTotal.textContent = pdfDoc.numPages;
            }

            if (currentPage > pdfDoc.numPages) {
                currentPage = 1;
            }

            if (viewMode === "matches" && matchingPages.length > 0) {
                renderAllMatches();
            } else {
                singlePageViewer.classList.remove("d-none");
                multiPageViewer.classList.add("d-none");

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

function createTextLayerForPage(pageNumber, page, viewport, wrapper) {
    const layer = document.createElement("div");
    layer.className = "text-layer dynamic-text-layer";

    layer.style.position = "absolute";
    layer.style.left = "0";
    layer.style.top = "0";
    layer.style.width = viewport.width + "px";
    layer.style.height = viewport.height + "px";
    layer.style.zIndex = "2";
    layer.style.opacity = "1";
    layer.style.lineHeight = "1";
    layer.style.overflow = "hidden";

    wrapper.appendChild(layer);

    getTextContentForPage(pageNumber, page).then(function (textContent) {
        const matchedItemIndexes = buildTextItemMatchIndexes(
            textContent.items,
            parsedSearch
        );

        textContent.items.forEach(function (item, index) {
            const span = document.createElement("span");
            const text = item.str || "";

            span.textContent = text;

            const transform = pdfjsLib.Util.transform(
                viewport.transform,
                item.transform
            );

            const fontHeight = Math.hypot(transform[2], transform[3]);
            const fontWidthScale =
                Math.hypot(transform[0], transform[1]) / Math.max(fontHeight, 1);

            span.style.position = "absolute";
            span.style.whiteSpace = "pre";
            span.style.transformOrigin = "0 0";
            span.style.color = "transparent";
            span.style.left = transform[4] + "px";
            span.style.top = transform[5] - fontHeight + "px";
            span.style.fontSize = fontHeight + "px";
            span.style.transform = "scaleX(" + fontWidthScale + ")";

            if (isMatchedText(text) || matchedItemIndexes.has(index)) {
                span.classList.add("highlight-text");
            }

            layer.appendChild(span);
        });
    });
}

function renderMatchPage(pageNumber) {
    return getPdfPage(pageNumber).then(function (page) {
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
        pageCanvas.style.width = viewport.width + "px";
        pageCanvas.style.height = viewport.height + "px";

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
                createTextLayerForPage(pageNumber, page, viewport, wrapper);
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
        const page = await getPdfPage(pageNumber);
        const textContent = await getTextContentForPage(pageNumber, page);

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
        return parsed.regex
            ? parsed.regex.test(normalizedText)
            : false;
    }

    return parsed.regex
        ? parsed.regex.test(normalizedText)
        : containsExactSearchValue(normalizedText, parsed.value);
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

        if (isMobileViewport()) {
            return;
        }

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
    }
);

let touchStartDistance = null;

function getDistance(touches) {
    const dx = touches[0].clientX - touches[1].clientX;
    const dy = touches[0].clientY - touches[1].clientY;

    return Math.sqrt(dx * dx + dy * dy);
}

pdfContainer.addEventListener(
    "touchstart",
    function (event) {
        if (event.touches.length === 2) {
            touchStartDistance = getDistance(event.touches);
        }
    },
    { passive: true }
);

pdfContainer.addEventListener(
    "touchmove",
    function (event) {
        if (event.touches.length !== 2) {
            return;
        }

        event.preventDefault();

        const currentDistance = getDistance(event.touches);

        if (!touchStartDistance) {
            touchStartDistance = currentDistance;
            return;
        }

        const delta = (currentDistance - touchStartDistance) / 250;

        scaleMode = "custom";

        scale = Math.min(
            Math.max(scale + delta, 0.5),
            4
        );

        touchStartDistance = currentDistance;

        queueRenderPage(currentPage);
    },
    { passive: false }
);

pdfContainer.addEventListener("touchend", function () {
    touchStartDistance = null;
});

const mobilePrev =
    document.getElementById("mobile-prev-page");

const mobileNext =
    document.getElementById("mobile-next-page");

const mobileCurrent =
    document.getElementById("mobile-page-current");

const mobileTotal =
    document.getElementById("mobile-page-total");

if (mobileTotal && pageCountSpan) {
    mobileTotal.textContent = pageCountSpan.textContent;
}

if (mobilePrev) {
    mobilePrev.addEventListener("click", function () {
        goToPage(currentPage - 1);
    });
}

if (mobileNext) {
    mobileNext.addEventListener("click", function () {
        goToPage(currentPage + 1);
    });
}
