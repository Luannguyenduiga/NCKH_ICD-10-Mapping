package com.vnpt.his.backend.repository;

import com.vnpt.his.backend.model.Patient;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface PatientRepository extends JpaRepository<Patient, String> {

    /**
     * Tìm hồ sơ nội viện theo bất kỳ định danh nào bác sĩ đang cầm.
     *
     * Khớp CHÍNH XÁC chứ không khớp một phần: đây là đường tra cứu để quyết định
     * có phải hỏi tiếp EMR Cloud hay không, nên khớp gần đúng sẽ nhận nhầm một
     * bệnh nhân khác và chặn mất bước tra trên trục. Việc lọc gần đúng theo tên là
     * chuyện của ô tìm kiếm trên giao diện, không phải của truy vấn này.
     *
     * Tìm cả theo CCCD và thẻ BHYT chứ không chỉ mã bệnh án: bệnh nhân chuyển
     * tuyến tới thì bác sĩ cầm giấy tờ tùy thân, không cầm mã bệnh án của viện này.
     */
    @Query("SELECT p FROM Patient p WHERE p.id = :q OR p.citizenId = :q OR p.insuranceCard = :q")
    List<Patient> timTheoDinhDanh(@Param("q") String q);
}
