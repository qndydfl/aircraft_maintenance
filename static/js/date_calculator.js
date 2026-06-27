(function () {
    const result = document.getElementById("date-result");
    const longResult = document.getElementById("date-result-long");
    const note = document.getElementById("date-result-note");
    const input = document.getElementById("days-input");
    const localTime = document.getElementById("local-time");
    const utcTime = document.getElementById("utc-time");
    const presetButtons = document.querySelectorAll(".date-preset-btn");

    if (!result || !longResult || !note || !input) {
        return;
    }

    const todayText = result.dataset.today;
    const englishMonths = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ];

    function formatDate(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, "0");
        const day = String(date.getDate()).padStart(2, "0");
        return `${year}-${month}-${day}`;
    }

    function formatLongDate(date) {
        const day = String(date.getDate()).padStart(2, "0");
        const month = englishMonths[date.getMonth()];
        const year = date.getFullYear();
        return `${day} ${month} ${year}`;
    }

    function formatDateTime(date, useUtc) {
        const year = useUtc ? date.getUTCFullYear() : date.getFullYear();
        const month = String(
            (useUtc ? date.getUTCMonth() : date.getMonth()) + 1
        ).padStart(2, "0");
        const day = String(
            useUtc ? date.getUTCDate() : date.getDate()
        ).padStart(2, "0");
        const hours = String(
            useUtc ? date.getUTCHours() : date.getHours()
        ).padStart(2, "0");
        const minutes = String(
            useUtc ? date.getUTCMinutes() : date.getMinutes()
        ).padStart(2, "0");
        const seconds = String(
            useUtc ? date.getUTCSeconds() : date.getSeconds()
        ).padStart(2, "0");

        return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
    }

    function updateClock() {
        if (!localTime || !utcTime) {
            return;
        }

        const now = new Date();
        localTime.textContent = formatDateTime(now, false);
        utcTime.textContent = `${formatDateTime(now, true)} UTC`;
    }

    function updateDate(days) {
        const parsedDays = Number.parseInt(days, 10);

        if (!Number.isFinite(parsedDays) || parsedDays < 1) {
            return;
        }

        const today = new Date(`${todayText}T00:00:00`);
        const targetDate = new Date(today);
        targetDate.setDate(today.getDate() + parsedDays);

        result.textContent = formatDate(targetDate);
        longResult.textContent = formatLongDate(targetDate);
        note.textContent = `${todayText} 기준, 오늘 제외 ${parsedDays}일 후`;
        input.value = parsedDays;

        presetButtons.forEach((button) => {
            button.classList.toggle(
                "active",
                Number.parseInt(button.dataset.days, 10) === parsedDays
            );
        });
    }

    presetButtons.forEach((button) => {
        button.addEventListener("click", () => {
            updateDate(button.dataset.days);
        });
    });

    input.addEventListener("input", () => {
        updateDate(input.value);
    });

    updateClock();
    window.setInterval(updateClock, 1000);
})();
