package com.vnpt.his.backend.model;

import jakarta.persistence.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "service_bills")
public class ServiceBill {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    
    @ManyToOne
    @JoinColumn(name = "patient_id")
    private Patient patient;
    
    private Long examRecordId;
    private String serviceName; // e.g. "Chụp X-Quang ngực thẳng", "Xét nghiệm Tổng phân tích tế bào máu ngoại vi"
    private Double price;
    private Double bhytShare; // Amount covered by Health Insurance (e.g. 80%)
    private Double patientPay; // Amount paid by patient (e.g. 20%)
    private String status; // ORDERED, COMPLETED (results entered), PAID (billing paid)
    
    @Column(length = 2000)
    private String resultText; // Result of the laboratory/imaging service
    
    @Column(length = 1000)
    private String resultImage; // Image source (base64 or mockup URL)
    
    private LocalDateTime createdAt;

    public ServiceBill() {
        this.createdAt = LocalDateTime.now();
    }

    public ServiceBill(Patient patient, Long examRecordId, String serviceName, Double price, Double bhytShare, Double patientPay, String status) {
        this.patient = patient;
        this.examRecordId = examRecordId;
        this.serviceName = serviceName;
        this.price = price;
        this.bhytShare = bhytShare;
        this.patientPay = patientPay;
        this.status = status;
        this.createdAt = LocalDateTime.now();
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Patient getPatient() { return patient; }
    public void setPatient(Patient patient) { this.patient = patient; }

    public Long getExamRecordId() { return examRecordId; }
    public void setExamRecordId(Long examRecordId) { this.examRecordId = examRecordId; }

    public String getServiceName() { return serviceName; }
    public void setServiceName(String serviceName) { this.serviceName = serviceName; }

    public Double getPrice() { return price; }
    public void setPrice(Double price) { this.price = price; }

    public Double getBhytShare() { return bhytShare; }
    public void setBhytShare(Double bhytShare) { this.bhytShare = bhytShare; }

    public Double getPatientPay() { return patientPay; }
    public void setPatientPay(Double patientPay) { this.patientPay = patientPay; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public String getResultText() { return resultText; }
    public void setResultText(String resultText) { this.resultText = resultText; }

    public String getResultImage() { return resultImage; }
    public void setResultImage(String resultImage) { this.resultImage = resultImage; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
