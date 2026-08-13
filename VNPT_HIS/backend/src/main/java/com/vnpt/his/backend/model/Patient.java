package com.vnpt.his.backend.model;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.LocalDateTime;

@Entity
@Table(name = "patients")
public class Patient {
    @Id
    private String id; // BN0001, BN0002...
    private String name;
    private String gender;
    private String birthDate;
    private String citizenId;
    private String insuranceCard;
    private String address;
    private LocalDateTime createdAt;

    public Patient() {
        this.createdAt = LocalDateTime.now();
    }

    public Patient(String id, String name, String gender, String birthDate, String citizenId, String insuranceCard, String address) {
        this.id = id;
        this.name = name;
        this.gender = gender;
        this.birthDate = birthDate;
        this.citizenId = citizenId;
        this.insuranceCard = insuranceCard;
        this.address = address;
        this.createdAt = LocalDateTime.now();
    }

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getGender() { return gender; }
    public void setGender(String gender) { this.gender = gender; }

    public String getBirthDate() { return birthDate; }
    public void setBirthDate(String birthDate) { this.birthDate = birthDate; }

    public String getCitizenId() { return citizenId; }
    public void setCitizenId(String citizenId) { this.citizenId = citizenId; }

    public String getInsuranceCard() { return insuranceCard; }
    public void setInsuranceCard(String insuranceCard) { this.insuranceCard = insuranceCard; }

    public String getAddress() { return address; }
    public void setAddress(String address) { this.address = address; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
