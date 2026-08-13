import React, { useState, useEffect } from 'react';

function App() {
  const [activeTab, setActiveTab] = useState('reception');
  const [patients, setPatients] = useState([]);
  const [queue, setQueue] = useState([]);
  const [drugs, setDrugs] = useState([]);
  const [clsOrders, setClsOrders] = useState([]);
  const [stats, setStats] = useState({
    totalPatients: 0,
    waitingQueue: 0,
    examiningQueue: 0,
    totalRevenue: 0,
    insuranceRevenue: 0,
    activeRooms: []
  });
  
  const [notification, setNotification] = useState(null);
  const [loading, setLoading] = useState(false);

  // Form states
  const [patientForm, setPatientForm] = useState({ id: '', name: '', gender: 'Nam', birthDate: '', citizenId: '', insuranceCard: '', address: '' });
  const [searchPatientQuery, setSearchPatientQuery] = useState('');
  
  // Queue ticket registration state
  const [queueReg, setQueueReg] = useState({ patientId: '', clinicRoom: 'Phòng khám Nội 1' });
  const [printedTicket, setPrintedTicket] = useState(null);

  // Doctor exam state
  const [selectedQueueTicket, setSelectedQueueTicket] = useState(null);
  const [examRoom, setExamRoom] = useState('Phòng khám Nội 1');
  const [examForm, setExamForm] = useState({
    clinicalNote: '',
    pulse: '',
    temperature: '',
    bloodPressure: '',
    breathingRate: '',
    weight: '',
    height: '',
    primaryIcdCode: '',
    primaryIcdName: '',
    secondaryIcdCodes: ''
  });
  const [gatewaySuggestions, setGatewaySuggestions] = useState([]);
  const [customIcdSearch, setCustomIcdSearch] = useState('');
  const [icdSuggestions, setIcdSuggestions] = useState([]);
  const [prescribedDrugs, setPrescribedDrugs] = useState([]); // Array of { drugId, name, quantity, dosage }
  const [newPresc, setNewPresc] = useState({ drugId: '', quantity: 1, dosage: 'Ngày uống 2 lần, mỗi lần 1 viên sau ăn' });
  const [orderedServices, setOrderedServices] = useState([]); // Array of { serviceName, price }
  const [newService, setNewService] = useState({ serviceName: 'Chụp X-Quang ngực thẳng', price: 150000 });

  // Laboratory/CLS Result state
  const [selectedClsOrder, setSelectedClsOrder] = useState(null);
  const [clsForm, setClsForm] = useState({ resultText: '', resultImage: 'xray' });

  // Pharmacy state
  const [prescriptions, setPrescriptions] = useState([]);
  const [selectedPresc, setSelectedPresc] = useState(null);

  // Billing state
  const [billingPatientId, setBillingPatientId] = useState('');
  const [billingBills, setBillingBills] = useState([]);
  const [selectedBillingPatient, setSelectedBillingPatient] = useState(null);

  // Static ICD mock lookup for manual search fallback
  const STATIC_ICD_LIST = [
    { code: 'I10', name: 'Tăng huyết áp vô căn (nguyên phát)' },
    { code: 'E11', name: 'Đái tháo đường không phụ thuộc insulin (Tuýp 2)' },
    { code: 'J00', name: 'Viêm mũi họng cấp (Cảm thường)' },
    { code: 'J03', name: 'Viêm amidan cấp' },
    { code: 'J20', name: 'Viêm phế quản cấp' },
    { code: 'K29', name: 'Viêm dạ dày và tá tràng' },
    { code: 'M54', name: 'Đau lưng' },
    { code: 'N39', name: 'Nhiễm trùng đường tiểu' },
    { code: 'R50', name: 'Sốt không rõ nguyên nhân' },
    { code: 'K30', name: 'Chứng khó tiêu' }
  ];

  // CLS List
  const CLINICAL_SERVICES = [
    { serviceName: 'Chụp X-Quang ngực thẳng', price: 150000 },
    { serviceName: 'Xét nghiệm Tổng phân tích tế bào máu ngoại vi', price: 100000 },
    { serviceName: 'Siêu âm ổ bụng tổng quát', price: 200000 },
    { serviceName: 'Chụp CT Scanner sọ não', price: 1200000 },
    { serviceName: 'Điện tâm đồ (ECG)', price: 80000 }
  ];

  useEffect(() => {
    fetchPatients();
    fetchQueue();
    fetchDrugs();
    fetchClsOrders();
    fetchStats();
    fetchPrescriptions();
  }, [activeTab]);

  const showNotification = (message, type = 'success') => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 5000);
  };

  const fetchPatients = async () => {
    try {
      const res = await fetch('/api/patients');
      if (res.ok) {
        const data = await res.json();
        setPatients(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchQueue = async () => {
    try {
      const res = await fetch('/api/queue');
      if (res.ok) {
        const data = await res.json();
        setQueue(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchDrugs = async () => {
    try {
      const res = await fetch('/api/drugs');
      if (res.ok) {
        const data = await res.json();
        setDrugs(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchClsOrders = async () => {
    try {
      const res = await fetch('/api/cls/orders');
      if (res.ok) {
        const data = await res.json();
        setClsOrders(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchStats = async () => {
    try {
      const res = await fetch('/api/admin/dashboard');
      if (res.ok) {
        const data = await res.json();
        setStats(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchPrescriptions = async () => {
    try {
      const res = await fetch('/api/pharmacy/prescriptions');
      if (res.ok) {
        const data = await res.json();
        setPrescriptions(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Seed DB Function
  const handleResetDatabase = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/admin/reset', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        showNotification(data.message);
        fetchPatients();
        fetchQueue();
        fetchDrugs();
        fetchStats();
      }
    } catch (e) {
      showNotification('Không thể kết nối cơ sở dữ liệu backend!', 'error');
    } finally {
      setLoading(false);
    }
  };

  // Patient Actions
  const handleRegisterPatient = async (e) => {
    e.preventDefault();
    if (!patientForm.name || !patientForm.birthDate) {
      showNotification('Họ tên và ngày sinh là bắt buộc!', 'error');
      return;
    }
    try {
      const res = await fetch('/api/patients', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patientForm)
      });
      if (res.ok) {
        showNotification('Đăng ký hồ sơ bệnh nhân thành công!');
        setPatientForm({ id: '', name: '', gender: 'Nam', birthDate: '', citizenId: '', insuranceCard: '', address: '' });
        fetchPatients();
      } else {
        const err = await res.json();
        showNotification(err.message, 'error');
      }
    } catch (e) {
      showNotification('Không kết nối được backend', 'error');
    }
  };

  const handleDeletePatient = async (id) => {
    if (!window.confirm(`Bạn có chắc chắn muốn xóa bệnh nhân ${id}?`)) return;
    try {
      const res = await fetch(`/api/patients/${id}`, { method: 'DELETE' });
      if (res.ok) {
        showNotification('Đã xóa hồ sơ bệnh nhân.');
        fetchPatients();
      }
    } catch (e) {
      showNotification('Lỗi khi xóa bệnh nhân', 'error');
    }
  };

  // Queue Actions
  const handleCreateQueue = async (patient) => {
    setQueueReg({ patientId: patient.id, clinicRoom: 'Phòng khám Nội 1' });
    // Show quick print ticket
    try {
      const res = await fetch('/api/queue/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ patientId: patient.id, clinicRoom: 'Phòng khám Nội 1' })
      });
      if (res.ok) {
        const ticket = await res.json();
        setPrintedTicket(ticket);
        fetchQueue();
        showNotification('Đã phát số thứ tự khám.');
      }
    } catch (e) {
      showNotification('Lỗi phát số khám', 'error');
    }
  };

  const handleCreateQueueAndNavigate = async (patient) => {
    try {
      const res = await fetch('/api/queue/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ patientId: patient.id, clinicRoom: 'Phòng khám Nội 1' })
      });
      if (res.ok) {
        const ticket = await res.json();
        fetchQueue();
        
        // Directly select the ticket and clinic room, then switch active tab
        setSelectedQueueTicket(ticket);
        setExamRoom('Phòng khám Nội 1');
        
        // Also update queue status to EXAMINING in backend, same as loadExamPatient
        updateQueueTicketStatus(ticket.id, 'EXAMINING');
        
        // Clear exam states
        setExamForm({
          clinicalNote: '',
          pulse: '80',
          temperature: '36.5',
          bloodPressure: '120/80',
          breathingRate: '18',
          weight: '60',
          height: '165',
          primaryIcdCode: '',
          primaryIcdName: '',
          secondaryIcdCodes: ''
        });
        setGatewaySuggestions([]);
        setPrescribedDrugs([]);
        setOrderedServices([]);
        
        setActiveTab('examination');
        showNotification('Đã phát số khám và chuyển sang phòng khám.');
      }
    } catch (e) {
      showNotification('Lỗi phát số khám', 'error');
    }
  };

  const updateQueueTicketStatus = async (id, status) => {
    try {
      const res = await fetch('/api/queue/update-status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, status })
      });
      if (res.ok) {
        fetchQueue();
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Doctor Exam Actions
  const loadExamPatient = async (ticket) => {
    setSelectedQueueTicket(ticket);
    updateQueueTicketStatus(ticket.id, 'EXAMINING');
    
    // Clear exam states
    setExamForm({
      clinicalNote: '',
      pulse: '80',
      temperature: '36.5',
      bloodPressure: '120/80',
      breathingRate: '18',
      weight: '60',
      height: '165',
      primaryIcdCode: '',
      primaryIcdName: '',
      secondaryIcdCodes: ''
    });
    setGatewaySuggestions([]);
    setPrescribedDrugs([]);
    setOrderedServices([]);
  };

  const handleGatewayStandardize = async () => {
    if (!examForm.clinicalNote) {
      showNotification('Vui lòng nhập triệu chứng lâm sàng trước khi gọi Gateway!', 'warning');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch('/api/exam/standardize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clinicalNote: examForm.clinicalNote })
      });
      if (res.ok) {
        const data = await res.json();
        setGatewaySuggestions(data);
        showNotification('Đã nhận gợi ý mã ICD-10 từ Gateway chuẩn hóa!');
      }
    } catch (e) {
      showNotification('Lỗi kết nối Gateway!', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleIcdSearchChange = (e) => {
    const q = e.target.value;
    setCustomIcdSearch(q);
    if (!q) {
      setIcdSuggestions([]);
      return;
    }
    const filtered = STATIC_ICD_LIST.filter(item => 
      item.code.toLowerCase().includes(q.toLowerCase()) || 
      item.name.toLowerCase().includes(q.toLowerCase())
    );
    setIcdSuggestions(filtered);
  };

  const addIcdCode = (icd, type) => {
    if (type === 'primary') {
      setExamForm(prev => ({ ...prev, primaryIcdCode: icd.code, primaryIcdName: icd.name }));
    } else {
      const current = examForm.secondaryIcdCodes ? examForm.secondaryIcdCodes.split(',') : [];
      if (!current.includes(icd.code)) {
        current.push(icd.code);
        setExamForm(prev => ({ ...prev, secondaryIcdCodes: current.join(',') }));
      }
    }
    setCustomIcdSearch('');
    setIcdSuggestions([]);
  };

  const removeSecondaryIcd = (code) => {
    const current = examForm.secondaryIcdCodes.split(',').filter(c => c !== code);
    setExamForm(prev => ({ ...prev, secondaryIcdCodes: current.join(',') }));
  };

  // Prescription Item actions
  const handleAddDrug = () => {
    if (!newPresc.drugId) {
      showNotification('Vui lòng chọn thuốc!', 'warning');
      return;
    }
    const drugObj = drugs.find(d => d.id === parseInt(newPresc.drugId));
    if (!drugObj) return;

    if (drugObj.stock < newPresc.quantity) {
      showNotification(`Số lượng thuốc tồn kho không đủ (Hiện còn: ${drugObj.stock})!`, 'warning');
      return;
    }

    const item = {
      drugId: drugObj.id,
      name: drugObj.name,
      unit: drugObj.unit,
      quantity: newPresc.quantity,
      dosage: newPresc.dosage
    };

    setPrescribedDrugs(prev => [...prev, item]);
    setNewPresc({ drugId: '', quantity: 1, dosage: 'Ngày uống 2 lần, mỗi lần 1 viên sau ăn' });
  };

  const handleRemoveDrug = (index) => {
    setPrescribedDrugs(prev => prev.filter((_, i) => i !== index));
  };

  // Services actions
  const handleAddService = () => {
    const sObj = CLINICAL_SERVICES.find(s => s.serviceName === newService.serviceName);
    if (!sObj) return;

    setOrderedServices(prev => [...prev, sObj]);
  };

  const handleRemoveService = (index) => {
    setOrderedServices(prev => prev.filter((_, i) => i !== index));
  };

  // Complete Exam
  const handleSaveExam = async (status) => {
    if (!examForm.primaryIcdCode) {
      showNotification('Bác sĩ bắt buộc phải nhập chẩn đoán chính (ICD-10)!', 'error');
      return;
    }

    const payload = {
      patientId: selectedQueueTicket.patient.id,
      clinicRoom: examRoom,
      clinicalNote: examForm.clinicalNote,
      pulse: examForm.pulse,
      temperature: examForm.temperature,
      bloodPressure: examForm.bloodPressure,
      breathingRate: examForm.breathingRate,
      weight: examForm.weight,
      height: examForm.height,
      primaryIcdCode: examForm.primaryIcdCode,
      primaryIcdName: examForm.primaryIcdName,
      secondaryIcdCodes: examForm.secondaryIcdCodes,
      examStatus: status, // DRAFT or COMPLETED
      prescription: prescribedDrugs,
      services: orderedServices
    };

    try {
      const res = await fetch('/api/exam/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        showNotification(status === 'COMPLETED' ? 'Khám bệnh thành công! Hồ sơ đã lưu trữ và chuyển đến Viện phí/Kho dược.' : 'Đã lưu nháp hồ sơ khám.');
        setSelectedQueueTicket(null);
        fetchQueue();
        fetchClsOrders();
        fetchPrescriptions();
      }
    } catch (e) {
      showNotification('Lỗi khi lưu kết quả khám bệnh', 'error');
    }
  };

  // Laboratory / CLS results actions
  const handleSaveClsResult = async (e) => {
    e.preventDefault();
    if (!clsForm.resultText) {
      showNotification('Vui lòng nhập mô tả kết quả cận lâm sàng!', 'warning');
      return;
    }
    const mockImages = {
      xray: 'https://images.unsplash.com/photo-1576086213369-97a306d36557?auto=format&fit=crop&q=80&w=300',
      blood: 'https://images.unsplash.com/photo-1579154204601-01588f351167?auto=format&fit=crop&q=80&w=300'
    };

    const payload = {
      id: selectedClsOrder.id,
      resultText: clsForm.resultText,
      resultImage: mockImages[clsForm.resultImage]
    };

    try {
      const res = await fetch('/api/cls/result', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        showNotification('Đã trả kết quả cận lâm sàng cho bác sĩ!');
        setSelectedClsOrder(null);
        setClsForm({ resultText: '', resultImage: 'xray' });
        fetchClsOrders();
      }
    } catch (e) {
      showNotification('Lỗi gửi kết quả CLS', 'error');
    }
  };

  // Pharmacy dispensing
  const handleDispensePharmacy = async (examId) => {
    try {
      const res = await fetch(`/api/pharmacy/dispense/${examId}`, { method: 'POST' });
      if (res.ok) {
        showNotification('Cấp phát thuốc thành công! Số lượng thuốc trong kho đã tự động khấu trừ.');
        setSelectedPresc(null);
        fetchPrescriptions();
        fetchDrugs();
      } else {
        const err = await res.json();
        showNotification(err.message, 'error');
      }
    } catch (e) {
      showNotification('Lỗi cấp phát thuốc', 'error');
    }
  };

  // Billing Actions
  const handleLoadBilling = async (patientId) => {
    setBillingPatientId(patientId);
    try {
      const res = await fetch(`/api/billing/bills?patientId=${patientId}`);
      if (res.ok) {
        const data = await res.json();
        setBillingBills(data);
        if (data.length > 0) {
          setSelectedBillingPatient(data[0].patient);
        } else {
          // Find patient info in local patients list
          const p = patients.find(pat => pat.id === patientId);
          setSelectedBillingPatient(p || null);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handlePayBilling = async () => {
    try {
      const res = await fetch(`/api/billing/pay/${billingPatientId}`, { method: 'POST' });
      if (res.ok) {
        showNotification('Thanh toán viện phí thành công! Bệnh nhân đồng chi trả 20%, BHYT hỗ trợ 80%.');
        handleLoadBilling(billingPatientId);
        fetchStats();
      } else {
        const err = await res.json();
        showNotification(err.message, 'error');
      }
    } catch (e) {
      showNotification('Lỗi thanh toán viện phí', 'error');
    }
  };

  const totalBillPrice = billingBills.reduce((acc, curr) => acc + curr.price, 0);
  const totalBhytShare = billingBills.reduce((acc, curr) => acc + curr.bhytShare, 0);
  const totalPatientPay = billingBills.reduce((acc, curr) => acc + (curr.status === 'PAID' || curr.status === 'COMPLETED' || curr.status === 'ORDERED' ? curr.patientPay : 0), 0);
  const pendingPay = billingBills.filter(b => b.status !== 'PAID').reduce((acc, curr) => acc + curr.patientPay, 0);

  return (
    <div className="app-container">
      {/* Sidebar */}
      <div className="sidebar">
        <div className="logo-section">
          <h2>VNPT HIS</h2>
          <span>CLONE</span>
        </div>
        <ul className="nav-links">
          <li className={`nav-item ${activeTab === 'reception' ? 'active' : ''}`} onClick={() => setActiveTab('reception')}>
            Tiếp đón
          </li>
          <li className={`nav-item ${activeTab === 'examination' ? 'active' : ''}`} onClick={() => setActiveTab('examination')}>
            Khám bệnh
          </li>
          <li className={`nav-item ${activeTab === 'cls' ? 'active' : ''}`} onClick={() => setActiveTab('cls')}>
            Cận lâm sàng
          </li>
          <li className={`nav-item ${activeTab === 'pharmacy' ? 'active' : ''}`} onClick={() => setActiveTab('pharmacy')}>
            Kho dược & Phát thuốc
          </li>
          <li className={`nav-item ${activeTab === 'billing' ? 'active' : ''}`} onClick={() => setActiveTab('billing')}>
            Viện phí & BHYT
          </li>
          <li className={`nav-item ${activeTab === 'admin' ? 'active' : ''}`} onClick={() => setActiveTab('admin')}>
            Bảng điều khiển Admin
          </li>
        </ul>
        <div className="sidebar-footer">
          <div>Hệ thống thông tin bệnh viện</div>
          <div style={{ marginTop: '5px', fontSize: '0.7rem' }}>VNPT IT &copy; 2026</div>
        </div>
      </div>

      {/* Main Area */}
      <div className="main-content">
        {/* Topbar */}
        <div className="topbar">
          <div className="topbar-title">
            <span>Bệnh Viện Đa Khoa Mô Phỏng VNPT HIS</span>
            <span style={{ fontSize: '0.8rem', background: '#3b82f6', color: 'white', padding: '2px 8px', borderRadius: '10px' }}>
              Module: {activeTab === 'reception' ? 'TIẾP ĐÓN BỆNH NHÂN' : 
                       activeTab === 'examination' ? 'PHÒNG KHÁM LÂM SÀNG' :
                       activeTab === 'cls' ? 'PHÒNG CẬN LÂM SÀNG (PACS/LIS)' :
                       activeTab === 'pharmacy' ? 'KHO DƯỢC & PHÁT THUỐC BHYT' :
                       activeTab === 'billing' ? 'QUẦN THU VIỆN PHÍ & THỦ TỤC BHYT' : 'QUẢN TRỊ HỆ THỐNG'}
            </span>
          </div>
          <div className="topbar-info">
            <span className="system-time">Thứ Năm, 13-08-2026 16:00</span>
            <span className="user-profile">BS. Nguyễn Văn A</span>
          </div>
        </div>

        {/* Scrollable View Area */}
        <div className="view-area">
          {notification && (
            <div className={`alert-banner ${notification.type === 'error' ? 'alert-banner-error' : ''}`}>
              <span>{notification.message}</span>
              <button style={{ background: 'none', border: 'none', cursor: 'pointer', fontWeight: 'bold' }} onClick={() => setNotification(null)}>x</button>
            </div>
          )}

          {/* Setup seed database notification if empty */}
          {patients.length === 0 && !loading && (
            <div className="alert-banner" style={{ backgroundColor: '#ebf8ff', borderColor: '#3182ce', color: '#2b6cb0', marginBottom: '20px' }}>
              <span>Hệ thống cơ sở dữ liệu hiện đang trống! Hãy nhấp nút bên phải để khởi tạo dữ liệu khám bệnh mẫu tự động.</span>
              <button className="btn btn-primary" style={{ padding: '4px 12px', fontSize: '0.8rem' }} onClick={handleResetDatabase}>Nạp dữ liệu mẫu</button>
            </div>
          )}

          {/* PAGE: RECEPTION */}
          {activeTab === 'reception' && (
            <div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
                {/* Add Patient Form */}
                <div className="card">
                  <div className="card-title">Đăng ký Hồ sơ Bệnh nhân mới</div>
                  <form onSubmit={handleRegisterPatient}>
                    <div className="form-grid">
                      <div className="form-group">
                        <label>Mã Bệnh nhân (BN...)</label>
                        <input className="form-control" placeholder="Tự sinh nếu để trống" value={patientForm.id} onChange={(e) => setPatientForm({...patientForm, id: e.target.value})} />
                      </div>
                      <div className="form-group">
                        <label>Họ và Tên (*)</label>
                        <input className="form-control" placeholder="VD: Nguyễn Văn A" value={patientForm.name} onChange={(e) => setPatientForm({...patientForm, name: e.target.value})} required />
                      </div>
                    </div>
                    <div className="form-grid">
                      <div className="form-group">
                        <label>Giới tính</label>
                        <select className="form-control" value={patientForm.gender} onChange={(e) => setPatientForm({...patientForm, gender: e.target.value})}>
                          <option value="Nam">Nam</option>
                          <option value="Nữ">Nữ</option>
                          <option value="Khác">Khác</option>
                        </select>
                      </div>
                      <div className="form-group">
                        <label>Ngày sinh (*)</label>
                        <input type="date" className="form-control" value={patientForm.birthDate} onChange={(e) => setPatientForm({...patientForm, birthDate: e.target.value})} required />
                      </div>
                    </div>
                    <div className="form-grid">
                      <div className="form-group">
                        <label>Số CCCD / Định danh</label>
                        <input className="form-control" placeholder="12 chữ số" value={patientForm.citizenId} onChange={(e) => setPatientForm({...patientForm, citizenId: e.target.value})} />
                      </div>
                      <div className="form-group">
                        <label>Số Thẻ Bảo hiểm Y tế (BHYT)</label>
                        <input className="form-control" placeholder="VD: GD401..." value={patientForm.insuranceCard} onChange={(e) => setPatientForm({...patientForm, insuranceCard: e.target.value})} />
                      </div>
                    </div>
                    <div className="form-group" style={{ marginBottom: '15px' }}>
                      <label>Địa chỉ thường trú</label>
                      <input className="form-control" placeholder="Xã/Huyện/Tỉnh" value={patientForm.address} onChange={(e) => setPatientForm({...patientForm, address: e.target.value})} />
                    </div>
                    <button type="submit" className="btn btn-primary">Lưu hồ sơ bệnh nhân</button>
                  </form>
                </div>

                {/* Print queue ticket mockup */}
                <div className="card">
                  <div className="card-title">Phát số thứ tự & Hàng đợi phòng khám</div>
                  {printedTicket ? (
                    <div className="ticket-print-mockup">
                      <h3>BỆNH VIỆN MÔ PHỎNG VNPT</h3>
                      <p>PHIẾU KHÁM BỆNH BHYT</p>
                      <div className="ticket-number-large">{printedTicket.ticketNumber}</div>
                      <p>Bệnh nhân: <strong>{printedTicket.patient.name}</strong></p>
                      <p>Mã BN: {printedTicket.patient.id}</p>
                      <p>Phòng khám: <strong>{printedTicket.clinicRoom}</strong></p>
                      <p style={{ fontSize: '0.75rem', color: '#666', marginTop: '10px' }}>In lúc: {new Date(printedTicket.createdAt).toLocaleString('vi-VN')}</p>
                      <button className="btn btn-secondary" style={{ marginTop: '10px', fontSize: '0.8rem' }} onClick={() => setPrintedTicket(null)}>Phát phiếu tiếp theo</button>
                    </div>
                  ) : (
                    <div>
                      <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginBottom: '15px' }}>Chọn bệnh nhân trong danh sách bên dưới, click <strong>"Phát số khám"</strong> để phân buồng khám lâm sàng.</p>
                      <div className="table-container" style={{ maxHeight: '250px' }}>
                        <table className="table">
                          <thead>
                            <tr>
                              <th>Mã BN</th>
                              <th>Tên bệnh nhân</th>
                              <th>BHYT</th>
                              <th>Hành động</th>
                            </tr>
                          </thead>
                          <tbody>
                            {patients.slice(0, 5).map(p => (
                              <tr key={p.id}>
                                <td>{p.id}</td>
                                <td><strong>{p.name}</strong></td>
                                <td>{p.insuranceCard ? <span className="badge badge-completed">BHYT</span> : <span className="badge badge-waiting">Viện phí</span>}</td>
                                <td>
                                  <button className="btn btn-primary" style={{ padding: '4px 8px', fontSize: '0.75rem' }} onClick={() => handleCreateQueue(p)}>Phát số khám</button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Master Patient Table */}
              <div className="card">
                <div className="card-title">Danh sách Bệnh nhân đăng ký tại Hệ thống</div>
                <div className="table-container">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Mã BN</th>
                        <th>Họ và Tên</th>
                        <th>Giới tính</th>
                        <th>Ngày sinh</th>
                        <th>CCCD</th>
                        <th>Mã thẻ BHYT</th>
                        <th>Địa chỉ</th>
                        <th>Hành động</th>
                      </tr>
                    </thead>
                    <tbody>
                      {patients.map(p => (
                        <tr key={p.id}>
                          <td><strong>{p.id}</strong></td>
                          <td><strong>{p.name}</strong></td>
                          <td>{p.gender}</td>
                          <td>{p.birthDate}</td>
                          <td>{p.citizenId || '---'}</td>
                          <td>{p.insuranceCard || '---'}</td>
                          <td>{p.address || '---'}</td>
                          <td>
                            <div style={{ display: 'flex', gap: '5px' }}>
                              <button className="btn btn-secondary" style={{ padding: '4px 8px', fontSize: '0.75rem' }} onClick={() => handleCreateQueueAndNavigate(p)}>Khám bệnh</button>
                              <button className="btn btn-danger" style={{ padding: '4px 8px', fontSize: '0.75rem' }} onClick={() => handleDeletePatient(p.id)}>Xóa</button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* PAGE: DOCTOR EXAMINATION */}
          {activeTab === 'examination' && (
            <div className="exam-layout">
              {/* Queue Panel */}
              <div className="queue-panel">
                <div className="queue-title">Hàng đợi Phòng khám</div>
                <div className="form-group" style={{ marginBottom: '15px' }}>
                  <label>Chọn Phòng khám</label>
                  <select className="form-control" value={examRoom} onChange={(e) => setExamRoom(e.target.value)}>
                    <option value="Phòng khám Nội 1">Phòng khám Nội 1</option>
                    <option value="Phòng khám Ngoại">Phòng khám Ngoại</option>
                    <option value="Phòng khám Sản">Phòng khám Sản</option>
                  </select>
                </div>
                <ul className="queue-list">
                  {queue.filter(q => q.clinicRoom === examRoom && q.status !== 'COMPLETED').map(q => (
                    <li 
                      key={q.id} 
                      className={`queue-item ${selectedQueueTicket?.id === q.id ? 'active' : ''}`}
                      onClick={() => loadExamPatient(q)}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div>
                          <span className="patient-num">{q.ticketNumber}</span>
                          <strong>{q.patient.name}</strong>
                        </div>
                        <span className={`badge ${q.status === 'EXAMINING' ? 'badge-examining' : 'badge-waiting'}`}>
                          {q.status === 'EXAMINING' ? 'Đang khám' : 'Chờ khám'}
                        </span>
                      </div>
                      <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: '5px' }}>
                        Mã BN: {q.patient.id} | BHYT: {q.patient.insuranceCard ? 'Có' : 'Không'}
                      </div>
                    </li>
                  ))}
                  {queue.filter(q => q.clinicRoom === examRoom && q.status !== 'COMPLETED').length === 0 && (
                    <p style={{ color: '#94a3b8', fontSize: '0.85rem', textAlign: 'center', padding: '20px 0' }}>Không có bệnh nhân chờ.</p>
                  )}
                </ul>
              </div>

              {/* Patient Exam Panel */}
              <div className="patient-panel">
                {selectedQueueTicket ? (
                  <div className="card">
                    <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span>Bệnh án lâm sàng: <strong>{selectedQueueTicket.patient.name}</strong> ({selectedQueueTicket.patient.gender} - {new Date().getFullYear() - new Date(selectedQueueTicket.patient.birthDate).getFullYear()} tuổi)</span>
                      <span>Mã bệnh nhân: <strong>{selectedQueueTicket.patient.id}</strong></span>
                    </div>

                    <div className="tab-header">
                      <span className="tab-btn active">Khám & Chẩn đoán</span>
                    </div>

                    {/* Vital signs */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '10px', marginBottom: '15px' }}>
                      <div className="form-group">
                        <label>Mạch (lần/phút)</label>
                        <input className="form-control" type="number" value={examForm.pulse} onChange={(e) => setExamForm({...examForm, pulse: e.target.value})} />
                      </div>
                      <div className="form-group">
                        <label>Huyết áp (mmHg)</label>
                        <input className="form-control" placeholder="120/80" value={examForm.bloodPressure} onChange={(e) => setExamForm({...examForm, bloodPressure: e.target.value})} />
                      </div>
                      <div className="form-group">
                        <label>Nhiệt độ (&deg;C)</label>
                        <input className="form-control" type="number" step="0.1" value={examForm.temperature} onChange={(e) => setExamForm({...examForm, temperature: e.target.value})} />
                      </div>
                      <div className="form-group">
                        <label>Nhịp thở (lần/phút)</label>
                        <input className="form-control" type="number" value={examForm.breathingRate} onChange={(e) => setExamForm({...examForm, breathingRate: e.target.value})} />
                      </div>
                      <div className="form-group">
                        <label>Cân nặng (kg)</label>
                        <input className="form-control" type="number" step="0.5" value={examForm.weight} onChange={(e) => setExamForm({...examForm, weight: e.target.value})} />
                      </div>
                      <div className="form-group">
                        <label>Chiều cao (cm)</label>
                        <input className="form-control" type="number" value={examForm.height} onChange={(e) => setExamForm({...examForm, height: e.target.value})} />
                      </div>
                    </div>

                    {/* Symptoms textarea */}
                    <div className="form-group" style={{ marginBottom: '15px' }}>
                      <label>Triệu chứng lâm sàng / Lý do khám</label>
                      <textarea 
                        className="form-control" 
                        placeholder="Nhập triệu chứng lâm sàng của bệnh nhân..."
                        value={examForm.clinicalNote}
                        onChange={(e) => setExamForm({...examForm, clinicalNote: e.target.value})}
                      />
                      <button type="button" className="btn btn-secondary" style={{ marginTop: '5px', alignSelf: 'flex-start' }} onClick={handleGatewayStandardize} disabled={loading}>
                        {loading ? 'Đang gọi Gateway...' : 'Gọi Gateway Chuẩn Hóa Chẩn Đoán'}
                      </button>
                    </div>

                    {/* Gateway Suggestion Box */}
                    {gatewaySuggestions.length > 0 && (
                      <div className="gateway-suggestions-box">
                        <strong style={{ fontSize: '0.85rem', color: '#1e3a8a', display: 'block', marginBottom: '8px' }}>
                          Gợi ý mã ICD-10 từ SMIG Gateway ({gatewaySuggestions.length} nhóm bệnh):
                        </strong>
                        {gatewaySuggestions.map((group, groupIdx) => (
                          <div key={groupIdx} style={{ marginBottom: '15px', borderBottom: groupIdx < gatewaySuggestions.length - 1 ? '1px dashed #cbd5e1' : 'none', paddingBottom: '10px' }}>
                            <div style={{ fontSize: '0.8rem', color: '#475569', fontWeight: 'bold' }}>
                              Nhóm {groupIdx + 1}: <span style={{ color: '#0b559f', fontStyle: 'italic' }}>"{group.fragment}"</span>
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginTop: '6px' }}>
                              {group.predictions?.slice(0, 4).map((p, idx) => (
                                <div key={idx} className="suggestion-item">
                                  <div>
                                    <strong style={{ color: '#2563eb' }}>{p.code}</strong> - {p.name_vi} 
                                    <span style={{ marginLeft: '10px', fontSize: '0.75rem', color: '#059669', fontStyle: 'italic' }}>({p.confidence.toFixed(1)}%)</span>
                                  </div>
                                  <div style={{ display: 'flex', gap: '5px' }}>
                                    <button className="btn btn-primary" style={{ padding: '2px 6px', fontSize: '0.7rem' }} onClick={() => addIcdCode({ code: p.code, name: p.name_vi }, 'primary')}>Chính</button>
                                    <button className="btn btn-secondary" style={{ padding: '2px 6px', fontSize: '0.7rem' }} onClick={() => addIcdCode({ code: p.code, name: p.name_vi }, 'secondary')}>Kèm</button>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Diagnosis Results Input */}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginTop: '15px' }}>
                      <div className="form-group">
                        <label>Chẩn đoán chính (*)</label>
                        <input className="form-control" style={{ fontWeight: 'bold', color: 'var(--primary-color)' }} placeholder="Chưa chọn chẩn đoán chính" value={examForm.primaryIcdCode ? `${examForm.primaryIcdCode} - ${examForm.primaryIcdName}` : ''} readOnly />
                      </div>
                      <div className="form-group">
                        <label>Chẩn đoán kèm theo</label>
                        <div className="icd-tags">
                          {examForm.secondaryIcdCodes ? examForm.secondaryIcdCodes.split(',').map(code => (
                            <span key={code} className="icd-tag">
                              {code}
                              <span className="icd-tag-remove" onClick={() => removeSecondaryIcd(code)}>x</span>
                            </span>
                          )) : <span style={{ color: '#94a3b8', fontSize: '0.85rem', fontStyle: 'italic' }}>Chưa chọn chẩn đoán kèm theo</span>}
                        </div>
                      </div>
                    </div>

                    {/* Manual ICD search */}
                    <div className="icd-search-box" style={{ marginTop: '10px' }}>
                      <label style={{ fontSize: '0.8rem', fontWeight: '600', color: '#64748b' }}>Tìm kiếm thủ công ICD-10 (gõ tên hoặc mã):</label>
                      <input className="form-control" placeholder="Gõ tăng huyết áp, dạ dày hoặc I10, K29..." value={customIcdSearch} onChange={handleIcdSearchChange} />
                      {icdSuggestions.length > 0 && (
                        <div className="icd-dropdown">
                          {icdSuggestions.map((icd, idx) => (
                            <div key={idx} className="icd-dropdown-item" style={{ display: 'flex', justifyContent: 'space-between' }}>
                              <span><strong>{icd.code}</strong> - {icd.name}</span>
                              <div style={{ display: 'flex', gap: '5px' }}>
                                <button className="btn btn-primary" style={{ padding: '2px 6px', fontSize: '0.7rem' }} onClick={() => addIcdCode(icd, 'primary')}>Chính</button>
                                <button className="btn btn-secondary" style={{ padding: '2px 6px', fontSize: '0.7rem' }} onClick={() => addIcdCode(icd, 'secondary')}>Kèm</button>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Cận Lâm Sàng Order Section */}
                    <div style={{ marginTop: '20px', borderTop: '1px solid #e2e8f0', paddingTop: '15px' }}>
                      <label style={{ fontWeight: '600', color: 'var(--primary-color)' }}>Chỉ định cận lâm sàng (Dịch vụ kỹ thuật):</label>
                      <div style={{ display: 'flex', gap: '10px', marginTop: '10px' }}>
                        <select className="form-control" value={newService.serviceName} onChange={(e) => {
                          const s = CLINICAL_SERVICES.find(serv => serv.serviceName === e.target.value);
                          setNewService(s);
                        }}>
                          {CLINICAL_SERVICES.map((s, idx) => (
                            <option key={idx} value={s.serviceName}>{s.serviceName} (Giá: {s.price.toLocaleString()}đ)</option>
                          ))}
                        </select>
                        <button type="button" className="btn btn-secondary" onClick={handleAddService}>Thêm chỉ định</button>
                      </div>
                      <div className="icd-tags" style={{ marginTop: '10px' }}>
                        {orderedServices.map((s, idx) => (
                          <span key={idx} className="icd-tag" style={{ border: '1px solid #7c3aed', color: '#7c3aed' }}>
                            {s.serviceName} ({s.price.toLocaleString()}đ)
                            <span className="icd-tag-remove" onClick={() => handleRemoveService(idx)}>x</span>
                          </span>
                        ))}
                      </div>
                    </div>

                    {/* Prescriptions Section */}
                    <div style={{ marginTop: '20px', borderTop: '1px solid #e2e8f0', paddingTop: '15px' }}>
                      <label style={{ fontWeight: '600', color: 'var(--primary-color)' }}>Đơn thuốc cấp phát (Dược Ngoại trú BHYT):</label>
                      <div className="prescription-item-row" style={{ marginTop: '10px' }}>
                        <select className="form-control" value={newPresc.drugId} onChange={(e) => setNewPresc({...newPresc, drugId: e.target.value})}>
                          <option value="">-- Chọn thuốc trong kho --</option>
                          {drugs.map(d => (
                            <option key={d.id} value={d.id}>{d.name} ({d.unit}) - Tồn: {d.stock}</option>
                          ))}
                        </select>
                        <input className="form-control" type="number" placeholder="SL" value={newPresc.quantity} onChange={(e) => setNewPresc({...newPresc, quantity: parseInt(e.target.value)})} />
                        <input className="form-control" placeholder="Cách dùng (Sáng 1, Tối 1...)" value={newPresc.dosage} onChange={(e) => setNewPresc({...newPresc, dosage: e.target.value})} />
                        <button type="button" className="btn btn-secondary" onClick={handleAddDrug}>Thêm thuốc</button>
                      </div>

                      <div className="table-container" style={{ marginTop: '10px' }}>
                        <table className="table">
                          <thead>
                            <tr>
                              <th>Tên thuốc</th>
                              <th>Đơn vị</th>
                              <th>Số lượng</th>
                              <th>Cách dùng</th>
                              <th>Hành động</th>
                            </tr>
                          </thead>
                          <tbody>
                            {prescribedDrugs.map((d, idx) => (
                              <tr key={idx}>
                                <td><strong>{d.name}</strong></td>
                                <td>{d.unit}</td>
                                <td>{d.quantity}</td>
                                <td>{d.dosage}</td>
                                <td>
                                  <button className="btn btn-danger" style={{ padding: '2px 6px', fontSize: '0.7rem' }} onClick={() => handleRemoveDrug(idx)}>Xóa</button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    {/* Action buttons */}
                    <div style={{ display: 'flex', gap: '10px', marginTop: '20px', justifyContent: 'flex-end' }}>
                      <button className="btn btn-secondary" onClick={() => handleSaveExam('DRAFT')}>Lưu nháp hồ sơ</button>
                      <button className="btn btn-success" onClick={() => handleSaveExam('COMPLETED')}>Hoàn thành khám</button>
                    </div>
                  </div>
                ) : (
                  <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '300px' }}>
                    <p style={{ color: '#94a3b8', fontStyle: 'italic' }}>Vui lòng chọn một bệnh nhân từ danh sách chờ khám bên trái.</p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* PAGE: LABORATORY & LIS */}
          {activeTab === 'cls' && (
            <div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
                <div className="card">
                  <div className="card-title">Danh sách Chỉ định Cận lâm sàng từ Bác sĩ</div>
                  <div className="table-container">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Mã BN</th>
                          <th>Tên bệnh nhân</th>
                          <th>Dịch vụ chỉ định</th>
                          <th>Trạng thái</th>
                          <th>Hành động</th>
                        </tr>
                      </thead>
                      <tbody>
                        {clsOrders.filter(b => b.status === 'ORDERED' || b.status === 'COMPLETED').map(o => (
                          <tr key={o.id}>
                            <td>{o.patient.id}</td>
                            <td><strong>{o.patient.name}</strong></td>
                            <td>{o.serviceName}</td>
                            <td>
                              <span className={`badge ${o.status === 'COMPLETED' ? 'badge-completed' : 'badge-ordered'}`}>
                                {o.status === 'COMPLETED' ? 'Đã trả kết quả' : 'Đang chờ thực hiện'}
                              </span>
                            </td>
                            <td>
                              <button className="btn btn-primary" style={{ padding: '4px 8px', fontSize: '0.75rem' }} onClick={() => {
                                setSelectedClsOrder(o);
                                setClsForm({ resultText: o.resultText || '', resultImage: 'xray' });
                              }}>
                                Nhập kết quả
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="card">
                  <div className="card-title">Nhập kết quả Cận lâm sàng & Ảnh Y khoa (PACS)</div>
                  {selectedClsOrder ? (
                    <form onSubmit={handleSaveClsResult}>
                      <div style={{ marginBottom: '10px' }}>
                        <p>Bệnh nhân: <strong>{selectedClsOrder.patient.name}</strong></p>
                        <p>Dịch vụ thực hiện: <strong style={{ color: '#7c3aed' }}>{selectedClsOrder.serviceName}</strong></p>
                      </div>
                      <div className="form-group" style={{ marginBottom: '12px' }}>
                        <label>Chọn Mẫu ảnh kết quả (PACS Mockup)</label>
                        <select className="form-control" value={clsForm.resultImage} onChange={(e) => setClsForm({...clsForm, resultImage: e.target.value})}>
                          <option value="xray">Phim chụp X-Quang Ngực Thẳng</option>
                          <option value="blood">Kết quả chỉ số huyết học</option>
                        </select>
                      </div>
                      <div className="form-group" style={{ marginBottom: '15px' }}>
                        <label>Kết quả phân tích cận lâm sàng (*)</label>
                        <textarea 
                          className="form-control" 
                          placeholder="Mô tả kết quả xét nghiệm hoặc chẩn đoán hình ảnh..."
                          value={clsForm.resultText}
                          onChange={(e) => setClsForm({...clsForm, resultText: e.target.value})}
                          required
                        />
                      </div>

                      {/* Mockup image preview */}
                      <div style={{ border: '1px solid #ddd', padding: '10px', borderRadius: '5px', marginBottom: '15px', backgroundColor: '#000', textAlign: 'center' }}>
                        <p style={{ color: '#fff', fontSize: '0.75rem', marginBottom: '5px' }}>ẢNH PHÁC HỌA LÂM SÀNG (PACS PREVIEW)</p>
                        {clsForm.resultImage === 'xray' ? (
                          <div style={{ color: '#fff', fontSize: '0.8rem', padding: '30px' }}>
                            <svg width="60" height="60" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ margin: '0 auto 10px' }}>
                              <path d="M4 19.5V15a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v4.5M6 10V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v4M12 4v10" />
                            </svg>
                            [ MÔ PHỎNG PHIM CHỤP X-QUANG PHỔI ]
                          </div>
                        ) : (
                          <div style={{ color: '#fff', fontSize: '0.8rem', padding: '30px' }}>
                            <svg width="60" height="60" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ margin: '0 auto 10px' }}>
                              <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
                            </svg>
                            [ MÔ PHỎNG BẢNG THÔNG SỐ XÉT NGHIỆM HUYẾT HỌC ]
                          </div>
                        )}
                      </div>

                      <button type="submit" className="btn btn-primary">Trả kết quả về hồ sơ bệnh nhân</button>
                    </form>
                  ) : (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '250px', color: '#94a3b8', fontStyle: 'italic' }}>
                      Chọn một chỉ định cận lâm sàng để tiến hành nhập kết quả.
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* PAGE: PHARMACY */}
          {activeTab === 'pharmacy' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '20px' }}>
              <div className="card">
                <div className="card-title">Đơn thuốc Chờ phát thuốc (BHYT)</div>
                <div className="table-container">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Mã BN</th>
                        <th>Tên bệnh nhân</th>
                        <th>Chẩn đoán chính</th>
                        <th>Phòng khám</th>
                        <th>Hành động</th>
                      </tr>
                    </thead>
                    <tbody>
                      {prescriptions.map((p, idx) => (
                        <tr key={idx}>
                          <td>{p.patient.id}</td>
                          <td><strong>{p.patient.name}</strong></td>
                          <td><strong>{p.primaryIcdCode}</strong> - {p.primaryIcdName}</td>
                          <td>{p.clinicRoom}</td>
                          <td>
                            <button className="btn btn-primary" style={{ padding: '4px 8px', fontSize: '0.75rem' }} onClick={() => setSelectedPresc(p)}>
                              Chi tiết đơn thuốc
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="card">
                <div className="card-title">Chi tiết Cấp phát thuốc & Khấu kho dược</div>
                {selectedPresc ? (
                  <div>
                    <div style={{ borderBottom: '1px solid #eee', paddingBottom: '10px', marginBottom: '15px' }}>
                      <p>Họ tên: <strong>{selectedPresc.patient.name}</strong> (Mã BN: {selectedPresc.patient.id})</p>
                      <p>Mã BHYT: <strong>{selectedPresc.patient.insuranceCard || 'Không'}</strong></p>
                    </div>
                    <div className="table-container" style={{ marginBottom: '15px' }}>
                      <table className="table">
                        <thead>
                          <tr>
                            <th>Tên thuốc</th>
                            <th>Đơn vị</th>
                            <th>Số lượng</th>
                            <th>Cách dùng</th>
                          </tr>
                        </thead>
                        <tbody>
                          {selectedPresc.items.map((item, idx) => (
                            <tr key={idx}>
                              <td>{item.drug.name}</td>
                              <td>{item.drug.unit}</td>
                              <td><strong>{item.quantity}</strong></td>
                              <td><span style={{ fontSize: '0.8rem', color: '#64748b' }}>{item.dosage}</span></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
                      <button className="btn btn-secondary" onClick={() => setSelectedPresc(null)}>Quay lại</button>
                      <button className="btn btn-success" onClick={() => handleDispensePharmacy(selectedPresc.examRecordId)}>Phát thuốc & Trừ kho</button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <strong style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>Bảng tồn kho dược phẩm hiện tại:</strong>
                    <div className="table-container" style={{ marginTop: '10px', maxHeight: '280px' }}>
                      <table className="table">
                        <thead>
                          <tr>
                            <th>Mã thuốc</th>
                            <th>Tên thuốc</th>
                            <th>Số lượng tồn</th>
                            <th>Đơn vị</th>
                            <th>Giá BHYT</th>
                          </tr>
                        </thead>
                        <tbody>
                          {drugs.map(d => (
                            <tr key={d.id}>
                              <td>{d.code}</td>
                              <td><strong>{d.name}</strong></td>
                              <td style={{ color: d.stock < 100 ? 'red' : 'green', fontWeight: 'bold' }}>{d.stock}</td>
                              <td>{d.unit}</td>
                              <td>{d.price.toLocaleString()}đ</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* PAGE: BILLING */}
          {activeTab === 'billing' && (
            <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: '20px' }}>
              {/* Left sidebar search */}
              <div className="card" style={{ padding: '15px' }}>
                <div className="queue-title">Tìm kiếm viện phí</div>
                <div className="form-group" style={{ marginBottom: '15px' }}>
                  <label>Mã Bệnh nhân</label>
                  <input className="form-control" placeholder="BN0001, BN0002..." value={billingPatientId} onChange={(e) => setBillingPatientId(e.target.value)} />
                  <button className="btn btn-primary" style={{ marginTop: '8px' }} onClick={() => handleLoadBilling(billingPatientId)}>Tìm hóa đơn</button>
                </div>
                <strong style={{ fontSize: '0.8rem', color: '#64748b' }}>Bệnh nhân đăng ký gần đây:</strong>
                <ul className="queue-list" style={{ marginTop: '5px' }}>
                  {patients.slice(0, 4).map(p => (
                    <li 
                      key={p.id} 
                      className={`queue-item ${billingPatientId === p.id ? 'active' : ''}`}
                      onClick={() => handleLoadBilling(p.id)}
                    >
                      <strong>{p.name}</strong> (Mã: {p.id})
                    </li>
                  ))}
                </ul>
              </div>

              {/* Billing list */}
              <div className="card">
                <div className="card-title">Hóa đơn Dịch vụ kỹ thuật & Đồng chi trả BHYT</div>
                {selectedBillingPatient ? (
                  <div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid #eee', paddingBottom: '12px', marginBottom: '15px' }}>
                      <div>
                        <p>Họ tên bệnh nhân: <strong>{selectedBillingPatient.name}</strong></p>
                        <p>Mã hồ sơ: <strong>{selectedBillingPatient.id}</strong></p>
                        <p>Giới tính: {selectedBillingPatient.gender} | Ngày sinh: {selectedBillingPatient.birthDate}</p>
                      </div>
                      <div>
                        <p>Mã số Thẻ BHYT: <strong>{selectedBillingPatient.insuranceCard || 'Không tham gia BHYT (Thanh toán 100%)'}</strong></p>
                        <p>Mức hưởng BHYT: <strong style={{ color: '#059669' }}>{selectedBillingPatient.insuranceCard ? 'Đồng chi trả 20% (BHYT hỗ trợ 80%)' : 'Thu viện phí 100%'}</strong></p>
                      </div>
                    </div>

                    <div className="table-container" style={{ marginBottom: '15px' }}>
                      <table className="table">
                        <thead>
                          <tr>
                            <th>Tên chỉ định kỹ thuật</th>
                            <th>Giá dịch vụ</th>
                            <th>BHYT chi trả (80%)</th>
                            <th>Bệnh nhân trả (20%)</th>
                            <th>Trạng thái</th>
                          </tr>
                        </thead>
                        <tbody>
                          {billingBills.map(b => (
                            <tr key={b.id}>
                              <td>{b.serviceName}</td>
                              <td>{b.price.toLocaleString()}đ</td>
                              <td>{selectedBillingPatient.insuranceCard ? b.bhytShare.toLocaleString() : '0'}đ</td>
                              <td>{selectedBillingPatient.insuranceCard ? b.patientPay.toLocaleString() : b.price.toLocaleString()}đ</td>
                              <td>
                                <span className={`badge ${b.status === 'PAID' ? 'badge-completed' : 'badge-waiting'}`}>
                                  {b.status === 'PAID' ? 'Đã thu tiền' : 'Chờ thu tiền'}
                                </span>
                              </td>
                            </tr>
                          ))}
                          {billingBills.length === 0 && (
                            <tr>
                              <td colSpan="5" style={{ textAlign: 'center', color: '#64748b', fontStyle: 'italic' }}>Không tìm thấy dịch vụ cận lâm sàng nào cần thanh toán tiền.</td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>

                    {billingBills.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px', borderTop: '2px solid #e2e8f0', paddingTop: '15px' }}>
                        <div style={{ fontSize: '0.9rem' }}>Tổng chi phí dịch vụ: <strong>{totalBillPrice.toLocaleString()} VNĐ</strong></div>
                        <div style={{ fontSize: '0.9rem', color: '#059669' }}>Bảo hiểm chi trả (BHYT): <strong>{(selectedBillingPatient.insuranceCard ? totalBhytShare : 0).toLocaleString()} VNĐ</strong></div>
                        <div style={{ fontSize: '1.1rem', color: 'red' }}>Số tiền bệnh nhân thanh toán: <strong>{(selectedBillingPatient.insuranceCard ? totalPatientPay : totalBillPrice).toLocaleString()} VNĐ</strong></div>
                        
                        {pendingPay > 0 && (
                          <button className="btn btn-success" style={{ marginTop: '10px' }} onClick={handlePayBilling}>
                            Thu tiền & Đóng hồ sơ thanh toán
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '250px', color: '#94a3b8', fontStyle: 'italic' }}>
                    Chọn một bệnh nhân từ cột tìm kiếm bên trái để hiển thị chi tiết thanh toán viện phí.
                  </div>
                )}
              </div>
            </div>
          )}

          {/* PAGE: ADMIN / SETTINGS */}
          {activeTab === 'admin' && (
            <div>
              {/* Stats Cards */}
              <div className="dashboard-grid">
                <div className="metric-card">
                  <div className="metric-label">Tổng số Bệnh nhân</div>
                  <div className="metric-value">{stats.totalPatients}</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Số người Chờ khám</div>
                  <div className="metric-value">{stats.waitingQueue}</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Doanh thu thu từ Bệnh nhân</div>
                  <div className="metric-value">{(stats.totalRevenue || 0).toLocaleString()}đ</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Bảo hiểm Xã hội quyết toán</div>
                  <div className="metric-value">{(stats.insuranceRevenue || 0).toLocaleString()}đ</div>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '20px' }}>
                <div className="card">
                  <div className="card-title">Các Buồng/Phòng Đang hoạt động</div>
                  <div className="table-container">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Tên phòng chuyên môn</th>
                          <th>Bác sĩ phụ trách</th>
                          <th>Tình trạng</th>
                        </tr>
                      </thead>
                      <tbody>
                        {stats.activeRooms?.map((room, idx) => (
                          <tr key={idx}>
                            <td><strong>{room.name}</strong></td>
                            <td>{room.doctor}</td>
                            <td>
                              <span className={`badge ${room.status === 'Đang khám' || room.status === 'Sẵn sàng' ? 'badge-completed' : 'badge-waiting'}`}>
                                {room.status}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="card">
                  <div className="card-title">Thiết lập & Điều phối hệ thống</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                    <p style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>Dưới đây là các lệnh dành cho người quản trị để điều phối cơ sở dữ liệu và xóa dữ liệu thừa.</p>
                    <div style={{ border: '1px solid #fee2e2', padding: '15px', borderRadius: '5px', backgroundColor: '#fef2f2' }}>
                      <strong style={{ color: '#991b1b', display: 'block', marginBottom: '8px' }}>Cảnh báo khôi phục:</strong>
                      <span style={{ fontSize: '0.85rem', color: '#7f1d1d' }}>Hành động này sẽ xóa toàn bộ bệnh án, lượt chờ khám, đơn thuốc và hóa đơn hiện tại trong cơ sở dữ liệu PostgreSQL và khôi phục về danh mục thuốc, bệnh án mẫu mặc định.</span>
                      <button 
                        className="btn btn-danger" 
                        style={{ marginTop: '12px', width: '100%' }}
                        onClick={handleResetDatabase}
                        disabled={loading}
                      >
                        {loading ? 'Đang thực thi lệnh...' : 'Reset & Seed Dữ Liệu Khám Mẫu'}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
