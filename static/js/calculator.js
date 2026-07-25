(function () {
    const result = document.getElementById("date-result");
    const longResult = document.getElementById("date-result-long");
    const note = document.getElementById("date-result-note");
    const input = document.getElementById("days-input");
    const baseDateInput = document.getElementById("base-date-input");
    const form = document.getElementById("date-calculator-form");
    const dateCalculatorModal = document.getElementById("date-calculator-modal");
    const fuelDensityForm = document.getElementById("fuel-density-form");
    const fuelDensityModal = document.getElementById("fuel-density-modal");
    const fuelDensityInput = document.getElementById("fuel-density-input");
    const fuelDensityResult = document.getElementById("fuel-density-result");
    const fuelDensityNote = document.getElementById("fuel-density-note");
    const timeCalculatorForm = document.getElementById("time-calculator-form");
    const timeCalculatorModal = document.getElementById("time-calculator-modal");
    const timeBaseInput = document.getElementById("time-base-input");
    const timeAddInput = document.getElementById("time-add-input");
    const timeCalculatorResult = document.getElementById("time-calculator-result");
    const timeCalculatorNote = document.getElementById("time-calculator-note");
    const consumptionRateForm = document.getElementById("consumption-rate-form");
    const consumptionRateModal = document.getElementById("consumption-rate-modal");
    const consumptionAddQtyInput = document.getElementById("consumption-add-qty-input");
    const consumptionHoursInput = document.getElementById("consumption-hours-input");
    const consumptionRateResult = document.getElementById("consumption-rate-result");
    const consumptionRateNote = document.getElementById("consumption-rate-note");
    const basicCalculatorDisplay = document.getElementById("basic-calculator-display");
    const basicCalculatorExpression = document.getElementById("basic-calculator-expression");
    const basicCalculatorModal = document.getElementById("basic-calculator-modal");
    const basicCalculatorKeys = document.querySelectorAll(
        "[data-basic-calc-number], [data-basic-calc-operator], [data-basic-calc-action]"
    );
    const localTime = document.getElementById("local-time");
    const utcTime = document.getElementById("utc-time");
    const presetButtons = document.querySelectorAll(".date-preset-btn");

    if (!result || !longResult || !note || !input || !baseDateInput) {
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
    let basicCurrentValue = "0";
    let basicStoredValue = null;
    let basicPendingOperator = null;
    let basicShouldResetDisplay = false;

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

    function formatDateTimeParts(date, useUtc) {
        const year = useUtc ? date.getUTCFullYear() : date.getFullYear();
        const month = englishMonths[
            useUtc ? date.getUTCMonth() : date.getMonth()
        ];
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

        return {
            date: `${day} ${month} ${year}`,
            time: `${hours}:${minutes}:${seconds}${useUtc ? " UTC" : ""}`,
        };
    }

    function renderClockValue(element, parts) {
        element.innerHTML = "";

        const dateLine = document.createElement("span");
        dateLine.className = "date-clock-date";
        dateLine.textContent = parts.date;

        const timeLine = document.createElement("span");
        timeLine.className = "date-clock-time";
        timeLine.textContent = parts.time;

        element.append(dateLine, timeLine);
    }

    function updateClock() {
        if (!localTime || !utcTime) {
            return;
        }

        const now = new Date();
        renderClockValue(localTime, formatDateTimeParts(now, false));
        renderClockValue(utcTime, formatDateTimeParts(now, true));
    }

    function updateDate(days, options) {
        const settings = options || {};
        const parsedDays = Number.parseInt(days, 10);
        const baseDateText = baseDateInput.value || todayText;
        const normalizedDays = Number.isFinite(parsedDays) && parsedDays >= 0
            ? parsedDays
            : 0;

        if ((days || "").toString().trim() && parsedDays < 0) {
            return;
        }

        const baseDate = new Date(`${baseDateText}T00:00:00`);
        const targetDate = new Date(baseDate);
        targetDate.setDate(baseDate.getDate() + normalizedDays);

        result.textContent = formatDate(targetDate);
        longResult.textContent = formatLongDate(targetDate);
        note.textContent = normalizedDays === 0
            ? `${baseDateText} 기준 날짜`
            : `${baseDateText} 기준, 선택 날짜 제외 ${normalizedDays}일 후`;

        if (!settings.keepInputEmpty) {
            input.value = normalizedDays;
        }

        presetButtons.forEach((button) => {
            button.classList.toggle(
                "active",
                Number.parseInt(button.dataset.days, 10) === normalizedDays
            );
        });
    }

    function resetDateCalculator() {
        if (!input) {
            return;
        }

        input.value = "";
        updateDate(0, { keepInputEmpty: true });
    }

    function updateFuelDensity() {
        if (!fuelDensityInput || !fuelDensityResult || !fuelDensityNote) {
            return;
        }

        const density = Number.parseFloat(fuelDensityInput.value);

        if (!Number.isFinite(density) || density < 0) {
            fuelDensityResult.textContent = "-";
            fuelDensityNote.textContent = "Density g/cm3 값을 입력하세요";
            return;
        }

        const poundsPerGallon = density * (3.78533 / 0.4539);
        fuelDensityResult.textContent = poundsPerGallon.toFixed(2);
        fuelDensityNote.textContent = `${density.toFixed(3)} g/cm3 기준`;
    }

    function resetFuelDensity() {
        if (!fuelDensityInput) {
            return;
        }

        fuelDensityInput.value = "0";
        updateFuelDensity();
    }

    function parseHourMinute(value) {
        const normalizedValue = String(value || "").trim();

        if (!normalizedValue) {
            return 0;
        }

        const colonMatch = normalizedValue.match(/^(\d+):(\d{1,2})$/);

        if (colonMatch) {
            return (Number.parseInt(colonMatch[1], 10) * 60)
                + Number.parseInt(colonMatch[2], 10);
        }

        const digitMatch = normalizedValue.match(/^\d+$/);

        if (digitMatch) {
            const hoursText = normalizedValue.slice(0, -2) || "0";
            const minutesText = normalizedValue.slice(-2);

            return (Number.parseInt(hoursText, 10) * 60)
                + Number.parseInt(minutesText, 10);
        }

        return null;
    }

    function formatHourMinute(totalMinutes) {
        const hours = Math.floor(totalMinutes / 60);
        const minutes = String(totalMinutes % 60).padStart(2, "0");
        return `${hours}:${minutes}`;
    }

    function updateTimeCalculator() {
        if (
            !timeBaseInput
            || !timeAddInput
            || !timeCalculatorResult
            || !timeCalculatorNote
        ) {
            return;
        }

        const baseMinutes = parseHourMinute(timeBaseInput.value);
        const addMinutes = parseHourMinute(timeAddInput.value);

        if (baseMinutes === null || addMinutes === null) {
            timeCalculatorResult.textContent = "-";
            timeCalculatorNote.textContent = "HH:MM 또는 숫자 형식으로 입력하세요";
            return;
        }

        timeCalculatorResult.textContent = formatHourMinute(
            baseMinutes + addMinutes
        );
        const baseLabel = timeBaseInput.value.trim() || "0";
        const addLabel = timeAddInput.value.trim() || "0";
        timeCalculatorNote.textContent = `${baseLabel} + ${addLabel} 기준`;
    }

    function resetTimeCalculator() {
        if (!timeBaseInput || !timeAddInput) {
            return;
        }

        timeBaseInput.value = "";
        timeAddInput.value = "";
        updateTimeCalculator();
    }

    function parseBlockHours(value) {
        const normalizedValue = String(value || "").trim();

        if (!normalizedValue) {
            return 0;
        }

        const colonMatch = normalizedValue.match(/^(\d+):(\d{1,2})$/);

        if (colonMatch) {
            const hours = Number.parseInt(colonMatch[1], 10);
            const minutes = Number.parseInt(colonMatch[2], 10);

            return minutes < 60 ? hours + (minutes / 60) : null;
        }

        if (!/^\d+(?:\.\d+)?$/.test(normalizedValue)) {
            return null;
        }

        const decimalHours = Number(normalizedValue);

        return Number.isFinite(decimalHours) && decimalHours >= 0
            ? decimalHours
            : null;
    }

    function updateConsumptionRate() {
        if (
            !consumptionAddQtyInput
            || !consumptionHoursInput
            || !consumptionRateResult
            || !consumptionRateNote
        ) {
            return;
        }

        const quantityText = consumptionAddQtyInput.value.trim();
        const hoursText = consumptionHoursInput.value.trim();

        if (!quantityText && !hoursText) {
            consumptionRateResult.textContent = "0.000 qty/hr";
            consumptionRateNote.textContent = "Add Qty와 Total Block Time을 입력하세요";
            return;
        }

        const quantity = Number(quantityText);
        const blockHours = parseBlockHours(hoursText);

        if (!Number.isFinite(quantity) || quantity < 0) {
            consumptionRateResult.textContent = "-";
            consumptionRateNote.textContent = "Add Qty를 올바르게 입력하세요";
            return;
        }

        if (blockHours === null || blockHours <= 0) {
            consumptionRateResult.textContent = "-";
            consumptionRateNote.textContent = "Total Block Time은 0보다 커야 합니다";
            return;
        }

        const rate = quantity / blockHours;
        consumptionRateResult.textContent = `${rate.toFixed(3)} qty/hr`;
        consumptionRateNote.textContent = `${quantityText} ÷ ${hoursText} hr`;
    }

    function resetConsumptionRate() {
        if (!consumptionAddQtyInput || !consumptionHoursInput) {
            return;
        }

        consumptionAddQtyInput.value = "";
        consumptionHoursInput.value = "";
        updateConsumptionRate();
    }

    function formatBasicNumber(value) {
        if (!Number.isFinite(value)) {
            return "Error";
        }

        const rounded = Math.round((value + Number.EPSILON) * 10000000000) / 10000000000;
        return String(rounded);
    }

    function renderBasicCalculator() {
        if (!basicCalculatorDisplay || !basicCalculatorExpression) {
            return;
        }

        const operatorLabelMap = {
            "+": "+",
            "-": "−",
            "*": "×",
            "/": "÷",
        };

        basicCalculatorDisplay.textContent = basicCurrentValue;
        basicCalculatorExpression.textContent = basicPendingOperator
            ? `${basicStoredValue} ${operatorLabelMap[basicPendingOperator]}`
            : "";
    }

    function calculateBasicValue(firstValue, operator, secondValue) {
        const first = Number.parseFloat(firstValue);
        const second = Number.parseFloat(secondValue);

        if (!Number.isFinite(first) || !Number.isFinite(second)) {
            return "Error";
        }

        if (operator === "+") {
            return formatBasicNumber(first + second);
        }

        if (operator === "-") {
            return formatBasicNumber(first - second);
        }

        if (operator === "*") {
            return formatBasicNumber(first * second);
        }

        if (operator === "/") {
            return second === 0 ? "Error" : formatBasicNumber(first / second);
        }

        return basicCurrentValue;
    }

    function inputBasicNumber(numberValue) {
        if (basicCurrentValue === "Error" || basicShouldResetDisplay) {
            basicCurrentValue = numberValue;
            basicShouldResetDisplay = false;
            return;
        }

        if (basicCurrentValue === "0") {
            basicCurrentValue = numberValue;
            return;
        }

        basicCurrentValue += numberValue;
    }

    function inputBasicDecimal() {
        if (basicCurrentValue === "Error" || basicShouldResetDisplay) {
            basicCurrentValue = "0.";
            basicShouldResetDisplay = false;
            return;
        }

        if (!basicCurrentValue.includes(".")) {
            basicCurrentValue += ".";
        }
    }

    function handleBasicOperator(operator) {
        if (basicCurrentValue === "Error") {
            return;
        }

        if (basicStoredValue !== null && basicPendingOperator && !basicShouldResetDisplay) {
            basicCurrentValue = calculateBasicValue(
                basicStoredValue,
                basicPendingOperator,
                basicCurrentValue
            );
        }

        basicStoredValue = basicCurrentValue;
        basicPendingOperator = operator;
        basicShouldResetDisplay = true;
    }

    function handleBasicAction(action) {
        if (action === "clear") {
            resetBasicCalculator();
            return;
        }

        if (action === "backspace") {
            if (basicShouldResetDisplay || basicCurrentValue === "Error") {
                basicCurrentValue = "0";
                basicShouldResetDisplay = false;
                return;
            }

            basicCurrentValue = basicCurrentValue.length > 1
                ? basicCurrentValue.slice(0, -1)
                : "0";
            return;
        }

        if (action === "percent") {
            if (basicCurrentValue !== "Error") {
                basicCurrentValue = formatBasicNumber(Number.parseFloat(basicCurrentValue) / 100);
            }
            return;
        }

        if (action === "toggle-sign") {
            if (basicCurrentValue !== "0" && basicCurrentValue !== "Error") {
                basicCurrentValue = basicCurrentValue.startsWith("-")
                    ? basicCurrentValue.slice(1)
                    : `-${basicCurrentValue}`;
            }
            return;
        }

        if (action === "decimal") {
            inputBasicDecimal();
            return;
        }

        if (action === "equals" && basicStoredValue !== null && basicPendingOperator) {
            basicCurrentValue = calculateBasicValue(
                basicStoredValue,
                basicPendingOperator,
                basicCurrentValue
            );
            basicStoredValue = null;
            basicPendingOperator = null;
            basicShouldResetDisplay = true;
        }
    }

    function resetBasicCalculator() {
        basicCurrentValue = "0";
        basicStoredValue = null;
        basicPendingOperator = null;
        basicShouldResetDisplay = false;
    }

    presetButtons.forEach((button) => {
        button.addEventListener("click", () => {
            updateDate(button.dataset.days);
        });
    });

    input.addEventListener("input", () => {
        updateDate(input.value, { keepInputEmpty: input.value.trim() === "" });
    });

    baseDateInput.addEventListener("input", () => {
        updateDate(input.value, { keepInputEmpty: input.value.trim() === "" });
    });

    if (form) {
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            updateDate(input.value, { keepInputEmpty: input.value.trim() === "" });
        });
    }

    if (dateCalculatorModal) {
        dateCalculatorModal.addEventListener("hidden.bs.modal", resetDateCalculator);
    }

    if (fuelDensityInput) {
        fuelDensityInput.addEventListener("input", updateFuelDensity);
    }

    if (fuelDensityForm) {
        fuelDensityForm.addEventListener("submit", (event) => {
            event.preventDefault();
            updateFuelDensity();
        });
    }

    if (fuelDensityModal) {
        fuelDensityModal.addEventListener("hidden.bs.modal", resetFuelDensity);
    }

    if (timeBaseInput) {
        timeBaseInput.addEventListener("input", updateTimeCalculator);
    }

    if (timeAddInput) {
        timeAddInput.addEventListener("input", updateTimeCalculator);
    }

    if (timeCalculatorForm) {
        timeCalculatorForm.addEventListener("submit", (event) => {
            event.preventDefault();
            updateTimeCalculator();
        });
    }

    if (timeCalculatorModal) {
        timeCalculatorModal.addEventListener("hidden.bs.modal", resetTimeCalculator);
    }

    if (consumptionAddQtyInput) {
        consumptionAddQtyInput.addEventListener("input", updateConsumptionRate);
    }

    if (consumptionHoursInput) {
        consumptionHoursInput.addEventListener("input", updateConsumptionRate);
    }

    if (consumptionRateForm) {
        consumptionRateForm.addEventListener("submit", (event) => {
            event.preventDefault();
            updateConsumptionRate();
        });
    }

    if (consumptionRateModal) {
        consumptionRateModal.addEventListener("hidden.bs.modal", resetConsumptionRate);
    }

    basicCalculatorKeys.forEach((button) => {
        button.addEventListener("click", () => {
            if (button.dataset.basicCalcNumber !== undefined) {
                inputBasicNumber(button.dataset.basicCalcNumber);
            } else if (button.dataset.basicCalcOperator) {
                handleBasicOperator(button.dataset.basicCalcOperator);
            } else if (button.dataset.basicCalcAction) {
                handleBasicAction(button.dataset.basicCalcAction);
            }

            renderBasicCalculator();
        });
    });

    if (basicCalculatorModal) {
        basicCalculatorModal.addEventListener("hidden.bs.modal", () => {
            resetBasicCalculator();
            renderBasicCalculator();
        });
    }

    updateFuelDensity();
    updateTimeCalculator();
    updateConsumptionRate();
    renderBasicCalculator();
    updateClock();
    window.setInterval(updateClock, 1000);
})();
