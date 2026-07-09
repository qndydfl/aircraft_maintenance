document.addEventListener("DOMContentLoaded", function () {
    const loading = document.getElementById("global-loading");
    const bottomNav = document.querySelector(".mobile-bottom-nav");
    const bottomNavLinks = document.querySelectorAll(".mobile-bottom-nav a");

    if ("scrollRestoration" in window.history) {
        window.history.scrollRestoration = "manual";
    }

    function syncViewportHeight() {
        const viewport = window.visualViewport;
        const viewportHeight = viewport ? viewport.height : window.innerHeight;
        const viewportOffsetTop = viewport ? viewport.offsetTop : 0;
        const viewportBottomOffset = Math.max(
            0,
            window.innerHeight - viewportHeight - viewportOffsetTop
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

    function settleMobileViewport() {
        syncViewportHeight();
        window.scrollTo(0, 0);

        window.requestAnimationFrame(function () {
            syncViewportHeight();
            window.scrollTo(0, 0);
        });

        window.setTimeout(function () {
            syncViewportHeight();
            window.scrollTo(0, 0);
        }, 120);

        window.setTimeout(function () {
            syncViewportHeight();
            window.scrollTo(0, 0);
        }, 320);
    }

    syncViewportHeight();
    window.addEventListener("resize", syncViewportHeight);
    window.addEventListener("orientationchange", syncViewportHeight);
    window.addEventListener("pageshow", settleMobileViewport);
    window.addEventListener("load", settleMobileViewport);

    if (window.visualViewport) {
        window.visualViewport.addEventListener("resize", syncViewportHeight);
        window.visualViewport.addEventListener("scroll", syncViewportHeight);
    }

    bottomNavLinks.forEach(function (link) {
        link.addEventListener("click", function () {
            window.sessionStorage.setItem("mobileNavSettling", "1");
            settleMobileViewport();
        });
    });

    if (window.sessionStorage.getItem("mobileNavSettling") === "1") {
        window.sessionStorage.removeItem("mobileNavSettling");
        settleMobileViewport();
    }

    if (!loading) {
        return;
    }

    function showLoading(title, message) {
        const titleEl = loading.querySelector(".loading-title");
        const messageEl = loading.querySelector(".loading-message");

        titleEl.textContent = title || "Processing...";
        messageEl.textContent = message || "Please wait.";

        loading.classList.remove("d-none");
    }

    function hideLoading() {
        loading.classList.add("d-none");
    }

    window.addEventListener("pageshow", hideLoading);
    window.addEventListener("popstate", hideLoading);

    document.querySelectorAll("form").forEach(function (form) {
        form.addEventListener("submit", function () {
            if (form.dataset.localSubmit === "true") {
                return;
            }

            if (form.dataset.submitted === "true") {
                return;
            }

            form.dataset.submitted = "true";

            const title = form.dataset.loadingTitle || "Processing...";
            const message = form.dataset.loadingMessage || "Please wait.";

            showLoading(title, message);

            form.querySelectorAll("button[type='submit'], input[type='submit']").forEach(function (button) {
                button.disabled = true;
                button.setAttribute("aria-disabled", "true");
            });
        });
    });

    document.querySelectorAll("a[data-loading='true']").forEach(function (link) {
        link.addEventListener("click", function () {
            const title = link.dataset.loadingTitle || "Loading...";
            const message = link.dataset.loadingMessage || "Please wait.";

            showLoading(title, message);
        });
    });

    document.querySelectorAll("a[href]").forEach(function (link) {
        link.addEventListener("click", function () {
            const href = link.getAttribute("href") || "";

            if (
                link.dataset.loading ||
                link.dataset.viewerMatchLink ||
                link.target === "_blank" ||
                link.hasAttribute("download") ||
                href.startsWith("#") ||
                href.startsWith("javascript:")
            ) {
                return;
            }

            const targetUrl = new URL(href, window.location.href);
            const isViewerLink =
                targetUrl.pathname.includes("/pdf/viewer/") ||
                targetUrl.pathname.includes("/viewer/");

            if (!isViewerLink || targetUrl.origin !== window.location.origin) {
                return;
            }

            showLoading(
                link.dataset.loadingTitle || "Opening Viewer",
                link.dataset.loadingMessage || "Loading PDF viewer..."
            );
        });
    });
});

if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
        navigator.serviceWorker.register("/service-worker.js").catch(function () {
            // PWA support is optional, so the site should continue normally.
        });
    });
}
