package com.vnpt.his.backend.repository;

import com.vnpt.his.backend.model.ExamRecord;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
import java.util.List;
import java.util.Optional;

@Repository
public interface ExamRecordRepository extends JpaRepository<ExamRecord, Long> {
    List<ExamRecord> findByPatientId(String patientId);
    Optional<ExamRecord> findFirstByPatientIdOrderByCreatedAtDesc(String patientId);
}
