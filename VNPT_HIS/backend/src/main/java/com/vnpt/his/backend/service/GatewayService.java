package com.vnpt.his.backend.service;

import com.vnpt.his.backend.model.Patient;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import java.util.*;

@Service
public class GatewayService {

    @Value("${smig.gateway.url:http://127.0.0.1:8000}")
    private String gatewayUrl;

    private final RestTemplate restTemplate = new RestTemplate();

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
                pred.put("confidence", 90.0); // High confidence for exact keyword matches
                pred.put("suggested_verification_status", "confirmed");
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
                pred.put("confidence", 50.0); // Low confidence as it's generic suggestion
                pred.put("suggested_verification_status", "unconfirmed");
                predictions.add(pred);
            }
        }

        Map<String, Object> diagnosisGroup = new HashMap<>();
        diagnosisGroup.put("fragment", query);
        diagnosisGroup.put("predictions", predictions);
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
            condReq.put("icd10_display", icdDisplay);
            condReq.put("confidence_score", confidence);
            condReq.put("clinical_status", "active");
            condReq.put("gender", "Nam".equals(patient.getGender()) ? "male" : "female");
            condReq.put("birth_date", patient.getBirthDate());

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
