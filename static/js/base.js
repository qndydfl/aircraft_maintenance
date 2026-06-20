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

    document.querySelectorAll("form").forEach(function (form) {
        form.addEventListener("submit", function () {
            const title = form.dataset.loadingTitle || "Processing...";
            const message = form.dataset.loadingMessage || "Please wait.";

            showLoading(title, message);
        });
    });

    document.querySelectorAll("a[data-loading='true']").forEach(function (link) {
        link.addEventListener("click", function () {
            const title = link.dataset.loadingTitle || "Loading...";
            const message = link.dataset.loadingMessage || "Please wait.";

            showLoading(title, message);
        });
    });
});