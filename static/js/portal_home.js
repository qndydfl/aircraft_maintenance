(() => {
    "use strict";

    const timeFormatters = {
        utc: new Intl.DateTimeFormat("en-GB", {
            timeZone: "UTC",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false,
        }),
        kst: new Intl.DateTimeFormat("en-GB", {
            timeZone: "Asia/Seoul",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false,
        }),
    };
    const dateFormatters = {
        utc: new Intl.DateTimeFormat("en-GB", {
            timeZone: "UTC",
            weekday: "short",
            day: "2-digit",
            month: "short",
            year: "numeric",
        }),
        kst: new Intl.DateTimeFormat("en-GB", {
            timeZone: "Asia/Seoul",
            weekday: "short",
            day: "2-digit",
            month: "short",
            year: "numeric",
        }),
    };

    const updateClocks = () => {
        const now = new Date();

        ["utc", "kst"].forEach((zone) => {
            const clock = document.querySelector(`[data-home-clock="${zone}"]`);
            const date = document.querySelector(`[data-home-date="${zone}"]`);

            if (clock) clock.textContent = timeFormatters[zone].format(now);
            if (date) date.textContent = dateFormatters[zone].format(now);
        });
    };

    updateClocks();
    window.setInterval(updateClocks, 1000);
})();
