document.addEventListener("DOMContentLoaded", function () {
    const workspace = document.querySelector("[data-chapter-workspace]");

    if (!workspace) {
        return;
    }

    /* =================================================
           Elements
        ================================================= */

    const frame = document.getElementById("chapter-viewer-frame");

    const loading = workspace.querySelector("[data-chapter-viewer-loading]");
    const viewerEmpty = workspace.querySelector("[data-chapter-viewer-empty]");

    const paneButtons = workspace.querySelectorAll(
        "[data-workspace-pane-button]",
    );

    const chapterLinks = workspace.querySelectorAll(
        "[data-chapter-viewer-link]",
    );

    const viewerButton = workspace.querySelector(
        '[data-workspace-pane-button="viewer"]',
    );

    const viewerChapter = workspace.querySelector("[data-viewer-chapter]");

    const viewerPage = workspace.querySelector("[data-viewer-page]");

    const viewerMatch = workspace.querySelector("[data-viewer-match]");

    const viewerStatus = workspace.querySelector("[data-viewer-status]");

    const mobileQuery = window.matchMedia("(max-width: 48rem)");
    const outlineCache = new Map();
    let activeChapterId = "";
    let activePageNumber = null;

    /* =================================================
           Pane
        ================================================= */

    function setActivePane(pane) {
        workspace.dataset.activePane = pane;

        paneButtons.forEach(function (button) {
            const isActive = button.dataset.workspacePaneButton === pane;

            button.classList.toggle("is-active", isActive);

            button.setAttribute("aria-selected", isActive ? "true" : "false");
        });
    }

    /* =================================================
           Loading
        ================================================= */

    function showFrameLoading() {
        if (loading) {
            loading.classList.remove("is-hidden");
        }

        if (viewerEmpty) {
            viewerEmpty.hidden = true;
        }

        if (frame) {
            frame.dataset.hasViewer = "true";
            frame.classList.remove("is-empty");
        }

        if (viewerStatus) {
            viewerStatus.textContent = "LOADING";

            viewerStatus.classList.add("is-loading");
        }
    }

    function hideFrameLoading() {
        if (loading) {
            loading.classList.add("is-hidden");
        }

        if (viewerStatus) {
            viewerStatus.textContent = "READY";

            viewerStatus.classList.remove("is-loading");
        }
    }

    function frameHasViewer() {
        return frame && frame.dataset.hasViewer === "true";
    }

    /* =================================================
           Chapter ID from URL
        ================================================= */

    function getChapterId(url) {
        if (!url) {
            return "";
        }

        try {
            const parsedUrl = new URL(url, window.location.href);

            const match = parsedUrl.pathname.match(
                /\/manual-chapters\/(\d+)\/pdf\/viewer\/?/,
            );

            return match ? match[1] : "";
        } catch (error) {
            return "";
        }
    }

    /* =================================================
    Workspace URL State
    ================================================= */

    function updateWorkspaceUrl(chapterId, pageNumber) {
        const url = new URL(window.location.href);

        /* Chapter */

        if (chapterId) {
            url.searchParams.set("chapter", chapterId);
        }

        /* Page */

        const parsedPage = Number(pageNumber);

        if (Number.isFinite(parsedPage) && parsedPage >= 1) {
            url.searchParams.set("page", parsedPage);
        }

        window.history.replaceState(
            {
                chapter: chapterId || null,

                page: Number.isFinite(parsedPage) ? parsedPage : null,
            },
            "",
            url.href,
        );
    }

    /* =================================================
           Chapter title
        ================================================= */

    function getChapterTitle(link) {
        if (!link) {
            return "";
        }

        const task = link.dataset.chapterTask || "";

        if (task) {
            return task;
        }

        const titleElement = link.querySelector(
            ".manual-workspace-chapter-main strong",
        );

        if (!titleElement) {
            return "";
        }

        return titleElement.textContent.replace(/\s+/g, " ").trim();
    }

    /* =================================================
           PDF outline below active chapter
        ================================================= */

    function getOutlineContainer(link, chapterId) {
        let container = link.nextElementSibling;

        if (
            container &&
            container.classList.contains("manual-workspace-subchapters")
        ) {
            return container;
        }

        container = document.createElement("div");
        container.className = "manual-workspace-subchapters";
        container.dataset.outlineChapterId = chapterId;
        container.hidden = true;
        link.insertAdjacentElement("afterend", container);

        return container;
    }

    function setOutlineNodeExpanded(node, expanded) {
        if (!node) {
            return;
        }

        const children = node.querySelector(
            ":scope > .manual-workspace-subchapter-children",
        );
        const toggle = node.querySelector(
            ":scope > .manual-workspace-subchapter-row > .manual-workspace-subchapter-toggle",
        );

        if (!children || !toggle) {
            return;
        }

        children.hidden = !expanded;
        toggle.classList.toggle("is-expanded", expanded);
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        toggle.setAttribute(
            "aria-label",
            expanded ? "하위 목차 접기" : "하위 목차 펼치기",
        );
    }

    function collapseOutlineBranches(container) {
        container
            .querySelectorAll(".manual-workspace-subchapter-node")
            .forEach(function (node) {
                setOutlineNodeExpanded(node, false);
            });
    }

    function expandActiveOutlinePath(button) {
        let node = button
            ? button.closest(".manual-workspace-subchapter-node")
            : null;

        if (node) {
            setOutlineNodeExpanded(node, true);
        }

        while (node) {
            const parentChildren = node.parentElement;

            if (
                !parentChildren ||
                !parentChildren.classList.contains(
                    "manual-workspace-subchapter-children",
                )
            ) {
                break;
            }

            const parentNode = parentChildren.parentElement;

            setOutlineNodeExpanded(parentNode, true);
            node = parentNode;
        }
    }

    function syncActiveSubchapter(chapterId, pageNumber) {
        if (!chapterId || !Number.isFinite(Number(pageNumber))) {
            return;
        }

        const link = Array.from(chapterLinks).find(function (item) {
            return String(item.dataset.chapterId || getChapterId(item.href)) ===
                String(chapterId);
        });

        if (!link) {
            return;
        }

        const container = getOutlineContainer(link, chapterId);
        const buttons = Array.from(
            container.querySelectorAll("[data-outline-page]"),
        );
        let activeButton = null;

        buttons.forEach(function (button) {
            const outlinePage = Number(button.dataset.outlinePage);

            if (outlinePage <= Number(pageNumber)) {
                activeButton = button;
            }
        });

        if (!activeButton && buttons.length) {
            activeButton = buttons[0];
        }

        buttons.forEach(function (button) {
            const isActive = button === activeButton;

            button.classList.toggle("is-active", isActive);

            if (isActive) {
                button.setAttribute("aria-current", "page");
            } else {
                button.removeAttribute("aria-current");
            }
        });

        collapseOutlineBranches(container);
        expandActiveOutlinePath(activeButton);

        if (activeButton) {
            activeButton.scrollIntoView({
                block: "nearest",
                behavior: "auto",
            });
        }
    }

    function buildOutlineTree(outline) {
        const roots = [];
        const stack = [];

        outline.forEach(function (entry) {
            const depth = Math.max(0, Math.min(Number(entry.depth) || 0, 6));
            const node = {
                title: entry.title || "Untitled",
                pageNumber: entry.pageNumber,
                depth: depth,
                children: [],
            };

            while (stack.length > depth) {
                stack.pop();
            }

            if (stack.length) {
                stack[stack.length - 1].children.push(node);
            } else {
                roots.push(node);
            }

            stack.push(node);
        });

        return roots;
    }

    function goToOutlinePage(chapterId, pageNumber) {
        activePageNumber = pageNumber;
        syncActiveSubchapter(chapterId, pageNumber);

        if (viewerPage) {
            viewerPage.textContent = "Page " + pageNumber;
        }

        updateWorkspaceUrl(chapterId, pageNumber);
        frame.contentWindow.postMessage(
            {
                source: "manual-chapter-workspace",
                type: "go-to-page",
                pageNumber: pageNumber,
            },
            window.location.origin,
        );

        if (mobileQuery.matches) {
            setActivePane("viewer");
        }
    }

    function createOutlineNode(node, chapterId) {
        const wrapper = document.createElement("div");
        const row = document.createElement("div");
        const pageNumber = Number(node.pageNumber);
        const hasPage = Number.isFinite(pageNumber) && pageNumber >= 1;
        const hasChildren = node.children.length > 0;
        const toggle = document.createElement("button");
        const item = document.createElement("button");
        const title = document.createElement("span");

        wrapper.className = "manual-workspace-subchapter-node";
        row.className = "manual-workspace-subchapter-row";
        toggle.type = "button";
        toggle.className = "manual-workspace-subchapter-toggle";
        toggle.innerHTML =
            '<span class="manual-workspace-arrow" aria-hidden="true">›</span>';
        item.type = "button";
        item.className = "manual-workspace-subchapter";
        title.className = "manual-workspace-subchapter-title";
        title.textContent = node.title;

        if (hasChildren) {
            toggle.setAttribute("aria-expanded", "false");
            toggle.setAttribute("aria-label", "하위 목차 펼치기");
            toggle.addEventListener("click", function () {
                const children = wrapper.querySelector(
                    ":scope > .manual-workspace-subchapter-children",
                );

                setOutlineNodeExpanded(wrapper, children.hidden);
            });
        } else {
            toggle.classList.add("is-placeholder");
            toggle.tabIndex = -1;
            toggle.setAttribute("aria-hidden", "true");
        }

        if (hasChildren) {
            item.classList.add("is-parent");
        }

        if (!hasPage) {
            item.classList.add("is-heading");
        } else {
            const page = document.createElement("span");

            item.dataset.outlinePage = pageNumber;
            page.className = "manual-workspace-subchapter-page";
            page.textContent = pageNumber;
            item.appendChild(title);
            item.appendChild(page);
            item.addEventListener("click", function () {
                goToOutlinePage(chapterId, pageNumber);
            });
        }

        if (!hasPage) {
            item.appendChild(title);

            if (hasChildren) {
                item.addEventListener("click", function () {
                    const children = wrapper.querySelector(
                        ":scope > .manual-workspace-subchapter-children",
                    );

                    setOutlineNodeExpanded(wrapper, children.hidden);
                });
            }
        }

        row.appendChild(toggle);
        row.appendChild(item);
        wrapper.appendChild(row);

        if (hasChildren) {
            const children = document.createElement("div");

            children.className = "manual-workspace-subchapter-children";
            children.hidden = true;
            node.children.forEach(function (child) {
                children.appendChild(createOutlineNode(child, chapterId));
            });
            wrapper.appendChild(children);
        }

        return wrapper;
    }

    function renderChapterOutline(chapterId, forceRender) {
        const link = Array.from(chapterLinks).find(function (item) {
            return String(item.dataset.chapterId || getChapterId(item.href)) ===
                String(chapterId);
        });

        document
            .querySelectorAll(".manual-workspace-subchapters")
            .forEach(function (container) {
                container.hidden =
                    container.dataset.outlineChapterId !== String(chapterId);
            });

        if (!link || !outlineCache.has(String(chapterId))) {
            return;
        }

        const outline = outlineCache.get(String(chapterId));
        const container = getOutlineContainer(link, chapterId);

        container.hidden = false;

        if (container.dataset.outlineRendered === "true" && !forceRender) {
            syncActiveSubchapter(chapterId, activePageNumber);
            return;
        }

        container.innerHTML = "";
        container.dataset.outlineRendered = "true";

        if (!outline.length) {
            const empty = document.createElement("div");

            empty.className = "manual-workspace-subchapter-empty";
            empty.textContent = "No PDF contents";
            container.appendChild(empty);
            return;
        }

        buildOutlineTree(outline).forEach(function (node) {
            container.appendChild(createOutlineNode(node, chapterId));
        });

        syncActiveSubchapter(chapterId, activePageNumber);
    }

    /* =================================================
           Active Chapter
        ================================================= */

    function setActiveChapter(chapterId) {
        if (!chapterId) {
            return;
        }

        let activeLink = null;
        const chapterChanged = String(activeChapterId) !== String(chapterId);

        activeChapterId = String(chapterId);

        chapterLinks.forEach(function (link) {
            const linkChapterId =
                link.dataset.chapterId || getChapterId(link.href);

            const isActive = String(linkChapterId) === String(chapterId);

            link.classList.toggle("is-active", isActive);

            if (isActive) {
                activeLink = link;
            }
        });

        if (!activeLink) {
            return;
        }

        if (viewerChapter) {
            const title = getChapterTitle(activeLink);

            if (title) {
                viewerChapter.textContent = title;
            }
        }

        renderChapterOutline(chapterId);

        if (chapterChanged) {
            activeLink.scrollIntoView({
                block: "nearest",
                behavior: "auto",
            });
        }
    }

    /* =================================================
    Viewer Information
    ================================================= */

    function updateViewerInfo(data) {
        if (!data) {
            return;
        }

        /* =============================================
        Chapter
        ============================================= */

        if (data.chapterId) {
            setActiveChapter(data.chapterId);
        }

        /* =============================================
        Page
        ============================================= */

        if (viewerPage && Number.isFinite(Number(data.pageNumber))) {
            activePageNumber = Number(data.pageNumber);
            viewerPage.textContent = "Page " + Number(data.pageNumber);
            syncActiveSubchapter(data.chapterId, activePageNumber);
        }

        /* =============================================
        Match
        ============================================= */

        if (viewerMatch) {
            const current = Number(data.currentMatchIndex || 0);

            const total = Number(data.matchCount || 0);

            if (total > 0) {
                viewerMatch.textContent = "Match " + current + " / " + total;

                viewerMatch.hidden = false;
            } else {
                viewerMatch.textContent = "";

                viewerMatch.hidden = true;
            }
        }

        /* =============================================
        Parent URL
        ============================================= */

        updateWorkspaceUrl(data.chapterId, data.pageNumber);
    }

    /* =================================================
           Pane Buttons
        ================================================= */

    paneButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            if (button.disabled) {
                return;
            }

            const pane = button.dataset.workspacePaneButton;

            if (!pane) {
                return;
            }

            setActivePane(pane);
        });
    });

    /* =================================================
           Chapter Click
        ================================================= */

    chapterLinks.forEach(function (link) {
        link.addEventListener("click", function (event) {
            if (!frame) {
                return;
            }

            event.preventDefault();

            const viewerUrl = link.getAttribute("href");

            if (!viewerUrl) {
                return;
            }

            showFrameLoading();

            const chapterId = link.dataset.chapterId || getChapterId(viewerUrl);

            setActiveChapter(chapterId);

            /* =============================================
            Update Workspace URL immediately
            ============================================= */

            try {
                const parsedViewerUrl = new URL(
                    viewerUrl,
                    window.location.href,
                );

                const requestedPage = parseInt(
                    parsedViewerUrl.searchParams.get("page") || "1",
                    10,
                );

                activePageNumber = requestedPage;
                updateWorkspaceUrl(chapterId, requestedPage);
                syncActiveSubchapter(chapterId, requestedPage);
            } catch (error) {
                activePageNumber = 1;
                updateWorkspaceUrl(chapterId, 1);
            }

            frame.src = viewerUrl;

            if (viewerButton) {
                viewerButton.disabled = false;
            }

            if (mobileQuery.matches) {
                setActivePane("viewer");
            }
        });
    });

    /* =================================================
           iframe Load Fallback
        ================================================= */

    if (frame) {
        frame.addEventListener("load", function () {
            /*
             * iframe HTML 자체가 로드된 상태.
             *
             * PDF.js 렌더 완료 상태는
             * postMessage가 알려준다.
             */

            try {
                const viewerUrl = frame.contentWindow.location.href;

                const chapterId = getChapterId(viewerUrl);

                if (chapterId) {
                    setActiveChapter(chapterId);
                }
            } catch (error) {
                /*
                 * postMessage를
                 * primary sync로 사용.
                 */
            }
        });
    }

    /* =================================================
           PDF Viewer → Workspace
        ================================================= */

    window.addEventListener("message", function (event) {
        /* -----------------------------------------
                   Security
                ----------------------------------------- */

        if (event.origin !== window.location.origin) {
            return;
        }

        if (frame && event.source !== frame.contentWindow) {
            return;
        }

        const data = event.data || {};

        if (data.source !== "manual-pdf-viewer") {
            return;
        }

        if (data.type === "outline-ready") {
            const outline = Array.isArray(data.outline) ? data.outline : [];

            outlineCache.set(String(data.chapterId), outline);
            renderChapterOutline(data.chapterId, true);
            syncActiveSubchapter(data.chapterId, data.pageNumber);
            return;
        }

        /* -----------------------------------------
                   Page rendered
                ----------------------------------------- */

        if (data.type === "page-change") {
            hideFrameLoading();

            updateViewerInfo(data);

            return;
        }

        /* -----------------------------------------
        Match changed
        ----------------------------------------- */

        if (data.type === "match-change") {
            hideFrameLoading();

            updateViewerInfo(data);

            return;
        }

        if (data.type === "navigation-start") {
            showFrameLoading();

            if (data.targetChapterId) {
                setActiveChapter(data.targetChapterId);
            }

            if (viewerPage && data.targetPageNumber) {
                viewerPage.textContent = "Page " + data.targetPageNumber;
            }

            if (data.targetChapterId) {
                updateWorkspaceUrl(
                    data.targetChapterId,
                    data.targetPageNumber || 1,
                );
            }

            return;
        }
    });

    /* =================================================
           Initial Chapter
        ================================================= */

    if (frame) {
        const initialChapterId = getChapterId(frame.getAttribute("src"));

        if (initialChapterId) {
            setActiveChapter(initialChapterId);
        }
    }

    /* =================================================
           Initial Mobile State
        ================================================= */

    if (mobileQuery.matches) {
        if (frameHasViewer()) {
            setActivePane("viewer");
        } else {
            setActivePane("chapters");
        }
    }

    /* =================================================
           Responsive
        ================================================= */

    function handleResponsiveChange(event) {
        if (!event.matches) {
            return;
        }

        if (frameHasViewer()) {
            setActivePane("viewer");
        } else {
            setActivePane("chapters");
        }
    }

    if (typeof mobileQuery.addEventListener === "function") {
        mobileQuery.addEventListener("change", handleResponsiveChange);
    }
});
