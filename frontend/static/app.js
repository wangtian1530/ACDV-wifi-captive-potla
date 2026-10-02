const WS_PORT = 8765;
const mainContent = document.getElementById("main-content");
let currentRenderType = null;
let ws = null;
let lastPayloadJson = null;
let cachedPortalInfo = null;
let _reqId = 0;
const _pending = {};
let userChatFetched = false;

function getDeviceId() {
    try {
        let id = localStorage.getItem("acdv_device_id");
        if (!id) {
            id = "dev-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
            localStorage.setItem("acdv_device_id", id);
        }
        return id;
    } catch (e) { return "dev-fallback"; }
}

function _wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.hostname}:${WS_PORT}`;
}

function wsRequest(payload) {
    return new Promise((resolve, reject) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            reject(new Error("WebSocket chưa kết nối. Vui lòng thử lại."));
            return;
        }
        const id = "req-" + (++_reqId) + "-" + Date.now();
        _pending[id] = { resolve, reject };
        ws.send(JSON.stringify(Object.assign({}, payload, { id })));
        setTimeout(() => {
            if (_pending[id]) {
                const p = _pending[id];
                delete _pending[id];
                p.reject(new Error("Hết thời gian chờ phản hồi server."));
            }
        }, 15000);
    });
}

function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    try {
        ws = new WebSocket(_wsUrl());
        ws.onopen = () => {
            try { ws.send(JSON.stringify({ type: 'init', device_id: getDeviceId(), role: 'user' })); } catch (e) {}
        };
        ws.onmessage = async (event) => {
            let payload;
            try { payload = JSON.parse(event.data); } catch (e) { return; }

            if (payload.type === 'response' && payload.id) {
                const p = _pending[payload.id];
                if (p) {
                    delete _pending[payload.id];
                    if (payload.ok) p.resolve(payload);
                    else p.reject(new Error(payload.message || 'Thao tác thất bại.'));
                }
                return;
            }

            if (payload.type === 'chat_new') {
                const rec = payload.message;
                appendUserMessage(rec);
                if (payload.voucher_notice) { renderPendingVoucher(); loadUserChat(); }
                if (document.visibilityState === 'visible') {
                    try { await wsRequest({ type: 'chat_mark_read' }); } catch (e) {}
                }
                updateUserChatBadge();
                return;
            }

            if (payload.type === 'admin_status_refresh' || payload.type === 'admin_dashboard') return;
            const json = JSON.stringify(payload);
            if (json === lastPayloadJson) return;
            lastPayloadJson = json;
            _applyRender(payload);
            refreshUserPanels();
        };
        ws.onclose = () => setTimeout(connectWS, 3000);
        ws.onerror = () => { try { ws.close(); } catch (e) {} };
    } catch (e) {
        setTimeout(connectWS, 3000);
    }
}

// ============ QR SCAN INLINE ============
let qrScanner = null;

function closeQrScanner() {
    if (qrScanner && typeof qrScanner.stop === 'function') {
        try { qrScanner.stop().then(()=>{}).catch(()=>{}); } catch (e) {}
    }
    qrScanner = null;
    const wrap = document.getElementById('qr-scanner-wrap');
    if (wrap) wrap.remove();
}

function ensureHtml5QrLib() {
    return new Promise((resolve, reject) => {
        if (window.Html5Qrcode) return resolve();
        if (document.getElementById('html5qr-lib')) {
            document.getElementById('html5qr-lib').addEventListener('load', () => resolve());
            return;
        }
        const s = document.createElement('script');
        s.id = 'html5qr-lib';
        s.src = 'https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';
        s.onload = () => resolve();
        s.onerror = () => reject(new Error('Không tải được thư viện quét QR.'));
        document.head.appendChild(s);
    });
}

async function startQrScanner() {
    const isSecure = location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
    if (!isSecure) {
        renderError('Trình duyệt không cho phép mở camera trên kết nối HTTP không bảo mật. Hãy mở trang qua HTTPS hoặc localhost để dùng quét QR.');
        return;
    }
    closeQrScanner();
    try { await ensureHtml5QrLib(); } catch (e) { renderError(e.message); return; }

    const wrap = document.createElement('div');
    wrap.id = 'qr-scanner-wrap';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.95);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;';
    wrap.innerHTML = `
        <div style="width:100%;max-width:480px;background:#fff;border-radius:16px;padding:16px;box-shadow:0 10px 40px rgba(0,0,0,.4);">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                <h3 style="margin:0;color:#0f172a;"><i class="fas fa-qrcode" style="color:#667eea;"></i> Quét mã QR</h3>
                <button type="button" id="qr-close-btn" class="btn btn-danger" style="padding:6px 12px;">Đóng</button>
            </div>
            <div id="reader"></div>
            <div id="camera-status" style="margin-top:10px;text-align:center;color:#475569;font-size:14px;min-height:20px;">Đang mở camera...</div>
        </div>
    `;
    document.body.appendChild(wrap);
    document.getElementById('qr-close-btn').addEventListener('click', closeQrScanner);
    const statusEl = document.getElementById('camera-status');

    try {
        qrScanner = new Html5Qrcode('reader');
        await qrScanner.start(
            { facingMode: 'environment' },
            { fps: 10, qrbox: { width: 260, height: 260 } },
            (decodedText) => { handleQrDecoded(decodedText); },
            () => {}
        );
        statusEl.textContent = 'Camera sẵn sàng. Hãy đưa mã QR vào khung quét.';
    } catch (err) {
        let msg;
        if (err && err.name === 'NotAllowedError') {
            msg = 'Bạn đã chặn quyền camera. Hãy bật quyền truy cập máy ảnh cho trình duyệt rồi thử lại.';
        } else if (err && err.name === 'NotFoundError') {
            msg = 'Không tìm thấy camera trên thiết bị.';
        } else {
            msg = 'Không thể mở camera. Hãy kiểm tra quyền truy cập máy ảnh, hoặc nhập mã thẻ thủ công bên dưới.';
        }
        statusEl.textContent = msg;
        statusEl.style.color = '#b91c1c';
    }
}

function handleQrDecoded(decodedText) {
    let token = null;
    try {
        const url = new URL(decodedText, 'http://10.10.10.1');
        token = url.searchParams.get('token');
    } catch (e) {
        const m = decodedText.match(/token=([^&]+)/);
        token = m ? m[1] : null;
    }
    if (!token) {
        alert('Mã QR không hợp lệ (không có token kích hoạt).');
        return;
    }
    activateQrToken(token);
}

async function activateQrToken(token) {
    closeQrScanner();
    renderLoader();
    try {
        const result = await wsRequest({ type: 'qr_activate', token, device_id: getDeviceId() });
        if (!result.ok) throw new Error(result.message || 'Không thể kích hoạt QR.');
        renderQrResult(true, 'Kích hoạt thành công', 'Voucher đã được kích hoạt cho thiết bị của bạn.');
        lastPayloadJson = null;
        setTimeout(() => { lastPayloadJson = null; }, 2500);
    } catch (error) {
        renderQrResult(false, 'Kích hoạt không thành công', error.message);
        lastPayloadJson = null;
        setTimeout(() => { lastPayloadJson = null; }, 2500);
    }
}

async function activateQrUrlToken() {
    const token = new URLSearchParams(window.location.search).get('qr_token');
    if (!token) return false;
    renderQrWelcome();
    await new Promise((r) => setTimeout(r, 1500));
    renderQrThanks();
    await new Promise((r) => setTimeout(r, 700));
    return activateQrToken(token);
}

// ============ RENDER ============
function renderQrWelcome() {
    mainContent.innerHTML = `<div class="qr-welcome">
        <div class="qr-welcome-mark"><i class="fas fa-wifi"></i></div>
        <div class="qr-welcome-kicker">ACDV-Teams Network</div>
        <h1>Đang kết nối dịch vụ</h1>
        <p>Hệ thống đang xác thực thiết bị và chuẩn bị quyền truy cập Internet.</p>
        <div class="qr-progress"><span></span></div>
        <div class="qr-welcome-status"><i class="fas fa-shield-alt"></i> Kết nối an toàn</div></div>`;
}
function renderQrThanks() {
    mainContent.innerHTML = `<div class="qr-welcome qr-thanks">
        <div class="qr-welcome-mark"><i class="fas fa-check"></i></div>
        <div class="qr-welcome-kicker">ACDV-Teams Network</div>
        <h1>Cảm ơn quý khách</h1>
        <p>Đang hoàn tất kích hoạt gói truy cập của bạn.</p>
        <div class="qr-welcome-status"><i class="fas fa-bolt"></i> Sắp sẵn sàng</div></div>`;
}
function renderQrResult(success, title, message) {
    mainContent.innerHTML = `<div class="qr-welcome qr-result ${success ? 'qr-result-success' : 'qr-result-error'}">
        <div class="qr-welcome-mark"><i class="fas ${success ? 'fa-check' : 'fa-times'}"></i></div>
        <div class="qr-welcome-kicker">ACDV-Teams Network</div>
        <h1>${title}</h1>
        <p>${message}</p>
        <div class="qr-welcome-status"><i class="fas ${success ? 'fa-wifi' : 'fa-circle-exclamation'}"></i> ${success ? 'Bạn có thể truy cập Internet' : 'Vui lòng kiểm tra thông tin và thử lại'}</div></div>`;
}
function renderLoader() {
    mainContent.innerHTML = `<div class="card" style="text-align:center;">
        <div class="card-title"><i class="fas fa-spinner fa-spin"></i> Đang tải...</div>
        <p style="color:#6b6b8a;">Vui lòng đợi một chút để hệ thống kiểm tra kết nối.</p></div>`;
}
function renderError(message) {
    mainContent.innerHTML = `<div class="card" style="max-width:600px;margin:0 auto;">
        <div class="card-title"><i class="fas fa-exclamation-circle"></i> Lỗi</div>
        <div class="alert alert-danger"><i class="fas fa-times-circle"></i> ${message}</div>
        <a href="/user" class="btn btn-primary"><i class="fas fa-sync"></i> Tải lại</a></div>`;
}

function renderActivationForm(status) {
    const infoText = status.in_nft ? 'Đã thêm vào nftables' : 'Chưa được thêm vào nftables';
    return `<div class="card" style="max-width:700px;margin:0 auto;">
        <div class="card-title"><i class="fas fa-wifi"></i> Kích hoạt truy cập</div>
        <div class="alert alert-info"><i class="fas fa-info-circle"></i><div>
            <strong>IP: ${status.ip}</strong>
            <p style="margin:5px 0 0 0;font-size:14px;">${status.is_online ? '🟢 Đã kết nối mạng' : '🔴 Chưa kết nối mạng'} • ${status.has_internet ? '🌐 Có internet' : '⛔ Chưa có internet'} • ${infoText}</p></div></div>
        <div class="scan-qr-row"><button type="button" id="scan-qr-button" class="btn btn-primary"><i class="fas fa-qrcode"></i> Quét QR trực tiếp</button></div>
        <form id="activate-form">
            <div class="form-group"><label><i class="fas fa-ticket-alt"></i> Mã thẻ cào</label>
            <input id="voucher-code" type="text" name="code" placeholder="VD: ABCD-EFGH-IJKL" autocomplete="off" required /></div>
            <button type="submit" class="btn btn-success" style="width:100%;font-size:18px;padding:16px;"><i class="fas fa-rocket"></i> Kích hoạt ngay</button>
        </form>
        
        <!-- Bổ sung 2 dòng này để hiện khung nhận voucher và chat dù chưa active -->
        <div id="pending-voucher-card" class="chat-panel small-panel" style="margin-top: 20px;"></div>
        <div id="user-chat-panel" class="chat-panel" style="margin-top: 20px;"></div>
    </div>`;
}
function renderStatus(status) {
    const timeLeft = status.remaining_time ? `${Math.floor(status.remaining_time/3600)}h ${Math.floor((status.remaining_time%3600)/60)}m` : '0h 0m';
    const dataLeft = status.remaining_data >= 999999 ? 'Không giới hạn' : `${(status.remaining_data/1024).toFixed(2)} GB (${status.remaining_data} MB)`;
    let progress = 0;
    let progressClass = 'progress-bar-success';
    let dataProgressHtml = '';
    if (status.max_data_mb > 0) {
        progress = Math.min(100, Math.round((status.traffic_used_mb / status.max_data_mb) * 100));
        progressClass = progress < 70 ? 'progress-bar-success' : (progress < 90 ? 'progress-bar-warning' : 'progress-bar-danger');
        dataProgressHtml = `<div style="margin-top:20px;background:linear-gradient(135deg,#f0f4ff,#e8ecff);padding:20px;border-radius:16px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <span style="font-weight:700;color:#667eea;font-size:16px;"><i class="fas fa-database"></i> Data đã nạp</span>
                <span style="background:#667eea;color:white;padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600;">${(status.max_data_mb/1024).toFixed(1)} GB</span></div>
            <div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:14px;">
                <span style="color:#4a5568;"><i class="fas fa-arrow-down"></i> Đã dùng: <strong>${(status.traffic_used_mb/1024).toFixed(2)} GB</strong></span>
                <span style="color:#4a5568;"><i class="fas fa-arrow-up"></i> Còn lại: <strong>${(status.remaining_data/1024).toFixed(2)} GB</strong></span></div>
            <div class="progress" style="height:20px;border-radius:30px;"><div class="progress-bar ${progressClass}" style="width:${progress}%;height:100%;border-radius:30px;font-size:12px;font-weight:700;line-height:20px;">${progress}%</div></div>
            <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;font-size:13px;color:#6b6b8a;">
                <span>📥 Download: ${status.download} MB</span><span>📤 Upload: ${status.upload} MB</span></div></div>`;
    }
    const isExpired = status.remaining_data <= 0 && status.max_data_mb > 0;
    const vouchersInfo = (status.voucher_codes && status.voucher_codes.length > 0)
        ? `<div style="margin-top:14px;background:linear-gradient(135deg,#f6f8ff,#eef2ff);padding:16px;border-radius:14px;border:1px solid #e0e7ff;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                  <span style="font-weight:700;color:#667eea;"><i class="fas fa-ticket-alt"></i> Voucher đã nạp (${status.vouchers_active||0})</span>
                  <span style="font-size:12px;color:#6b6b8a;">${status.consuming ? '<span style="color:#10b981;font-weight:bold;">● Đang tiêu thụ</span>' : 'Tạm ngưng'}</span></div>
              <div style="display:flex;flex-wrap:wrap;gap:6px;max-height:90px;overflow-y:auto;padding:4px;">
                  ${status.voucher_codes.map(c => `<span style="background:#667eea;color:#fff;padding:4px 10px;border-radius:20px;font-size:12px;font-family:monospace;">${c}</span>`).join('')}</div>
          </div>` : '';
    if (isExpired) {
        return `<div class="card" style="max-width:600px;margin:0 auto;">
            <div class="card-title" style="justify-content:center;"><i class="fas fa-exclamation-triangle" style="color:#f5576c;"></i> Đã hết dung lượng</div>
            <div class="alert alert-danger"><i class="fas fa-times-circle"></i><div><strong>⛔ Bạn đã sử dụng hết dung lượng!</strong>
                <p style="margin:5px 0 0 0;font-size:14px;">Đã dùng: ${(status.traffic_used_mb/1024).toFixed(2)} GB / ${(status.max_data_mb/1024).toFixed(2)} GB</p>
                <p style="margin:5px 0 0 0;font-size:14px;">Vui lòng nhập mã thẻ mới để tiếp tục truy cập Internet.</p></div></div>
            <form id="activate-form"><div class="form-group"><label><i class="fas fa-ticket-alt"></i> Mã thẻ cào</label>
                <input id="voucher-code" type="text" name="code" placeholder="VD: ABCD-EFGH-IJKL" autocomplete="off" required /></div>
                <button type="submit" class="btn btn-success" style="width:100%;font-size:18px;padding:16px;"><i class="fas fa-rocket"></i> Nạp thêm dung lượng</button></form></div>`;
    }
    return `<div class="card">
        <div class="card-title"><i class="fas fa-tachometer-alt"></i> Trạng thái kết nối</div>
        <div class="alert ${status.has_internet ? 'alert-success' : 'alert-warning'}">
            <i class="fas ${status.has_internet ? 'fa-check-circle' : 'fa-exclamation-triangle'}"></i>
            <div><strong>${status.has_internet ? '✅ Đang kết nối Internet' : '⏳ Đang chờ Internet'}</strong>
                <p style="margin:5px 0 0 0;font-size:14px;">IP: ${status.ip}</p>
                <p style="margin:5px 0 0 0;font-size:12px;color:#6b6b8a;">🛡️ Trong nftables: ${status.in_nft ? '✅' : '❌'}</p></div></div>
        <div class="grid-2">
            <div class="stat-card"><div class="icon">⏱️</div><div class="value">${timeLeft}</div><div class="label">Thời gian còn lại</div></div>
            <div class="stat-card"><div class="icon">📊</div><div class="value">${dataLeft}</div><div class="label">Dung lượng còn lại</div></div></div>
        ${dataProgressHtml}${vouchersInfo}
        <div class="scan-qr-row"><button type="button" id="scan-qr-button" class="btn btn-primary"><i class="fas fa-qrcode"></i> Quét QR trực tiếp</button></div>
        <div id="pending-voucher-card" class="chat-panel small-panel"></div>
        <div id="user-chat-panel" class="chat-panel"></div></div>`;
}

function updateSupportLinks(info) {
    if (!info) return;
    const fbLink = document.getElementById('fb-link');
    const tgLink = document.getElementById('tg-link');
    const phoneLink = document.getElementById('phone-link');
    const emailLink = document.getElementById('email-link');
    const supportLink = document.getElementById('support-link');
    if (fbLink) { fbLink.href = `https://facebook.com/${info.facebook}`; fbLink.textContent = info.facebook; }
    if (tgLink) { tgLink.href = `https://t.me/${info.telegram}`; tgLink.textContent = info.telegram; }
    if (phoneLink) { phoneLink.href = `tel:${info.phone}`; phoneLink.textContent = info.phone; }
    if (emailLink) { emailLink.href = `mailto:${info.email}`; emailLink.textContent = info.email; }
    if (supportLink) { supportLink.href = `https://t.me/${info.telegram}`; supportLink.textContent = `@${info.telegram}`; }
}

function _renderFromStatus(status) {
    const isDataExpired = status.remaining_data <= 0 && status.max_data_mb > 0;
    let t = status.status;
    if (status.status === 'active' && isDataExpired) t = 'data_expired';
    return t;
}

function _applyRender(status) {
    if (!status) return;
    const t = _renderFromStatus(status);
    if (t === currentRenderType) {
        patchStatus(status);
        return;
    }
    currentRenderType = t;
    
    // Đảm bảo mọi màn hình đều render kèm theo khung chat và load dữ liệu chat
    if (t === 'expired') {
        mainContent.innerHTML = renderExpired();
    } else if (t === 'data_expired' || t === 'active') {
        mainContent.innerHTML = renderStatus(status);
    } else {
        mainContent.innerHTML = renderActivationForm(status);
    }
    
    bindActivateForm();
    loadUserChat();          // Gọi load chat cho mọi trạng thái
    renderPendingVoucher();  // Gọi load voucher chờ cho mọi trạng thái

    if (cachedPortalInfo) updateSupportLinks(cachedPortalInfo.contact);
    userChatFetched = false;
}
function patchStatus(status) {
    if (!status || !mainContent) return;
    // Cập nhật các giá trị số realtime mà không rebuild khung chat
    const m = mainContent.querySelectorAll('.grid-2 .stat-card .value');
    const timeLeft = status.remaining_time ? `${Math.floor(status.remaining_time/3600)}h ${Math.floor((status.remaining_time%3600)/60)}m` : '0h 0m';
    const dataLeft = status.remaining_data >= 999999 ? 'Không giới hạn' : `${(status.remaining_data/1024).toFixed(2)} GB (${status.remaining_data} MB)`;
    if (m && m[0]) m[0].textContent = timeLeft;
    if (m && m[1]) m[1].textContent = dataLeft;
    if (status.max_data_mb > 0) {
        const p = Math.min(100, Math.round((status.traffic_used_mb / status.max_data_mb) * 100));
        const bar = mainContent.querySelector('.progress-bar');
        if (bar) {
            bar.style.width = p + '%';
            bar.textContent = p + '%';
            bar.className = 'progress-bar ' + (p < 70 ? 'progress-bar-success' : (p < 90 ? 'progress-bar-warning' : 'progress-bar-danger'));
        }
    }
}

function renderExpired() {
    return `<div class="card" style="max-width:600px;margin:0 auto;">
        <div class="card-title" style="justify-content:center;"><i class="fas fa-exclamation-triangle" style="color:#f5576c;"></i> Thẻ đã hết hạn</div>
        <div class="alert alert-danger"><i class="fas fa-times-circle"></i><div><strong>⏰ Thẻ cào của bạn đã hết hạn!</strong>
            <p style="margin:5px 0 0 0;font-size:14px;">Vui lòng nhập mã thẻ mới để tiếp tục truy cập Internet.</p></div></div>
        <form id="activate-form"><div class="form-group"><label><i class="fas fa-ticket-alt"></i> Mã thẻ cào</label>
            <input id="voucher-code" type="text" name="code" placeholder="VD: ABCD-EFGH-IJKL" autocomplete="off" required /></div>
            <button type="submit" class="btn btn-success" style="width:100%;font-size:18px;padding:16px;"><i class="fas fa-rocket"></i> Kích hoạt ngay</button></form>
            
        <!-- Bổ sung khung chat và voucher -->
        <div id="pending-voucher-card" class="chat-panel small-panel" style="margin-top: 20px;"></div>
        <div id="user-chat-panel" class="chat-panel" style="margin-top: 20px;"></div>
    </div>`;
}
// ============ THAO TÁC ============
async function sendActivation(code) {
    return wsRequest({ type: 'activate', code: code.toUpperCase(), device_id: getDeviceId() });
}

async function refreshUserPanels() {
    try {
        const pendingRes = await wsRequest({ type: 'pending_get', device_id: getDeviceId() });
        renderPendingVoucherCard(pendingRes.pending || null);
        await loadUserChat();
    } catch (e) { console.error(e); }
}

async function refreshManually() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        renderLoader();
        lastPayloadJson = null;
        setTimeout(() => { lastPayloadJson = null; }, 300);
    } else {
        renderError('Mất kết nối WebSocket.');
    }
}

function bindActivateForm() {
    const form = document.getElementById('activate-form');
    if (!form) return;
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const code = document.getElementById('voucher-code').value;
        if (!code) return;
        try {
            renderLoader();
            const result = await sendActivation(code);
            if (result.ok || result.status === 'success') lastPayloadJson = null;
            else renderError(result.message || 'Kích hoạt thất bại.');
        } catch (error) { renderError(error.message); }
    });
    const scanButton = document.getElementById('scan-qr-button');
    if (scanButton) scanButton.addEventListener('click', startQrScanner);
}

function esc(s) {
    return String(s == null ? '' : s).replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function mountUserChat(panel) {
    if (!panel || panel.dataset.mountedChat) return;
    panel.dataset.mountedChat = "1";
    panel.classList.add('messenger');
    panel.innerHTML = `
        <div class="chat-header">
            <div class="messenger-avatar"><i class="fas fa-headset"></i><span id="user-chat-online"></span></div>
            <div class="messenger-title">
                <strong>Hỗ trợ viên / Admin</strong>
                <small id="user-chat-sub">ACDV-Teams</small>
            </div>
            <div id="user-chat-badge-wrap"></div>
        </div>
        <div class="chat-list" id="user-chat-list"><div class="chat-empty">Đang tải tin nhắn...</div></div>
        <form id="chat-form" class="chat-form">
            <input id="chat-input" type="text" maxlength="500" placeholder="Viết tin nhắn..." autocomplete="off" />
            <button type="submit" class="btn btn-primary"><i class="fas fa-paper-plane"></i></button>
        </form>`;
    const form = document.getElementById('chat-form');
    if (form) {
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const input = document.getElementById('chat-input');
            const text = (input.value || '').trim();
            if (!text) return;
            try {
                await wsRequest({ type: 'chat_send', text, sender: 'user', device_id: getDeviceId() });
                input.value = '';
                try { await wsRequest({ type: 'chat_mark_read' }); } catch (e) {}
            } catch (e) { console.error(e); }
        });
    }
}

function renderUserMessageList(messages) {
    const list = document.getElementById('user-chat-list');
    if (!list) return;
    const arr = (messages || []).slice(-40);
    if (!arr.length) {
        list.innerHTML = '<div class="chat-empty">Chưa có tin nhắn. Hãy nhắn cho admin để được hỗ trợ.</div>';
        return;
    }
    list.innerHTML = arr.map((msg) => {
        const who = msg.sender === 'user' ? 'Bạn' : (msg.sender === 'admin' ? 'Hỗ trợ viên' : 'Hệ thống');
        return `<div class="chat-item ${msg.sender === 'user' ? 'mine' : 'other'}" data-mid="${msg.id}">
            <div class="chat-meta">${who}</div>
            <div class="chat-text">${esc(msg.text)}</div>
        </div>`;
    }).join('');
    scrollChatBottom(list);
}

function appendUserMessage(msg) {
    if (!msg) return;
    mountUserChat(document.getElementById('user-chat-panel'));
    const list = document.getElementById('user-chat-list');
    if (!list) return;
    if (list.querySelector('[data-mid="' + msg.id + '"]')) return;
    if (!list.childElementCount || list.querySelector('.chat-empty')) {
        // khung rỗng/hay vừa mount -> load lại đầy đủ
        loadUserChat();
        return;
    }
    const who = msg.sender === 'user' ? 'Bạn' : (msg.sender === 'admin' ? 'Hỗ trợ viên' : 'Hệ thống');
    const div = document.createElement('div');
    div.className = 'chat-item ' + (msg.sender === 'user' ? 'mine' : 'other');
    div.setAttribute('data-mid', msg.id);
    div.innerHTML = `<div class="chat-meta">${who}</div><div class="chat-text">${esc(msg.text)}</div>`;
    list.appendChild(div);
    scrollChatBottom(list);
}

function scrollChatBottom(list) {
    if (list) { try { list.scrollTop = list.scrollHeight; } catch (e) {} }
}

function updateUserChatBadge(count) {
    const wrap = document.getElementById('user-chat-badge-wrap');
    if (!wrap) return;
    const n = Number(count || 0);
    if (n > 0) {
        wrap.innerHTML = `<span class="badge badge-unread chat-unread">${n}</span>`;
    } else {
        wrap.innerHTML = '';
    }
}

async function loadUserChat() {
    const panel = document.getElementById('user-chat-panel');
    if (!panel) return;
    mountUserChat(panel);
    try {
        const res = await wsRequest({ type: 'chat_get', device_id: getDeviceId() });
        const msgs = res.messages || [];
        renderUserMessageList(msgs);
        updateUserChatBadge(res.unread || 0);
        if (document.visibilityState === 'visible') {
            try { await wsRequest({ type: 'chat_mark_read' }); } catch (e) {}
        }
        userChatFetched = true;
    } catch (e) { /* bỏ qua khi chưa kết nối */ }
}

async function renderPendingVoucher() {
    try {
        const res = await wsRequest({ type: 'pending_get', device_id: getDeviceId() });
        renderPendingVoucherCard(res.pending || null);
    } catch (e) {}
}

function renderPendingVoucherCard(pending) {
    const panel = document.getElementById('pending-voucher-card');
    if (!panel) return;
    if (!pending) {
        panel.innerHTML = '<div class="chat-header"><i class="fas fa-ticket-alt"></i> Voucher đang chờ</div><div class="chat-empty">Chưa có voucher nào được admin gửi cho bạn.</div>';
        return;
    }
    panel.innerHTML = `<div class="chat-header"><i class="fas fa-ticket-alt"></i> Voucher được gửi từ Admin</div>
        <div class="pending-box">
            <div><strong>Mã voucher:</strong> ${pending.voucher_code}</div>
            <div><strong>Thông tin:</strong> ${pending.message || 'Admin đã gửi voucher cho bạn.'}</div>
            <button id="activate-pending-button" class="btn btn-success" type="button"><i class="fas fa-wifi"></i> Kết nối Internet (Kích hoạt)</button>
        </div>`;
    const button = document.getElementById('activate-pending-button');
    if (button) button.addEventListener('click', activatePending);
}

async function activatePending() {
    try {
        renderLoader();
        const result = await wsRequest({ type: 'activate_pending', device_id: getDeviceId() });
        if (!result.ok) throw new Error(result.message || 'Không thể kích hoạt voucher.');
        lastPayloadJson = null;
    } catch (error) { renderError(error.message); }
}

function bindRefreshButton() {
    const button = document.getElementById('refresh-button');
    if (button) button.addEventListener('click', refreshManually);
}

async function initPage() {
    renderLoader();
    connectWS();
    try {
        cachedPortalInfo = await wsRequest({ type: 'portal_info' }).then(r => r.portal_info);
    } catch (e) {}
    // Nếu sau 6 giây vẫn CHƯA nhận được status nào (WS chậm/lỗi),
    // hiển thị form kích hoạt thay vì dừng mãi ở màn hình "Đang tải".
    setTimeout(() => {
        if (currentRenderType === null) {
            currentRenderType = 'inactive';
            mainContent.innerHTML = renderActivationForm({ ip: 'Đang xác định', in_nft: false, is_online: false, has_internet: false });
            bindActivateForm();
            loadUserChat();
            renderPendingVoucher();
        }
    }, 6000);
}

document.addEventListener('DOMContentLoaded', async () => {
    const did = await activateQrUrlToken();
    if (did === true) return;
    initPage();
});
