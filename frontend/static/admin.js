// ===== ACDV Admin — giao tiếp qua WebSocket =====
let mainContent = null;
let dashboardTimer = null;
let adminChatDeviceId = "";
let isLoginShown = false;
let dashboardLoaded = false;
const WS_PORT = 8765;
let ws = null;
let _reqId = 0;
const _pending = {};

function _wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.hostname}:${WS_PORT}`;
}

function wsRequest(payload) {
    return new Promise((resolve, reject) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            reject(new Error("WebSocket chưa kết nối."));
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
        }, 20000);
    });
}

function adminApi(type, payload) {
    return wsRequest(Object.assign({ type }, payload || {}));
}

function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    try {
        ws = new WebSocket(_wsUrl());
        ws.onopen = async () => {
            try { ws.send(JSON.stringify({ type: 'init', device_id: 'admin-ws', role: 'admin' })); } catch (e) {}
            try {
                const pi = await adminApi('portal_info');
                if (pi && pi.portal_info) updateSupportLinks(pi.portal_info.contact);
            } catch (e) {}
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
                handleAdminChatNew(rec);
                return;
            }

            if (payload.type === 'admin_status_refresh') {
                if (!payload.authorized) {
                    if (!isLoginShown) { isLoginShown = true; dashboardLoaded = false; renderLogin(); }
                } else {
                    if (!dashboardLoaded) refreshAdminPage();
                }
                return;
            }

            if (payload.type === 'admin_dashboard') {
                renderAdminDashboard(payload.dashboard);
                return;
            }
        };
        ws.onclose = () => setTimeout(connectWS, 3000);
        ws.onerror = () => { try { ws.close(); } catch (e) {} };
    } catch (e) {
        setTimeout(connectWS, 3000);
    }
}

function renderLoader() {
    mainContent.innerHTML = `
        <div class="card" style="text-align:center;">
            <div class="card-title"><i class="fas fa-spinner fa-spin"></i> Đang tải...</div>
            <p style="color:#6b6b8a;">Vui lòng đợi một chút.</p>
        </div>
    `;
}

function renderError(message) {
    mainContent.innerHTML = `
        <div class="card" style="max-width:700px;margin:0 auto;">
            <div class="card-title"><i class="fas fa-exclamation-circle"></i> Lỗi</div>
            <div class="alert alert-danger"><i class="fas fa-times-circle"></i> ${message}</div>
            <a href="/admin" class="btn btn-primary"><i class="fas fa-sync"></i> Tải lại</a>
        </div>
    `;
}

function renderAdminDashboard(payload) {
    const voucherRows = payload.vouchers.map((voucher) => {
        const statusText = voucher.used ? "Đã dùng" : "Chưa dùng";
        let extraInfo = '';
        if (voucher.type === 'data') {
            const used = voucher.connet_data || 0;
            const max = voucher.max_data || 0;
            const pct = max > 0 ? Math.round((used / max) * 100) : 0;
            extraInfo = `<br><span style="font-size:11px;color:#667eea;">Data: ${used}/${max} MB (${pct}%)</span>`;
        }
        const qrButton = voucher.used ? "<span style=\"color:#94a3b8;\">Đã kích hoạt</span>" : `<button type="button" class="btn btn-primary qr-button" data-code="${voucher.code}"><i class="fas fa-qrcode"></i> Phát QR</button>`;
        return `<tr><td>${voucher.code}${extraInfo}</td><td>${voucher.label}</td><td>${statusText}</td><td>${qrButton}</td></tr>`;
    }).join("");

    const devicesByGroup = (payload.devices || []).reduce((groups, device) => {
        const group = device.group || device.source || "Mạng nội bộ";
        (groups[group] ||= []).push(device);
        return groups;
    }, {});
    const deviceGroups = Object.entries(devicesByGroup).map(([group, devices]) => `
        <div class="device-group">
            <div class="device-group-title"><i class="fas fa-layer-group"></i> ${group} <span>${devices.length}</span></div>
            <div class="table-responsive"><table><thead><tr><th>IP</th><th>MAC</th><th>Tên thiết bị</th><th>Nguồn</th><th>Device ID</th><th>Hành động</th></tr></thead><tbody>
                ${devices.map((device) => `<tr><td>${device.ip || "-"}</td><td style="font-family:monospace;">${device.mac || "-"}</td><td><strong>${device.hostname || `Thiết bị ${device.ip}`}</strong></td><td>${device.source || "Mạng nội bộ"}</td><td style="font-family:monospace;font-size:12px;">${device.device_id || device.mac || device.ip || '-'}</td><td><div class="action-stack"><button data-ip="${device.ip}" class="btn btn-danger revoke-button">Thu hồi</button><button data-ip="${device.ip}" data-device-id="${device.device_id || device.mac || device.ip || ''}" class="btn btn-primary send-voucher-button">Gửi voucher</button></div></td></tr>`).join("")}
            </tbody></table></div>
        </div>
    `).join("");

    const packageOptions = payload.packages.map((pkg) => `
        <option value="${pkg.key}">${pkg.label} (${pkg.type === 'time' ? pkg.value + ' ' + pkg.unit : pkg.value + ' ' + pkg.unit})</option>
    `).join("");

    // ===== Lịch sử giao dịch (nạp thẻ) — gom transactions từ tất cả IP =====
    function fmtTime(ts) {
        if (!ts) return "-";
        const d = new Date(ts * 1000);
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    const allTransactions = [];
    (payload.ip_analysis || []).forEach((item) => {
        (item.transactions || []).forEach((tx) => {
            allTransactions.push({ ip: item.ip, tx });
        });
    });
    // Sắp xếp mới nhất lên trên
    allTransactions.sort((a, b) => (b.tx.timestamp || 0) - (a.tx.timestamp || 0));
    const transactionRows = allTransactions.map(({ ip, tx }) => {
        let amount = tx.type === "time"
            ? `${tx.hours || "?"} giờ`
            : `${tx.data_mb || 0} MB`;
        return `<tr>
            <td>${fmtTime(tx.timestamp)}</td>
            <td style="font-family:monospace;">${ip}</td>
            <td style="font-family:monospace;">${tx.voucher_code || "-"}</td>
            <td>${tx.type === "time" ? "⏱ Thời gian" : "📦 Data"}</td>
            <td>${amount}</td>
        </tr>`;
    }).join("");

    // ===== Khai thác data_voucher: bound_device + lịch sử thiết bị từng voucher =====
    const dataVoucherRows = Object.entries(payload.data_voucher || {}).map(([code, info]) => {
        const usedMb = Number(info.connet_data || 0);
        const maxMb = Number(info.max_data || 0);
        const pct = maxMb > 0 ? Math.round((usedMb / maxMb) * 100) : 0;
        const bound = info.bound_device || "-";
        const devices = (info.devices || []).map(d => d.ip).join(", ") || "-";
        const devCount = (info.devices || []).length;
        const statusHtml = pct >= 100
            ? '<span style="color:#ef4444;font-weight:bold;">Hết</span>'
            : (info.bound_device ? '<span style="color:#10b981;font-weight:bold;">Đang cầm</span>' : '<span style="color:#64748b;">Trống</span>');
        return `<tr>
            <td style="font-family:monospace;">${code}</td>
            <td>${usedMb} / ${maxMb} <span style="color:#667eea;">(${pct}%)</span></td>
            <td><div class="progress"><div class="progress-bar ${pct >= 100 ? 'progress-bar-danger' : pct > 70 ? 'progress-bar-warning' : 'progress-bar-success'}" style="width:${Math.min(pct,100)}%"></div></div></td>
            <td style="font-family:monospace;font-size:12px;">${bound}</td>
            <td style="font-size:12px;" title="${devices}">${devCount} thiết bị</td>
            <td style="font-size:12px;">${devices}</td>
            <td>${statusHtml}</td>
        </tr>`;
    }).join("");

    // ===== Phân tích theo IP =====
    const ipRows = (payload.ip_analysis || []).map((item) => {
        const vouchers = (item.voucher_codes || []).join(", ") || "-";
        const consumingHtml = item.consuming
            ? '<span style="color:#10b981;font-weight:bold;">● Đang</span>'
            : '<span style="color:#888;">○ Tạm ngưng</span>';
        const statusHtml = item.status === "active" ? "Hoạt động" : item.status;
        return `<tr>
            <td>${item.ip}</td>
            <td style="font-size:12px;">${vouchers}</td>
            <td>${item.total_data_mb}</td>
            <td>${item.used_mb}</td>
            <td>${item.remaining_mb} <span style="color:#667eea;">(${item.total_data_mb > 0 ? Math.round((item.used_mb / item.total_data_mb) * 100) : 0}%)</span></td>
            <td>${consumingHtml}</td>
            <td>${statusHtml}</td>
        </tr>`;
    }).join("");

    // ===== Phân tích theo Voucher (connet_data) =====
    const voucherAnalysisRows = (payload.voucher_analysis || []).map((item) => {
        const pct = item.max_data_mb > 0 ? Math.round((item.connet_data_mb / item.max_data_mb) * 100) : 0;
        const bound = item.bound_device || "-";
        const devices = (item.all_devices || []).join(", ") || "-";
        return `<tr>
            <td style="font-family:monospace;">${item.code}</td>
            <td>${item.label}</td>
            <td>${item.max_data_mb}</td>
            <td>${item.connet_data_mb}</td>
            <td>${item.remaining_mb} <span style="color:#667eea;">(${pct}%)</span></td>
            <td style="font-family:monospace;font-size:12px;" title="${devices}">${bound}</td>
            <td>${pct >= 100 ? '<span style="color:#ef4444;font-weight:bold;">Hết</span>' : (item.active_count > 0 ? '<span style="color:#10b981;font-weight:bold;">Đang dùng</span>' : 'Chưa dùng')}</td>
        </tr>`;
    }).join("");

    const financial = payload.financial_report || {};
    const formatCurrency = (value) => `${Number(value || 0).toLocaleString("vi-VN")} đ`;
    const topVoucherUsers = (payload.voucher_usage_by_ip || []).slice(0, 10);
    const topVoucherUserRows = topVoucherUsers.map((item, index) => `
        <tr>
            <td>${index + 1}</td>
            <td style="font-family:monospace;">${item.ip || "-"}</td>
            <td><strong>${item.voucher_count || 0}</strong></td>
            <td><strong>${Number(item.spent || 0).toLocaleString("vi-VN")} đ</strong></td>
            <td style="font-size:12px;">${(item.voucher_codes || []).join(", ") || "-"}</td>
            <td>${item.consuming ? '<span style="color:#10b981;font-weight:bold;">● Đang hoạt động</span>' : '<span style="color:#64748b;">Tạm ngưng</span>'}</td>
        </tr>
    `).join("");

    mainContent.innerHTML = `
        <div class="card">
            <div class="card-title"><i class="fas fa-tachometer-alt"></i> Bảng điều khiển Admin</div>
            <div class="grid-3">
                <div class="stat-card"><div class="icon">🎫</div><div class="value">${payload.total_vouchers}</div><div class="label">Tổng số thẻ</div></div>
                <div class="stat-card"><div class="icon">✅</div><div class="value">${payload.used_vouchers}</div><div class="label">Thẻ đã dùng</div></div>
                <div class="stat-card"><div class="icon">🌐</div><div class="value">${payload.active_ips.length}</div><div class="label">IP đang hoạt động</div></div>
            </div>
            <div style="margin-top:20px;display:flex;gap:10px;flex-wrap:wrap;">
                <button id="sync-button" class="btn btn-warning">Đồng bộ nft</button>
                <button id="logout-button" class="btn btn-danger">Đăng xuất</button>
            </div>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-chart-line"></i> Báo cáo tài chính</div>
            <div class="grid-4">
                <div class="stat-card"><div class="icon">🎫</div><div class="value">${financial.activated_count || 0}</div><div class="label">Voucher đã kích hoạt</div></div>
                <div class="stat-card"><div class="icon">💰</div><div class="value">${formatCurrency(financial.estimated_revenue)}</div><div class="label">Doanh thu ước tính</div></div>
                <div class="stat-card"><div class="icon">📦</div><div class="value">${formatCurrency(financial.created_value)}</div><div class="label">Tổng giá trị đã tạo</div></div>
                <div class="stat-card"><div class="icon">⏳</div><div class="value">${formatCurrency(financial.unused_value)}</div><div class="label">Giá trị chưa kích hoạt</div></div>
            </div>
            <p style="margin-top:16px;color:#6b6b8a;font-size:13px;"><i class="fas fa-info-circle"></i> Doanh thu ước tính được tính theo giá voucher đã kích hoạt, chưa thay thế sổ kế toán hoặc đối soát thanh toán thực tế.</p>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-ranking-star"></i> IP sử dụng nhiều voucher nhất</div>
            <div class="table-responsive"><table>
                <thead><tr><th>Hạng</th><th>IP</th><th>Số voucher</th><th>Đã chi</th><th>Mã đã nạp</th><th>Trạng thái</th></tr></thead>
                <tbody>${topVoucherUserRows || '<tr><td colspan="6">Chưa có dữ liệu sử dụng voucher.</td></tr>'}</tbody>
            </table></div>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-plus-circle"></i> Tạo thẻ mới</div>
            <form id="create-form">
                <div class="form-group">
                    <label>Gói thẻ</label>
                    <select name="package_key" required>${packageOptions}</select>
                </div>
                <button class="btn btn-success" style="width:100%;">Tạo thẻ</button>
            </form>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-list"></i> Danh sách thẻ</div>
            <div class="table-responsive"><table><thead><tr><th>Mã</th><th>Gói</th><th>Trạng thái</th><th>QR kích hoạt</th></tr></thead><tbody>${voucherRows}</tbody></table></div>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-signal"></i> Phân tích theo IP (dữ liệu theo thời gian thực)</div>
            <div class="table-responsive"><table>
                <thead><tr><th>IP</th><th>Voucher đã nạp</th><th>Tổng (MB)</th><th>Đã dùng (MB)</th><th>Còn lại (MB)</th><th>Đang tiêu thụ</th><th>Trạng thái</th></tr></thead>
                <tbody>${ipRows || '<tr><td colspan="7">Không có dữ liệu.</td></tr>'}</tbody>
            </table></div>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-gauge-high"></i> Phân tích theo Voucher · connet_data</div>
            <div class="table-responsive"><table>
                <thead><tr><th>Code</th><th>Gói</th><th>Giới hạn (MB)</th><th>Đã dùng (MB)</th><th>Còn lại</th><th>Thiết bị giữ (bound)</th><th>Trạng thái</th></tr></thead>
                <tbody>${voucherAnalysisRows || '<tr><td colspan="7">Không có dữ liệu.</td></tr>'}</tbody>
            </table></div>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-ticket"></i> Chi tiết voucher data · bound & lịch sử thiết bị</div>
            <div class="table-responsive"><table>
                <thead><tr><th>Mã voucher</th><th>Dung lượng (MB)</th><th>Tiến độ</th><th>Thiết bị đang giữ (bound)</th><th>Số thiết bị</th><th>IP từng dùng</th><th>Trạng thái</th></tr></thead>
                <tbody>${dataVoucherRows || '<tr><td colspan="7">Không có dữ liệu.</td></tr>'}</tbody>
            </table></div>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-history"></i> Lịch sử giao dịch nạp thẻ</div>
            <div class="table-responsive"><table>
                <thead><tr><th>Thời gian</th><th>IP</th><th>Mã thẻ</th><th>Loại</th><th>Dung lượng</th></tr></thead>
                <tbody>${transactionRows || '<tr><td colspan="5">Chưa có giao dịch nào.</td></tr>'}</tbody>
            </table></div>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-network-wired"></i> Thiết bị đang kết nối</div>
            ${deviceGroups || '<p style="color:#64748b;">Chưa phát hiện thiết bị trong mạng.</p>'}
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-ticket-alt"></i> Voucher chờ kích hoạt</div>
            <div class="table-responsive"><table>
                <thead><tr><th>Device ID</th><th>IP</th><th>Voucher</th><th>Trạng thái</th><th>Thông báo</th></tr></thead>
                <tbody>
                    ${Object.entries(payload.pending_activations || {}).map(([key, item]) => `<tr><td style="font-family:monospace;">${item.device_id || key}</td><td>${item.ip || '-'}</td><td style="font-family:monospace;">${item.voucher_code || '-'}</td><td>${item.status || 'pending'}</td><td>${item.message || 'Đã gửi cho user'}</td></tr>`).join('') || '<tr><td colspan="5">Chưa có voucher nào đang chờ kích hoạt.</td></tr>'}
                </tbody>
            </table></div>
        </div>
        <div class="card admin-messenger-card">
            <div class="card-title"><i class="fas fa-comments"></i> Hỗ trợ khách hàng <span id="admin-unread-total" class="admin-unread-total"></span></div>
            <div class="admin-messenger">
                <div class="admin-conv-list" id="admin-conv-list">
                    <div class="conv-empty">Đang tải danh sách chat...</div>
                </div>
                <div class="admin-chat-pane">
                    <div class="chat-panel" id="admin-chat-box">
                        <div class="chat-empty" style="text-align:center;padding:40px 20px;">
                            <i class="fas fa-comments" style="font-size:44px;color:#cbd5e1;display:block;margin-bottom:12px;"></i>
                            Chọn 1 hội thoại bên trái để bắt đầu trò chuyện.
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;

    document.getElementById("create-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const packageKey = event.target.package_key.value;
        try {
            renderLoader();
            await adminApi('admin_create_voucher', { package_key: packageKey });
            await refreshAdminPage();
        } catch (error) {
            renderError(error.message);
        }
    });

    document.getElementById("sync-button").addEventListener("click", async () => {
        try {
            renderLoader();
            await adminApi('admin_nft_sync');
            await refreshAdminPage();
        } catch (error) {
            renderError(error.message);
        }
    });

    document.getElementById("logout-button").addEventListener("click", async () => {
        try {
            await adminApi('admin_logout');
            renderLogin();
        } catch (error) {
            renderError(error.message);
        }
    });
document.querySelectorAll(".send-voucher-button").forEach((button) => {
        button.addEventListener("click", () => {
            const deviceId = button.dataset.deviceId || button.dataset.ip || '';
            const ip = button.dataset.ip || '';
            if (!deviceId) {
                alert('Không xác định được thiết bị để gửi voucher.');
                return;
            }

            // Tạo khung popup trực quan trên giao diện Admin
            const popupBg = document.createElement('div');
            popupBg.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;';
            popupBg.innerHTML = `
                <div style="background:#fff;padding:24px;border-radius:12px;width:100%;max-width:400px;box-shadow:0 10px 25px rgba(0,0,0,0.2);">
                    <h3 style="margin-top:0;color:#1e293b;margin-bottom:16px;"><i class="fas fa-ticket-alt"></i> Chọn gói gửi cho ${ip || deviceId}</h3>
                    <div class="form-group" style="margin-bottom:16px;">
                        <label style="display:block;margin-bottom:6px;font-weight:600;font-size:13px;">Chọn loại gói muốn cấp:</label>
                        <select id="popup-package-select" class="form-control" style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:6px;">
                            ${packageOptions}
                        </select>
                    </div>
                    <div style="display:flex;justify-content:flex-end;gap:8px;">
                        <button type="button" id="popup-cancel" class="btn btn-danger" style="padding:6px 12px;">Hủy</button>
                        <button type="button" id="popup-confirm" class="btn btn-success senddddd" style="padding:6px 12px;">Xác nhận gửi</button>
                    </div>
                </div>
            `;
            document.body.appendChild(popupBg);

            // Xử lý nút Hủy đóng popup
            popupBg.querySelector('#popup-cancel').addEventListener('click', () => {
                popupBg.remove();
            });

            // Xử lý nút Xác nhận gửi gói đã chọn
            popupBg.querySelector('#popup-confirm').addEventListener('click', async () => {
                const selectedPackageKey = popupBg.querySelector('#popup-package-select').value;
                popupBg.remove();

                try {
                    renderLoader();
                    const result = await adminApi('admin_send_voucher', { 
                        device_id: deviceId, 
                        ip: ip, 
                        package_key: selectedPackageKey // Lấy đúng gói admin chọn từ dropdown truyền đi
                    });
                    alert(`Đã gửi voucher ${result.voucher.code} cho thiết bị ${deviceId} thành công!`);
                    await refreshAdminPage();
                } catch (error) {
                    alert(error.message);
                    await refreshAdminPage();
                }
            });
        });
    });
    document.querySelectorAll(".senddddd").forEach((button) => {
        button.addEventListener("click", async () => {
            const deviceId = button.dataset.deviceId || button.dataset.ip || '';
            if (!deviceId) {
                alert('Không xác định được thiết bị để gửi voucher.');
                return;
            }
            try {
                const result = await adminApi('admin_send_voucher', { device_id: deviceId, ip: button.dataset.ip || '', package_key: 'time_1h' });
                alert(`Đã gửi voucher ${result.voucher.code} cho thiết bị ${deviceId}.`);
                await refreshAdminPage();
            } catch (error) {
                alert(error.message);
            }
        });
    });

    document.querySelectorAll(".revoke-button").forEach((button) => {
        button.addEventListener("click", async () => {
            const ip = button.dataset.ip;
            if (!ip || !confirm(`Bạn có chắc muốn thu hồi IP ${ip}?`)) {
                return;
            }
            try {
                renderLoader();
                await adminApi('admin_revoke', { ip });
                await refreshAdminPage();
            } catch (error) {
                renderError(error.message);
            }
        });
    });
}


function updateSupportLinks(info) {
    const fbLink = document.getElementById("fb-link");
    const tgLink = document.getElementById("tg-link");
    const phoneLink = document.getElementById("phone-link");
    const emailLink = document.getElementById("email-link");
    if (!fbLink || !tgLink || !phoneLink || !emailLink) return;
    fbLink.href = `https://facebook.com/${info.facebook}`;
    fbLink.textContent = info.facebook;
    tgLink.href = `https://t.me/${info.telegram}`;
    tgLink.textContent = info.telegram;
    phoneLink.href = `tel:${info.phone}`;
    phoneLink.textContent = info.phone;
    emailLink.href = `mailto:${info.email}`;
    emailLink.textContent = info.email;
}

async function loadAdminChat(ipOrId = '') {
    try {
        const res = ipOrId ? await adminApi('chat_get', { ip: ipOrId }) : await adminApi('chat_get', {});
        return res.messages || [];
    } catch (e) { return []; }
}

function renderLogin() {
    isLoginShown = true;
    dashboardLoaded = false;
    mainContent.innerHTML = `
        <div class="card" style="max-width:500px;margin:0 auto;">
            <div class="card-title"><i class="fas fa-user-lock"></i> Đăng nhập Admin</div>
            <form id="login-form">
                <div class="form-group">
                    <label>Mật khẩu</label>
                    <input type="password" name="password" placeholder="Nhập mật khẩu" required />
                </div>
                <button type="submit" class="btn btn-success" style="width:100%;">Đăng nhập</button>
            </form>
        </div>
    `;
    document.getElementById("login-form").addEventListener("submit", (event) => {
        event.preventDefault();
        const password = event.target.password.value;
        renderLoader();
        adminApi('admin_login', { password }).then(async (r) => {
            if (r.ok) { await refreshAdminPage(); }
            else { renderError(r.message || "Đăng nhập thất bại."); renderLogin(); }
        }).catch((e) => { renderError(e.message); renderLogin(); });
    });
}
// Hàm vẽ hoặc cập nhật nội dung tin nhắn bên trong khung chat
function renderAdminChat(messages = []) {
    const box = document.getElementById('admin-chat-box');
    if (!box) return;
    
    // Kiểm tra xem khung chat đã có form chưa, nếu chưa có thì khởi tạo bộ khung (chỉ làm 1 lần duy nhất)
    if (!document.getElementById('admin-chat-form')) {
        const selectedDevice = adminChatDeviceId || '';
        box.innerHTML = `
            <div class="chat-list" id="admin-chat-list-container"></div>
            <form id="admin-chat-form" class="chat-form">
                <input id="admin-chat-input" type="text" placeholder="${selectedDevice ? 'Nhắn tin cho ' + selectedDevice + '...' : 'Gửi tin nhắn cho user...'}" required />
                <button type="submit" class="btn btn-primary">Gửi</button>
            </form>
        `;

        // Gắn sự kiện submit cho form (chỉ gắn 1 lần)
        const form = document.getElementById('admin-chat-form');
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const input = document.getElementById('admin-chat-input');
            const text = (input.value || '').trim();
            if (!text) return;
            try {
                await adminApi('chat', { 
                    device_id: (adminChatDeviceId && !adminChatDeviceId.includes('.')) ? adminChatDeviceId : '', 
                    ip: adminChatDeviceId, 
                    text, 
                    sender: 'admin' 
                });
                input.value = '';
                // Tải lại tin nhắn và cập nhật
                const newMsgs = await loadAdminChat(adminChatDeviceId);
                renderAdminChat(newMsgs);
            } catch (err) { 
                alert(err.message); 
            }
        });
    }

    // Cập nhật phần danh sách tin nhắn bên trong container mà không làm mất form hay mất chữ đang gõ
    const listContainer = document.getElementById('admin-chat-list-container');
    if (listContainer) {
      const html = (messages || []).slice(-12).map((msg) => {
            // Xác định nhãn hiển thị: nếu là admin thì ghi 'Admin', nếu là user thì hiện IP/device đang chọn hoặc 'User'
            let senderLabel = 'System';
            if (msg.sender === 'admin') {
                senderLabel = 'Admin';
            } else if (msg.sender === 'user') {
                senderLabel = adminChatDeviceId ? `User (${adminChatDeviceId})` : 'User';
            }

            return `
                <div class="chat-item ${msg.sender === 'admin' ? 'mine' : 'other'}">
                    <div class="chat-meta">${senderLabel}</div>
                    <div class="chat-text">${(msg.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
                </div>
            `;
        }).join('') || '<div class="chat-empty">Chưa có cuộc trò chuyện nào với user này.</div>';
        listContainer.innerHTML = html;
        listContainer.scrollTop = listContainer.scrollHeight;
    }
}
async function refreshAdminPage() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    isLoginShown = false;
    dashboardLoaded = true;
    try {
        const res = await adminApi('admin_dashboard');
        if (res.dashboard) renderAdminDashboard(res.dashboard);
        renderAdminChat(await loadAdminChat(adminChatDeviceId)); 
        // Gọi ngay lập tức khi load xong dashboard:
adminApi('chat_list').then((res) => {
    if (res) {
        renderAdminConversations(res.conversations || []);
        updateAdminUnreadTotal(res.total_unread || 0);
    }
}).catch(() => {});
      if (!dashboardTimer) {
            dashboardTimer = setInterval(async () => {
                if (document.hidden) return;
                const active = document.activeElement;
                if (active && (active.tagName === "INPUT" || active.tagName === "SELECT" || active.tagName === "TEXTAREA")) return;
                
                try {
                    // Cập nhật danh sách chat và tổng tin chưa đọc hoàn toàn qua WebSocket
                    const res = await adminApi('chat_list');
                    if (res) {
                        renderAdminConversations(res.conversations || []);
                        updateAdminUnreadTotal(res.total_unread || 0);
                    }
                } catch (e) {
                    // Xử lý lỗi ngầm nếu mất kết nối tạm thời
                }
            }, 5000); // Cứ mỗi 5 giây cập nhật lại danh sách chat ngầm qua WebSocket
        }

    } catch (e) {
        dashboardLoaded = false;
        renderError(e.message);
    }
} 
// Hàm vẽ danh sách hội thoại bên cột trái
function renderAdminConversations(convs) {
    const list = document.getElementById('admin-conv-list');
    if (!list) return;

    if (!convs || convs.length === 0) {
        list.innerHTML = `<div class="conv-empty" style="padding:20px;text-align:center;color:#6b6b8a;">Chưa có cuộc trò chuyện nào.</div>`;
        return;
    }

    list.innerHTML = convs.map((c) => {
        const label = c.ip ? `IP: ${c.ip}` : (c.peer || 'Khách');
        const unreadHtml = c.unread > 0 ? `<span class="badge-unread conv-badge" style="background:#ef4444;color:#fff;padding:2px 6px;border-radius:10px;font-size:10px;margin-left:5px;">${c.unread}</span>` : '';
        const active = adminChatDeviceId === c.peer ? 'active' : '';
        
        // Cắt ngắn nội dung tin nhắn cuối cho gọn
        let preview = c.last_text || '...';
        if (preview.length > 25) preview = preview.substring(0, 25) + '...';

        return `<div class="conv-item ${active}" data-peer="${c.peer || ''}" style="padding:12px;border-bottom:1px solid #e2e8f0;cursor:pointer;display:flex;justify-content:space-between;align-items:center;">
            <div class="conv-body" style="overflow:hidden;">
                <div class="conv-top" style="display:flex;justify-content:space-between;margin-bottom:4px;">
                    <strong class="conv-name" style="font-size:13px;color:#1e293b;">${label}</strong>
                    <span class="conv-time" style="font-size:11px;color:#94a3b8;">${c.last_ts ? new Date(c.last_ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : ''}</span>
                </div>
                <div class="conv-preview" style="font-size:12px;color:#64748b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                    ${preview} ${unreadHtml}
                </div>
            </div>
        </div>`;
    }).join('');

    // Gắn sự kiện click để khi chọn hội thoại nào thì mở chat với người đó
    list.querySelectorAll('.conv-item').forEach((item) => {
        item.addEventListener('click', async () => {
            const peer = item.getAttribute('data-peer');
            adminChatDeviceId = peer;
            
            // Đánh dấu active giao diện
            list.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
            item.classList.add('active');

            // Gọi lệnh chat_open để server đánh dấu đã đọc và trả về lịch sử tin nhắn
            try {
                const res = await adminApi('chat_open', { peer });
                renderAdminChat(res.messages || []);
                // Cập nhật lại danh sách ngay lập tức để mất badge chưa đọc
                const listRes = await adminApi('chat_list');
                if (listRes) {
                    renderAdminConversations(listRes.conversations || []);
                    updateAdminUnreadTotal(listRes.total_unread || 0);
                }
            } catch (e) {
                console.error(e);
            }
        });
    });
}
// Hàm xử lý khi có tin nhắn mới đẩy từ WebSocket xuống
function handleAdminChatNew(msg) {
    if (!msg) return;

    // 1. Nếu tin nhắn mới đến từ đúng người đang mở chat
    if (adminChatDeviceId && (adminChatDeviceId === msg.peer || (msg.ip && adminChatDeviceId === msg.ip))) {
        // Chỉ tải lại tin nhắn và cập nhật vào list-container, KHÔNG vẽ lại toàn bộ khung chat box
        loadAdminChat(adminChatDeviceId).then((messages) => {
            const listContainer = document.getElementById('admin-chat-list-container');
            if (listContainer) {
                const html = (messages || []).slice(-12).map((m) => {
                    let senderLabel = 'System';
                    if (m.sender === 'admin') {
                        senderLabel = 'Admin';
                    } else if (m.sender === 'user') {
                        senderLabel = adminChatDeviceId ? `User (${adminChatDeviceId})` : 'User';
                    }
                    return `
                        <div class="chat-item ${m.sender === 'admin' ? 'mine' : 'other'}">
                            <div class="chat-meta">${senderLabel}</div>
                            <div class="chat-text">${(m.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
                        </div>
                    `;
                }).join('') || '<div class="chat-empty">Chưa có cuộc trò chuyện nào với user này.</div>';
                
                listContainer.innerHTML = html;
                listContainer.scrollTop = listContainer.scrollHeight;
            }
        });
    }

    // 2. Cập nhật lại danh sách hội thoại bên cột trái (để nhảy preview và số đếm)
    adminApi('chat_list').then((res) => {
        if (res) {
            renderAdminConversations(res.conversations || []);
            updateAdminUnreadTotal(res.total_unread || 0);
        }
    }).catch(() => {});
}

// Hàm cập nhật tổng số lượng tin nhắn chưa đọc lên tiêu đề
function updateAdminUnreadTotal(total) {
    const badge = document.getElementById('admin-unread-total');
    if (!badge) return;
    if (total > 0) {
        badge.textContent = `(${total} chưa đọc)`;
        badge.style.display = 'inline-block';
    } else {
        badge.textContent = '';
        badge.style.display = 'none';
    }
}

async function initPage() {
    mainContent = document.getElementById("main-content");
    isLoginShown = false;
    dashboardLoaded = false;
    renderLoader();
    connectWS();
}

document.addEventListener('DOMContentLoaded', () => {
    mainContent = document.getElementById("main-content");
    initPage();
});
