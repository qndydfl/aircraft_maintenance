
document.addEventListener("DOMContentLoaded", function () {
    const loading = document.getElementById("global-loading");
    const bottomNav = document.querySelector(".mobile-bottom-nav");
    const bottomNavLinks = document.querySelectorAll(".mobile-bottom-nav a");
    const appMain = document.querySelector(".app-main");

    /*
     * PWA 실행 상태 확인
     *
     * Android/Chrome:
     *   display-mode: standalone
     *
     * iPhone/iPad:
     *   window.navigator.standalone
     */
    const standaloneMediaQuery = window.matchMedia(
        "(display-mode: standalone)"
    );

    function isPwaStandalone() {
        return (
            standaloneMediaQuery.matches ||
            window.navigator.standalone === true
        );
    }

    function syncPwaMode() {
        const standalone = isPwaStandalone();

        document.documentElement.classList.toggle(
            "pwa-standalone",
            standalone
        );

        document.body.classList.toggle(
            "pwa-standalone",
            standalone
        );

        document.documentElement.classList.toggle(
            "browser-mode",
            !standalone
        );

        document.body.classList.toggle(
            "browser-mode",
            !standalone
        );
    }

    syncPwaMode();

    /*
     * display-mode가 실행 중 변경되는 경우 대응
     */
    if (typeof standaloneMediaQuery.addEventListener === "function") {
        standaloneMediaQuery.addEventListener(
            "change",
            syncPwaMode
        );
    } else if (
        typeof standaloneMediaQuery.addListener === "function"
    ) {
        standaloneMediaQuery.addListener(syncPwaMode);
    }

    if ("scrollRestoration" in window.history) {
        window.history.scrollRestoration = "manual";
    }

    function syncViewportHeight() {
        const viewport = window.visualViewport;

        const viewportHeight = viewport
            ? viewport.height
            : window.innerHeight;

        const viewportOffsetTop = viewport
            ? viewport.offsetTop
            : 0;

        const viewportBottomOffset = Math.max(
            0,
            window.innerHeight -
                viewportHeight -
                viewportOffsetTop
        );

        document.documentElement.style.setProperty(
            "--app-viewport-height",
            `${viewportHeight}px`
        );

        document.documentElement.style.setProperty(
            "--app-viewport-bottom-offset",
            `${viewportBottomOffset}px`
        );

        if (bottomNav) {
            bottomNav.style.transform = "translateZ(0)";
        }
    }

    function resetScrollPosition() {
        window.scrollTo({
            top: 0,
            left: 0,
            behavior: "instant",
        });

        if (appMain) {
            appMain.scrollTop = 0;
        }
    }

    function settleMobileViewport() {
        syncPwaMode();
        syncViewportHeight();
        resetScrollPosition();

        window.requestAnimationFrame(function () {
            syncViewportHeight();
            resetScrollPosition();
        });

        window.setTimeout(function () {
            syncViewportHeight();
            resetScrollPosition();
        }, 120);

        window.setTimeout(function () {
            syncViewportHeight();
            resetScrollPosition();
        }, 320);
    }

    syncViewportHeight();

    window.addEventListener(
        "resize",
        syncViewportHeight
    );

    window.addEventListener(
        "orientationchange",
        function () {
            window.setTimeout(function () {
                syncPwaMode();
                syncViewportHeight();
            }, 100);
        }
    );

    window.addEventListener(
        "pageshow",
        function () {
            syncPwaMode();
            settleMobileViewport();
        }
    );

    window.addEventListener(
        "load",
        function () {
            syncPwaMode();
            settleMobileViewport();
        }
    );

    if (window.visualViewport) {
        window.visualViewport.addEventListener(
            "resize",
            syncViewportHeight
        );

        window.visualViewport.addEventListener(
            "scroll",
            syncViewportHeight
        );
    }

    bottomNavLinks.forEach(function (link) {
        link.addEventListener("click", function () {
            window.sessionStorage.setItem(
                "mobileNavSettling",
                "1"
            );

            settleMobileViewport();
        });
    });

    if (
        window.sessionStorage.getItem(
            "mobileNavSettling"
        ) === "1"
    ) {
        window.sessionStorage.removeItem(
            "mobileNavSettling"
        );

        settleMobileViewport();
    }

    /*
     * 전역 Loading UI가 없으면
     * 아래 로딩 관련 코드는 실행하지 않음
     */
    if (!loading) {
        return;
    }

    function showLoading(title, message) {
        const titleEl = loading.querySelector(
            ".loading-title"
        );

        const messageEl = loading.querySelector(
            ".loading-message"
        );

        if (titleEl) {
            titleEl.textContent =
                title || "Processing...";
        }

        if (messageEl) {
            messageEl.textContent =
                message || "Please wait.";
        }

        loading.classList.remove("d-none");
        loading.setAttribute("aria-hidden", "false");
    }

    function hideLoading() {
        loading.classList.add("d-none");
        loading.setAttribute("aria-hidden", "true");
    }

    window.addEventListener("pageshow", hideLoading);
    window.addEventListener("popstate", hideLoading);

    document
        .querySelectorAll("form")
        .forEach(function (form) {
            form.addEventListener(
                "submit",
                function () {
                    if (
                        form.dataset.localSubmit ===
                        "true"
                    ) {
                        return;
                    }

                    if (
                        form.dataset.submitted ===
                        "true"
                    ) {
                        return;
                    }

                    form.dataset.submitted = "true";

                    const title =
                        form.dataset.loadingTitle ||
                        "Processing...";

                    const message =
                        form.dataset.loadingMessage ||
                        "Please wait.";

                    showLoading(title, message);

                    form.querySelectorAll(
                        [
                            "button[type='submit']",
                            "input[type='submit']",
                        ].join(",")
                    ).forEach(function (button) {
                        button.disabled = true;

                        button.setAttribute(
                            "aria-disabled",
                            "true"
                        );
                    });
                }
            );
        });

    document
        .querySelectorAll(
            "a[data-loading='true']"
        )
        .forEach(function (link) {
            link.addEventListener(
                "click",
                function () {
                    const title =
                        link.dataset.loadingTitle ||
                        "Loading...";

                    const message =
                        link.dataset.loadingMessage ||
                        "Please wait.";

                    showLoading(title, message);
                }
            );
        });

    document
        .querySelectorAll("a[href]")
        .forEach(function (link) {
            link.addEventListener(
                "click",
                function () {
                    const href =
                        link.getAttribute("href") || "";

                    if (
                        link.dataset.loading ||
                        link.dataset.viewerMatchLink ||
                        link.target === "_blank" ||
                        link.hasAttribute("download") ||
                        href.startsWith("#") ||
                        href.startsWith(
                            "javascript:"
                        ) ||
                        href.startsWith("mailto:") ||
                        href.startsWith("tel:")
                    ) {
                        return;
                    }

                    let targetUrl;

                    try {
                        targetUrl = new URL(
                            href,
                            window.location.href
                        );
                    } catch (error) {
                        return;
                    }

                    const isViewerLink =
                        targetUrl.pathname.includes(
                            "/pdf/viewer/"
                        ) ||
                        targetUrl.pathname.includes(
                            "/viewer/"
                        );

                    const isInternalLink =
                        targetUrl.origin ===
                        window.location.origin;

                    if (
                        !isViewerLink ||
                        !isInternalLink
                    ) {
                        return;
                    }

                    showLoading(
                        link.dataset.loadingTitle ||
                            "Opening Viewer",
                        link.dataset.loadingMessage ||
                            "Loading PDF viewer..."
                    );
                }
            );
        });
});


/*
 * Service Worker 등록
 */
if ("serviceWorker" in navigator) {
    window.addEventListener(
        "load",
        function () {
            navigator.serviceWorker
                .register("/service-worker.js")
                .catch(function (error) {
                    console.warn(
                        "Service Worker registration failed:",
                        error
                    );
                });
        }
    );
}