document.addEventListener("DOMContentLoaded", function () {
    const filter = document.getElementById("manual-search-filter");
    const form = document.querySelector(".manual-search-form");
    const aircraftSelect = document.getElementById("manual-search-aircraft");
    const toggle = document.querySelector(
        '[data-bs-target="#manual-search-filter"]'
    );
    const label = toggle ? toggle.querySelector(".filter-toggle-label") : null;

    if (form && aircraftSelect) {
        aircraftSelect.addEventListener("change", function () {
            form.querySelectorAll('input[name="manual_type"]:checked')
                .forEach(function (input) {
                    input.checked = false;
                });
            form.requestSubmit();
        });
    }

    if (!filter || !toggle || !label) {
        return;
    }

    const syncToggleLabel = function () {
        const isOpen = filter.classList.contains("show");
        label.textContent = isOpen ? "Hide Filters" : "Show Filters";
        toggle.classList.toggle("collapsed", !isOpen);
        toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    };

    syncToggleLabel();
    filter.addEventListener("shown.bs.collapse", syncToggleLabel);
    filter.addEventListener("hidden.bs.collapse", syncToggleLabel);
});
