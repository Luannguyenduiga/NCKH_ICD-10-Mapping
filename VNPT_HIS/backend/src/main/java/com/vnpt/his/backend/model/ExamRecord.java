package com.vnpt.his.backend.model;

import jakarta.persistence.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "exam_records")
public class ExamRecord {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    
    @ManyToOne
    @JoinColumn(name = "patient_id")
    private Patient patient;
    
    @Column(length = 4000)
    private String clinicalNote; // Symptoms, diagnosis info
    
    private Integer pulse; // Mạch (lần/phút)
    private Double temperature; // Nhiệt độ
    private String bloodPressure; // Huyết áp
    private Integer breathingRate; // Nhịp thở
    private Double weight; // Cân nặng (kg)
    private Double height; // Chiều cao (cm)
    
    private String primaryIcdCode; // ICD-10 chính
    private String primaryIcdName;
    
    @Column(length = 2000)
    private String secondaryIcdCodes; // Comma-separated or JSON list of secondary codes, e.g. "I11,E11"
    
    private String examStatus; // DRAFT, COMPLETED
    private String clinicRoom;
    private LocalDateTime createdAt;

    public ExamRecord() {
        this.createdAt = LocalDateTime.now();
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Patient getPatient() { return patient; }
    public void setPatient(Patient patient) { this.patient = patient; }

    public String getClinicalNote() { return clinicalNote; }
    public void setClinicalNote(String clinicalNote) { this.clinicalNote = clinicalNote; }

    public Integer getPulse() { return pulse; }
    public void setPulse(Integer pulse) { this.pulse = pulse; }

    public Double getTemperature() { return temperature; }
    public void setTemperature(Double temperature) { this.temperature = temperature; }

    public String getBloodPressure() { return bloodPressure; }
    public void setBloodPressure(String bloodPressure) { this.bloodPressure = bloodPressure; }

    public Integer getBreathingRate() { return breathingRate; }
    public void setBreathingRate(Integer breathingRate) { this.breathingRate = breathingRate; }

    public Double getWeight() { return weight; }
    public void setWeight(Double weight) { this.weight = weight; }

    public Double getHeight() { return height; }
    public void setHeight(Double height) { this.height = height; }

    public String getPrimaryIcdCode() { return primaryIcdCode; }
    public void setPrimaryIcdCode(String primaryIcdCode) { this.primaryIcdCode = primaryIcdCode; }

    public String getPrimaryIcdName() { return primaryIcdName; }
    public void setPrimaryIcdName(String primaryIcdName) { this.primaryIcdName = primaryIcdName; }

    public String getSecondaryIcdCodes() { return secondaryIcdCodes; }
    public void setSecondaryIcdCodes(String secondaryIcdCodes) { this.secondaryIcdCodes = secondaryIcdCodes; }

    public String getExamStatus() { return examStatus; }
    public void setExamStatus(String examStatus) { this.examStatus = examStatus; }

    public String getClinicRoom() { return clinicRoom; }
    public void setClinicRoom(String clinicRoom) { this.clinicRoom = clinicRoom; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
