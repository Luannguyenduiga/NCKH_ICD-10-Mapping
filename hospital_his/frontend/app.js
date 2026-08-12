document.addEventListener("DOMContentLoaded", () => {
    const GATEWAY_BASE = "http://127.0.0.1:8000";

    // DOM Elements
    const patientTableBody = document.getElementById("patient-table-body");
    const patientCountBadge = document.getElementById("patient-count");
    const searchInput = document.getElementById("patient-search");
    const btnResetDb = document.getElementById("btn-reset-db");
    const btnAddPatientModal = document.getElementById("btn-add-patient-modal");
    const btnCancelModal = document.getElementById("btn-cancel-modal");
    const btnCopyFhir = document.getElementById("btn-copy-fhir");
    const btnClearInspector = document.getElementById("btn-clear-inspector");
    const addPatientModal = document.getElementById("add-patient-modal");
    const closeModalBtn = document.getElementById("close-modal-btn");
    const addPatientForm = document.getElementById("add-patient-form");
    const fhirJsonDisplay = document.getElementById("fhir-json-display");
    const logConsoleDisplay = document.getElementById("log-console-display");
    const gatewayStatusDot = document.getElementById("gateway-status-dot");
    const gatewayStatusText = document.getElementById("gateway-status-text");
    const tabButtons = document.querySelectorAll(".tab-btn");
    const tabPanes = document.querySelectorAll(".tab-pane");

    let patientsData = [];
    let currentFhirPayload = null;

    /**
     * Thoát ký tự HTML trước khi ghép vào innerHTML.
     * Tên bệnh nhân và ghi chú lâm sàng là dữ liệu người dùng nhập, nếu ghép thẳng
     * thì một chuỗi như <img onerror=...> sẽ được thực thi (stored XSS).
     */
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));

    // --- Tab Switching Logic ---
    tabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            tabButtons.forEach(b => b.classList.remove("active"));
            tabPanes.forEach(p => p.classList.remove("active"));
            btn.classList.add("active");
            document.getElementById(btn.getAttribute("data-tab")).classList.add("active");
        });
    });

    // --- Logger Helpers ---
    function addLog(message, type = "info") {
        const line = document.createElement("div");
        line.className = `log-line ${type}`;
        line.innerHTML = `<strong>[${new Date().toLocaleTimeString()}]</strong> ${esc(message)}`;
        logConsoleDisplay.appendChild(line);
        logConsoleDisplay.scrollTop = logConsoleDisplay.scrollHeight;
    }

    function clearLogs() {
        logConsoleDisplay.innerHTML = "";
        addLog("Bảng log đã được xóa sạch.", "info");
    }

    function showTab(tabId) {
        document.querySelector(`[data-tab='${tabId}']`).click();
    }

    // --- Check Gateway Health ---
    async function checkGatewayHealth() {
        try {
            const res = await fetch(`${GATEWAY_BASE}/health`, { mode: "cors" });
            if (!res.ok) throw new Error("HTTP Error");
            const data = await res.json();
            gatewayStatusDot.className = "status-dot connected";
            gatewayStatusText.innerText = data.model_loaded
                ? "Gateway Online"
                : "Gateway Online (mô hình NLP chưa nạp xong)";
        } catch (e) {
            gatewayStatusDot.className = "status-dot disconnected";
            gatewayStatusText.innerText = "Gateway Offline";
        }
    }

    checkGatewayHealth();
    setInterval(checkGatewayHealth, 5000);

    // --- Fetch Patients ---
    async function fetchPatients() {
        try {
            const response = await fetch("/api/patients");
            if (!response.ok) throw new Error("Failed to fetch patients");
            patientsData = await response.json();
            renderPatientTable(patientsData);
        } catch (e) {
            addLog(`Lỗi tải danh sách bệnh nhân: ${e.message}`, "error");
            patientTableBody.innerHTML = `
                <tr>
                    <td colspan="9" class="text-center" style="color: var(--status-failed)">
                        <i class="fa-solid fa-triangle-exclamation"></i> Không thể kết nối đến backend HIS
                    </td>
                </tr>
            `;
        }
    }

    const STATUS_PILLS = {
        "Synced": ['synced', 'fa-circle-check', 'Đã liên thông'],
        "Failed": ['failed', 'fa-circle-xmark', 'Thất bại'],
        "Needs Review": ['review', 'fa-user-doctor', 'Chờ bác sĩ duyệt'],
    };

    /**
     * Mã ICD-10 dạng liền không dấu chấm (A00.0 -> A000).
     *
     * Suy ra từ mã đã lưu thay vì thêm cột vào SQLite: hai dạng luôn phải khớp
     * nhau nên lưu cả hai là tạo cơ hội cho chúng lệch nhau. Ký hiệu †/* cũng
     * được gỡ vì chúng không thuộc mã.
     */
    function noDotCode(code) {
        return (code || "").replace(/[†*‡]/g, "").replace(/\./g, "").trim();
    }

    function confidenceTag(score) {
        if (score === null || score === undefined) return "";
        const band = score >= 85 ? "high" : score >= 60 ? "medium" : "low";
        return `<span class="confidence-tag ${band}">${Number(score).toFixed(1)}%</span>`;
    }

    // --- Render Table ---
    function renderPatientTable(patients) {
        patientCountBadge.innerText = `${patients.length} bệnh nhân`;

        if (patients.length === 0) {
            patientTableBody.innerHTML = `
                <tr><td colspan="9" class="text-center text-muted">Không tìm thấy bệnh nhân nào.</td></tr>
            `;
            return;
        }

        patientTableBody.innerHTML = patients.map(p => {
            const [cls, icon, label] = STATUS_PILLS[p.sync_status]
                || ['unsynced', 'fa-circle-minus', 'Chưa liên thông'];
            const statusPill = `<span class="status-pill ${cls}"><i class="fa-solid ${icon}"></i> ${label}</span>`;

            // Một dòng chẩn đoán có thể ra nhiều bệnh. Bảng patients chỉ giữ được
            // chẩn đoán chính nên danh sách đầy đủ lấy từ `conditions`; hồ sơ cũ
            // (đồng bộ trước khi có bảng này) vẫn hiển thị được nhờ nhánh dự phòng.
            const conditions = (p.conditions && p.conditions.length)
                ? p.conditions
                : (p.icd10_code
                    ? [{ icd10_code: p.icd10_code, icd10_display: p.icd10_display,
                         confidence_score: p.confidence_score, status: p.sync_status }]
                    : []);
            const empty = `<span style="color: var(--text-muted)">-</span>`;

            const icdCodeStr = conditions.length
                ? conditions.map(c => {
                    const pending = c.status && c.status !== "Synced"
                        ? ` <i class="fa-solid fa-hourglass-half" title="${esc(c.status)}"></i>` : "";
                    const desc = c.icd10_display
                        ? `<span class="icd-desc-text" title="${esc(c.icd10_display)}${c.fragment ? ` ← "${esc(c.fragment)}"` : ""}">${esc(c.icd10_display)}</span>`
                        : "";
                    return `<div class="icd-line"><span class="icd-code-badge">${esc(c.icd10_code)}</span>`
                        + `${confidenceTag(c.confidence_score)}${pending}${desc}</div>`;
                }).join("")
                : empty;
            const icdNoDotStr = conditions.length
                ? conditions.map(c =>
                    `<div class="icd-line"><span class="icd-code-nodot">${esc(noDotCode(c.icd10_code))}</span></div>`
                  ).join("")
                : empty;
            const icdDescStr = "";

            const syncBtnContent = p.sync_status === "Synced"
                ? `<i class="fa-solid fa-arrows-spin"></i> Đồng bộ lại`
                : `<i class="fa-solid fa-cloud-arrow-up"></i> Đồng bộ`;

            return `
                <tr data-patient-id="${esc(p.id)}">
                    <td class="mrn-text">${esc(p.id)}</td>
                    <td class="patient-name">${esc(p.name)}</td>
                    <td>${esc(p.gender)}</td>
                    <td>${esc(p.birth_date)}</td>
                    <td class="diagnosis-text" title="${esc(p.clinical_note)}">${esc(p.clinical_note)}</td>
                    <td>${icdCodeStr}${icdDescStr}</td>
                    <td>${icdNoDotStr}</td>
                    <td>
                        ${statusPill}
                        ${p.sync_time ? `<div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 4px;">${esc(p.sync_time)}</div>` : ""}
                    </td>
                    <td class="text-center">
                        <div class="action-buttons">
                            <button class="btn btn-sm btn-sync" data-action="sync" title="Đồng bộ hồ sơ lên trục liên thông">
                                ${syncBtnContent}
                            </button>
                            <button class="btn btn-sm btn-delete" data-action="delete" title="Xóa bệnh án khỏi HIS cục bộ">
                                <i class="fa-solid fa-trash-can"></i> Xóa
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join("");
    }

    /**
     * Ủy quyền sự kiện thay cho onclick="fn('${p.id}')".
     * Cách cũ nội suy mã bệnh nhân vào chuỗi JavaScript, nên một mã chứa dấu nháy
     * sẽ phá vỡ cú pháp và cho phép chèn mã tùy ý.
     */
    patientTableBody.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-action]");
        if (!button) return;
        const patientId = button.closest("tr")?.dataset.patientId;
        if (!patientId) return;

        if (button.dataset.action === "sync") syncPatientRecord(patientId, button);
        if (button.dataset.action === "delete") deletePatientRecord(patientId);
    });

    // --- Search Patient ---
    searchInput.addEventListener("input", (e) => {
        const query = e.target.value.toLowerCase().trim();
        if (!query) return renderPatientTable(patientsData);

        renderPatientTable(patientsData.filter(p =>
            p.id.toLowerCase().includes(query) ||
            p.name.toLowerCase().includes(query) ||
            p.clinical_note.toLowerCase().includes(query)
        ));
    });

    // --- Reset Database ---
    btnResetDb.addEventListener("click", async () => {
        if (!confirm("Bạn có chắc chắn muốn reset cơ sở dữ liệu HIS về danh sách mẫu mặc định không?")) return;

        try {
            const res = await fetch("/api/reset", { method: "POST" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error resetting db");

            addLog("Cơ sở dữ liệu HIS đã được khôi phục về trạng thái mặc định.", "success");
            resetInspector();
            fetchPatients();
        } catch (e) {
            addLog(`Lỗi khi reset DB: ${e.message}`, "error");
        }
    });

    // --- Modal Actions ---
    btnAddPatientModal.addEventListener("click", () => addPatientModal.classList.add("active"));

    function closeModal() {
        addPatientModal.classList.remove("active");
        addPatientForm.reset();
    }

    closeModalBtn.addEventListener("click", closeModal);
    btnCancelModal.addEventListener("click", closeModal);
    window.addEventListener("click", (e) => {
        if (e.target === addPatientModal) closeModal();
    });

    // --- Add Patient Form ---
    addPatientForm.addEventListener("submit", async (e) => {
        e.preventDefault();

        const payload = {
            id: document.getElementById("p-id").value.trim(),
            name: document.getElementById("p-name").value.trim(),
            gender: document.getElementById("p-gender").value,
            birth_date: document.getElementById("p-dob").value,
            clinical_note: document.getElementById("p-clinical").value.trim()
        };

        try {
            const res = await fetch("/api/patients", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await res.json();

            if (!res.ok) {
                const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
                alert(`Lỗi: ${detail}`);
                addLog(`Lỗi thêm bệnh nhân mới: ${detail}`, "error");
                return;
            }

            addLog(`Đã thêm mới hồ sơ bệnh án cho bệnh nhân ${payload.name} (${payload.id}).`, "success");
            closeModal();
            fetchPatients();
        } catch (err) {
            addLog(`Lỗi thêm bệnh nhân mới: ${err.message}`, "error");
        }
    });

    // --- Delete Patient ---
    async function deletePatientRecord(patientId) {
        if (!confirm(`Bạn có chắc chắn muốn xóa bệnh án của bệnh nhân ${patientId} khỏi hệ thống không?`)) return;

        try {
            const res = await fetch(`/api/patients/${encodeURIComponent(patientId)}`, { method: "DELETE" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Không xóa được");

            addLog(`Đã xóa thành công bệnh án của bệnh nhân ${patientId}.`, "success");
            fetchPatients();
        } catch (err) {
            addLog(`Lỗi khi xóa bệnh án ${patientId}: ${err.message}`, "error");
        }
    }

    // --- Sync Patient ---
    async function syncPatientRecord(patientId, btn) {
        const originalBtnHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Xử lý...`;

        addLog(`=== BẮT ĐẦU ĐỒNG BỘ BỆNH NHÂN: ${patientId} ===`, "info");
        addLog(`[BƯỚC 1/3] Gọi Gateway chuẩn hóa chẩn đoán lâm sàng bằng mô hình NLP...`, "info");
        showTab("raw-response");

        try {
            const res = await fetch(`/api/sync/${encodeURIComponent(patientId)}`, { method: "POST" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Unknown sync error");

            // Một dòng chẩn đoán có thể ra nhiều bệnh, mỗi bệnh một Condition
            // riêng, nên nhật ký phải kể từng mã thay vì chỉ mã chính.
            const conditions = data.conditions || [];
            if (conditions.length > 1) {
                addLog(`[BƯỚC 2/3] Tách được ${conditions.length} chẩn đoán độc lập `
                     + `trong một dòng bệnh án.`, "info");
            }
            conditions.forEach(c => {
                const line = `${c.icd10_code || c.code} - ${c.icd10_display || c.name_vi} `
                    + `(${c.confidence_score ?? c.confidence}%)`
                    + (c.fragment ? ` ← "${c.fragment}"` : "");
                if (c.status === "Synced") {
                    addLog(`   ✓ ${line} → FHIR Condition ${c.fhir_condition_id}`, "success");
                } else if (c.status === "Needs Review") {
                    addLog(`   ⏸ ${line} → chờ bác sĩ xác nhận`, "error");
                } else {
                    addLog(`   ✗ ${line} → ${c.message || "lỗi liên thông"}`, "error");
                }
            });

            if (data.status === "Synced") {
                addLog(`[BƯỚC 3/3] Đã truyền ${conditions.length} HL7 FHIR Condition `
                     + `lên EMR Cloud.`, "success");
                fetchPatients();
                await loadAndDisplayFhirResource(data.fhir_condition_id);
            } else if (data.status === "Needs Review") {
                addLog(`[DỪNG] ${data.message}`, "error");
                addLog(`Mã đề xuất cao nhất: ${data.icd10_code} - ${data.icd10_display} `
                     + `(${data.confidence_score}%). Các phương án khác:`, "info");
                (data.candidates || []).slice(1).forEach(c =>
                    addLog(`   • ${c.code} - ${c.name_vi} (${c.confidence}%)`, "info"));
                fetchPatients();
                // Vẫn còn mã đã liên thông được thì hiển thị tài nguyên của nó.
                const firstSynced = conditions.find(c => c.status === "Synced");
                if (firstSynced) await loadAndDisplayFhirResource(firstSynced.fhir_condition_id);
            } else {
                addLog(`Đồng bộ thất bại: ${data.message}`, "error");
                fetchPatients();
            }
        } catch (err) {
            addLog(`Lỗi đồng bộ hồ sơ bệnh nhân ${patientId}: ${err.message}`, "error");
            fetchPatients();
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalBtnHtml;
        }
    }

    /**
     * Đọc lại tài nguyên đã liên thông để đối chiếu.
     * Đi vòng qua Gateway thay vì gọi thẳng HAPI FHIR ở cổng 8090: tránh phụ thuộc
     * cấu hình CORS của máy chủ FHIR, và dùng đúng id mà EMR Cloud đã xác nhận.
     */
    async function loadAndDisplayFhirResource(conditionId) {
        if (!conditionId) return;
        try {
            addLog(`Đang truy vấn lại tài nguyên FHIR từ EMR Cloud: Condition/${conditionId}`, "info");
            const response = await fetch(`${GATEWAY_BASE}/api/fhir/condition/${encodeURIComponent(conditionId)}`);
            if (!response.ok) throw new Error(`EMR Cloud trả về HTTP ${response.status}`);

            currentFhirPayload = await response.json();
            fhirJsonDisplay.innerText = JSON.stringify(currentFhirPayload, null, 2);
            btnCopyFhir.disabled = false;
            showTab("fhir-json");
            addLog(`Đã tải và hiển thị tài nguyên HL7 FHIR Condition từ EMR Cloud.`, "success");
        } catch (e) {
            addLog(`Không đọc lại được tài nguyên trên EMR Cloud: ${e.message}`, "error");
        }
    }

    // --- Copy FHIR JSON ---
    btnCopyFhir.addEventListener("click", () => {
        if (!currentFhirPayload) return;

        navigator.clipboard.writeText(JSON.stringify(currentFhirPayload, null, 2))
            .then(() => {
                const originalText = btnCopyFhir.innerHTML;
                btnCopyFhir.innerHTML = `<i class="fa-solid fa-check"></i> Đã sao chép!`;
                setTimeout(() => { btnCopyFhir.innerHTML = originalText; }, 2000);
            })
            .catch(err => addLog(`Lỗi sao chép JSON: ${err.message}`, "error"));
    });

    function resetInspector() {
        fhirJsonDisplay.innerText = "// Chọn bệnh nhân và bấm 'Đồng bộ' để hiển thị cấu trúc tài nguyên HL7 FHIR Condition tiêu chuẩn quốc tế...";
        btnCopyFhir.disabled = true;
        currentFhirPayload = null;
    }

    btnClearInspector.addEventListener("click", () => {
        clearLogs();
        resetInspector();
    });

    fetchPatients();
});
