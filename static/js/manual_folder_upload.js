(function () {
    "use strict";

    const ZIP_LIMIT = 0xffffffff;
    let crcTable = null;

    function getCrcTable() {
        if (crcTable) {
            return crcTable;
        }

        crcTable = new Uint32Array(256);

        for (let index = 0; index < 256; index += 1) {
            let value = index;

            for (let bit = 0; bit < 8; bit += 1) {
                value = (value & 1) ? (0xedb88320 ^ (value >>> 1)) : (value >>> 1);
            }

            crcTable[index] = value >>> 0;
        }

        return crcTable;
    }

    async function calculateCrc32(file, onProgress) {
        const table = getCrcTable();
        const reader = file.stream().getReader();
        let crc = 0xffffffff;

        while (true) {
            const result = await reader.read();

            if (result.done) {
                break;
            }

            for (const byte of result.value) {
                crc = table[(crc ^ byte) & 0xff] ^ (crc >>> 8);
            }

            onProgress(result.value.byteLength);
        }

        return (crc ^ 0xffffffff) >>> 0;
    }

    function normalizeRelativePath(file) {
        const rawPath = file.webkitRelativePath || file.name;
        return rawPath
            .replace(/\\/g, "/")
            .split("/")
            .filter(function (part) {
                return part && part !== "." && part !== "..";
            })
            .join("/");
    }

    function dosDateTime(lastModified) {
        const date = new Date(lastModified || Date.now());
        const year = Math.max(1980, date.getFullYear());

        return {
            date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
            time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
        };
    }

    function localHeader(entry) {
        const buffer = new ArrayBuffer(30 + entry.name.length);
        const view = new DataView(buffer);
        const bytes = new Uint8Array(buffer);
        view.setUint32(0, 0x04034b50, true);
        view.setUint16(4, 20, true);
        view.setUint16(6, 0x0800, true);
        view.setUint16(8, 0, true);
        view.setUint16(10, entry.time, true);
        view.setUint16(12, entry.date, true);
        view.setUint32(14, entry.crc, true);
        view.setUint32(18, entry.size, true);
        view.setUint32(22, entry.size, true);
        view.setUint16(26, entry.name.length, true);
        bytes.set(entry.name, 30);
        return bytes;
    }

    function centralHeader(entry) {
        const buffer = new ArrayBuffer(46 + entry.name.length);
        const view = new DataView(buffer);
        const bytes = new Uint8Array(buffer);
        view.setUint32(0, 0x02014b50, true);
        view.setUint16(4, 20, true);
        view.setUint16(6, 20, true);
        view.setUint16(8, 0x0800, true);
        view.setUint16(10, 0, true);
        view.setUint16(12, entry.time, true);
        view.setUint16(14, entry.date, true);
        view.setUint32(16, entry.crc, true);
        view.setUint32(20, entry.size, true);
        view.setUint32(24, entry.size, true);
        view.setUint16(28, entry.name.length, true);
        view.setUint32(42, entry.offset, true);
        bytes.set(entry.name, 46);
        return bytes;
    }

    function endRecord(entryCount, centralSize, centralOffset) {
        const buffer = new ArrayBuffer(22);
        const view = new DataView(buffer);
        view.setUint32(0, 0x06054b50, true);
        view.setUint16(8, entryCount, true);
        view.setUint16(10, entryCount, true);
        view.setUint32(12, centralSize, true);
        view.setUint32(16, centralOffset, true);
        return new Uint8Array(buffer);
    }

    async function createPackageZip(fileList, onProgress, archiveName) {
        const files = Array.from(fileList);

        if (!files.length) {
            throw new Error("업로드할 파일이 들어 있는 폴더를 선택해 주세요.");
        }

        if (files.length > 65535) {
            throw new Error("한 번에 업로드할 수 있는 파일 수는 65,535개 이하입니다.");
        }

        const totalBytes = files.reduce(function (sum, file) {
            return sum + file.size;
        }, 0);

        if (totalBytes >= ZIP_LIMIT || files.some(function (file) { return file.size >= ZIP_LIMIT; })) {
            throw new Error("폴더 업로드 크기는 4GB 미만이어야 합니다.");
        }

        const encoder = new TextEncoder();
        const entries = [];
        let scannedBytes = 0;
        let offset = 0;

        for (let index = 0; index < files.length; index += 1) {
            const file = files[index];
            const path = normalizeRelativePath(file);
            const name = encoder.encode(path);
            const timestamp = dosDateTime(file.lastModified);
            const crc = await calculateCrc32(file, function (bytes) {
                scannedBytes += bytes;
                const percent = totalBytes ? Math.round((scannedBytes / totalBytes) * 100) : 100;
                onProgress(percent, index + 1, files.length, path);
            });
            const entry = {
                file: file,
                name: name,
                crc: crc,
                size: file.size,
                date: timestamp.date,
                time: timestamp.time,
                offset: offset,
            };
            entries.push(entry);
            offset += 30 + name.length + file.size;

            if (offset >= ZIP_LIMIT) {
                throw new Error("생성되는 ZIP 크기는 4GB 미만이어야 합니다.");
            }
        }

        const parts = [];

        entries.forEach(function (entry) {
            parts.push(localHeader(entry), entry.file);
        });

        const centralOffset = offset;
        let centralSize = 0;

        entries.forEach(function (entry) {
            const header = centralHeader(entry);
            parts.push(header);
            centralSize += header.byteLength;
        });

        parts.push(endRecord(entries.length, centralSize, centralOffset));
        const firstPath = normalizeRelativePath(files[0]);
        const rootName = archiveName || firstPath.split("/")[0] || "manual-folder";
        const safeName = rootName.replace(/[\\/:*?"<>|]+/g, "_");
        return new File(parts, safeName + ".zip", { type: "application/zip" });
    }

    document.addEventListener("DOMContentLoaded", function () {
        const folderInput = document.querySelector("[data-manual-folder-input]");
        const filesInput = document.querySelector("[data-manual-files-input]");

        if (!folderInput || !filesInput) {
            return;
        }

        const form = folderInput.form;
        const zipInput = form.querySelector("[data-package-zip-input]");
        const status = form.querySelector("[data-manual-folder-status]");
        const submitButton = form.querySelector("button[type='submit']");
        let preparing = false;
        let preparationId = 0;

        function showStatus(message, isError) {
            status.hidden = false;
            status.textContent = message;
            status.classList.toggle("is-error", Boolean(isError));
        }

        zipInput.addEventListener("change", function () {
            if (zipInput.dataset.folderGenerated === "true") {
                return;
            }

            preparationId += 1;
            preparing = false;
            folderInput.value = "";
            filesInput.value = "";
            status.hidden = true;
            submitButton.disabled = false;
        });

        async function prepareSelection(sourceInput, otherInput, archiveName) {
            const currentPreparationId = preparationId + 1;
            preparationId = currentPreparationId;
            zipInput.value = "";
            delete zipInput.dataset.folderGenerated;
            otherInput.value = "";

            if (!sourceInput.files.length) {
                status.hidden = true;
                return;
            }

            preparing = true;
            submitButton.disabled = true;
            showStatus("폴더 구조를 확인하고 있습니다...", false);

            try {
                const selectedFileCount = sourceInput.files.length;
                const zipFile = await createPackageZip(sourceInput.files, function (percent, current, total, path) {
                    if (currentPreparationId !== preparationId) {
                        return;
                    }

                    showStatus(
                        "패키지 준비 " + percent + "% (" + current + "/" + total + ") · " + path,
                        false
                    );
                }, archiveName);

                if (currentPreparationId !== preparationId) {
                    return;
                }

                const transfer = new DataTransfer();
                transfer.items.add(zipFile);
                zipInput.files = transfer.files;
                zipInput.dataset.folderGenerated = "true";
                showStatus(
                    selectedFileCount + "개 파일을 준비했습니다: " + zipFile.name,
                    false
                );
            } catch (error) {
                if (currentPreparationId !== preparationId) {
                    return;
                }

                showStatus(error.message || "파일을 준비하지 못했습니다.", true);
                sourceInput.value = "";
            } finally {
                if (currentPreparationId === preparationId) {
                    preparing = false;
                    submitButton.disabled = false;
                }
            }
        }

        folderInput.addEventListener("change", function () {
            prepareSelection(folderInput, filesInput, "");
        });

        filesInput.addEventListener("change", function () {
            prepareSelection(filesInput, folderInput, "manual-files");
        });

        form.addEventListener("submit", function (event) {
            if (!preparing) {
                return;
            }

            event.preventDefault();
            event.stopImmediatePropagation();
            showStatus("폴더 준비가 끝날 때까지 잠시 기다려 주세요.", false);
        }, true);
    });
})();
