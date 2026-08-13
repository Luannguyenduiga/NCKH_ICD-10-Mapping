package com.vnpt.his.backend.repository;

import com.vnpt.his.backend.model.PrescriptionItem;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
import java.util.List;

@Repository
public interface PrescriptionItemRepository extends JpaRepository<PrescriptionItem, Long> {
    List<PrescriptionItem> findByExamRecordId(Long examRecordId);
    void deleteByExamRecordId(Long examRecordId);
}
