package com.vnpt.his.backend.service;

import com.vnpt.his.backend.model.Patient;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import java.time.Duration;
import java.util.*;

@Service
public class GatewayService {

    @Value("${smig.gateway.url:http://127.0.0.1:8000}")
    private String gatewayUrl;

    /**
     * Cơ sở khám chữa bệnh mà bản HIS này phục vụ.
     *
     * Gửi kèm mỗi yêu cầu sinh Condition để MỘT bản Gateway phục vụ được nhiều
     * bệnh viện - mô hình NLP chiếm vài GB RAM nên chạy hai bản trên một máy thử
     * nghiệm là quá nặng. Thiếu trường này thì Gateway lấy mã trong cấu hình của
     * chính nó, và mọi chẩn đoán của hai bệnh viện đều mang tên cùng một cơ sở.
     */
    @Value("${smig.facility.code:BV-VNPT-02}")
    private String facilityCode;

    @Value("${smig.facility.name:Benh vien VNPT}")
    private String facilityName;

    private final RestTemplate restTemplate = buildRestTemplate();

    /**
     * RestTemplate có thời gian chờ tường minh.
     *
     * Mặc định của {@code new RestTemplate()} là chờ VÔ HẠN. Gateway nạp mô hình
     * NLP nên lần gọi đầu sau khi khởi động có thể chậm; không đặt thời gian chờ
     * thì luồng khám bệnh của HIS treo theo mà không có cách nào thoát.
     */
    private static RestTemplate buildRestTemplate() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(5));
        factory.setReadTimeout(Duration.ofSeconds(60));
        return new RestTemplate(factory);
    }

    /** Chỉ đưa vào thân yêu cầu khi có giá trị thật, tránh gửi chuỗi rỗng. */
    private static void putIfPresent(Map<String, Object> target, String key, String value) {
        if (value != null && !value.trim().isEmpty()) {
            target.put(key, value.trim());
        }
    }

    // A lightweight local fallback dictionary for basic ICD-10 mapping if SMIG Gateway is offline
    private static final Map<String, String> FALLBACK_ICD_DICT = new LinkedHashMap<>();
    static {
        FALLBACK_ICD_DICT.put("I10", "Tăng huyết áp vô căn (nguyên phát)");
        FALLBACK_ICD_DICT.put("E11", "Đái tháo đường tuýp 2");
        FALLBACK_ICD_DICT.put("J00", "Viêm mũi họng cấp (cảm thường)");
        FALLBACK_ICD_DICT.put("J03", "Viêm amidan cấp");
        FALLBACK_ICD_DICT.put("J20", "Viêm phế quản cấp");
        FALLBACK_ICD_DICT.put("K29", "Viêm dạ dày và tá tràng");
        FALLBACK_ICD_DICT.put("M54", "Đau lưng");
        FALLBACK_ICD_DICT.put("N39", "Rối loạn khác của hệ tiết niệu (Nhiễm trùng đường tiểu)");
        FALLBACK_ICD_DICT.put("R50", "Sốt không rõ nguyên nhân");
        FALLBACK_ICD_DICT.put("K30", "Chứng khó tiêu (Đau bao tử)");
    }

    /**
     * Standardizes a raw clinical note by calling SMIG Gateway or falling back to local search.
     */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> standardize(String query) {
        if (query == null || query.trim().isEmpty()) {
            return Collections.emptyList();
        }

        try {
            String url = gatewayUrl + "/api/standardize";
            Map<String, String> request = new HashMap<>();
            request.put("query", query);

            // Call the gateway
            Map<String, Object> response = restTemplate.postForObject(url, request, Map.class);
            if (response != null) {
                // If the response contains 'diagnoses' list
                if (response.containsKey("diagnoses") && response.get("diagnoses") != null) {
                    return (List<Map<String, Object>>) response.get("diagnoses");
                }
                
                // Fallback for older Gateway versions returning 'predictions' directly
                if (response.containsKey("predictions") && response.get("predictions") != null) {
                    Map<String, Object> diagnosisGroup = new HashMap<>();
                    diagnosisGroup.put("fragment", query);
                    diagnosisGroup.put("predictions", response.get("predictions"));
                    return Collections.singletonList(diagnosisGroup);
                }
            }
        } catch (Exception e) {
            System.err.println("SMIG Gateway is unreachable at " + gatewayUrl + ". Using local fallback mapping. Error: " + e.getMessage());
        }

        // Fallback implementation: match text with local dictionary
        return getLocalFallbackSuggestions(query);
    }

    /**
     * Look up the local dictionary by query string
     */
    public List<Map<String, Object>> getLocalFallbackSuggestions(String query) {
        String lowerQuery = query.toLowerCase();
        List<Map<String, Object>> predictions = new ArrayList<>();

        for (Map.Entry<String, String> entry : FALLBACK_ICD_DICT.entrySet()) {
            String code = entry.getKey();
            String name = entry.getValue();

            // Match if code or name matches query
            if (code.toLowerCase().contains(lowerQuery) || name.toLowerCase().contains(lowerQuery)) {
                Map<String, Object> pred = new HashMap<>();
                pred.put("code", code);
                pred.put("name_vi", name);
                // Đây là kết quả tra bảng cứng 10 mã, KHÔNG phải kết quả của mô
                // hình NLP trên 48.391 mục từ. Không được để nó mang vẻ chắc
                // chắn ngang nhau: bác sĩ phải biết mình đang nhìn phương án dự
                // phòng thì mới đánh giá đúng.
                pred.put("confidence", 50.0);
                pred.put("suggested_verification_status", "unconfirmed");
                pred.put("requires_review", true);
                pred.put("source", "fallback");
                pred.put("source_note", "Gateway không phản hồi - tra từ điển cục bộ, cần bác sĩ xác nhận");
                predictions.add(pred);
            }
        }

        // If no match found, create a generic match or return default list
        if (predictions.isEmpty()) {
            // Default to return a list of top 3 common diseases as suggestions
            int count = 0;
            for (Map.Entry<String, String> entry : FALLBACK_ICD_DICT.entrySet()) {
                if (count++ >= 4) break;
                Map<String, Object> pred = new HashMap<>();
                pred.put("code", entry.getKey());
                pred.put("name_vi", entry.getValue());
                pred.put("confidence", 30.0); // Gợi ý chung chung, độ tin cậy thấp hơn nữa
                pred.put("suggested_verification_status", "unconfirmed");
                pred.put("requires_review", true);
                pred.put("source", "fallback");
                pred.put("source_note", "Gateway không phản hồi - gợi ý chung, KHÔNG dựa trên nội dung bệnh án");
                predictions.add(pred);
            }
        }

        Map<String, Object> diagnosisGroup = new HashMap<>();
        diagnosisGroup.put("fragment", query);
        diagnosisGroup.put("predictions", predictions);
        diagnosisGroup.put("source", "fallback");
        return Collections.singletonList(diagnosisGroup);
    }

    /**
     * Synchronizes a patient diagnosis to the HAPI FHIR server via SMIG Gateway.
     */
    @SuppressWarnings("unchecked")
    public String syncToFhir(Patient patient, String clinicalNote, String icdCode, String icdDisplay, double confidence) {
        try {
            // 1. Build FHIR Condition resource
            String conditionUrl = gatewayUrl + "/api/fhir/condition";
            Map<String, Object> condReq = new HashMap<>();
            condReq.put("patient_id", patient.getId());
            condReq.put("patient_name", patient.getName());
            condReq.put("raw_clinical_note", clinicalNote);
            condReq.put("icd10_code", icdCode);
            // Bỏ trống khi HIS không có tên bệnh thật: Gateway tra trong danh mục
            // 12.137 mã của Bộ Y tế, chuẩn hơn bất kỳ bảng nào HIS mang theo. Điền
            // một nhãn chung cho có thì nhãn ấy đi thẳng lên trục thành tên bệnh,
            // và bác sĩ tuyến sau đọc bệnh án về không biết bệnh gì để điều trị.
            putIfPresent(condReq, "icd10_display", icdDisplay);
            condReq.put("confidence_score", confidence);
            condReq.put("clinical_status", "active");
            condReq.put("gender", "Nam".equals(patient.getGender()) ? "male" : "female");
            condReq.put("birth_date", patient.getBirthDate());
            // Định danh cấp quốc gia. Thiếu hai trường này thì trục chỉ quy được
            // hồ sơ trong phạm vi bệnh viện, và cùng một người khám ở nơi khác sẽ
            // thành một bệnh nhân riêng.
            putIfPresent(condReq, "citizen_id", patient.getCitizenId());
            putIfPresent(condReq, "insurance_card", patient.getInsuranceCard());
            // Bệnh viện đang ghi hồ sơ. Nhờ nó một bản Gateway phục vụ được cả
            // HIS này lẫn HIS khác mà chẩn đoán trên trục vẫn ghi đúng nơi ghi.
            putIfPresent(condReq, "facility_code", facilityCode);
            putIfPresent(condReq, "facility_name", facilityName);

            Map<String, Object> fhirCondition = restTemplate.postForObject(conditionUrl, condReq, Map.class);
            if (fhirCondition == null) {
                return null;
            }

            // 2. Push patient and condition resources to EMR Cloud (HAPI FHIR)
            String syncUrl = gatewayUrl + "/api/fhir/sync";
            Map<String, Object> syncReq = new HashMap<>();
            syncReq.put("condition", fhirCondition);
            
            Map<String, Object> patientMap = new HashMap<>();
            patientMap.put("id", patient.getId());
            patientMap.put("name", patient.getName());
            patientMap.put("gender", "Nam".equals(patient.getGender()) ? "male" : "female");
            patientMap.put("birth_date", patient.getBirthDate());
            // Phải khớp với khối gửi ở bước 1: Gateway đối chiếu hai bên và trả
            // 422 nếu định danh mâu thuẫn, thay vì âm thầm chọn một trong hai.
            putIfPresent(patientMap, "citizen_id", patient.getCitizenId());
            putIfPresent(patientMap, "insurance_card", patient.getInsuranceCard());
            syncReq.put("patient", patientMap);

            Map<String, Object> syncResp = restTemplate.postForObject(syncUrl, syncReq, Map.class);
            if (syncResp != null && syncResp.containsKey("condition_id")) {
                return syncResp.get("condition_id").toString();
            }
        } catch (Exception e) {
            System.err.println("Failed to sync FHIR resource for patient " + patient.getId() + ": " + e.getMessage());
        }
        return null;
    }
}
