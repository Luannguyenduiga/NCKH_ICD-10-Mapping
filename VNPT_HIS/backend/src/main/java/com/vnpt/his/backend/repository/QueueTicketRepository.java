package com.vnpt.his.backend.repository;

import com.vnpt.his.backend.model.QueueTicket;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
import java.util.List;

@Repository
public interface QueueTicketRepository extends JpaRepository<QueueTicket, Long> {
    List<QueueTicket> findByClinicRoomAndStatus(String clinicRoom, String status);
    List<QueueTicket> findByStatus(String status);
    List<QueueTicket> findByPatientIdAndStatus(String patientId, String status);
}
