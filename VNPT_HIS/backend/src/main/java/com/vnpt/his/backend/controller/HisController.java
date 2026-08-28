package com.vnpt.his.backend.controller;

import com.vnpt.his.backend.model.*;
import com.vnpt.his.backend.repository.*;
import com.vnpt.his.backend.service.EmrLookupService;
import com.vnpt.his.backend.service.GatewayService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;
import java.util.*;

@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "*")
public class HisController {

    @Autowired
    private PatientRepository patientRepository;

    @Autowired
    private QueueTicketRepository queueTicketRepository;

    @Autowired
    private ExamRecordRepository examRecordRepository;

    @Autowired
    private DrugItemRepository drugItemRepository;

    @Autowired
    private PrescriptionItemRepository prescriptionItemRepository;

    @Autowired
    private ServiceBillRepository serviceBillRepository;

    @Autowired
    private GatewayService gatewayService;

    @Autowired
    private EmrLookupService emrLookupService;

    // --- RECEPTION & PATIENTS ---

    @GetMapping("/patients")
    public List<Patient> getAllPatients() {
        return patientRepository.findAll();
    }

    /**
     * Tra cứu bệnh nhân theo mã bệnh án, CCCD hoặc thẻ BHYT.
     *
     * Hai tầng, đúng thứ tự đó:
     *
     * <ol>
     *   <li><b>Nội viện trước.</b> Hồ sơ của chính bệnh viện này thì bác sĩ sửa
     *       được, nên phải ưu tiên - hỏi trục trước rồi trả về bản chỉ đọc là lấy
     *       mất quyền sửa một hồ sơ vốn có thể sửa.</li>
     *   <li><b>EMR Cloud sau.</b> Không có nội viện nghĩa là bệnh nhân chưa từng
     *       khám ở đây - đúng ca chuyển tuyến, và là lúc cần bệnh sử nhất.</li>
     * </ol>
     *
     * Không tìm thấy ở cả hai nơi vẫn trả 200 kèm {@code found=false}, không trả
     * 404: "không có hồ sơ" là một câu trả lời hợp lệ của nghiệp vụ tiếp đón, còn
     * 404 sẽ lẫn với lỗi sai đường dẫn và giao diện không phân biệt được.
     */
    @GetMapping("/patients/lookup")
    public ResponseEntity<?> lookupPatient(@RequestParam("q") String q) {
        String query = q == null ? "" : q.trim();
        if (query.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of(
                    "found", false,
                    "message", "Chưa nhập định danh cần tra."));
        }

        List<Patient> noiVien = patientRepository.timTheoDinhDanh(query);
        if (!noiVien.isEmpty()) {
            Patient p = noiVien.get(0);
            Map<String, Object> hoSo = new LinkedHashMap<>();
            hoSo.put("id", p.getId());
            hoSo.put("name", p.getName());
            hoSo.put("gender", p.getGender());
            hoSo.put("birthDate", p.getBirthDate());
            hoSo.put("citizenId", p.getCitizenId());
            hoSo.put("insuranceCard", p.getInsuranceCard());
            hoSo.put("address", p.getAddress());
            hoSo.put("isLocal", true);
            hoSo.put("conditions", List.of());
            return ResponseEntity.ok(Map.of(
                    "found", true, "source", "LOCAL", "patient", hoSo));
        }

        Map<String, Object> tuTruc = emrLookupService.timBenhNhan(query);
        if (tuTruc != null) {
            return ResponseEntity.ok(Map.of(
                    "found", true, "source", "EMR", "patient", tuTruc));
        }

        return ResponseEntity.ok(Map.of(
                "found", false,
                "message", "Không tìm thấy '" + query
                        + "' ở nội viện lẫn trên EMR Cloud."));
    }

    @PostMapping("/patients")
    public ResponseEntity<?> registerPatient(@RequestBody Patient patient) {
        if (patient.getId() == null || patient.getId().trim().isEmpty()) {
            // Auto generate ID if not provided
            String id = "BN" + String.format("%04d", (patientRepository.count() + 1));
            patient.setId(id);
        }
        if (patientRepository.existsById(patient.getId())) {
            return ResponseEntity.badRequest().body(Map.of("message", "Mã bệnh nhân " + patient.getId() + " đã tồn tại!"));
        }
        Patient saved = patientRepository.save(patient);
        return ResponseEntity.ok(saved);
    }

    @DeleteMapping("/patients/{id}")
    @Transactional
    public ResponseEntity<?> deletePatient(@PathVariable String id) {
        if (!patientRepository.existsById(id)) {
            return ResponseEntity.notFound().build();
        }
        // Clean up linked data
        List<QueueTicket> tickets = queueTicketRepository.findByPatientIdAndStatus(id, "WAITING");
        queueTicketRepository.deleteAll(tickets);
        
        List<ServiceBill> bills = serviceBillRepository.findByPatientId(id);
        serviceBillRepository.deleteAll(bills);

        patientRepository.deleteById(id);
        return ResponseEntity.ok(Map.of("message", "Đã xóa hồ sơ bệnh nhân " + id));
    }

    // --- QUEUE MANAGEMENT ---

    @GetMapping("/queue")
    public List<QueueTicket> getQueue() {
        return queueTicketRepository.findAll();
    }

    @PostMapping("/queue/register")
    @Transactional
    public ResponseEntity<?> registerQueue(@RequestBody Map<String, String> payload) {
        String patientId = payload.get("patientId");
        String clinicRoom = payload.get("clinicRoom");

        Patient patient = patientRepository.findById(patientId)
                .orElseThrow(() -> new IllegalArgumentException("Không tìm thấy bệnh nhân ID: " + patientId));

        // Get max ticket number for today
        int nextNum = 1001;
        List<QueueTicket> allTickets = queueTicketRepository.findAll();
        if (!allTickets.isEmpty()) {
            nextNum = allTickets.stream()
                    .mapToInt(QueueTicket::getTicketNumber)
                    .max()
                    .orElse(1000) + 1;
        }

        QueueTicket ticket = new QueueTicket(nextNum, patient, clinicRoom, "WAITING");
        QueueTicket saved = queueTicketRepository.save(ticket);
        return ResponseEntity.ok(saved);
    }

    @PostMapping("/queue/update-status")
    public ResponseEntity<?> updateQueueStatus(@RequestBody Map<String, Object> payload) {
        Long ticketId = Long.valueOf(payload.get("id").toString());
        String status = payload.get("status").toString();

        QueueTicket ticket = queueTicketRepository.findById(ticketId)
                .orElseThrow(() -> new IllegalArgumentException("Không tìm thấy số thứ tự ID: " + ticketId));

        ticket.setStatus(status);
        QueueTicket saved = queueTicketRepository.save(ticket);
        return ResponseEntity.ok(saved);
    }

    // --- CLINICAL EXAMINATION ---

    @PostMapping("/exam/standardize")
    public List<Map<String, Object>> standardizeDiagnosis(@RequestBody Map<String, String> payload) {
        String clinicalNote = payload.get("clinicalNote");
        return gatewayService.standardize(clinicalNote);
    }

    @PostMapping("/exam/save")
    @Transactional
    public ResponseEntity<?> saveExamRecord(@RequestBody Map<String, Object> payload) {
        String patientId = payload.get("patientId").toString();
        Patient patient = patientRepository.findById(patientId)
                .orElseThrow(() -> new IllegalArgumentException("Không tìm thấy bệnh nhân ID: " + patientId));

        // Create or Update Exam Record
        ExamRecord record = new ExamRecord();
        if (payload.containsKey("id") && payload.get("id") != null) {
            Long id = Long.valueOf(payload.get("id").toString());
            record = examRecordRepository.findById(id).orElse(new ExamRecord());
        }
        
        record.setPatient(patient);
        record.setClinicalNote((String) payload.get("clinicalNote"));
        record.setPulse(payload.get("pulse") != null ? Integer.parseInt(payload.get("pulse").toString()) : null);
        record.setTemperature(payload.get("temperature") != null ? Double.parseDouble(payload.get("temperature").toString()) : null);
        record.setBloodPressure((String) payload.get("bloodPressure"));
        record.setBreathingRate(payload.get("breathingRate") != null ? Integer.parseInt(payload.get("breathingRate").toString()) : null);
        record.setWeight(payload.get("weight") != null ? Double.parseDouble(payload.get("weight").toString()) : null);
        record.setHeight(payload.get("height") != null ? Double.parseDouble(payload.get("height").toString()) : null);
        
        record.setPrimaryIcdCode((String) payload.get("primaryIcdCode"));
        record.setPrimaryIcdName((String) payload.get("primaryIcdName"));
        record.setSecondaryIcdCodes((String) payload.get("secondaryIcdCodes")); // stored as comma-separated
        
        record.setExamStatus((String) payload.get("examStatus")); // DRAFT or COMPLETED
        record.setClinicRoom((String) payload.get("clinicRoom"));
        
        ExamRecord savedRecord = examRecordRepository.save(record);

        // Save Prescription Items
        if (payload.containsKey("prescription") && payload.get("prescription") != null) {
            // Delete old prescription items if editing
            prescriptionItemRepository.deleteByExamRecordId(savedRecord.getId());

            List<Map<String, Object>> prescList = (List<Map<String, Object>>) payload.get("prescription");
            for (Map<String, Object> pItem : prescList) {
                Long drugId = Long.valueOf(pItem.get("drugId").toString());
                Integer qty = Integer.parseInt(pItem.get("quantity").toString());
                String dosage = (String) pItem.get("dosage");

                DrugItem drug = drugItemRepository.findById(drugId)
                        .orElseThrow(() -> new IllegalArgumentException("Không tìm thấy thuốc ID: " + drugId));

                PrescriptionItem item = new PrescriptionItem(savedRecord.getId(), drug, qty, dosage);
                prescriptionItemRepository.save(item);
            }
        }

        // Save Services Ordered (Cận lâm sàng)
        if (payload.containsKey("services") && payload.get("services") != null) {
            List<Map<String, Object>> serviceList = (List<Map<String, Object>>) payload.get("services");
            for (Map<String, Object> sItem : serviceList) {
                String serviceName = sItem.get("serviceName").toString();
                Double price = Double.parseDouble(sItem.get("price").toString());
                
                // BHYT Sharing calculation
                Double bhytShare = price * 0.8; // Default 80% coverage
                Double patientPay = price * 0.2; // 20% patient co-payment

                // Check if already ordered to prevent duplicate
                List<ServiceBill> existingBills = serviceBillRepository.findByExamRecordId(savedRecord.getId());
                boolean exists = existingBills.stream().anyMatch(b -> b.getServiceName().equals(serviceName));
                if (!exists) {
                    ServiceBill bill = new ServiceBill(patient, savedRecord.getId(), serviceName, price, bhytShare, patientPay, "ORDERED");
                    serviceBillRepository.save(bill);
                }
            }
        }

        // Update queue ticket status for this patient in this room to COMPLETED if status is COMPLETED
        if ("COMPLETED".equals(record.getExamStatus())) {
            List<QueueTicket> tickets = queueTicketRepository.findByPatientIdAndStatus(patientId, "EXAMINING");
            if (tickets.isEmpty()) {
                tickets = queueTicketRepository.findByPatientIdAndStatus(patientId, "WAITING");
            }
            for (QueueTicket t : tickets) {
                t.setStatus("COMPLETED");
                queueTicketRepository.save(t);
            }

            // Sync clinical diagnoses to EMR Cloud (HAPI FHIR) via SMIG Gateway
            String primaryCode = record.getPrimaryIcdCode();
            String primaryName = record.getPrimaryIcdName();
            if (primaryCode != null && !primaryCode.isEmpty()) {
                gatewayService.syncToFhir(patient, record.getClinicalNote(), primaryCode, primaryName, 95.0);
            }

            String secondaryCodes = record.getSecondaryIcdCodes();
            if (secondaryCodes != null && !secondaryCodes.isEmpty()) {
                for (String code : secondaryCodes.split(",")) {
                    if (!code.trim().isEmpty()) {
                        gatewayService.syncToFhir(patient, record.getClinicalNote(), code.trim(), "Chẩn đoán kèm theo", 90.0);
                    }
                }
            }
        }

        return ResponseEntity.ok(savedRecord);
    }

    @GetMapping("/exam/patient/{patientId}")
    public List<ExamRecord> getExamHistory(@PathVariable String patientId) {
        return examRecordRepository.findByPatientId(patientId);
    }

    @GetMapping("/exam/records")
    public List<ExamRecord> getAllExamRecords() {
        return examRecordRepository.findAll();
    }

    // --- PHARMACY & DRUGS ---

    @GetMapping("/drugs")
    public List<DrugItem> getDrugs(@RequestParam(required = false) String search) {
        if (search != null && !search.trim().isEmpty()) {
            return drugItemRepository.findByNameContainingIgnoreCase(search);
        }
        return drugItemRepository.findAll();
    }

    @PostMapping("/drugs")
    public DrugItem saveDrug(@RequestBody DrugItem drug) {
        return drugItemRepository.save(drug);
    }

    @GetMapping("/pharmacy/prescriptions")
    public List<Map<String, Object>> getPendingPrescriptions() {
        List<ExamRecord> completedExams = examRecordRepository.findAll(); // simplified filter
        List<Map<String, Object>> result = new ArrayList<>();

        for (ExamRecord exam : completedExams) {
            List<PrescriptionItem> items = prescriptionItemRepository.findByExamRecordId(exam.getId());
            if (items.isEmpty()) continue;

            Map<String, Object> map = new HashMap<>();
            map.put("examRecordId", exam.getId());
            map.put("patient", exam.getPatient());
            map.put("primaryIcdCode", exam.getPrimaryIcdCode());
            map.put("primaryIcdName", exam.getPrimaryIcdName());
            map.put("clinicRoom", exam.getClinicRoom());
            map.put("createdAt", exam.getCreatedAt());
            map.put("items", items);

            // Simple check: check if already paid/dispensed in billing or custom status
            // For prototype, we show all, but pharmacy can click "Dispense"
            result.add(map);
        }
        return result;
    }

    @PostMapping("/pharmacy/dispense/{examRecordId}")
    @Transactional
    public ResponseEntity<?> dispensePrescription(@PathVariable Long examRecordId) {
        List<PrescriptionItem> items = prescriptionItemRepository.findByExamRecordId(examRecordId);
        if (items.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("message", "Không tìm thấy đơn thuốc cho bệnh án này!"));
        }

        // Deduct inventory
        for (PrescriptionItem item : items) {
            DrugItem drug = item.getDrug();
            int qty = item.getQuantity();
            if (drug.getStock() < qty) {
                return ResponseEntity.badRequest().body(Map.of("message", "Thuốc '" + drug.getName() + "' không đủ số lượng tồn kho (Tồn: " + drug.getStock() + ", Cần: " + qty + ")"));
            }
            drug.setStock(drug.getStock() - qty);
            drugItemRepository.save(drug);
        }

        return ResponseEntity.ok(Map.of("message", "Đã cấp phát thuốc thành công!"));
    }

    // --- LABORATORY / CLS ---

    @GetMapping("/cls/orders")
    public List<ServiceBill> getClsOrders() {
        // Services with status ORDERED or COMPLETED (not PAID yet, or paid but ordered for testing)
        return serviceBillRepository.findAll();
    }

    @PostMapping("/cls/result")
    public ResponseEntity<?> enterClsResult(@RequestBody Map<String, Object> payload) {
        Long billId = Long.valueOf(payload.get("id").toString());
        String resultText = (String) payload.get("resultText");
        String resultImage = (String) payload.get("resultImage"); // mockup image url or base64

        ServiceBill bill = serviceBillRepository.findById(billId)
                .orElseThrow(() -> new IllegalArgumentException("Không tìm thấy chỉ định dịch vụ ID: " + billId));

        bill.setResultText(resultText);
        bill.setResultImage(resultImage);
        bill.setStatus("COMPLETED"); // Mark as completed (result ready)
        ServiceBill saved = serviceBillRepository.save(bill);
        return ResponseEntity.ok(saved);
    }

    // --- BILLING / HỌC PHÍ / VIỆN PHÍ ---

    @GetMapping("/billing/bills")
    public List<ServiceBill> getBills(@RequestParam(required = false) String patientId) {
        if (patientId != null && !patientId.trim().isEmpty()) {
            return serviceBillRepository.findByPatientId(patientId);
        }
        return serviceBillRepository.findAll();
    }

    @PostMapping("/billing/pay/{patientId}")
    @Transactional
    public ResponseEntity<?> payInvoice(@PathVariable String patientId) {
        List<ServiceBill> bills = serviceBillRepository.findByPatientIdAndStatus(patientId, "COMPLETED");
        List<ServiceBill> orderedBills = serviceBillRepository.findByPatientIdAndStatus(patientId, "ORDERED");
        
        List<ServiceBill> toPay = new ArrayList<>();
        toPay.addAll(bills);
        toPay.addAll(orderedBills);

        if (toPay.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("message", "Không có hóa đơn dịch vụ nào cần thanh toán cho bệnh nhân này!"));
        }

        for (ServiceBill bill : toPay) {
            bill.setStatus("PAID");
            serviceBillRepository.save(bill);
        }

        return ResponseEntity.ok(Map.of("message", "Đã thanh toán thành công " + toPay.size() + " hóa đơn viện phí."));
    }

    // --- ADMIN DASHBOARD & SEED DATA ---

    @GetMapping("/admin/dashboard")
    public Map<String, Object> getDashboardStats() {
        long totalPatients = patientRepository.count();
        long waitingQueue = queueTicketRepository.findByStatus("WAITING").size();
        long examiningQueue = queueTicketRepository.findByStatus("EXAMINING").size();
        
        List<ServiceBill> paidBills = serviceBillRepository.findByStatus("PAID");
        double totalRevenue = paidBills.stream().mapToDouble(ServiceBill::getPatientPay).sum();
        double insuranceRevenue = paidBills.stream().mapToDouble(ServiceBill::getBhytShare).sum();

        List<Map<String, String>> activeRooms = List.of(
            Map.of("name", "Phòng khám Nội 1", "doctor", "BS. Nguyễn Văn A", "status", "Đang khám"),
            Map.of("name", "Phòng khám Ngoại", "doctor", "BS. Trần Thị B", "status", "Đang khám"),
            Map.of("name", "Phòng khám Sản", "doctor", "BS. Lê Hồng C", "status", "Nghỉ"),
            Map.of("name", "Phòng chụp X-Quang", "doctor", "KTV. Phạm Văn D", "status", "Sẵn sàng")
        );

        Map<String, Object> stats = new HashMap<>();
        stats.put("totalPatients", totalPatients);
        stats.put("waitingQueue", waitingQueue);
        stats.put("examiningQueue", examiningQueue);
        stats.put("totalRevenue", totalRevenue);
        stats.put("insuranceRevenue", insuranceRevenue);
        stats.put("activeRooms", activeRooms);
        return stats;
    }

    @PostMapping("/admin/reset")
    @Transactional
    public ResponseEntity<?> resetAndSeedDatabase() {
        // Clear all database tables
        queueTicketRepository.deleteAll();
        prescriptionItemRepository.deleteAll();
        serviceBillRepository.deleteAll();
        examRecordRepository.deleteAll();
        patientRepository.deleteAll();
        drugItemRepository.deleteAll();

        // 1. Seed Patients
        Patient p1 = new Patient("BN0001", "Nguyễn Văn Hùng", "Nam", "1980-05-12", "001080005124", "GD4010120152431", "Quận 1, TP. Hồ Chí Minh");
        Patient p2 = new Patient("BN0002", "Trần Thị Thanh Vân", "Nữ", "1995-10-24", "079095010245", "DN4790120154321", "Quận Bình Thạnh, TP. Hồ Chí Minh");
        Patient p3 = new Patient("BN0003", "Phạm Minh Hoàng", "Nam", "2018-02-15", "001218004128", "TE1010120187425", "Quận Gò Vấp, TP. Hồ Chí Minh");
        Patient p4 = new Patient("BN0004", "Lê Thị Mai", "Nữ", "1953-08-30", "001053002514", "HT1010120054321", "Quận Hoàn Kiếm, Hà Nội");
        
        patientRepository.saveAll(List.of(p1, p2, p3, p4));

        // 2. Seed Drug Inventory
        DrugItem d1 = new DrugItem("D001", "Paracetamol 500mg (Panadol Extra)", "Viên", 1500.0, 1000, "Uống sau ăn 1-2 viên khi sốt, tối đa 4 viên/ngày");
        DrugItem d2 = new DrugItem("D002", "Amoxicillin 500mg", "Viên", 2500.0, 500, "Uống 2 lần/ngày, mỗi lần 1 viên sau ăn (Kháng sinh)");
        DrugItem d3 = new DrugItem("D003", "Amlodipine 5mg (Tăng huyết áp)", "Viên", 3000.0, 800, "Uống 1 viên vào buổi sáng sau ăn");
        DrugItem d4 = new DrugItem("D004", "Metformin 850mg (Đường huyết)", "Viên", 4000.0, 600, "Uống 1 viên sau ăn tối");
        DrugItem d5 = new DrugItem("D005", "Salbutamol 2mg (Hen suyễn)", "Viên", 1200.0, 300, "Uống 1 viên khi khó thở");
        DrugItem d6 = new DrugItem("D006", "Gaviscon Suspension (Dạ dày)", "Gói", 8500.0, 200, "Uống 1 gói sau ăn 30 phút hoặc khi đau");
        DrugItem d7 = new DrugItem("D007", "Vitamin C 500mg", "Viên", 800.0, 1500, "Uống 1 viên vào buổi sáng sau ăn");
        
        drugItemRepository.saveAll(List.of(d1, d2, d3, d4, d5, d6, d7));

        // 3. Seed Queues
        QueueTicket q1 = new QueueTicket(1001, p1, "Phòng khám Nội 1", "WAITING");
        QueueTicket q2 = new QueueTicket(1002, p2, "Phòng khám Nội 1", "EXAMINING");
        QueueTicket q3 = new QueueTicket(1003, p3, "Phòng khám Ngoại", "WAITING");
        
        queueTicketRepository.saveAll(List.of(q1, q2, q3));

        return ResponseEntity.ok(Map.of(
            "status", "success",
            "message", "Cơ sở dữ liệu VNPT HIS đã được thiết lập lại và nạp dữ liệu mẫu thành công!"
        ));
    }
}
