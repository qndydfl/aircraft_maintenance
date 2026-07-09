document.addEventListener("DOMContentLoaded", function () {
    const filter = document.getElementById("manual-search-filter");
    const toggle = document.querySelector(
        '[data-bs-target="#manual-search-filter"]'
    );
    const label = toggle ? toggle.querySelector(".filter-toggle-label") : null;

    if (!filter || !toggle || !label) {
        return;
    }

    const syncToggleLabel = function () {
        const isOpen = filter.classList.contains("show");
        label.textContent = isOpen ? "Hide Search Options" : "Show Search Options";
        toggle.classList.toggle("collapsed", !isOpen);
        toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    };

    syncToggleLabel();
    filter.addEventListener("shown.bs.collapse", syncToggleLabel);
    filter.addEventListener("hidden.bs.collapse", syncToggleLabel);
});
