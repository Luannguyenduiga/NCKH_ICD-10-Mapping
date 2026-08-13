package com.vnpt.his.backend.model;

import jakarta.persistence.*;

@Entity
@Table(name = "prescription_items")
public class PrescriptionItem {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    
    private Long examRecordId;
    
    @ManyToOne
    @JoinColumn(name = "drug_id")
    private DrugItem drug;
    
    private Integer quantity;
    private String dosage; // e.g. "Uống 2 viên/ngày, chia 2 lần sáng tối"

    public PrescriptionItem() {}

    public PrescriptionItem(Long examRecordId, DrugItem drug, Integer quantity, String dosage) {
        this.examRecordId = examRecordId;
        this.drug = drug;
        this.quantity = quantity;
        this.dosage = dosage;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Long getExamRecordId() { return examRecordId; }
    public void setExamRecordId(Long examRecordId) { this.examRecordId = examRecordId; }

    public DrugItem getDrug() { return drug; }
    public void setDrug(DrugItem drug) { this.drug = drug; }

    public Integer getQuantity() { return quantity; }
    public void setQuantity(Integer quantity) { this.quantity = quantity; }

    public String getDosage() { return dosage; }
    public void setDosage(String dosage) { this.dosage = dosage; }
}
