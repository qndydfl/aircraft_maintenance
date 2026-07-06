document.addEventListener("DOMContentLoaded", function () {
    const filter = document.getElementById("manual-search-filter");
    const prompt = document.querySelector(".manual-search-empty-prompt");

    if (!filter || !prompt) {
        return;
    }

    const hidePrompt = function () {
        prompt.classList.add("d-none");
    };
    const showPrompt = function () {
        prompt.classList.remove("d-none");
    };

    if (filter.classList.contains("show")) {
        hidePrompt();
    } else {
        showPrompt();
    }

    filter.addEventListener("show.bs.collapse", hidePrompt);
    filter.addEventListener("shown.bs.collapse", hidePrompt);
    filter.addEventListener("hide.bs.collapse", showPrompt);
    filter.addEventListener("hidden.bs.collapse", showPrompt);
});