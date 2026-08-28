package com.vnpt.his.backend.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriComponentsBuilder;

import java.time.Duration;
import java.util.*;

/**
 * Tra cứu bệnh nhân trên EMR Cloud (trục dữ liệu HL7 FHIR).
 *
 * Dùng khi bệnh nhân KHÔNG có hồ sơ tại bệnh viện này - tức ca chuyển tuyến, đúng
 * lúc bác sĩ cần bệnh sử nhất. Đọc trực tiếp từ máy chủ FHIR, cùng cách với bản
 * HIS Python ({@code hospital_his/server.py}), để hai bản HIS cho ra cùng một kết
 * quả khi tra cùng một người.
 *
 * <p><b>Chỉ ĐỌC.</b> Mọi đường ghi lên trục vẫn đi qua SMIG Gateway
 * ({@link GatewayService}), vì chỉ Gateway mới gắn được mã cơ sở và khóa nghiệp vụ.
 */
@Service
public class EmrLookupService {

    @Value("${smig.fhir.url:http://127.0.0.1:8090/fhir}")
    private String fhirUrl;

    /**
     * Mã cơ sở của chính bệnh viện này, dùng dựng {@code identifier.system} của mã
     * bệnh án. Phải khớp với {@code smig.facility.code} mà {@link GatewayService}
     * gửi lên, nếu không thì hồ sơ ghi lên một namespace mà tra cứu lại tìm ở
     * namespace khác, và kết quả luôn rỗng mà không có lỗi nào hiện ra.
     */
    @Value("${smig.facility.code:BV-VNPT-02}")
    private String facilityCode;

    private static final String NS = "https://smig.nckh.vn/fhir";
    private static final String SYSTEM_CCCD = NS + "/identifier/cccd";
    private static final String SYSTEM_BHYT = NS + "/identifier/bhyt";
    private static final String SYSTEM_FACILITY = NS + "/identifier/co-so-kcb";

    private final RestTemplate restTemplate = buildRestTemplate();

    /** Cùng lý do với {@link GatewayService}: mặc định của RestTemplate là chờ vô hạn. */
    private static RestTemplate buildRestTemplate() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(3));
        factory.setReadTimeout(Duration.ofSeconds(10));
        return new RestTemplate(factory);
    }

    /**
     * Chuẩn hóa mã cơ sở đúng như Gateway làm ({@code fhir_helper.to_fhir_id}).
     *
     * Ghép thẳng mã thô thì với mã sạch như "BV-VNPT-02" hai bên vẫn trùng, nhưng
     * mã có dấu gạch dưới hay khoảng trắng sẽ cho hai chuỗi khác nhau - và tra cứu
     * im lặng trả về rỗng chứ không báo lỗi gì.
     */
    static String toFhirId(String value) {
        String cleaned = (value == null ? "" : value.trim()).replaceAll("[^A-Za-z0-9.\\-]", "-");
        cleaned = cleaned.replaceAll("-{2,}", "-").replaceAll("^-+|-+$", "");
        if (cleaned.isEmpty()) {
            cleaned = "khong-ro-co-so";
        }
        return cleaned.length() > 64 ? cleaned.substring(0, 64) : cleaned;
    }

    private String mrnSystem() {
        return NS + "/identifier/mrn/" + toFhirId(facilityCode);
    }

    /**
     * Tìm một bệnh nhân trên trục theo bất kỳ định danh nào bác sĩ đang cầm.
     *
     * Thử lần lượt <b>mã bệnh án → CCCD → thẻ BHYT</b>. Thứ tự này quan trọng: mã
     * bệnh án chỉ có nghĩa trong nội bộ viện này nên với bệnh nhân chuyển từ nơi
     * khác tới thì nó luôn trượt, và chỉ CCCD/BHYT mới tra ra. Hỏi mỗi namespace
     * mã bệnh án là ca chuyển tuyến không bao giờ thấy bệnh sử.
     *
     * @return bản đồ mô tả hồ sơ, hoặc {@code null} nếu trục không có ai khớp
     */
    public Map<String, Object> timBenhNhan(String dinhDanh) {
        String q = dinhDanh == null ? "" : dinhDanh.trim();
        if (q.isEmpty()) {
            return null;
        }

        for (String he : List.of(mrnSystem(), SYSTEM_CCCD, SYSTEM_BHYT)) {
            Map<String, Object> resource = timTaiNguyenDauTien("Patient", "identifier", he + "|" + q);
            if (resource != null) {
                return dungHoSo(resource);
            }
        }
        return null;
    }

    /** Gọi một phép tìm kiếm FHIR và lấy tài nguyên đầu tiên trong Bundle. */
    @SuppressWarnings("unchecked")
    private Map<String, Object> timTaiNguyenDauTien(String loai, String thamSo, String giaTri) {
        String url = UriComponentsBuilder.fromHttpUrl(fhirUrl)
                .pathSegment(loai)
                .queryParam(thamSo, giaTri)
                .build()
                .encode()
                .toUriString();
        try {
            Map<String, Object> bundle = restTemplate.getForObject(url, Map.class);
            List<Map<String, Object>> entries =
                    bundle == null ? null : (List<Map<String, Object>>) bundle.get("entry");
            if (entries == null || entries.isEmpty()) {
                return null;
            }
            return (Map<String, Object>) entries.get(0).get("resource");
        } catch (Exception e) {
            // EMR Cloud không chạy là chuyện bình thường khi demo cục bộ. Trả rỗng
            // để phía gọi báo "không tìm thấy", thay vì làm hỏng cả màn tiếp đón.
            System.err.println("[EmrLookup] Không đọc được " + url + ": " + e.getMessage());
            return null;
        }
    }

    /** Dựng hồ sơ trả về giao diện từ tài nguyên Patient, kèm các chẩn đoán. */
    @SuppressWarnings("unchecked")
    private Map<String, Object> dungHoSo(Map<String, Object> resource) {
        String emrId = (String) resource.get("id");

        Map<String, String> dinhDanh = new HashMap<>();
        for (Map<String, Object> i : (List<Map<String, Object>>)
                resource.getOrDefault("identifier", List.of())) {
            Object he = i.get("system"), gt = i.get("value");
            if (he != null && gt != null) {
                dinhDanh.put(he.toString(), gt.toString());
            }
        }

        Map<String, Object> hoSo = new LinkedHashMap<>();
        // Giao diện thao tác theo mã bệnh án, không theo id nội bộ của EMR. Tra
        // bằng CCCD thì lấy chính số CCCD làm "mã bệnh án" sẽ hiển thị sai, nên ưu
        // tiên mã bệnh án đọc được từ hồ sơ.
        hoSo.put("id", dinhDanh.getOrDefault(mrnSystem(), emrId));
        hoSo.put("emrId", emrId);
        hoSo.put("name", docHoTen(resource));
        hoSo.put("gender", docGioiTinh(resource));
        hoSo.put("birthDate", resource.getOrDefault("birthDate", "---"));
        hoSo.put("citizenId", dinhDanh.get(SYSTEM_CCCD));
        hoSo.put("insuranceCard", dinhDanh.get(SYSTEM_BHYT));
        // Hồ sơ chỉ đang ĐỌC từ trục, chưa tiếp nhận vào viện này nên không có bản
        // ghi cục bộ để sửa. Giao diện dựa vào cờ này để hiện dạng chỉ đọc.
        hoSo.put("isLocal", false);
        hoSo.put("conditions", docChanDoan(emrId));
        return hoSo;
    }

    @SuppressWarnings("unchecked")
    private static String docHoTen(Map<String, Object> resource) {
        List<Map<String, Object>> names =
                (List<Map<String, Object>>) resource.getOrDefault("name", List.of());
        if (names.isEmpty()) {
            return "Bệnh nhân EMR";
        }
        Map<String, Object> n = names.get(0);
        Object text = n.get("text");
        if (text != null && !text.toString().trim().isEmpty()) {
            return text.toString();
        }
        List<String> given = (List<String>) n.getOrDefault("given", List.of());
        String hoTen = (String.join(" ", given) + " " + n.getOrDefault("family", "")).trim();
        return hoTen.isEmpty() ? "Bệnh nhân EMR" : hoTen;
    }

    private static String docGioiTinh(Map<String, Object> resource) {
        Object g = resource.get("gender");
        if ("male".equals(g)) return "Nam";
        if ("female".equals(g)) return "Nữ";
        return "Khác";
    }

    /**
     * Đọc các chẩn đoán của một hồ sơ trên trục.
     *
     * Mỗi chẩn đoán mang theo cơ sở đã lập ra nó, và cờ {@code laNgoaiVien} để giao
     * diện phân biệt bản ghi của viện mình với bản ghi do nơi khác lập. Không phân
     * biệt được thì bác sĩ không biết mình đang đọc bệnh sử của ai ghi.
     */
    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> docChanDoan(String emrPatientId) {
        List<Map<String, Object>> ketQua = new ArrayList<>();
        String url = UriComponentsBuilder.fromHttpUrl(fhirUrl)
                .pathSegment("Condition")
                .queryParam("subject", "Patient/" + emrPatientId)
                .build()
                .encode()
                .toUriString();

        Map<String, Object> bundle;
        try {
            bundle = restTemplate.getForObject(url, Map.class);
        } catch (Exception e) {
            System.err.println("[EmrLookup] Không đọc được chẩn đoán: " + e.getMessage());
            return ketQua;
        }
        if (bundle == null) {
            return ketQua;
        }

        for (Map<String, Object> entry :
                (List<Map<String, Object>>) bundle.getOrDefault("entry", List.of())) {
            Map<String, Object> cond = (Map<String, Object>) entry.get("resource");
            if (cond == null) {
                continue;
            }

            Map<String, Object> code = (Map<String, Object>) cond.getOrDefault("code", Map.of());
            List<Map<String, Object>> codings =
                    (List<Map<String, Object>>) code.getOrDefault("coding", List.of());
            Map<String, Object> coding = codings.isEmpty() ? Map.of() : codings.get(0);

            List<Map<String, Object>> notes =
                    (List<Map<String, Object>>) cond.getOrDefault("note", List.of());
            String ghiChu = notes.isEmpty() ? "" : String.valueOf(notes.get(0).getOrDefault("text", ""));

            Map<String, Object> meta = (Map<String, Object>) cond.getOrDefault("meta", Map.of());
            Map<String, Object> nguon = Map.of();
            for (Map<String, Object> tag :
                    (List<Map<String, Object>>) meta.getOrDefault("tag", List.of())) {
                if (SYSTEM_FACILITY.equals(tag.get("system"))) {
                    nguon = tag;
                    break;
                }
            }
            Object maCoSo = nguon.get("code");

            Map<String, Object> item = new LinkedHashMap<>();
            item.put("conditionId", cond.get("id"));
            item.put("icd10Code", coding.getOrDefault("code", ""));
            item.put("icd10Display", coding.getOrDefault("display", ""));
            item.put("note", ghiChu.isEmpty() ? "Chẩn đoán liên thông" : ghiChu);
            item.put("facilityCode", maCoSo);
            item.put("facilityName", nguon.getOrDefault("display", maCoSo));
            item.put("laNgoaiVien", maCoSo != null && !maCoSo.equals(facilityCode));
            item.put("recordedDate", cond.getOrDefault("recordedDate", null));
            ketQua.add(item);
        }
        return ketQua;
    }
}
