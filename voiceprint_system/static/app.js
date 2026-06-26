/* ==========================================================================
   🎙️ 声纹会议转译管理系统 - 前端 JS 控制中心
   ========================================================================== */

const API_BASE = window.location.origin;
let jwtToken = localStorage.getItem("token") || "";
let activeLogInterval = null;
let currentActiveTask = null;

// HTTP 请求通用封装（携带 JWT 认证）
async function apiFetch(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    
    // 注入 Token
    options.headers = options.headers || {};
    if (jwtToken) {
        options.headers["Authorization"] = `Bearer ${jwtToken}`;
    }
    
    try {
        const response = await fetch(url, options);
        
        // 身份验证过期或失效，强制退回登录页
        if (response.status === 401 && endpoint !== "/api/auth/login") {
            logout();
            showToast("您的登录已过期，请重新登录", "error");
            throw new Error("Unauthorized");
        }
        
        // 解析错误
        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            const errMsg = errData.detail || `请求失败 (${response.status})`;
            throw new Error(errMsg);
        }
        
        return await response.json();
    } catch (error) {
        console.error(`API Error [${endpoint}]:`, error);
        throw error;
    }
}

// ----------------------------------------------------------------------
// 1. 初始化 & 认证控制
// ----------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    initApp();
    setupEventListeners();
});

async function initApp() {
    if (!jwtToken) {
        showLoginView();
        return;
    }
    
    try {
        // 验证现有 Token 是否有效
        await apiFetch("/api/auth/verify");
        showAppView();
    } catch (e) {
        logout();
    }
}

function showLoginView() {
    document.getElementById("login-container").classList.remove("hidden");
    document.getElementById("app-container").classList.add("hidden");
}

function showAppView() {
    document.getElementById("login-container").classList.add("hidden");
    document.getElementById("app-container").classList.remove("hidden");
    
    // 初始化数据载入
    refreshStats();
    loadAudioFiles();
    loadMeetings();
}

function logout() {
    jwtToken = "";
    localStorage.removeItem("token");
    if (activeLogInterval) {
        clearInterval(activeLogInterval);
    }
    showLoginView();
}

// ----------------------------------------------------------------------
// 2. 界面选项卡切换 (Tabs Router)
// ----------------------------------------------------------------------
const tabItems = document.querySelectorAll(".menu-item");
const tabPanels = document.querySelectorAll(".tab-panel");

tabItems.forEach(item => {
    item.addEventListener("click", (e) => {
        e.preventDefault();
        const targetTab = item.getAttribute("data-tab");
        
        // 侧边栏按钮高亮
        tabItems.forEach(i => i.classList.remove("active"));
        item.classList.add("active");
        
        // 面板隐藏与显示
        tabPanels.forEach(p => p.classList.remove("active"));
        document.getElementById(targetTab).classList.add("active");
        
        // 切换到声纹页面时，重新拉取最新数据
        if (targetTab === "speakers-panel") {
            loadSpeakersPage();
        } else if (targetTab === "config-panel") {
            loadConfigPage();
        } else if (targetTab === "transcribe-panel") {
            loadAudioFiles();
            loadMeetings();
        }
    });
});

// ----------------------------------------------------------------------
// 3. 全局 Toast 吐司通知提示
// ----------------------------------------------------------------------
function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    
    let icon = "fa-circle-check";
    if (type === "error") icon = "fa-circle-exclamation";
    if (type === "info") icon = "fa-circle-info";
    
    toast.innerHTML = `
        <i class="fa-solid ${icon} toast-icon"></i>
        <div class="toast-content">${message}</div>
    `;
    
    container.appendChild(toast);
    
    // 渐退动画
    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(50px)";
        toast.style.transition = "opacity 0.4s, transform 0.4s";
        setTimeout(() => toast.remove(), 400);
    }, 3500);
}

// ----------------------------------------------------------------------
// 4. 事件监听器配置
// ----------------------------------------------------------------------
function setupEventListeners() {
    // 登录表单
    const loginForm = document.getElementById("login-form");
    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const usernameInput = document.getElementById("username").value;
        const passwordInput = document.getElementById("password").value;
        const loginBtn = document.getElementById("login-btn");
        const loginError = document.getElementById("login-error");
        
        loginBtn.disabled = true;
        loginBtn.querySelector("span").innerText = "登录中...";
        loginError.classList.add("hidden");
        
        try {
            const formData = new FormData();
            formData.append("username", usernameInput);
            formData.append("password", passwordInput);
            
            const res = await fetch(`${API_BASE}/api/auth/login`, {
                method: "POST",
                body: formData
            });
            
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || "用户名或密码错误");
            }
            
            const data = await res.json();
            jwtToken = data.access_token;
            localStorage.setItem("token", jwtToken);
            
            showToast("欢迎回来！登录成功");
            showAppView();
        } catch (err) {
            loginError.innerText = err.message;
            loginError.classList.remove("hidden");
            // 抖动动画
            const card = document.querySelector(".login-card");
            card.style.animation = "none";
            setTimeout(() => card.style.animation = "shake 0.3s ease", 10);
        } finally {
            loginBtn.disabled = false;
            loginBtn.querySelector("span").innerText = "登 录";
        }
    });

    // 登出按钮
    document.getElementById("logout-btn").addEventListener("click", () => {
        logout();
        showToast("已安全登出系统", "info");
    });

    // 高级参数抽屉折叠
    const configToggle = document.getElementById("config-toggle");
    configToggle.addEventListener("click", () => {
        const content = document.getElementById("config-content");
        const chevron = document.getElementById("config-chevron");
        const isHidden = content.classList.contains("hidden");
        
        if (isHidden) {
            content.classList.remove("hidden");
            chevron.style.transform = "rotate(180deg)";
        } else {
            content.classList.add("hidden");
            chevron.style.transform = "rotate(0deg)";
        }
    });
    // 监听指定发言人数复选框
    const specifySpeakers = document.getElementById("specify-speakers");
    specifySpeakers.addEventListener("change", () => {
        const numInput = document.getElementById("num-speakers");
        if (specifySpeakers.checked) {
            numInput.classList.remove("hidden");
        } else {
            numInput.classList.add("hidden");
        }
    });
    // 相似度阈值 Slider 数值同步
    const thresholdSlider = document.getElementById("cosine-threshold");
    thresholdSlider.addEventListener("input", (e) => {
        document.getElementById("threshold-val").innerText = e.target.value;
    });

    // 文件拖拽上传设置
    const uploadZone = document.getElementById("upload-zone");
    const fileInput = document.getElementById("audio-file-input");
    
    uploadZone.addEventListener("click", () => fileInput.click());
    
    uploadZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        uploadZone.classList.add("dragover");
    });
    
    uploadZone.addEventListener("dragleave", () => {
        uploadZone.classList.remove("dragover");
    });
    
    uploadZone.addEventListener("drop", (e) => {
        e.preventDefault();
        uploadZone.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) {
            handleFileUpload(e.dataTransfer.files[0]);
        }
    });
    
    fileInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) {
            handleFileUpload(e.target.files[0]);
        }
    });

    // 启动转译任务表单
    const transcribeForm = document.getElementById("transcribe-form");
    transcribeForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const selectedAudio = document.getElementById("select-audio-file").value;
        if (!selectedAudio) {
            showToast("请选择需要转译的会议音频文件", "error");
            return;
        }

        const engine = document.getElementById("asr-engine").value;
        const threshold = parseFloat(document.getElementById("cosine-threshold").value);
        const useNumSpk = document.getElementById("specify-speakers").checked;
        const numSpeakers = useNumSpk ? parseInt(document.getElementById("num-speakers").value) : null;

        const startBtn = document.getElementById("start-transcribe-btn");
        startBtn.disabled = true;
        startBtn.querySelector("span").innerText = "转写已启动...";

        try {
            const res = await apiFetch("/api/meetings/transcribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    audio: selectedAudio,
                    engine: engine,
                    threshold: threshold,
                    num_speakers: numSpeakers
                })
            });
            showToast(res.message);
            // 追踪日志
            startLogPolling(res.meeting);
        } catch (err) {
            showToast(err.message, "error");
            startBtn.disabled = false;
            startBtn.querySelector("span").innerText = "开始转译音频";
        }
    });

    // 终止任务按钮
    document.getElementById("stop-task-btn").addEventListener("click", async () => {
        if (!currentActiveTask) return;
        if (!confirm(`确认要强制终止正在处理的会议任务 【${currentActiveTask}】 吗？`)) return;

        try {
            await apiFetch(`/api/meetings/${currentActiveTask}/stop`, { method: "POST" });
            showToast("任务已成功终止", "info");
            stopLogPolling();
            loadMeetings();
        } catch (e) {
            showToast(e.message, "error");
        }
    });

    // 刷新会议列表
    document.getElementById("refresh-meetings-btn").addEventListener("click", () => {
        loadMeetings();
        showToast("已刷新会议历史列表");
    });

    // 刷新未知人声声纹池
    document.getElementById("refresh-unknown-btn").addEventListener("click", () => {
        loadUnknownSpeakers();
        showToast("已刷新待标定池");
    });

    // AI 总结配置表单
    const configForm = document.getElementById("config-form");
    configForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const provider = document.getElementById("ai-provider").value;
        const host = document.getElementById("ollama-host").value;
        const model = document.getElementById("ollama-model").value;
        const deepseekKey = document.getElementById("deepseek-key").value;
        const geminiKey = document.getElementById("gemini-key").value;

        try {
            await apiFetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    provider: provider,
                    ollama_host: host || null,
                    ollama_model: model || null,
                    deepseek_api_key: deepseekKey || null,
                    google_api_key: geminiKey || null
                })
            });
            showToast("系统参数配置已保存");
            loadConfigPage(); // 重新加载以掩码敏感值
        } catch (err) {
            showToast(err.message, "error");
        }
    });

    // 切换大模型厂商时，联动参数显示隐藏
    document.getElementById("ai-provider").addEventListener("change", (e) => {
        const val = e.target.value;
        document.querySelectorAll(".config-sub-group").forEach(el => el.classList.add("hidden"));
        if (val === "ollama") document.getElementById("config-ollama-group").classList.remove("hidden");
        if (val === "deepseek") document.getElementById("config-deepseek-group").classList.remove("hidden");
        if (val === "gemini") document.getElementById("config-gemini-group").classList.remove("hidden");
    });

    // 管理员密码更新
    const passwordForm = document.getElementById("password-form");
    passwordForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const oldPwd = document.getElementById("old-password").value;
        const newPwd = document.getElementById("new-password").value;
        const confirmPwd = document.getElementById("confirm-password").value;

        if (newPwd !== confirmPwd) {
            showToast("两次输入的新密码不一致", "error");
            return;
        }
        if (newPwd.length < 6) {
            showToast("新密码长度不能少于 6 位", "error");
            return;
        }

        try {
            await apiFetch("/api/auth/change-password", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ old_password: oldPwd, new_password: newPwd })
            });
            showToast("密码修改成功！");
            passwordForm.reset();
        } catch (err) {
            showToast(err.message, "error");
        }
    });

    // 弹窗关闭事件
    document.getElementById("modal-close-btn").addEventListener("click", () => {
        document.getElementById("meeting-modal").classList.add("hidden");
    });
    // 弹窗 Tab 切换
    const modalTabBtns = document.querySelectorAll(".modal-tab-btn");
    modalTabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            modalTabBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            
            const targetContentId = btn.getAttribute("data-content");
            document.querySelectorAll(".modal-tab-view").forEach(view => view.classList.remove("active"));
            document.getElementById(targetContentId).classList.add("active");
        });
    });
}

// ----------------------------------------------------------------------
// 5. 侧边栏统计信息刷新
// ----------------------------------------------------------------------
async function refreshStats() {
    try {
        const namedSpeakers = await apiFetch("/api/speakers/named");
        const unknownSpeakers = await apiFetch("/api/speakers/unknown");
        
        document.getElementById("stats-named").innerText = namedSpeakers.length;
        document.getElementById("stats-unknown").innerText = unknownSpeakers.length;
    } catch (e) {
        console.error("Failed to load statistics", e);
    }
}

// ----------------------------------------------------------------------
// 6. 音频上传处理 (XMLHttpRequest 进度监控)
// ----------------------------------------------------------------------
function handleFileUpload(file) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!["mp3", "wav", "m4a", "flac"].includes(ext)) {
        showToast("仅支持 .mp3, .wav, .m4a, .flac 格式音频文件", "error");
        return;
    }

    const container = document.getElementById("upload-progress-container");
    const bar = document.getElementById("upload-progress-bar");
    const filenameLabel = document.getElementById("upload-filename");
    const percentLabel = document.getElementById("upload-percent");

    filenameLabel.innerText = file.name;
    percentLabel.innerText = "0%";
    bar.style.width = "0%";
    container.classList.remove("hidden");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/meetings/upload`, true);
    
    // 注入 JWT 权限头部
    if (jwtToken) {
        xhr.setRequestHeader("Authorization", `Bearer ${jwtToken}`);
    }

    xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
            const percent = Math.round((e.loaded / e.total) * 100);
            percentLabel.innerText = `${percent}%`;
            bar.style.width = `${percent}%`;
        }
    };

    xhr.onload = () => {
        if (xhr.status === 200) {
            showToast("音频文件上传成功！");
            setTimeout(() => container.classList.add("hidden"), 1500);
            loadAudioFiles(file.name);
        } else {
            let errMsg = "上传失败";
            try {
                const parsed = JSON.parse(xhr.responseText);
                errMsg = parsed.detail || errMsg;
            } catch(ex){}
            showToast(errMsg, "error");
            container.classList.add("hidden");
        }
    };

    xhr.onerror = () => {
        showToast("网络连接错误，文件上传中止", "error");
        container.classList.add("hidden");
    };

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
}

// ----------------------------------------------------------------------
// 7. 加载音频列表到 Select 选择框
// ----------------------------------------------------------------------
async function loadAudioFiles(autoSelectFilename = "") {
    try {
        const files = await apiFetch("/api/audios");
        const select = document.getElementById("select-audio-file");
        
        // 清空
        select.innerHTML = `<option value="" disabled ${!autoSelectFilename ? 'selected' : ''}>-- 请选择音频文件 --</option>`;
        
        files.forEach(f => {
            const opt = document.createElement("option");
            opt.value = f;
            opt.innerText = f;
            if (f === autoSelectFilename) {
                opt.selected = true;
            }
            select.appendChild(opt);
        });
    } catch (e) {
        showToast("加载可用录音文件列表失败", "error");
    }
}

// ----------------------------------------------------------------------
// 8. 会议历史卡片列表渲染
// ----------------------------------------------------------------------
async function loadMeetings() {
    const container = document.getElementById("meetings-list-container");
    try {
        const meetings = await apiFetch("/api/meetings");
        
        if (meetings.length === 0) {
            container.innerHTML = `
                <div class="no-data-placeholder">
                    <i class="fa-regular fa-folder-open"></i>
                    <p>暂无历史会议记录，请在上方启动全新转译任务。</p>
                </div>`;
            return;
        }

        container.innerHTML = "";
        meetings.forEach(m => {
            const card = document.createElement("div");
            card.className = "meeting-card";
            
            let statusText = "已完成";
            let statusClass = "completed";
            if (m.status === "processing") {
                statusText = "转译中";
                statusClass = "processing";
                // 如果后端正在转写此会议，自动在前端拉起日志监听
                if (!currentActiveTask) {
                    startLogPolling(m.name);
                }
            } else if (m.status === "failed") {
                statusText = "已失败";
                statusClass = "failed";
            }

            card.innerHTML = `
                <div>
                    <div class="meeting-card-title">${m.name}</div>
                    <div class="meeting-card-meta">
                        <span><i class="fa-regular fa-clock"></i> 创建时间: ${m.creation_time}</span>
                        <span><i class="fa-solid fa-server"></i> ASR 引擎: ${m.engine}</span>
                        <span>状态: <span class="status-badge ${statusClass}">${statusText}</span></span>
                    </div>
                </div>
                <div class="meeting-card-actions">
                    <button class="btn-primary btn-sm" onclick="openMeetingDetails('${m.name}', 'transcript')" ${!m.has_transcript ? 'disabled' : ''}>
                        <i class="fa-solid fa-align-left"></i> 查看文本
                    </button>
                    <button class="btn-secondary btn-sm" onclick="openMeetingDetails('${m.name}', 'summary')" ${!m.has_transcript ? 'disabled' : ''}>
                        <i class="fa-solid fa-file-lines"></i> 会议纪要
                    </button>
                    <button class="btn-danger btn-sm" onclick="deleteMeetingConfirm('${m.name}')" title="删除会议结果">
                        <i class="fa-regular fa-trash-can"></i>
                    </button>
                </div>
            `;
            container.appendChild(card);
        });
    } catch (e) {
        showToast("加载历史会议列表失败", "error");
    }
}

async function deleteMeetingConfirm(name) {
    if (!confirm(`您确定要彻底删除会议 【${name}】 的转写结果、音频缓存和 AI 纪要吗？该操作不可恢复！`)) return;
    
    try {
        await apiFetch(`/api/meetings/${name}`, { method: "DELETE" });
        showToast("会议数据已彻底删除");
        loadMeetings();
        refreshStats();
    } catch (e) {
        showToast(e.message, "error");
    }
}

// ----------------------------------------------------------------------
// 9. 任务转写日志长轮询监控 (Logs Tracker)
// ----------------------------------------------------------------------
function startLogPolling(meetingName) {
    if (activeLogInterval) clearInterval(activeLogInterval);
    
    currentActiveTask = meetingName;
    
    // UI 徽标显示
    const activeBadge = document.getElementById("active-task-name");
    activeBadge.innerText = `正在处理: ${meetingName}`;
    activeBadge.classList.remove("hidden");
    document.getElementById("stop-task-btn").classList.remove("hidden");
    document.getElementById("start-transcribe-btn").disabled = true;
    document.getElementById("start-transcribe-btn").querySelector("span").innerText = "转写后台进行中...";

    const consoleBox = document.getElementById("log-console");
    consoleBox.innerText = `[INFO] 已连接至任务 ${meetingName} 日志追踪流...\n`;

    activeLogInterval = setInterval(async () => {
        try {
            // 1. 获取日志
            const logData = await apiFetch(`/api/meetings/${meetingName}/log`);
            consoleBox.innerText = logData.log;
            consoleBox.scrollTop = consoleBox.scrollHeight; // 滚动到底部
            
            // 2. 检测该会议的实时运行状态
            const meetings = await apiFetch("/api/meetings");
            const matching = meetings.find(x => x.name === meetingName);
            
            if (matching && matching.status !== "processing") {
                // 状态不是 processing，说明转译已经结束（成功/失败）
                if (matching.status === "completed") {
                    showToast(`🎉 会议 【${meetingName}】 转译并声纹匹配成功！`);
                } else {
                    showToast(`⚠️ 会议 【${meetingName}】 处理失败，请检查运行日志`, "error");
                }
                stopLogPolling();
                loadMeetings();
                refreshStats();
            }
        } catch (err) {
            console.error("Log fetch error:", err);
        }
    }, 1500);
}

function stopLogPolling() {
    if (activeLogInterval) {
        clearInterval(activeLogInterval);
        activeLogInterval = null;
    }
    currentActiveTask = null;
    
    document.getElementById("active-task-name").classList.add("hidden");
    document.getElementById("stop-task-btn").classList.add("hidden");
    
    const startBtn = document.getElementById("start-transcribe-btn");
    startBtn.disabled = false;
    startBtn.querySelector("span").innerText = "开始转译音频";
}

// ----------------------------------------------------------------------
// 10. 全局声纹数据库治理 (Speakers Administration)
// ----------------------------------------------------------------------
async function loadSpeakersPage() {
    loadNamedSpeakers();
    loadUnknownSpeakers();
    refreshStats();
}

// A. 已标定发言人
async function loadNamedSpeakers() {
    const listEl = document.getElementById("named-speakers-list");
    listEl.innerHTML = `<div class="loading-spinner"><i class="fa-solid fa-circle-notch fa-spin"></i> 正在加载声纹库...</div>`;
    
    try {
        const speakers = await apiFetch("/api/speakers/named");
        if (speakers.length === 0) {
            listEl.innerHTML = `<div class="text-muted text-center py-4">数据库为空，暂无已命名的发言人。</div>`;
            return;
        }

        listEl.innerHTML = "";
        
        // 获取所有已命名发言人的名称以填充下拉列表
        const allNames = speakers.map(s => s.name);

        speakers.forEach((spk, index) => {
            const item = document.createElement("div");
            item.className = "named-speaker-item";
            
            // 构建模板 HTML
            let templatesHtml = "";
            if (spk.templates.length === 0) {
                templatesHtml = `<div class="text-muted text-sm">此成员名下暂无特征模板文件。</div>`;
            } else {
                spk.templates.forEach(t => {
                    const audioUrl = t.wav ? `/api/speakers/audio/named/${encodeURIComponent(spk.name)}/${encodeURIComponent(t.wav)}` : "";
                    
                    // 过滤出除了自己之外的其他成员
                    const otherNames = allNames.filter(n => n !== spk.name);
                    const optionsHtml = ["", ...otherNames].map(name => `<option value="${name}">${name}</option>`).join("");

                    templatesHtml += `
                        <div class="template-item">
                            <div class="template-info">
                                <i class="fa-regular fa-file-code"></i> 特征: ${t.npy}
                            </div>
                            ${t.wav ? `
                            <div class="audio-player-wrapper">
                                <audio controls preload="none">
                                    <source src="${audioUrl}" type="audio/wav">
                                    浏览器不支持音频播放
                                </audio>
                            </div>
                            ` : '<div class="text-muted text-sm mb-2">暂无配套人声音频</div>'}
                            
                            <div class="template-actions">
                                <select onchange="moveTemplate('${spk.name}', '${t.npy}', this.value)" title="重新指派并归类特征给他人">
                                    <option value="" disabled selected>-- 跨人员声纹再匹配 --</option>
                                    ${optionsHtml}
                                </select>
                                <button class="btn-danger btn-sm" onclick="deleteTemplate('${spk.name}', '${t.npy}')" title="彻底物理删除此特征模板">
                                    <i class="fa-solid fa-trash-can"></i> 删除
                                </button>
                            </div>
                        </div>
                    `;
                });
            }

            item.innerHTML = `
                <div class="named-speaker-header" onclick="toggleAccordion('spk-accordion-${index}')">
                    <h4><i class="fa-solid fa-user-check"></i> ${spk.name}</h4>
                    <span>${spk.template_count} 个声纹样本 &nbsp;<i class="fa-solid fa-chevron-down" id="arrow-spk-accordion-${index}"></i></span>
                </div>
                <div class="named-speaker-templates hidden" id="spk-accordion-${index}">
                    <div class="flex-row mb-3 flex-between">
                        <span class="text-muted text-sm">全局唯一姓名，更名将自动反向替换所有会议文档。</span>
                        <button class="btn-secondary btn-sm" onclick="renameSpeakerGlobally('${spk.name}')"><i class="fa-solid fa-user-pen"></i> 全局更名</button>
                    </div>
                    ${templatesHtml}
                </div>
            `;
            listEl.appendChild(item);
        });
    } catch (e) {
        listEl.innerHTML = `<div class="text-error text-center py-4">加载已标定声纹库失败: ${e.message}</div>`;
    }
}

function toggleAccordion(id) {
    const content = document.getElementById(id);
    const arrow = document.getElementById(`arrow-${id}`);
    const isHidden = content.classList.contains("hidden");
    
    if (isHidden) {
        content.classList.remove("hidden");
        arrow.style.transform = "rotate(180deg)";
    } else {
        content.classList.add("hidden");
        arrow.style.transform = "rotate(0deg)";
    }
}

async function renameSpeakerGlobally(oldName) {
    const newName = prompt(`请输入发言人 【${oldName}】 的新名字:`, oldName);
    if (!newName || newName.trim() === "" || newName === oldName) return;

    try {
        const res = await apiFetch(`/api/speakers/named/${encodeURIComponent(oldName)}/rename`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_name: newName.trim() })
        });
        showToast(res.message);
        loadSpeakersPage();
    } catch (e) {
        showToast(e.message, "error");
    }
}

async function deleteTemplate(speakerName, templateName) {
    if (!confirm(`您确定要删除发言人 【${speakerName}】 的特征样本 【${templateName}】 吗？`)) return;

    try {
        await apiFetch(`/api/speakers/named/${encodeURIComponent(speakerName)}/template/${encodeURIComponent(templateName)}`, {
            method: "DELETE"
        });
        showToast("声纹特征已删除");
        loadSpeakersPage();
    } catch (e) {
        showToast(e.message, "error");
    }
}

async function moveTemplate(speakerName, templateName, targetSpeaker) {
    if (!targetSpeaker) return;
    if (!confirm(`确认要将特征 【${templateName}】 移动到发言人 【${targetSpeaker}】 的目录下吗？`)) return;

    try {
        await apiFetch(`/api/speakers/named/${encodeURIComponent(speakerName)}/template/${encodeURIComponent(templateName)}/move`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target_speaker: targetSpeaker })
        });
        showToast(`成功将特征分配给 ${targetSpeaker}`);
        loadSpeakersPage();
    } catch (e) {
        showToast(e.message, "error");
    }
}

// B. 未标定发言人暂存池
async function loadUnknownSpeakers() {
    const listEl = document.getElementById("unknown-speakers-list");
    listEl.innerHTML = `<div class="loading-spinner"><i class="fa-solid fa-circle-notch fa-spin"></i> 正在加载待标定声纹...</div>`;
    
    try {
        const unknowns = await apiFetch("/api/speakers/unknown");
        const namedSpeakers = await apiFetch("/api/speakers/named");
        const namedNames = namedSpeakers.map(s => s.name);

        if (unknowns.length === 0) {
            listEl.innerHTML = `
                <div class="no-data-placeholder py-4 text-center">
                    <i class="fa-solid fa-user-shield text-success" style="font-size: 2.5rem; margin-bottom: 0.8rem;"></i>
                    <p class="text-success" style="font-weight: 500;">声纹库十分健康</p>
                    <p class="text-muted text-sm">当前所有提取到的声音均匹配成功，没有待标定人声。</p>
                </div>`;
            return;
        }

        listEl.innerHTML = "";
        
        unknowns.forEach(unk => {
            const card = document.createElement("div");
            card.className = "unknown-speaker-card";
            
            // 拼接参考文本 HTML
            let samplesHtml = "";
            if (Object.keys(unk.samples).length > 0) {
                samplesHtml += `<div class="unknown-samples"><h5><i class="fa-regular fa-comment-dots"></i> 说话样例文本参考:</h5>`;
                for (const [mName, phrases] of Object.entries(unk.samples)) {
                    phrases.forEach(phrase => {
                        samplesHtml += `<li>- "${phrase}" <span class="text-muted text-xs">(${mName})</span></li>`;
                    });
                }
                samplesHtml += `</div>`;
            }

            const optionsHtml = ["", ...namedNames].map(n => `<option value="${n}">${n}</option>`).join("");
            const audioUrl = `/api/speakers/audio/unknown/${unk.id}`;

            card.innerHTML = `
                <h4><i class="fa-solid fa-circle-exclamation animate-pulse"></i> 未知声纹: ${unk.id}</h4>
                <div class="unknown-speaker-meta">出现于会议: <b>${unk.meetings.join(", ") || "未知会议"}</b></div>
                
                ${unk.has_wav ? `
                <div class="audio-player-wrapper mb-3">
                    <audio controls preload="none">
                        <source src="${audioUrl}" type="audio/wav">
                        浏览器不支持音频播放
                    </audio>
                </div>
                ` : '<div class="text-muted text-sm mb-3">暂无配套人声音频剪辑</div>'}
                
                ${samplesHtml}

                <div class="promotion-form">
                    <div class="promotion-row">
                        <div class="promotion-group">
                            <label>方法一：输入新成员真实姓名</label>
                            <input type="text" id="promo-input-${unk.id}" placeholder="请输入名字" oninput="document.getElementById('promo-select-${unk.id}').value=''">
                        </div>
                        <div class="promotion-group">
                            <label>方法二：指派追加至已有成员</label>
                            <select id="promo-select-${unk.id}" onchange="document.getElementById('promo-input-${unk.id}').value=''">
                                <option value="" selected>-- 选择已有发言人 --</option>
                                ${optionsHtml}
                            </select>
                        </div>
                    </div>
                    <div class="promotion-actions">
                        <button class="btn-primary btn-sm" onclick="promoteSpeaker('${unk.id}')"><i class="fa-solid fa-user-plus"></i> 确认人名标定</button>
                        <button class="btn-danger btn-sm" onclick="discardUnknown('${unk.id}')" title="删除此无效声纹特征"><i class="fa-solid fa-trash-can"></i> 丢弃此声纹</button>
                    </div>
                </div>
            `;
            listEl.appendChild(card);
        });
    } catch (e) {
        listEl.innerHTML = `<div class="text-error text-center py-4">加载待标定池失败: ${e.message}</div>`;
    }
}

async function promoteSpeaker(unknownId) {
    const inputVal = document.getElementById(`promo-input-${unknownId}`).value.trim();
    const selectVal = document.getElementById(`promo-select-${unknownId}`).value;
    const finalName = inputVal || selectVal;
    
    if (!finalName) {
        showToast("请填入或选择要标定的人名姓名！", "error");
        return;
    }

    try {
        const res = await apiFetch(`/api/speakers/unknown/${unknownId}/promote`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_name: finalName })
        });
        showToast(res.message);
        loadSpeakersPage();
    } catch (e) {
        showToast(e.message, "error");
    }
}

async function discardUnknown(unknownId) {
    if (!confirm(`确认要从全局池中丢弃声纹样本 【${unknownId}】 吗？丢弃后它在此会议中的发言仍为"未知发言人"`)) return;

    try {
        await apiFetch(`/api/speakers/unknown/${unknownId}`, { method: "DELETE" });
        showToast("声纹特征已安全丢弃");
        loadSpeakersPage();
    } catch (e) {
        showToast(e.message, "error");
    }
}

// ----------------------------------------------------------------------
// 11. 会议详情弹窗与再匹配更名逻辑
// ----------------------------------------------------------------------
async function openMeetingDetails(meetingName, defaultTab = "transcript") {
    const modal = document.getElementById("meeting-modal");
    
    // 初始化清空弹窗内容
    document.getElementById("modal-meeting-name").innerText = meetingName;
    document.getElementById("modal-meeting-time").innerText = "载入中...";
    document.getElementById("modal-transcript-content").innerHTML = `<div class="loading-spinner"><i class="fa-solid fa-circle-notch fa-spin"></i> 正在读取对齐文本...</div>`;
    document.getElementById("modal-summary-content").innerHTML = `暂无会议总结，请点击上方按钮一键生成。`;
    document.getElementById("modal-speaker-remap-container").innerHTML = `<div class="text-muted">加载列表...</div>`;
    
    // 打开 Modal
    modal.classList.remove("hidden");
    
    // 选择默认 Tab
    document.querySelectorAll(".modal-tab-btn").forEach(btn => {
        if (btn.getAttribute("data-content") === `modal-${defaultTab}-tab`) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });
    document.querySelectorAll(".modal-tab-view").forEach(v => {
        if (v.id === `modal-${defaultTab}-tab`) {
            v.classList.add("active");
        } else {
            v.classList.remove("active");
        }
    });

    try {
        const data = await apiFetch(`/api/meetings/${encodeURIComponent(meetingName)}`);
        
        // 渲染基础信息
        document.getElementById("modal-meeting-time").innerText = `转译于: ${data.meta.creation_time || "未知"} | 引擎: ${data.meta.engine_used || "未知"}`;
        
        // 渲染对话文本
        if (data.transcript_md) {
            document.getElementById("modal-transcript-content").innerHTML = renderMarkdown(data.transcript_md);
        } else {
            document.getElementById("modal-transcript-content").innerText = "暂无对齐对话文本。";
        }
        
        // 渲染 AI 纪要
        if (data.summary_md) {
            document.getElementById("modal-summary-content").innerHTML = renderMarkdown(data.summary_md);
        } else {
            document.getElementById("modal-summary-content").innerHTML = `
                <div class="text-center py-5 text-muted">
                    <i class="fa-solid fa-brain" style="font-size: 2.2rem; margin-bottom: 1rem; color: var(--text-muted);"></i>
                    <p>尚未针对此会议生成 AI 总结报告</p>
                </div>
            `;
        }

        // 渲染局部发言人再匹配表单列表
        const namedSpeakers = await apiFetch("/api/speakers/named");
        const allNames = namedSpeakers.map(s => s.name);
        const remapContainer = document.getElementById("modal-speaker-remap-container");
        remapContainer.innerHTML = "";

        const speakerRefs = data.meta.speaker_references || {};
        if (Object.keys(speakerRefs).length === 0) {
            remapContainer.innerHTML = `<div class="text-muted text-sm text-center py-3">本场会议未发现有效角色。</div>`;
        } else {
            for (const [spkLabel, refInfo] of Object.entries(speakerRefs)) {
                const row = document.createElement("div");
                row.className = "remap-row";
                
                const currName = refInfo.voiceprint_id || "未知发言人";
                const isLabeled = refInfo.voiceprint_type === "labeled";
                
                const otherNames = allNames.filter(n => n !== currName);
                const optionsHtml = ["", ...otherNames].map(n => `<option value="${n}">${n}</option>`).join("");

                row.innerHTML = `
                    <div class="remap-label"><i class="fa-solid fa-comment-dots text-primary"></i> ${spkLabel}</div>
                    <div class="remap-meta">当前映射: <b class="${isLabeled ? 'text-primary' : 'text-error'}">${currName}</b></div>
                    <div class="remap-actions">
                        <select id="remap-select-${meetingName}-${spkLabel}" onchange="document.getElementById('remap-input-${meetingName}-${spkLabel}').value=''" title="重映射到声纹库已有人员">
                            <option value="" selected>-- 关联已有人员 --</option>
                            ${optionsHtml}
                        </select>
                        <input type="text" id="remap-input-${meetingName}-${spkLabel}" placeholder="或输入全新姓名" oninput="document.getElementById('remap-select-${meetingName}-${spkLabel}').value=''">
                        <button class="btn-primary btn-sm" onclick="saveSpeakerRemap('${meetingName}', '${spkLabel}')"><i class="fa-solid fa-floppy-disk"></i> 确认匹配</button>
                    </div>
                `;
                remapContainer.appendChild(row);
            }
        }

        // 绑定生成 AI 总结的按钮事件
        const sumBtn = document.getElementById("modal-regenerate-summary-btn");
        // 解绑旧事件，绑定新事件
        const newSumBtn = sumBtn.cloneNode(true);
        sumBtn.parentNode.replaceChild(newSumBtn, sumBtn);
        
        newSumBtn.addEventListener("click", () => triggerAiSummary(meetingName));

    } catch (e) {
        showToast("载入会议详情失败", "error");
        document.getElementById("meeting-modal").classList.add("hidden");
    }
}

async function saveSpeakerRemap(meetingName, speakerLabel) {
    const selectVal = document.getElementById(`remap-select-${meetingName}-${speakerLabel}`).value;
    const inputVal = document.getElementById(`remap-input-${meetingName}-${speakerLabel}`).value.trim();
    const finalName = inputVal || selectVal;
    
    if (!finalName) {
        showToast("请填入或选择要关联的姓名名字！", "error");
        return;
    }

    try {
        const res = await apiFetch(`/api/meetings/${encodeURIComponent(meetingName)}/remap-speaker`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                speaker_label: speakerLabel,
                new_name: finalName
            })
        });
        showToast(res.message);
        // 重新拉取以刷新文本
        openMeetingDetails(meetingName, "transcript");
        loadMeetings();
    } catch (e) {
        showToast(e.message, "error");
    }
}

// 异步长轮询刷新 AI 总结
async function triggerAiSummary(meetingName) {
    const contentBox = document.getElementById("modal-summary-content");
    const sumBtn = document.getElementById("modal-regenerate-summary-btn");
    
    sumBtn.disabled = true;
    sumBtn.querySelector("span").innerText = "正在大模型分析中...";
    contentBox.innerHTML = `
        <div class="text-center py-5">
            <i class="fa-solid fa-wand-magic-sparkles fa-spin text-primary" style="font-size: 2.5rem; margin-bottom: 1rem;"></i>
            <p style="font-weight: 500;">大模型正在流式总结中，通常需要 20-40 秒，请勿关闭窗口...</p>
        </div>
    `;

    try {
        await apiFetch(`/api/meetings/${encodeURIComponent(meetingName)}/summarize`, { method: "POST" });
        
        // 启动轮询检查 summary.md 文件是否已被写入完整内容
        let attempts = 0;
        const sumInterval = setInterval(async () => {
            attempts++;
            try {
                const data = await apiFetch(`/api/meetings/${encodeURIComponent(meetingName)}`);
                // 如果 summary_md 不是空且不再包含 "正在生成" 文本，说明完成
                if (data.summary_md && !data.summary_md.includes("正在生成") && !data.summary_md.includes("载入中")) {
                    clearInterval(sumInterval);
                    contentBox.innerHTML = renderMarkdown(data.summary_md);
                    sumBtn.disabled = false;
                    sumBtn.querySelector("span").innerText = "重新生成 AI 总结";
                    showToast("AI 会议总结已成功生成！");
                    loadMeetings(); // 刷新外挂列表里的总结勾选状态
                } else if (attempts > 50) { // 75秒超时
                    clearInterval(sumInterval);
                    contentBox.innerText = "大模型生成总结超时，请稍后重试。";
                    sumBtn.disabled = false;
                    sumBtn.querySelector("span").innerText = "生成 AI 总结";
                }
            } catch (err) {
                console.error("Poll summary error:", err);
            }
        }, 1500);

    } catch (e) {
        showToast(e.message, "error");
        sumBtn.disabled = false;
        sumBtn.querySelector("span").innerText = "生成 AI 总结";
        contentBox.innerText = `生成纪要失败: ${e.message}`;
    }
}

// ----------------------------------------------------------------------
// 12. 系统设置页面数据读取与写入
// ----------------------------------------------------------------------
async function loadConfigPage() {
    try {
        const config = await apiFetch("/api/config");
        
        document.getElementById("ai-provider").value = config.provider;
        document.getElementById("ollama-host").value = config.ollama_host || "";
        document.getElementById("ollama-model").value = config.ollama_model || "";
        
        if (config.deepseek_api_key) {
            document.getElementById("deepseek-key").value = config.deepseek_api_key;
        } else {
            document.getElementById("deepseek-key").value = "";
        }
        
        if (config.google_api_key) {
            document.getElementById("gemini-key").value = config.google_api_key;
        } else {
            document.getElementById("gemini-key").value = "";
        }

        // 联动选择框显示隐藏
        document.querySelectorAll(".config-sub-group").forEach(el => el.classList.add("hidden"));
        if (config.provider === "ollama") document.getElementById("config-ollama-group").classList.remove("hidden");
        if (config.provider === "deepseek") document.getElementById("config-deepseek-group").classList.remove("hidden");
        if (config.provider === "gemini") document.getElementById("config-gemini-group").classList.remove("hidden");

    } catch (e) {
        showToast("载入大模型系统设置失败", "error");
    }
}

// ----------------------------------------------------------------------
// 13. 超轻量级前端 Markdown 渲染渲染器
// ----------------------------------------------------------------------
function renderMarkdown(md) {
    if (!md) return "";
    let html = md
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

    // 标题替换
    html = html.replace(/^# (.*?)$/gm, '<h1>$1</h1>');
    html = html.replace(/^## (.*?)$/gm, '<h2>$1</h2>');
    html = html.replace(/^### (.*?)$/gm, '<h3>$1</h3>');
    
    // 换行/段落替换
    html = html.replace(/^\* \*\*(.*?)\*\*: (.*?)$/gm, '<li><strong>$1:</strong> $2</li>'); // 对话列表
    html = html.replace(/^\- (.*?)$/gm, '<li>$1</li>'); // 列表
    
    // 粗体字
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // 代码高亮
    html = html.replace(/`(.*?)`/g, '<code>$1</code>');
    
    // 段落换行保留
    html = html.replace(/\n\n/g, '<p></p>');
    html = html.replace(/\n/g, '<br>');
    
    return html;
}
