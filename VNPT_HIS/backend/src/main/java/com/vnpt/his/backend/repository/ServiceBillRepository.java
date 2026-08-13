package com.vnpt.his.backend.repository;

import com.vnpt.his.backend.model.ServiceBill;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
import java.util.List;

@Repository
public interface ServiceBillRepository extends JpaRepository<ServiceBill, Long> {
    List<ServiceBill> findByPatientId(String patientId);
    List<ServiceBill> findByExamRecordId(Long examRecordId);
    List<ServiceBill> findByStatus(String status);
    List<ServiceBill> findByPatientIdAndStatus(String patientId, String status);
}
