document.addEventListener("DOMContentLoaded", function () {
    const body = document.body;
    const sidebar = document.getElementById("app-sidebar");
    const openButtons = document.querySelectorAll("[data-sidebar-open]");
    const closeButtons = document.querySelectorAll("[data-sidebar-close]");
    const collapseButton = document.querySelector("[data-sidebar-collapse]");
    const mobileQuery = window.matchMedia("(max-width: 48rem)");

    if (!sidebar) {
        return;
    }

    function setDrawerOpen(isOpen) {
        body.classList.toggle("sidebar-drawer-open", isOpen);
        openButtons.forEach(function (button) {
            button.setAttribute("aria-expanded", isOpen ? "true" : "false");
        });
    }

    function syncCollapsedState() {
        if (mobileQuery.matches) {
            body.classList.remove("sidebar-collapsed");
            return;
        }

        body.classList.toggle(
            "sidebar-collapsed",
            window.localStorage.getItem("manualPortalSidebarCollapsed") === "1"
        );
    }

    openButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            setDrawerOpen(true);
        });
    });

    closeButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            setDrawerOpen(false);
        });
    });

    sidebar.querySelectorAll("a[href]").forEach(function (link) {
        link.addEventListener("click", function () {
            if (mobileQuery.matches) {
                setDrawerOpen(false);
            }
        });
    });

    const manualTreeSummary = sidebar.querySelector(
        ".sidebar-tree-root > summary"
    );

    if (manualTreeSummary) {
        manualTreeSummary.addEventListener("click", function (event) {
            if (!mobileQuery.matches && body.classList.contains("sidebar-collapsed")) {
                event.preventDefault();
                body.classList.remove("sidebar-collapsed");
                window.localStorage.setItem("manualPortalSidebarCollapsed", "0");
                manualTreeSummary.parentElement.open = true;
            }
        });
    }

    if (collapseButton) {
        collapseButton.addEventListener("click", function () {
            const collapsed = !body.classList.contains("sidebar-collapsed");
            body.classList.toggle("sidebar-collapsed", collapsed);
            window.localStorage.setItem(
                "manualPortalSidebarCollapsed",
                collapsed ? "1" : "0"
            );
            collapseButton.setAttribute(
                "aria-label",
                collapsed ? "Expand navigation" : "Collapse navigation"
            );
            collapseButton.title = collapsed
                ? "Expand navigation"
                : "Collapse navigation";
        });
    }

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            setDrawerOpen(false);
        }
    });

    function handleViewportChange() {
        setDrawerOpen(false);
        syncCollapsedState();
    }

    if (typeof mobileQuery.addEventListener === "function") {
        mobileQuery.addEventListener("change", handleViewportChange);
    } else {
        mobileQuery.addListener(handleViewportChange);
    }

    syncCollapsedState();
});
