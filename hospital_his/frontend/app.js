document.addEventListener("DOMContentLoaded", () => {
    // Địa chỉ Gateway do máy chủ HIS quyết định, KHÔNG ghi cứng ở đây: mỗi bệnh
    // viện chạy một bản Gateway riêng, có thể ở cổng khác. Giá trị dưới đây chỉ
    // là mặc định dùng tạm cho tới khi /api/config trả về.
    let GATEWAY_BASE = "http://127.0.0.1:8000";
    let FACILITY_CODE = "";

    async function loadConfig() {
        try {
            const res = await fetch("/api/config");
            if (!res.ok) return;
            const cfg = await res.json();
            if (cfg.gateway_url) GATEWAY_BASE = cfg.gateway_url;
            FACILITY_CODE = cfg.facility_code || "";
            const link = document.querySelector(".gateway-btn");
            if (link) link.href = GATEWAY_BASE;
        } catch (e) {
            /* Giữ mặc định; checkGatewayHealth sẽ báo offline nếu sai. */
        }
    }

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
    const diagnoseModal = document.getElementById("diagnose-modal");
    const diagnoseForm = document.getElementById("diagnose-form");
    const closeDiagnoseBtn = document.getElementById("close-diagnose-btn");
    const btnCancelDiagnose = document.getElementById("btn-cancel-diagnose");
    const addPatientForm = document.getElementById("add-patient-form");
    const conditionsModal = document.getElementById("conditions-modal");
    const historyModal = document.getElementById("history-modal");
    const historyPatient = document.getElementById("h-patient");
    const historySummary = document.getElementById("history-summary");
    const historyBody = document.getElementById("history-body");
    const closeHistoryBtn = document.getElementById("close-history-btn");
    const btnCloseHistory = document.getElementById("btn-close-history");
    const conditionsList = document.getElementById("conditions-list");
    const conditionsPatient = document.getElementById("c-patient");
    const closeConditionsBtn = document.getElementById("close-conditions-btn");
    const btnCloseConditions = document.getElementById("btn-close-conditions");
    const btnAddFromConditions = document.getElementById("btn-add-from-conditions");
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

    // Nạp cấu hình TRƯỚC lần kiểm tra đầu tiên, nếu không lần đó sẽ hỏi nhầm
    // cổng mặc định và báo offline oan.
    loadConfig().then(checkGatewayHealth);
    setInterval(checkGatewayHealth, 5000);

    // --- Fetch Patients ---
    async function fetchPatients() {
        try {
            const response = await fetch("/api/patients");
            if (!response.ok) throw new Error("Failed to fetch patients");
            patientsData = await response.json();
            renderPatientTable(patientsData);
            return patientsData;
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

    function statusPillHtml(status) {
        const [cls, icon, label] = STATUS_PILLS[status]
            || ['unsynced', 'fa-circle-minus', 'Chưa liên thông'];
        return `<span class="status-pill ${cls}"><i class="fa-solid ${icon}"></i> ${label}</span>`;
    }

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

    // Condition.clinicalStatus (FHIR R4) kèm nhãn tiếng Việt, theo thứ tự dùng
    // cho ô chọn ở màn hình sửa chẩn đoán. Bệnh đã điều trị xong nên chuyển sang
    // "resolved" chứ đừng gỡ khỏi bệnh án: gỡ là mất bệnh sử.
    const CLINICAL_STATUSES = [
        ["active", "Đang mắc"],
        ["recurrence", "Tái phát"],
        ["relapse", "Tái phát sau lui bệnh"],
        ["remission", "Thuyên giảm"],
        ["inactive", "Không còn hoạt động"],
        ["resolved", "Đã khỏi"],
    ];
    const CLINICAL_LABELS = Object.fromEntries(CLINICAL_STATUSES);

    /**
     * Nhãn trạng thái lâm sàng.
     *
     * Bệnh đã khỏi mà hiển thị y hệt bệnh đang mắc là đọc sai bệnh án, nên trạng
     * thái khác "đang mắc" được tô riêng thay vì lẫn vào danh sách.
     */
    function clinicalBadge(status) {
        const value = status || "active";
        const label = CLINICAL_LABELS[value] || value;
        return `<span class="clinical-badge ${value === "active" ? "on" : "off"}">${esc(label)}</span>`;
    }

    function confidenceTag(score) {
        if (score === null || score === undefined) return "";
        const band = score >= 85 ? "high" : score >= 60 ? "medium" : "low";
        return `<span class="confidence-tag ${band}">${Number(score).toFixed(1)}%</span>`;
    }

    /**
     * Danh sách chẩn đoán của một hồ sơ.
     *
     * Một dòng chẩn đoán có thể ra nhiều bệnh. Bảng patients chỉ giữ được chẩn
     * đoán chính nên danh sách đầy đủ lấy từ `conditions`; hồ sơ cũ (đồng bộ
     * trước khi có bảng này) vẫn đọc được nhờ nhánh dự phòng.
     */
    function conditionsOf(p) {
        if (p.conditions && p.conditions.length) return p.conditions;
        return p.icd10_code
            ? [{ icd10_code: p.icd10_code, icd10_display: p.icd10_display,
                 confidence_score: p.confidence_score, status: p.sync_status }]
            : [];
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
            const statusPill = statusPillHtml(p.sync_status);
            const conditions = conditionsOf(p);
            const empty = `<span style="color: var(--text-muted)">-</span>`;

            const icdCodeStr = conditions.length
                ? conditions.map(c => {
                    const pending = c.status && c.status !== "Synced"
                        ? ` <i class="fa-solid fa-hourglass-half" title="${esc(c.status)}"></i>` : "";
                    const desc = c.icd10_display
                        ? `<span class="icd-desc-text" title="${esc(c.icd10_display)}${c.fragment ? ` ← "${esc(c.fragment)}"` : ""}">${esc(c.icd10_display)}</span>`
                        : "";
                    // Trạng thái "đang mắc" là mặc định nên không cần nhắc; chỉ
                    // hiện khi khác đi, để bệnh đã khỏi không bị đọc nhầm.
                    const lamSang = c.clinical_status && c.clinical_status !== "active"
                        ? ` ${clinicalBadge(c.clinical_status)}` : "";
                    return `<div class="icd-line"><span class="icd-code-badge">${esc(c.icd10_code)}</span>`
                        + `${confidenceTag(c.confidence_score)}${pending}${lamSang}${desc}</div>`;
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
                            <button class="btn btn-sm btn-diagnose" data-action="diagnose" title="Chẩn đoán thêm bệnh mới, giữ nguyên các chẩn đoán cũ">
                                <i class="fa-solid fa-notes-medical"></i> Chẩn đoán thêm
                            </button>
                            <button class="btn btn-sm btn-history" data-action="history" title="Xem toàn bộ bệnh sử: khám tại đây và ở các tuyến khác trên trục dữ liệu">
                                <i class="fa-solid fa-clock-rotate-left"></i> Xem bệnh sử
                            </button>
                            <button class="btn btn-sm btn-edit" data-action="edit-conditions" title="Sửa hoặc gỡ các chẩn đoán đã ghi nhận, kể cả của lần khám trước">
                                <i class="fa-solid fa-pen-to-square"></i> Sửa chẩn đoán
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
        if (button.dataset.action === "diagnose") openDiagnoseModal(patientId, button);
        if (button.dataset.action === "history") {
            // Hồ sơ đọc từ trục chưa có mã bệnh án tại viện này, và mã của viện
            // khác thì KHÔNG tra được - "BN0002" ở hai nơi là hai người. Dùng
            // định danh toàn quốc làm khóa tra, đó là thứ duy nhất quy đúng người.
            const p = patientsData.find(x => String(x.id) === String(patientId));
            const khoa = (p && p.is_local === false)
                ? (p.citizen_id || p.insurance_card || patientId)
                : patientId;
            openHistoryModal(khoa);
        }
        if (button.dataset.action === "edit-conditions") openConditionsModal(patientId);
        if (button.dataset.action === "delete") deletePatientRecord(patientId);
    });

    // --- Search Patient ---
    searchInput.addEventListener("input", (e) => {
        const query = e.target.value.toLowerCase().trim();
        if (!query) return renderPatientTable(patientsData);

        // Lọc cả theo định danh toàn quốc: bệnh nhân chuyển tuyến tới thì bác sĩ
        // cầm CCCD chứ không cầm mã bệnh án của viện mình.
        renderPatientTable(patientsData.filter(p =>
            p.id.toLowerCase().includes(query) ||
            p.name.toLowerCase().includes(query) ||
            (p.citizen_id || "").toLowerCase().includes(query) ||
            (p.insurance_card || "").toLowerCase().includes(query) ||
            (p.clinical_note || "").toLowerCase().includes(query)
        ));
    });

    searchInput.addEventListener("keydown", async (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            const query = searchInput.value.trim();
            if (!query) return;

            // Search locally first
            const localResults = patientsData.filter(p =>
                p.id.toLowerCase() === query.toLowerCase()
            );

            if (localResults.length === 0) {
                // If not found locally, query backend to pull from EMR Cloud
                addLog(`Không tìm thấy bệnh nhân "${query}" cục bộ. Đang truy vấn EMR Cloud...`, "info");
                try {
                    const response = await fetch(`/api/patients?search_id=${encodeURIComponent(query)}`);
                    if (response.ok) {
                        const results = await response.json();
                        if (results && results.length > 0) {
                            addLog(`Đã tải thành công hồ sơ bệnh nhân "${query}" từ EMR Cloud!`, "success");
                            // Add to local patientsData if not already present
                            const existingIds = patientsData.map(p => p.id);
                            results.forEach(p => {
                                if (!existingIds.includes(p.id)) {
                                    patientsData.push(p);
                                }
                            });
                            // Hiển thị THẲNG hồ sơ vừa đọc về. Lọc lại danh sách theo
                            // `p.id === query` là sai: nhánh này chỉ chạy khi tra bằng
                            // CCCD/BHYT, lúc đó `query` là số định danh toàn quốc còn
                            // `p.id` là mã bệnh án nội viện - hai thứ không bao giờ bằng
                            // nhau, nên bảng ra rỗng dù đã tải được hồ sơ và nhật ký đã
                            // báo thành công.
                            renderPatientTable(results);
                        } else {
                            addLog(`Không tìm thấy bệnh nhân "${query}" trên cả cục bộ lẫn EMR Cloud.`, "error");
                        }
                    }
                } catch (err) {
                    addLog(`Lỗi truy vấn EMR Cloud: ${err.message}`, "error");
                }
            }
        }
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
        if (e.target === diagnoseModal) closeDiagnoseModal();
        if (e.target === conditionsModal) closeConditionsModal();
    });

    // --- Chẩn đoán thêm cho bệnh nhân đã có hồ sơ ---
    // Giữ lại nút vừa bấm để trả trạng thái "Xử lý..." về đúng chỗ sau khi xong.
    let diagnoseTarget = { patientId: null, btn: null };

    function openDiagnoseModal(patientId, btn) {
        const row = btn.closest("tr");
        const ten = row?.querySelector(".patient-name")?.innerText || "";
        diagnoseTarget = { patientId, btn };
        document.getElementById("d-patient").value = `${patientId} — ${ten}`;
        document.getElementById("d-clinical").value = "";
        diagnoseModal.classList.add("active");
        document.getElementById("d-clinical").focus();
    }

    function closeDiagnoseModal() {
        diagnoseModal.classList.remove("active");
        diagnoseForm.reset();
    }

    closeDiagnoseBtn.addEventListener("click", closeDiagnoseModal);
    btnCancelDiagnose.addEventListener("click", closeDiagnoseModal);

    diagnoseForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const note = document.getElementById("d-clinical").value.trim();
        if (!note || !diagnoseTarget.patientId) return;
        closeDiagnoseModal();
        syncPatientRecord(diagnoseTarget.patientId, diagnoseTarget.btn, note);
    });

    // --- Sửa chẩn đoán đã ghi nhận ---------------------------------------------
    // Bệnh án chỉ thêm được chẩn đoán thì một mã do NLP suy sai sẽ nằm lại vĩnh
    // viễn: bác sĩ chỉ còn cách xóa cả hồ sơ rồi nhập lại. Khối dưới đây mở phần
    // sửa cho từng chẩn đoán, kể cả chẩn đoán của những lần khám trước.

    let conditionsPatientId = null;

    function currentConditionsPatient() {
        return patientsData.find(p => p.id === conditionsPatientId) || null;
    }

    function openConditionsModal(patientId) {
        conditionsPatientId = patientId;
        renderConditionsModal();
        conditionsModal.classList.add("active");
    }

    function closeConditionsModal() {
        conditionsModal.classList.remove("active");
        conditionsPatientId = null;
    }

    closeConditionsBtn.addEventListener("click", closeConditionsModal);
    btnCloseConditions.addEventListener("click", closeConditionsModal);
    btnAddFromConditions.addEventListener("click", () => {
        const patientId = conditionsPatientId;
        const btn = patientTableBody.querySelector(
            `tr[data-patient-id="${CSS.escape(patientId)}"] button[data-action="diagnose"]`);
        closeConditionsModal();
        if (btn) openDiagnoseModal(patientId, btn);
    });

    // --- Benh su toan tuyen -------------------------------------------------

    /**
     * Mở bệnh sử đầy đủ của một hồ sơ: nội viện cộng mọi tuyến khác trên trục.
     *
     * Khác modal "Sửa chẩn đoán" ở chỗ đó chỉ đọc bệnh án CỤC BỘ, nên chẩn đoán
     * do tuyến khác lập không bao giờ hiện ra ở đó. Đây là màn chỉ đọc, cố ý
     * không có nút sửa: bệnh viện này không sửa bản ghi của cơ sở khác.
     */
    async function openHistoryModal(patientId) {
        historyModal.classList.add("active");
        historyPatient.innerHTML = `<strong>${esc(patientId)}</strong>`;
        historySummary.innerHTML = "";
        historyBody.innerHTML = `<p class="cond-empty">Đang đọc bệnh sử trên trục dữ liệu...</p>`;

        let data;
        try {
            const res = await fetch(`/api/patients/${encodeURIComponent(patientId)}/history`);
            if (!res.ok) {
                const loi = await res.json().catch(() => ({}));
                throw new Error(loi.detail || `HTTP ${res.status}`);
            }
            data = await res.json();
        } catch (err) {
            historyBody.innerHTML =
                `<p class="cond-empty">Không đọc được bệnh sử: ${esc(err.message)}</p>`;
            return;
        }
        renderHistory(data);
    }

    function closeHistoryModal() {
        historyModal.classList.remove("active");
    }

    function renderHistory(data) {
        const p = data.patient || {};
        const dinhDanh = [
            p.citizen_id ? `CCCD ${esc(p.citizen_id)}` : null,
            p.insurance_card ? `BHYT ${esc(p.insurance_card)}` : null,
        ].filter(Boolean).join(" · ");

        // Mã bệnh án nơi khác LUÔN kèm tên cơ sở: "BN0002" của viện này và viện
        // kia là hai người khác nhau, hiện trần mã là mời người đọc nhầm.
        const maNoiKhac = (p.ma_benh_an_noi_khac || [])
            .map(m => `<span class="mrn-text" title="Mã bệnh án tại cơ sở khác">
                         ${esc(m.value)} @ ${esc(m.facility_code)}</span>`).join(" ");

        historyPatient.innerHTML =
            `<strong>${esc(p.name || "")}</strong> <span class="mrn-text">${esc(p.id || "")}</span>`
            + `<div class="cond-note">${esc(p.gender || "")} · ${esc(p.birth_date || "")}`
            + (dinhDanh ? ` · ${dinhDanh}` : "") + `</div>`
            + (maNoiKhac ? `<div class="cond-note">Mã bệnh án nơi khác: ${maNoiKhac}</div>` : "")
            // Chưa tiếp nhận thì bác sĩ phải biết ngay, nếu không sẽ tưởng đây là
            // bệnh nhân của mình và đi tìm bệnh án nội viện không có thật.
            + (data.co_ho_so_cuc_bo === false
                ? `<p class="form-hint">Bệnh nhân <strong>chưa có bệnh án tại bệnh viện
                     này</strong>. Toàn bộ bệnh sử dưới đây đọc từ trục dữ liệu và chỉ
                     xem được. Muốn khám tại đây thì tiếp nhận bằng
                     <em>Thêm Bệnh nhân mới</em>, nhập đúng CCCD/BHYT để trục quy về
                     cùng một người.</p>`
                : "");

        // Nói rõ bệnh sử này quét được tới đâu. "Không có dữ liệu" mà không kèm lý
        // do thì bác sĩ không biết nên bổ sung CCCD, bật lại trục, hay tin là
        // bệnh nhân thật sự chưa từng khám ở đâu.
        const nguon = data.tra_cuu_bang
            ? { cccd: "số CCCD", bhyt: "thẻ BHYT", mrn: "mã bệnh án" }[data.tra_cuu_bang]
            : null;
        historySummary.innerHTML =
            `<div class="history-summary">
                <span><strong>${data.tong_tren_truc || 0}</strong> chẩn đoán trên trục</span>
                <span><strong>${data.so_co_so || 0}</strong> cơ sở đã ghi nhận</span>
                ${data.so_co_so_ngoai_vien
                    ? `<span class="facility-badge"><i class="fa-solid fa-hospital"></i>
                         ${data.so_co_so_ngoai_vien} tuyến khác</span>` : ""}
                ${nguon ? `<span class="history-src">Quy chiếu theo ${nguon}</span>` : ""}
             </div>`
            + (data.canh_bao ? `<p class="form-hint">${esc(data.canh_bao)}</p>` : "");

        const khoi = [];

        for (const nhom of data.theo_co_so || []) {
            const nhan = nhom.la_ngoai_vien
                ? `<span class="facility-badge"><i class="fa-solid fa-hospital"></i> Tuyến khác</span>`
                : `<span class="clinical-badge on">Tại bệnh viện này</span>`;
            khoi.push(`
                <div class="history-group ${nhom.la_ngoai_vien ? "ngoai-vien" : ""}">
                    <div class="history-group-head">
                        <strong>${esc(nhom.facility_name)}</strong>
                        <span class="mrn-text">${esc(nhom.facility_code)}</span>
                        ${nhan}
                        <span class="history-count">${nhom.so_chan_doan} chẩn đoán</span>
                    </div>
                    ${nhom.chan_doan.map(historyItem).join("")}
                </div>`);
        }

        // Chưa liên thông thì tuyến khác CHƯA đọc được. Để riêng một khối chứ
        // không xếp vào cơ sở nào, vì xếp lẫn là nói rằng nơi khác đã thấy.
        if ((data.chua_lien_thong || []).length) {
            khoi.push(`
                <div class="history-group chua-lien-thong">
                    <div class="history-group-head">
                        <strong>Chưa liên thông</strong>
                        <span class="history-count">${data.chua_lien_thong.length} chẩn đoán</span>
                    </div>
                    <p class="form-hint">Các chẩn đoán này mới nằm trong bệnh án của bệnh viện
                       này, chưa đẩy lên trục nên tuyến khác chưa đọc được.</p>
                    ${data.chua_lien_thong.map(historyItem).join("")}
                </div>`);
        }

        historyBody.innerHTML = khoi.length ? khoi.join("") : `
            <p class="cond-empty">Chưa ghi nhận chẩn đoán nào cho hồ sơ này, ở bệnh viện
               này lẫn trên trục dữ liệu.</p>`;
    }

    function historyItem(c) {
        const ngay = c.recorded_date ? String(c.recorded_date).slice(0, 10) : "";
        return `
            <div class="history-item">
                <div class="cond-ids">
                    <span class="icd-code-badge">${esc(c.icd10_code || "")}</span>
                    <span class="icd-code-nodot">${esc(noDotCode(c.icd10_code || ""))}</span>
                    ${clinicalBadge(c.clinical_status)}
                    ${confidenceTag(c.confidence_score)}
                    ${ngay ? `<span class="cond-time">${esc(ngay)}</span>` : ""}
                </div>
                <div class="cond-name">${esc(c.icd10_display || "")}</div>
                ${c.fragment ? `<div class="cond-meta">
                    <span class="cond-fragment">← "${esc(c.fragment)}"</span></div>` : ""}
            </div>`;
    }

    closeHistoryBtn.addEventListener("click", closeHistoryModal);
    btnCloseHistory.addEventListener("click", closeHistoryModal);

    function renderConditionsModal() {
        const patient = currentConditionsPatient();
        if (!patient) return closeConditionsModal();

        // Hồ sơ đang xem trực tiếp từ EMR Cloud chưa có bản ghi cục bộ nào để
        // sửa. Hiện dạng chỉ đọc kèm lời giải thích, thay vì để bác sĩ bấm Sửa
        // rồi nhận lỗi 404 mà không hiểu vì sao.
        const chiDoc = patient.is_local === false;
        conditionsPatient.innerHTML =
            `<strong>${esc(patient.name)}</strong> <span class="mrn-text">${esc(patient.id)}</span>`
            + `<div class="cond-note">${esc(patient.clinical_note || "")}</div>`
            + (chiDoc
                ? `<p class="form-hint">Hồ sơ này đang được đọc từ EMR Cloud (bệnh viện khác
                     ghi nhận). Bệnh viện này không sửa bản ghi của cơ sở khác; hãy tiếp nhận
                     bệnh nhân bằng <em>Thêm Bệnh nhân mới</em> rồi chẩn đoán tại đây.</p>`
                : "");

        const conditions = conditionsOf(patient);
        if (!conditions.length) {
            conditionsList.innerHTML =
                `<p class="cond-empty">Hồ sơ chưa có chẩn đoán nào. Dùng
                 <em>Chẩn đoán thêm bệnh mới</em> để ghi nhận chẩn đoán đầu tiên.</p>`;
            return;
        }

        conditionsList.innerHTML = conditions.map(c => {
            const ngoaiVien = c.la_ngoai_vien
                ? `<span class="facility-badge" title="Chẩn đoán do cơ sở khác ghi nhận">
                     <i class="fa-solid fa-hospital"></i> ${esc(c.facility_name || "Ngoại viện")}</span>`
                : "";
            const khoaSua = chiDoc || c.la_ngoai_vien;
            return `
                <div class="cond-card" data-code="${esc(c.icd10_code)}">
                    <div class="cond-head">
                        <div class="cond-ids">
                            <span class="icd-code-badge">${esc(c.icd10_code)}</span>
                            <span class="icd-code-nodot">${esc(noDotCode(c.icd10_code))}</span>
                            ${confidenceTag(c.confidence_score)}
                            ${ngoaiVien}
                        </div>
                        <div class="cond-actions">
                            <button class="btn btn-sm btn-edit" data-cond-action="edit"
                                ${khoaSua ? "disabled" : ""}
                                title="Sửa mã ICD-10 hoặc trạng thái lâm sàng của chẩn đoán này">
                                <i class="fa-solid fa-pen"></i> Sửa
                            </button>
                            <button class="btn btn-sm btn-delete" data-cond-action="delete"
                                ${khoaSua ? "disabled" : ""}
                                title="Gỡ hẳn chẩn đoán này khỏi bệnh án và khỏi EMR Cloud">
                                <i class="fa-solid fa-trash-can"></i> Gỡ
                            </button>
                        </div>
                    </div>
                    <div class="cond-name">${esc(c.icd10_display || "")}</div>
                    <div class="cond-meta">
                        ${statusPillHtml(c.status)}
                        ${clinicalBadge(c.clinical_status)}
                        ${c.fragment ? `<span class="cond-fragment">← "${esc(c.fragment)}"</span>` : ""}
                        ${c.sync_time ? `<span class="cond-time">${esc(c.sync_time)}</span>` : ""}
                    </div>
                    <div class="cond-editor-slot"></div>
                </div>`;
        }).join("");
    }

    function editorHtml(condition) {
        const hienTai = condition.clinical_status || "active";
        const options = CLINICAL_STATUSES.map(([value, label]) =>
            `<option value="${value}"${value === hienTai ? " selected" : ""}>${label}</option>`
        ).join("");
        return `
            <div class="cond-editor">
                <div class="form-group">
                    <label>Mô tả lâm sàng của riêng bệnh này</label>
                    <textarea class="e-note" rows="2"
                        placeholder="Ví dụ: tăng huyết áp vô căn">${esc(condition.fragment || "")}</textarea>
                </div>
                <button type="button" class="btn btn-sm btn-outline" data-cond-action="suggest">
                    <i class="fa-solid fa-wand-magic-sparkles"></i> Gợi ý mã ICD-10 từ mô tả
                </button>
                <div class="e-candidates"></div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Mã ICD-10 đúng</label>
                        <input type="text" class="e-code" value="${esc(condition.icd10_code || "")}">
                    </div>
                    <div class="form-group">
                        <label>Tên bệnh theo ICD-10</label>
                        <input type="text" class="e-name" value="${esc(condition.icd10_display || "")}">
                    </div>
                </div>
                <div class="form-group">
                    <label>Trạng thái lâm sàng</label>
                    <select class="e-status">${options}</select>
                </div>
                <p class="form-hint">
                    Lưu lại là hệ thống đẩy bản ghi đúng lên EMR Cloud rồi mới gỡ bản mang mã cũ,
                    nên trên trục không có lúc nào hồ sơ trống chẩn đoán. Bác sĩ tự chọn mã thì
                    độ tin cậy ghi nhận là 100% và trạng thái xác minh là <em>confirmed</em>.
                </p>
                <div class="cond-editor-actions">
                    <button type="button" class="btn btn-sm btn-secondary" data-cond-action="cancel">Hủy</button>
                    <button type="button" class="btn btn-sm btn-primary" data-cond-action="save">
                        <i class="fa-solid fa-floppy-disk"></i> Lưu và liên thông lại
                    </button>
                </div>
            </div>`;
    }

    conditionsList.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-cond-action]");
        const candidate = event.target.closest(".cand-item");
        const card = event.target.closest(".cond-card");
        if (!card) return;
        const code = card.dataset.code;

        // Chọn một phương án mã do mô hình gợi ý: điền vào ô mã và tên, bác sĩ
        // vẫn sửa tay được trước khi lưu.
        if (candidate && !button) {
            card.querySelector(".e-code").value = candidate.dataset.code;
            card.querySelector(".e-name").value = candidate.dataset.name;
            card.querySelectorAll(".cand-item").forEach(el => el.classList.remove("selected"));
            candidate.classList.add("selected");
            return;
        }
        if (!button) return;

        const action = button.dataset.condAction;
        if (action === "edit") {
            const patient = currentConditionsPatient();
            const condition = conditionsOf(patient).find(c => c.icd10_code === code);
            if (!condition) return;
            card.querySelector(".cond-editor-slot").innerHTML = editorHtml(condition);
            card.classList.add("editing");
            card.querySelector(".e-note").focus();
        } else if (action === "cancel") {
            card.querySelector(".cond-editor-slot").innerHTML = "";
            card.classList.remove("editing");
        } else if (action === "suggest") {
            suggestCodes(card, button);
        } else if (action === "save") {
            saveCondition(card, code, button);
        } else if (action === "delete") {
            deleteCondition(code);
        }
    });

    /**
     * Nhờ Gateway chuẩn hóa lại đoạn mô tả bác sĩ vừa sửa và liệt kê các mã ứng viên.
     *
     * Gọi thẳng Gateway chứ không qua HIS: đây là bước tra cứu, chưa đụng gì tới
     * bệnh án, nên không cần đi vòng qua máy chủ bệnh viện.
     */
    async function suggestCodes(card, button) {
        const note = card.querySelector(".e-note").value.trim();
        const box = card.querySelector(".e-candidates");
        if (!note) {
            box.innerHTML = `<p class="cand-empty">Hãy nhập mô tả lâm sàng trước khi xin gợi ý.</p>`;
            return;
        }

        const originalHtml = button.innerHTML;
        button.disabled = true;
        button.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Đang tra cứu...`;
        try {
            const res = await fetch(`${GATEWAY_BASE}/api/standardize`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: note }),
            });
            if (!res.ok) throw new Error(`Gateway trả về HTTP ${res.status}`);
            const data = await res.json();

            // Gateway tách được nhiều bệnh trong một câu thì gộp ứng viên của mọi
            // vế lại; bản cũ chưa có `diagnoses` nên vẫn đọc `predictions`.
            const groups = (data.diagnoses && data.diagnoses.length)
                ? data.diagnoses
                : [{ fragment: note, predictions: data.predictions || [] }];
            const seen = new Set();
            const candidates = [];
            groups.forEach(g => (g.predictions || []).forEach(p => {
                if (seen.has(p.code)) return;
                seen.add(p.code);
                candidates.push(p);
            }));

            box.innerHTML = candidates.length
                ? candidates.map(p => `
                    <div class="cand-item" data-code="${esc(p.code)}" data-name="${esc(p.name_vi)}">
                        <span class="icd-code-badge">${esc(p.code)}</span>
                        ${confidenceTag(p.confidence)}
                        <span class="cand-name">${esc(p.name_vi)}</span>
                    </div>`).join("")
                : `<p class="cand-empty">Mô hình không tìm được mã phù hợp. Bác sĩ nhập tay mã ICD-10.</p>`;
            addLog(`Gợi ý ${candidates.length} mã ICD-10 cho "${note}".`, "info");
        } catch (err) {
            box.innerHTML = `<p class="cand-empty">Không tra cứu được: ${esc(err.message)}</p>`;
            addLog(`Lỗi tra cứu mã ICD-10: ${err.message}`, "error");
        } finally {
            button.disabled = false;
            button.innerHTML = originalHtml;
        }
    }

    async function saveCondition(card, oldCode, button) {
        const payload = {
            icd10_code: card.querySelector(".e-code").value.trim(),
            icd10_display: card.querySelector(".e-name").value.trim(),
            clinical_status: card.querySelector(".e-status").value,
            fragment: card.querySelector(".e-note").value.trim() || null,
        };
        if (!payload.icd10_code || !payload.icd10_display) {
            alert("Cần đủ mã ICD-10 và tên bệnh trước khi lưu.");
            return;
        }

        const originalHtml = button.innerHTML;
        button.disabled = true;
        button.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Đang liên thông...`;
        showTab("raw-response");
        addLog(`=== SỬA CHẨN ĐOÁN ${oldCode} CỦA HỒ SƠ ${conditionsPatientId} ===`, "info");

        try {
            const res = await fetch(
                `/api/patients/${encodeURIComponent(conditionsPatientId)}`
                + `/conditions/${encodeURIComponent(oldCode)}`,
                {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Không sửa được chẩn đoán");

            addLog(data.message, "success");
            if (data.retired_condition_id) {
                addLog(`Đã gỡ bản ghi mang mã cũ khỏi EMR Cloud: `
                     + `Condition/${data.retired_condition_id}`, "info");
            }
            if (data.warning) addLog(data.warning, "error");

            await fetchPatients();
            renderConditionsModal();
            await loadAndDisplayFhirResource(data.fhir_condition_id);
        } catch (err) {
            addLog(`Lỗi sửa chẩn đoán ${oldCode}: ${err.message}`, "error");
            button.disabled = false;
            button.innerHTML = originalHtml;
        }
    }

    async function deleteCondition(code) {
        if (!confirm(`Gỡ hẳn chẩn đoán ${code} khỏi bệnh án và khỏi EMR Cloud?\n\n`
                   + `Nếu bệnh chỉ đã điều trị xong, hãy bấm Sửa và chuyển trạng thái `
                   + `sang "Đã khỏi" để giữ lại bệnh sử.`)) return;

        showTab("raw-response");
        try {
            const res = await fetch(
                `/api/patients/${encodeURIComponent(conditionsPatientId)}`
                + `/conditions/${encodeURIComponent(code)}`,
                { method: "DELETE" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Không gỡ được chẩn đoán");

            addLog(data.message, "success");
            if (data.warning) addLog(data.warning, "error");
            await fetchPatients();
            renderConditionsModal();
        } catch (err) {
            addLog(`Lỗi gỡ chẩn đoán ${code}: ${err.message}`, "error");
        }
    }

    // --- Add Patient Form ---
    addPatientForm.addEventListener("submit", async (e) => {
        e.preventDefault();

        const payload = {
            id: document.getElementById("p-id").value.trim(),
            name: document.getElementById("p-name").value.trim(),
            gender: document.getElementById("p-gender").value,
            birth_date: document.getElementById("p-dob").value,
            clinical_note: document.getElementById("p-clinical").value.trim(),
            // Để trống thì gửi null, không gửi chuỗi rỗng: Gateway phân biệt
            // "không có định danh toàn quốc" với "có nhưng rỗng".
            citizen_id: document.getElementById("p-cccd").value.trim() || null,
            insurance_card: document.getElementById("p-bhyt").value.trim() || null
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
    /**
     * Chạy luồng chuẩn hóa + liên thông cho một hồ sơ.
     *
     * `newNote` khác null nghĩa là "chẩn đoán thêm": gửi dòng bệnh án mới sang
     * endpoint giữ lại các chẩn đoán cũ, thay vì đồng bộ lại toàn bộ hồ sơ.
     */
    async function syncPatientRecord(patientId, btn, newNote = null) {
        const themMoi = newNote !== null;
        const originalBtnHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Xử lý...`;

        addLog(themMoi
            ? `=== CHẨN ĐOÁN THÊM CHO BỆNH NHÂN: ${patientId} ===`
            : `=== BẮT ĐẦU ĐỒNG BỘ BỆNH NHÂN: ${patientId} ===`, "info");
        if (themMoi) addLog(`Bệnh án mới: "${newNote}"`, "info");
        addLog(`[BƯỚC 1/3] Gọi Gateway chuẩn hóa chẩn đoán lâm sàng bằng mô hình NLP...`, "info");
        showTab("raw-response");

        try {
            const res = themMoi
                ? await fetch(`/api/patients/${encodeURIComponent(patientId)}/diagnosis`, {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ clinical_note: newNote })
                  })
                : await fetch(`/api/sync/${encodeURIComponent(patientId)}`, { method: "POST" });
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
