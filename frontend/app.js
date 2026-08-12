// API Configuration
const API_BASE = window.location.origin;

// Global state variables
let selectedPrediction = null;
let currentFhirPayload = null;
let apiOnline = false;

// DOM Elements
const elApiStatus = document.getElementById('api-status');
const elApiStatusText = document.getElementById('api-status-text');
const elPatientId = document.getElementById('patient-id');
const elPatientName = document.getElementById('patient-name');
const elClinicalNote = document.getElementById('clinical-note');
const elBtnAnalyze = document.getElementById('btn-analyze');
const elBtnClear = document.getElementById('btn-clear');
const elNormOutput = document.getElementById('norm-output');
const elNerOutput = document.getElementById('ner-output');
const elPredictionsOutput = document.getElementById('predictions-output');
const elFhirOutput = document.getElementById('fhir-output');
const elBtnCopyFhir = document.getElementById('btn-copy-fhir');
const elBtnSync = document.getElementById('btn-sync');
const elLatencyBox = document.getElementById('latency-box');
const elLatencyTime = document.getElementById('latency-time');
const elSyncLatencyBox = document.getElementById('sync-latency-box');
const elSyncLatencyTime = document.getElementById('sync-latency-time');
const elSamples = document.getElementById('sample-buttons');

// EMR Portal DOM Elements
const elPortalStatus = document.getElementById('portal-status');
const elPortalRecordsList = document.getElementById('portal-records-list');
const elBtnClearHistory = document.getElementById('btn-clear-history');

// URL extension do dự án sở hữu (khớp backend/fhir_helper.py)
const EXT_CONFIDENCE = 'https://smig.nckh.vn/fhir/StructureDefinition/nlp-confidence-score';

/**
 * Thoát ký tự HTML. Bắt buộc dùng cho mọi giá trị đến từ người dùng hoặc từ máy
 * chủ FHIR trước khi ghép vào innerHTML.
 */
function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

// Initialize App
window.addEventListener('DOMContentLoaded', () => {
    checkApiStatus();
    setupEventListeners();
    fetchSyncedHistory();
    setInterval(checkApiStatus, 10000);
});

function setupEventListeners() {
    elBtnAnalyze.addEventListener('click', analyzeText);
    elBtnClear.addEventListener('click', clearForm);
    elBtnCopyFhir.addEventListener('click', copyFhirToClipboard);
    elBtnSync.addEventListener('click', syncToEmrCloud);
    elBtnClearHistory.addEventListener('click', clearSyncedHistory);

    if (elSamples) {
        elSamples.addEventListener('click', (event) => {
            const btn = event.target.closest('button[data-sample]');
            if (!btn) return;
            elClinicalNote.value = btn.dataset.sample;
            analyzeText();
        });
    }
}

// 1. Health check to monitor if FastAPI server is active
async function checkApiStatus() {
    try {
        const response = await fetch(`${API_BASE}/health`);
        const data = await response.json();

        if (response.ok && data.model_loaded) {
            apiOnline = true;
            elApiStatus.className = 'status-badge online';
            elApiStatusText.textContent = 'API Cổng liên thông: Online';
            elBtnAnalyze.disabled = false;
        } else {
            setApiOffline();
        }
    } catch (error) {
        setApiOffline();
    }
}

function setApiOffline() {
    apiOnline = false;
    elApiStatus.className = 'status-badge';
    elApiStatusText.textContent = 'API Cổng liên thông: Offline';
    elBtnAnalyze.disabled = true;
}

// 2. Submit diagnosis text for NLP processing
async function analyzeText() {
    const text = elClinicalNote.value.trim();
    if (!text) {
        alert('Vui lòng nhập câu chẩn đoán của bác sĩ!');
        return;
    }

    elBtnAnalyze.disabled = true;
    elBtnAnalyze.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Đang phân tích...';

    elNormOutput.innerHTML = '<div class="placeholder-text"><i class="fa-solid fa-spinner fa-spin"></i> Đang chuẩn hóa văn bản...</div>';
    elNerOutput.innerHTML = '<div class="placeholder-text"><i class="fa-solid fa-spinner fa-spin"></i> Đang trích xuất thực thể...</div>';
    elPredictionsOutput.innerHTML = '<div class="placeholder-text"><i class="fa-solid fa-spinner fa-spin"></i> Đang đối sánh mã ICD-10...</div>';

    elFhirOutput.innerHTML = `<span class="json-placeholder">// Sau khi chọn mã ICD-10 ở cột 2\n// Bản ghi FHIR Condition Resource sẽ hiển thị tại đây</span>`;
    elBtnSync.disabled = true;
    selectedPrediction = null;
    currentFhirPayload = null;

    try {
        const response = await fetch(`${API_BASE}/api/standardize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: text })
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || 'Không thể kết nối đến máy chủ phân tích NLP.');
        }

        const data = await response.json();

        elLatencyBox.style.display = 'flex';
        elLatencyTime.textContent = data.latency_ms;

        renderNormalization(data.query, data.normalized_query);
        renderNER(data.query, data.entities);
        renderPredictions(data.predictions, data.diagnoses);
    } catch (error) {
        console.error(error);
        elNormOutput.innerHTML = `<span style="color:var(--status-offline)"><i class="fa-solid fa-circle-exclamation"></i> Lỗi: ${esc(error.message)}</span>`;
        elNerOutput.innerHTML = `<span style="color:var(--status-offline)">Lỗi tải dữ liệu.</span>`;
        elPredictionsOutput.innerHTML = `<span style="color:var(--status-offline)">Lỗi tải dữ liệu.</span>`;
    } finally {
        elBtnAnalyze.disabled = false;
        elBtnAnalyze.innerHTML = '<i class="fa-solid fa-microchip-ai"></i> Phân tích với NLP';
    }
}

/**
 * Bước 1: hiển thị kết quả chuẩn hóa.
 * Các từ được hệ thống thêm/thay thế được suy ra bằng cách so token giữa câu gốc
 * và câu đã chuẩn hóa, thay cho danh sách cụm từ viết cứng trong mã nguồn - danh
 * sách đó vừa phải sửa tay mỗi lần bổ sung viết tắt, vừa từng sai chính tả nên
 * không bao giờ khớp ("hen phế quan").
 */
function renderNormalization(original, normalized) {
    const originalTokens = new Set(original.toLowerCase().split(/\s+/));
    const html = normalized.split(/\s+/).map(word =>
        originalTokens.has(word)
            ? esc(word)
            : `<span class="norm-word-match">${esc(word)}</span>`
    ).join(' ');

    elNormOutput.innerHTML = `<p>${html}</p>`;
}

/**
 * Bước 2: bôi màu thực thể y khoa trong nguyên văn của bác sĩ.
 *
 * Dùng dò vị trí trực tiếp thay cho `new RegExp('\\b' + text + '\\b')`. Trong
 * JavaScript, `\b` chỉ nhận biết [A-Za-z0-9_], nên mọi cụm bắt đầu bằng ký tự
 * tiếng Việt có dấu ("đái tháo đường", "ệ") đều không bao giờ khớp.
 */
function renderNER(originalText, entities) {
    if (!entities.length) {
        elNerOutput.innerHTML = `<span class="placeholder-text">Không tìm thấy thực thể y khoa chuyên biệt.</span>`;
        return;
    }

    const haystack = originalText.toLowerCase();
    const spans = [];

    [...entities]
        .sort((a, b) => b.text.length - a.text.length)
        .forEach(ent => {
            const needle = ent.text.toLowerCase();
            if (!needle) return;

            let from = 0;
            while (from <= haystack.length - needle.length) {
                const at = haystack.indexOf(needle, from);
                if (at === -1) break;
                const end = at + needle.length;
                if (!spans.some(s => at < s.end && end > s.start)) {
                    spans.push({ start: at, end, ent });
                    break;
                }
                from = at + 1;
            }
        });

    spans.sort((a, b) => a.start - b.start);

    let html = '';
    let cursor = 0;
    spans.forEach(({ start, end, ent }) => {
        html += esc(originalText.slice(cursor, start));
        const title = ent.type === 'clinical_alias' ? 'Từ đồng nghĩa lâm sàng'
            : ent.type === 'synonym' ? 'Thuật ngữ thay thế' : 'Thuật ngữ chuẩn';
        html += `<span class="entity-highlight" data-code="${esc(ent.code)}" title="${esc(title)} → ${esc(ent.code)}">`
            + `${esc(originalText.slice(start, end))}</span>`;
        cursor = end;
    });
    html += esc(originalText.slice(cursor));

    elNerOutput.innerHTML = `<p>${html}</p>`;
}

// Bước 3: danh sách mã ICD-10 ứng viên
const BANDS = {
    high: ['conf-high', 'conf-high-bg'],
    medium: ['conf-med', 'conf-med-bg'],
    low: ['conf-low', 'conf-low-bg'],
};

function predictionCard(pred, index) {
    const [confClass, confBgClass] = BANDS[pred.confidence_band] || BANDS.low;
    const reviewFlag = pred.requires_review
        ? `<div class="pred-review-flag"><i class="fa-solid fa-triangle-exclamation"></i> `
          + `Dưới ngưỡng tự động - cần bác sĩ xác nhận (FHIR: ${esc(pred.suggested_verification_status)})</div>`
        : '';
    const reason = (pred.explanation || []).length
        ? `<div class="pred-explain"><i class="fa-solid fa-lightbulb"></i> ${esc(pred.explanation.join('; '))}</div>`
        : '';

    return `
        <div class="pred-item" data-index="${index}">
            <div class="pred-header">
                <span class="pred-code">${esc(pred.code)}</span>
                <span class="pred-code-nodot" title="Mã dạng liền không dấu chấm - dùng cho phần mềm viện và báo cáo BHYT">${esc(pred.code_no_dot || '')}</span>
                <span class="pred-confidence ${confClass}"> Độ tin cậy: ${pred.confidence}%</span>
            </div>
            <div class="pred-details">${esc(pred.name_vi)}</div>
            <div class="pred-match-meta">
                <span>Khớp với: "${esc(pred.matched_by)}"</span>
                <span>Tương đồng ngữ nghĩa: ${pred.similarity_score}</span>
            </div>
            <div class="confidence-bar-bg">
                <div class="confidence-bar-fill ${confBgClass}" style="width: ${pred.confidence}%"></div>
            </div>
            ${reason}
            ${reviewFlag}
        </div>
    `;
}

/**
 * Vẽ danh sách mã ứng viên, nhóm theo từng chẩn đoán khi câu chứa nhiều bệnh.
 *
 * Nhóm lại là bắt buộc chứ không phải trang trí: với "sỏi bàng quang, suy thận
 * cấp" thì N21.0 và N17.9 đều là mã ĐÚNG cho hai bệnh khác nhau, để chung một
 * danh sách phẳng sẽ bị đọc nhầm thành hai phương án loại trừ nhau và bác sĩ
 * chỉ chọn một.
 */
function renderPredictions(predictions, diagnoses) {
    if (!predictions.length) {
        elPredictionsOutput.innerHTML = `<span class="placeholder-text">Không tìm thấy mã ICD-10 phù hợp.</span>`;
        return;
    }

    const multi = Array.isArray(diagnoses) && diagnoses.length > 1;
    const groups = multi ? diagnoses : [{ fragment: null, predictions }];

    const ordered = [];
    let html = multi
        ? `<div class="diagnosis-notice"><i class="fa-solid fa-circle-info"></i> `
          + `Dòng chẩn đoán này chứa ${diagnoses.length} bệnh độc lập - mỗi bệnh cần một `
          + `FHIR Condition riêng.</div>`
        : '';

    groups.forEach((group, groupIndex) => {
        if (group.fragment) {
            html += `<div class="diagnosis-group-header">`
                + `<span class="diagnosis-index">Chẩn đoán ${groupIndex + 1}</span>`
                + `<span class="diagnosis-fragment">"${esc(group.fragment)}"</span></div>`;
        }
        html += (group.predictions || []).map(pred => {
            ordered.push(pred);
            return predictionCard(pred, ordered.length - 1);
        }).join('');
    });

    elPredictionsOutput.innerHTML = html;

    const predItems = elPredictionsOutput.querySelectorAll('.pred-item');
    predItems.forEach(item => {
        item.addEventListener('click', () => {
            predItems.forEach(i => i.classList.remove('selected'));
            item.classList.add('selected');
            selectedPrediction = ordered[parseInt(item.dataset.index, 10)];
            generateFhirResource(selectedPrediction);
        });
    });

    if (predItems.length > 0) predItems[0].click();
}

// 3. Request backend to generate FHIR Condition Resource
async function generateFhirResource(prediction) {
    const patientId = elPatientId.value.trim() || '037098001234';
    const patientName = elPatientName.value.trim() || 'Nguyễn Văn An';
    const rawNote = elClinicalNote.value.trim();

    elFhirOutput.innerHTML = '<span class="json-placeholder">// Đang sinh bản ghi HL7 FHIR...</span>';
    elBtnSync.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/api/fhir/condition`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                patient_id: patientId,
                patient_name: patientName,
                raw_clinical_note: rawNote,
                icd10_code: prediction.code,
                icd10_display: prediction.name_vi,
                confidence_score: prediction.confidence,
                clinical_status: 'active'
            })
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || 'Không thể khởi tạo FHIR Resource.');
        }

        currentFhirPayload = await response.json();
        elFhirOutput.innerHTML = highlightJson(currentFhirPayload);
        elBtnSync.disabled = false;
    } catch (error) {
        console.error(error);
        elFhirOutput.innerHTML = `<span style="color:var(--status-offline)">Lỗi: ${esc(error.message)}</span>`;
    }
}

// 4. Synchronize to EMR Cloud
async function syncToEmrCloud() {
    if (!currentFhirPayload || !selectedPrediction) return;

    elSyncLatencyBox.style.display = 'none';
    elBtnSync.disabled = true;
    elBtnSync.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> ĐANG ĐỒNG BỘ HỒ SƠ...';

    if (elPortalRecordsList.children.length === 0) {
        elPortalStatus.innerHTML = '<i class="fa-solid fa-tower-broadcast fa-fade"></i> Đang tải gói tin lên EMR Cloud Server y tế...';
        elPortalStatus.className = 'portal-status-idle';
    }

    const startTime = performance.now();

    try {
        const response = await fetch(`${API_BASE}/api/fhir/sync`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                condition: currentFhirPayload,
                patient: {
                    id: elPatientId.value.trim() || '037098001234',
                    name: elPatientName.value.trim() || 'Nguyễn Văn An'
                }
            })
        });

        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Không thể kết nối đến máy chủ liên thông.');

        // Đo đúng thời gian khứ hồi thật, không cộng thêm độ trễ trang trí.
        elSyncLatencyTime.textContent = (performance.now() - startTime).toFixed(0);
        elSyncLatencyBox.style.display = 'inline-block';

        elBtnSync.innerHTML = '<i class="fa-solid fa-check"></i> ĐỒNG BỘ THÀNH CÔNG!';
        elBtnSync.className = 'btn btn-primary btn-full';

        await fetchSyncedHistory();

        setTimeout(() => {
            elBtnSync.disabled = false;
            elBtnSync.innerHTML = '<i class="fa-solid fa-cloud-arrow-up"></i> ĐỒNG BỘ LÊN EMR CLOUD';
            elBtnSync.className = 'btn btn-accent btn-full';
        }, 3000);
    } catch (error) {
        console.error(error);
        alert('Lỗi liên thông EMR Cloud: ' + error.message);
        elBtnSync.disabled = false;
        elBtnSync.innerHTML = '<i class="fa-solid fa-cloud-arrow-up"></i> ĐỒNG BỘ LÊN EMR CLOUD';
    }
}

// 5. Fetch synced records
async function fetchSyncedHistory() {
    try {
        const response = await fetch(`${API_BASE}/api/fhir/sync`);
        if (!response.ok) throw new Error('Không thể tải lịch sử liên thông.');
        renderSyncedHistory(await response.json());
    } catch (error) {
        console.error('Lỗi tải lịch sử:', error);
    }
}

function renderSyncedHistory(records) {
    if (!records.length) {
        elPortalStatus.style.display = 'flex';
        elPortalStatus.innerHTML = '<i class="fa-solid fa-database"></i> Chưa có bệnh án nào được liên thông.';
        elPortalRecordsList.style.display = 'none';
        elPortalRecordsList.innerHTML = '';
        return;
    }

    elPortalStatus.style.display = 'none';
    elPortalRecordsList.style.display = 'flex';

    elPortalRecordsList.innerHTML = records.map(record => {
        const patientName = record.subject?.display || 'N/A';
        const patientId = record.subject?.reference?.replace('Patient/', '') || 'N/A';
        const icdCoding = record.code?.coding?.[0] || {};
        const nlpExt = record.extension?.find(e => e.url === EXT_CONFIDENCE);
        const confidencePct = nlpExt ? Math.round(nlpExt.valueDecimal * 100) : 0;
        const verification = record.verificationStatus?.coding?.[0]?.code || 'unknown';

        const confClass = confidencePct >= 85 ? 'conf-high' : confidencePct >= 60 ? 'conf-med' : 'conf-low';

        let formattedTime = 'N/A';
        if (record.recordedDate) {
            const date = new Date(record.recordedDate);
            formattedTime = isNaN(date) ? record.recordedDate
                : `${date.toLocaleTimeString('vi-VN')} ${date.toLocaleDateString('vi-VN')}`;
        }

        return `
            <div class="portal-record">
                <div class="record-badge"><i class="fa-solid fa-check"></i> Đã đồng bộ</div>
                <div class="record-row"><strong>Bệnh nhân:</strong> ${esc(patientName)} (${esc(patientId)})</div>
                <div class="record-row"><strong>Mã ICD-10:</strong> <span class="code-pill">${esc(icdCoding.code || 'N/A')}</span></div>
                <div class="record-row"><strong>Chẩn đoán chuẩn:</strong> ${esc(icdCoding.display || 'N/A')}</div>
                <div class="record-row"><strong>Độ tin cậy AI:</strong> <span class="${confClass}">${confidencePct}%</span>
                    &nbsp;<strong>Xác nhận:</strong> ${esc(verification)}</div>
                <div class="record-row"><strong>Thời gian đồng bộ:</strong> ${esc(formattedTime)}</div>
            </div>
        `;
    }).join('');
}

// 6. Clear synced records on EMR Cloud
async function clearSyncedHistory() {
    if (!confirm('Bạn có chắc chắn muốn xóa toàn bộ lịch sử bệnh án đã liên thông trên EMR Cloud không?')) return;

    try {
        const response = await fetch(`${API_BASE}/api/fhir/sync`, { method: 'DELETE' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Không thể xóa lịch sử.');

        await fetchSyncedHistory();
        alert(data.message || 'Đã xóa lịch sử liên thông.');
    } catch (error) {
        alert('Lỗi khi xóa lịch sử: ' + error.message);
    }
}

function clearForm() {
    elClinicalNote.value = '';
    elNormOutput.innerHTML = '<span class="placeholder-text">Chờ phân tích dữ liệu lâm sàng...</span>';
    elNerOutput.innerHTML = '<span class="placeholder-text">Chờ nhận dạng thực thể y tế...</span>';
    elPredictionsOutput.innerHTML = '<span class="placeholder-text">Chờ kết quả so khớp tương đồng...</span>';
    elFhirOutput.innerHTML = `<span class="json-placeholder">// Sau khi chọn mã ICD-10 ở cột 2\n// Bản ghi FHIR Condition Resource sẽ hiển thị tại đây</span>`;
    elBtnSync.disabled = true;
    elLatencyBox.style.display = 'none';
    elSyncLatencyBox.style.display = 'none';

    selectedPrediction = null;
    currentFhirPayload = null;
}

function copyFhirToClipboard() {
    if (!currentFhirPayload) {
        alert('Chưa có bản ghi FHIR để sao chép!');
        return;
    }

    navigator.clipboard.writeText(JSON.stringify(currentFhirPayload, null, 2)).then(() => {
        const origHTML = elBtnCopyFhir.innerHTML;
        elBtnCopyFhir.innerHTML = '<i class="fa-solid fa-check"></i> Đã chép!';
        setTimeout(() => { elBtnCopyFhir.innerHTML = origHTML; }, 1500);
    }).catch(err => alert('Lỗi khi sao chép: ' + err));
}

// JSON syntax highlighter
function highlightJson(jsonObj) {
    const json = esc(JSON.stringify(jsonObj, null, 2));
    return json.replace(
        /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
        (match) => {
            let cls = 'json-number';
            if (/^"/.test(match)) cls = /:$/.test(match) ? 'json-key' : 'json-string';
            else if (/true|false/.test(match)) cls = 'json-boolean';
            else if (/null/.test(match)) cls = 'json-null';
            return `<span class="${cls}">${match}</span>`;
        }
    );
}
