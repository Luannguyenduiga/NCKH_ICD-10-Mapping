package com.vnpt.his.backend.model;

import jakarta.persistence.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "queue_tickets")
public class QueueTicket {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Integer ticketNumber;
    
    @ManyToOne
    @JoinColumn(name = "patient_id")
    private Patient patient;
    
    private String clinicRoom; // e.g. "Phòng khám Nội 1", "Phòng khám Ngoại"
    private String status; // WAITING, EXAMINING, COMPLETED
    private LocalDateTime createdAt;

    public QueueTicket() {
        this.createdAt = LocalDateTime.now();
    }

    public QueueTicket(Integer ticketNumber, Patient patient, String clinicRoom, String status) {
        this.ticketNumber = ticketNumber;
        this.patient = patient;
        this.clinicRoom = clinicRoom;
        this.status = status;
        this.createdAt = LocalDateTime.now();
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Integer getTicketNumber() { return ticketNumber; }
    public void setTicketNumber(Integer ticketNumber) { this.ticketNumber = ticketNumber; }

    public Patient getPatient() { return patient; }
    public void setPatient(Patient patient) { this.patient = patient; }

    public String getClinicRoom() { return clinicRoom; }
    public void setClinicRoom(String clinicRoom) { this.clinicRoom = clinicRoom; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
