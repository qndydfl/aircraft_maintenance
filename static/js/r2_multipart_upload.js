document.addEventListener("DOMContentLoaded", function () {
    const forms = document.querySelectorAll("form[data-r2-direct-upload='true']");

    function csrfToken(form) {
        const input = form.querySelector("input[name='csrfmiddlewaretoken']");
        return input ? input.value : "";
    }

    async function postJson(form, url, payload) {
        const response = await fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken(form),
            },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(function () {
            return {};
        });

        if (!response.ok || !data.success) {
            throw new Error(data.message || "R2 업로드 요청을 처리하지 못했습니다.");
        }

        return data;
    }

    function uploadPart(url, blob, onProgress) {
        return new Promise(function (resolve, reject) {
            const request = new XMLHttpRequest();
            request.open("PUT", url, true);

            request.upload.addEventListener("progress", function (event) {
                if (event.lengthComputable) {
                    onProgress(event.loaded);
                }
            });

            request.addEventListener("load", function () {
                if (request.status >= 200 && request.status < 300) {
                    const etag = request.getResponseHeader("ETag");

                    if (!etag) {
                        reject(new Error("R2 응답에서 ETag를 확인하지 못했습니다. CORS 설정을 확인해 주세요."));
                        return;
                    }

                    resolve(etag);
                    return;
                }

                reject(new Error("R2 Part 업로드 실패 (HTTP " + request.status + ")"));
            });
            request.addEventListener("error", function () {
                reject(new Error("R2 연결 중 네트워크 오류가 발생했습니다."));
            });
            request.addEventListener("abort", function () {
                reject(new Error("R2 업로드가 취소되었습니다."));
            });
            request.send(blob);
        });
    }

    async function uploadPartWithRetry(url, blob, onProgress) {
        let lastError;

        for (let attempt = 1; attempt <= 3; attempt += 1) {
            try {
                return await uploadPart(url, blob, onProgress);
            } catch (error) {
                lastError = error;

                if (attempt < 3) {
                    await new Promise(function (resolve) {
                        window.setTimeout(resolve, attempt * 1000);
                    });
                }
            }
        }

        throw lastError;
    }

    forms.forEach(function (form) {
        const fileInput = form.querySelector("input[type='file']");
        const objectKeyInput = form.querySelector("[name='r2_object_key']");
        const originalNameInput = form.querySelector("[name='r2_original_name']");
        const fileSizeInput = form.querySelector("[name='r2_file_size']");
        const progressWrap = form.querySelector("[data-r2-progress]");
        const progressBar = form.querySelector("[data-r2-progress-bar]");
        const progressText = form.querySelector("[data-r2-progress-text]");
        const submitButton = form.querySelector("button[type='submit']");

        if (!fileInput || !objectKeyInput || !progressWrap || !progressBar) {
            return;
        }

        function setProgress(percent, message) {
            const normalized = Math.max(0, Math.min(100, Math.round(percent)));
            progressWrap.classList.remove("d-none");
            progressBar.style.width = normalized + "%";
            progressBar.setAttribute("aria-valuenow", String(normalized));
            progressBar.textContent = normalized + "%";

            if (progressText) {
                progressText.textContent = message || "Uploading...";
            }
        }

        function setBusy(isBusy) {
            fileInput.disabled = isBusy;

            if (submitButton) {
                submitButton.disabled = isBusy;
            }
        }

        form.addEventListener("submit", async function (event) {
            if (form.dataset.r2Submitting === "true") {
                return;
            }

            const file = fileInput.files && fileInput.files[0];

            if (!file && !objectKeyInput.value) {
                return;
            }

            event.preventDefault();
            event.stopImmediatePropagation();

            if (!file && objectKeyInput.value) {
                fileInput.disabled = true;
                form.dataset.r2Submitting = "true";
                HTMLFormElement.prototype.submit.call(form);
                return;
            }

            let uploadId = "";
            let objectKey = "";

            try {
                setBusy(true);
                progressBar.classList.remove("bg-danger");
                setProgress(0, "R2 Multipart Upload를 준비하고 있습니다...");

                const aircraftInput = form.querySelector("[name='aircraft']");
                const manualTypeInput = form.querySelector("[name='manual_type']");
                const initiate = await postJson(form, form.dataset.r2InitiateUrl, {
                    filename: file.name,
                    file_size: file.size,
                    upload_type: form.dataset.r2UploadType,
                    aircraft_id: aircraftInput ? aircraftInput.value : "",
                    manual_type: manualTypeInput ? manualTypeInput.value : "",
                });

                uploadId = initiate.upload_id;
                objectKey = initiate.object_key;
                const parts = [];
                let completedBytes = 0;

                for (let partNumber = 1; partNumber <= initiate.part_count; partNumber += 1) {
                    const start = (partNumber - 1) * initiate.chunk_size;
                    const end = Math.min(start + initiate.chunk_size, file.size);
                    const blob = file.slice(start, end);
                    const partInfo = await postJson(form, form.dataset.r2PartUrl, {
                        object_key: objectKey,
                        upload_id: uploadId,
                        part_number: partNumber,
                    });

                    const etag = await uploadPartWithRetry(
                        partInfo.upload_url,
                        blob,
                        function (loadedBytes) {
                            const percent = ((completedBytes + loadedBytes) / file.size) * 100;
                            setProgress(
                                percent,
                                "Part " + partNumber + " / " + initiate.part_count + " 업로드 중"
                            );
                        }
                    );

                    completedBytes += blob.size;
                    parts.push({
                        part_number: partNumber,
                        etag: etag,
                    });
                }

                setProgress(100, "R2에서 파일을 결합하고 있습니다...");
                await postJson(form, form.dataset.r2CompleteUrl, {
                    object_key: objectKey,
                    upload_id: uploadId,
                    parts: parts,
                });

                objectKeyInput.value = objectKey;
                originalNameInput.value = file.name;
                fileSizeInput.value = String(file.size);
                fileInput.disabled = true;
                form.dataset.r2Submitting = "true";
                setProgress(100, "업로드 완료. 매뉴얼 정보를 저장하고 있습니다...");
                HTMLFormElement.prototype.submit.call(form);
            } catch (error) {
                if (uploadId && objectKey) {
                    postJson(form, form.dataset.r2AbortUrl, {
                        object_key: objectKey,
                        upload_id: uploadId,
                    }).catch(function () {
                        // R2 automatically expires incomplete multipart uploads.
                    });
                }

                setBusy(false);
                setProgress(0, error.message || "업로드에 실패했습니다.");
                progressBar.classList.add("bg-danger");
            }
        }, true);
    });
});
