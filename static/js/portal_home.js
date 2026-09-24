document.addEventListener("DOMContentLoaded", () => {
    const body = document.body;

    const boot = document.getElementById("portalBoot");
    const bootBar = document.getElementById("bootProgressBar");
    const bootPercent = document.getElementById("bootPercent");
    const bootStatus = document.getElementById("bootStatus");

    const workspace = document.getElementById("commandWorkspace");
    const aircraftScene = document.getElementById("aircraftScene");

    const tiltPanels = document.querySelectorAll(
        "[data-tilt-panel]"
    );

    const reducedMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)"
    ).matches;

    const coarsePointer = window.matchMedia(
        "(hover: none) and (pointer: coarse)"
    ).matches;


    /* =====================================================
       Boot Sequence
    ====================================================== */

    const bootSteps = [
        {
            percent: 18,
            text: "INITIALIZING SYSTEM",
        },
        {
            percent: 36,
            text: "CHECKING MANUAL DATABASE",
        },
        {
            percent: 58,
            text: "LOADING PDF INDEX",
        },
        {
            percent: 78,
            text: "DISPATCH SYSTEM READY",
        },
        {
            percent: 100,
            text: "SYSTEM READY",
        },
    ];


    function updateBoot(step) {
        if (!bootBar || !bootPercent || !bootStatus) {
            return;
        }

        bootBar.style.width = `${step.percent}%`;
        bootPercent.textContent = `${step.percent}%`;
        bootStatus.textContent = step.text;
    }


    function finishBoot() {
        body.classList.add("command-ready");

        if (boot) {
            boot.classList.add("is-finished");
        }
    }


    function runBootSequence() {
        if (!boot) {
            body.classList.add("command-ready");
            return;
        }

        /*
         * Reduced motion:
         * immediately show dashboard.
         */
        if (reducedMotion) {
            updateBoot(bootSteps[bootSteps.length - 1]);

            window.setTimeout(() => {
                finishBoot();
            }, 100);

            return;
        }

        /*
         * Show full boot animation only once
         * per browser session.
         */
        const alreadyBooted = sessionStorage.getItem(
            "manualPortalBooted"
        );

        if (alreadyBooted === "1") {
            boot.classList.add("is-finished");
            body.classList.add("command-ready");
            return;
        }

        let index = 0;

        updateBoot({
            percent: 0,
            text: "STARTING SYSTEM",
        });

        const timer = window.setInterval(() => {
            if (index >= bootSteps.length) {
                window.clearInterval(timer);

                sessionStorage.setItem(
                    "manualPortalBooted",
                    "1"
                );

                window.setTimeout(() => {
                    finishBoot();
                }, 250);

                return;
            }

            updateBoot(bootSteps[index]);

            index += 1;
        }, 210);
    }


    runBootSequence();


    /* =====================================================
       Aircraft Mouse Parallax
    ====================================================== */

    function enableAircraftParallax() {
        if (
            reducedMotion ||
            coarsePointer ||
            !workspace ||
            !aircraftScene
        ) {
            return;
        }

        const depth = aircraftScene.querySelector(
            ".aircraft-scene-depth"
        );

        if (!depth) {
            return;
        }


        workspace.addEventListener(
            "mousemove",
            (event) => {
                const rect =
                    workspace.getBoundingClientRect();

                const mouseX =
                    (event.clientX - rect.left) /
                    rect.width;

                const mouseY =
                    (event.clientY - rect.top) /
                    rect.height;

                const normalizedX =
                    (mouseX - 0.5) * 2;

                const normalizedY =
                    (mouseY - 0.5) * 2;

                const rotateY =
                    normalizedX * 5;

                const rotateX =
                    normalizedY * -3;

                const moveX =
                    normalizedX * 6;

                const moveY =
                    normalizedY * 4;

                depth.style.transform = `
                    translate3d(
                        ${moveX}px,
                        ${moveY}px,
                        0
                    )
                    rotateX(${rotateX}deg)
                    rotateY(${rotateY}deg)
                `;
            }
        );


        workspace.addEventListener(
            "mouseleave",
            () => {
                depth.style.transform =
                    "translate3d(0, 0, 0) rotateX(0) rotateY(0)";
            }
        );
    }


    enableAircraftParallax();


    /* =====================================================
       HUD Card 3D Tilt
    ====================================================== */

    function enablePanelTilt() {
        if (
            reducedMotion ||
            coarsePointer
        ) {
            return;
        }

        tiltPanels.forEach((panel) => {

            panel.addEventListener(
                "mousemove",
                (event) => {
                    const rect =
                        panel.getBoundingClientRect();

                    const x =
                        event.clientX - rect.left;

                    const y =
                        event.clientY - rect.top;

                    const centerX =
                        rect.width / 2;

                    const centerY =
                        rect.height / 2;

                    const rotateY =
                        ((x - centerX) / centerX) * 3;

                    const rotateX =
                        ((centerY - y) / centerY) * 3;

                    panel.style.transform = `
                        perspective(700px)
                        rotateX(${rotateX}deg)
                        rotateY(${rotateY}deg)
                        translateY(-2px)
                    `;
                }
            );


            panel.addEventListener(
                "mouseleave",
                () => {
                    panel.style.transform = `
                        perspective(700px)
                        rotateX(0deg)
                        rotateY(0deg)
                        translateY(0)
                    `;
                }
            );

        });
    }


    enablePanelTilt();


    /* =====================================================
       Visibility Handling

       Stop unnecessary movement when browser tab
       is not visible.
    ====================================================== */

    document.addEventListener(
        "visibilitychange",
        () => {
            if (document.hidden) {
                body.classList.add(
                    "command-paused"
                );
            } else {
                body.classList.remove(
                    "command-paused"
                );
            }
        }
    );
});