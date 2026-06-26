document.addEventListener("DOMContentLoaded", function () {
    const loading = document.getElementById("global-loading");

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
