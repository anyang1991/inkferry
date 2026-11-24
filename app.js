"use strict";

const SESSION_KEY_NAME = "zhidu.adminKey";
const SELECTED_DEVICE_NAME = "zhidu.selectedDeviceId";
const androidBridge = window.InkFerryAndroid;

const dom = {
  app: document.getElementById("app"),
  unlock: document.getElementById("unlockPanel"),
  unlockForm: document.getElementById("unlockForm"),
  unlockInput: document.getElementById("adminKeyInput"),
  unlockMessage: document.getElementById("unlockMessage"),
  connectionChip: document.getElementById("connectionChip"),
  limitChip: document.getElementById("limitChip"),
  formatsChip: document.getElementById("formatsChip"),
  uploadForm: document.getElementById("uploadForm"),
  fileInput: document.getElementById("fileInput"),
  chooseButton: document.getElementById("chooseButton"),
  encodingSelect: document.getElementById("encodingSelect"),
  dropzone: document.getElementById("dropzone"),
  uploadHint: document.getElementById("uploadHint"),
  uploadError: document.getElementById("uploadError"),
  uploadSkeleton: document.getElementById("uploadSkeleton"),
  draftSection: document.getElementById("draftSection"),
  draftStats: document.getElementById("draftStats"),
  draftMeta: document.getElementById("draftMeta"),
  titleInput: document.getElementById("titleInput"),
  authorInput: document.getElementById("authorInput"),
  styleRadios: document.querySelectorAll('input[name="bookStyle"]'),
  chapterRadios: document.querySelectorAll('input[name="chapterMode"]'),
  formatRadios: document.querySelectorAll('input[name="bookFormat"]'),
  sendButton: document.getElementById("sendButton"),
  sendHint: document.getElementById("sendHint"),
  convertButton: document.getElementById("convertButton"),
  currentDownloadButton: document.getElementById("currentDownloadButton"),
  bookStatus: document.getElementById("bookStatus"),
  bookError: document.getElementById("bookError"),
  currentBook: document.getElementById("currentBook"),
  transferStatus: document.getElementById("transferStatus"),
  transferSteps: document.getElementById("transferSteps"),
  transferMessage: document.getElementById("transferMessage"),
  transferError: document.getElementById("transferError"),
  checkTransferButton: document.getElementById("checkTransferButton"),
  previewTitle: document.getElementById("previewTitle"),
  previewAuthor: document.getElementById("previewAuthor"),
  previewBody: document.getElementById("previewBody"),
  chapterList: document.getElementById("chapterList"),
  deviceError: document.getElementById("deviceError"),
  deviceSuccess: document.getElementById("deviceSuccess"),
  deviceList: document.getElementById("deviceList"),
  discoveryList: document.getElementById("discoveryList"),
  discoveryEmpty: document.getElementById("discoveryEmpty"),
  deviceSkeleton: document.getElementById("deviceSkeleton"),
  deviceEmpty: document.getElementById("deviceEmpty"),
  pairForm: document.getElementById("pairForm"),
  pairCodeInput: document.getElementById("pairCodeInput"),
  pairNameInput: document.getElementById("pairNameInput"),
  pairSubmit: document.getElementById("pairSubmit"),
  receiverLinkInput: document.getElementById("receiverLinkInput"),
  receiverNetworkNote: document.getElementById("receiverNetworkNote"),
  senderAccessSection: document.getElementById("senderAccessSection"),
  runtimeHint: document.getElementById("runtimeHint"),
  senderSelect: document.getElementById("senderSelect"),
  senderLinkInput: document.getElementById("senderLinkInput"),
  copySenderButton: document.getElementById("copySenderButton"),
  copyMessage: document.getElementById("copyMessage"),
};

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const state = {
  key: "",
  info: null,
  draft: null,
  book: null,
  transfer: null,
  transferWaitingTooLong: false,
  devices: [],
  available: [],
  loadingDevices: false,
  mutatingDevices: false,
  deviceRevision: 0,
  selectedDeviceId: "",
  previewChapterIndex: 0,
  deviceConfirmId: "",
  analyzing: false,
  converting: false,
  sending: false,
  transferCheckBusy: false,
  senderUrls: [],
  senderIndex: 0,
  transferController: null,
};

function selectedValue(radios) {
  const checked = Array.from(radios).find((item) => item.checked);
  return checked ? checked.value : "";
}

function setBusy(button, busy, labelBusy) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = labelBusy;
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
    delete button.dataset.label;
  }
  button.disabled = busy;
}

function setLiveMessage(element, message) {
  element.textContent = message;
  element.hidden = !message;
}

function authHeaders(extraHeaders = {}) {
  const headers = { ...extraHeaders };
  if (state.key) headers["X-Admin-Key"] = state.key;
  return headers;
}

function errorMessage(status, payload) {
  if (payload && typeof payload.error === "string" && payload.error) return payload.error;
  if (status === 401) return "管理密钥无效或已过期。";
  if (status === 404) return "没有找到请求的资源。";
  if (status === 413) return "文件超过服务允许的大小。";
  return `请求失败（${status}）。`;
}
async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: authHeaders(options.headers || {}),
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      payload = null;
    }
  }
  if (!response.ok) {
    if (response.status === 401) invalidateKey("管理密钥无效或已过期，请重新输入。");
    throw new ApiError(errorMessage(response.status, payload), response.status);
  }
  return payload;
}

function readHashKey() {
  const rawHash = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  const params = new URLSearchParams(rawHash);
  const value = params.get("key");
  return value ? value.trim() : "";
}

function invalidateKey(message) {
  state.key = "";
  sessionStorage.removeItem(SESSION_KEY_NAME);
  dom.app.hidden = true;
  dom.unlock.hidden = false;
  setLiveMessage(dom.unlockMessage, message);
  dom.unlockInput.focus();
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 KB";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderConnection() {
  dom.connectionChip.textContent = state.info ? "服务已连接" : "连接检查中";
  if (state.info) {
    dom.connectionChip.classList.add("is-ready");
    dom.limitChip.textContent = `单次上限 ${formatBytes(state.info.max_upload_bytes)}`;
    const formats = Array.isArray(state.info.formats) ? state.info.formats : [];
    dom.formatsChip.textContent = formats.length ? `支持 ${formats.join(" / ").toUpperCase()}` : "格式未公布";
  }
}

function renderDevices() {
  dom.deviceSkeleton.hidden = true;
  const list = dom.deviceList;
  const focused = list.contains(document.activeElement) ? document.activeElement : null;
  list.replaceChildren();

  state.devices.forEach((device) => {
    const item = document.createElement("li");
    item.className = "device-card";
    if (device.id === state.selectedDeviceId) item.classList.add("is-selected");

    const selectButton = document.createElement("button");
    selectButton.type = "button";
    selectButton.className = "device-select";
    selectButton.setAttribute("aria-pressed", device.id === state.selectedDeviceId ? "true" : "false");
    selectButton.dataset.deviceId = device.id;

    const name = document.createElement("strong");
    name.textContent = device.name || "未命名 Kindle";
    const status = document.createElement("span");
    status.className = device.online ? "device-online" : "device-offline";
    status.textContent = device.online ? "在线" : "离线";
    const note = document.createElement("small");
    note.textContent = device.id === state.selectedDeviceId ? "当前发送目标" : "点击选择";
    selectButton.append(name, status, note);

    const manage = document.createElement("div");
    manage.className = "device-manage";
    if (state.deviceConfirmId === device.id) {
      const confirmLabel = document.createElement("span");
      confirmLabel.textContent = "删除后需重新配对。";
      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.className = "button danger small";
      confirmButton.textContent = "确认删除";
      confirmButton.dataset.confirmId = device.id;
      const cancelButton = document.createElement("button");
      cancelButton.type = "button";
      cancelButton.className = "button ghost small";
      cancelButton.textContent = "取消";
      cancelButton.dataset.cancelId = device.id;
      manage.append(confirmLabel, confirmButton, cancelButton);
    } else {
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "button ghost small";
      deleteButton.textContent = "删除";
      deleteButton.dataset.deleteId = device.id;
      deleteButton.setAttribute("aria-label", `删除设备 ${device.name || "未命名 Kindle"}`);
      manage.append(deleteButton);
    }

    item.append(selectButton, manage);
    list.append(item);
  });

  if (focused) {
    const [key, value] = Object.entries(focused.dataset)[0] || [];
    [...list.querySelectorAll("button")].find((button) => key && button.dataset[key] === value)?.focus();
  }
  dom.deviceEmpty.hidden = state.devices.length > 0;
  renderSendTarget();
}

function renderDiscovery() {
  const list = dom.discoveryList;
  const focusedId = list.contains(document.activeElement) ? document.activeElement.dataset.receiverId : "";
  list.replaceChildren();
  for (const device of state.available) {
    const row = document.createElement("li");
    row.className = "device-card discovery-card";
    const details = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = device.name;
    const address = document.createElement("small");
    address.className = "quiet";
    address.textContent = device.address;
    details.append(name, address);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button secondary";
    button.textContent = "配对";
    button.dataset.receiverId = device.id;
    button.setAttribute("aria-label", `配对 ${device.name}`);
    row.append(details, button);
    list.append(row);
    if (device.id === focusedId) button.focus();
  }
  dom.discoveryEmpty.hidden = state.available.length > 0;
}

function renderSendTarget() {
  const device = state.devices.find((item) => item.id === state.selectedDeviceId);
  const format = selectedValue(dom.formatRadios);
  const busy = state.sending || state.converting || state.analyzing;
  const canConvert = Boolean(state.draft && state.info && state.info.formats.includes(format) && !busy);
  const canSend = canConvert && device && format === "mobi";

  dom.sendButton.disabled = !canSend;
  dom.convertButton.disabled = !canConvert;
  dom.chooseButton.disabled = busy;
  dom.dropzone.disabled = busy;
  if (!state.draft) {
    dom.sendHint.textContent = "上传 TXT 后可选择已配对的 Kindle。";
  } else if (format === "epub") {
    dom.sendHint.textContent = "EPUB 可下载保存。向 Kindle 发送请选择 MOBI。";
  } else if (!device) {
    dom.sendHint.textContent = "请在 Kindle 打开收件页，然后选择设备并配对。";
  } else {
    dom.sendHint.textContent = `将转换为 ${format.toUpperCase()} 后发送到 ${device.name}。`;
  }
}

function renderTransferSteps() {
  const items = [];
  if (state.transfer) {
    const reached = state.transfer.status === "queued" || state.transfer.status === "downloaded";
    items.push(
      ["upload", "书稿已解析", true],
      ["converted", "转换完成", reached],
      ["queued", "已进入传输队列", reached],
      ["downloaded", "Kindle 已下载", state.transfer.status === "downloaded"]
    );
  } else {
    items.push(
      ["upload", "书稿已解析", Boolean(state.draft)],
      ["converted", "等待转换", false],
      ["queued", "等待队列", false],
      ["downloaded", "等待下载", false]
    );
  }
  dom.transferSteps.replaceChildren();
  items.forEach(([key, label, done]) => {
    const step = document.createElement("li");
    step.className = done ? "is-done" : "";
    step.dataset.stage = key;
    step.textContent = label;
    dom.transferSteps.append(step);
  });
}
function renderTransfer() {
  if (!state.transfer) {
    dom.transferStatus.hidden = true;
    dom.transferMessage.textContent = "";
    dom.transferError.hidden = true;
    dom.checkTransferButton.hidden = true;
    dom.transferSteps.replaceChildren();
    return;
  }

  dom.transferStatus.hidden = false;
  const device = state.devices.find((item) => item.id === state.transfer.deviceId);
  const deviceName = device ? device.name : state.transfer.deviceName || "已选设备";
  let statusText = "";
  if (state.transfer.phase === "converting") statusText = "正在转换并准备发送。";
  else if (state.transfer.status === "queued") statusText = `已排队，等待 ${deviceName} 下载。`;
  else if (state.transfer.status === "downloaded") statusText = `${deviceName} 已下载这本书。`;
  else if (state.transfer.phase === "failed") statusText = "发送未完成。";
  else statusText = "正在发送。";
  dom.transferMessage.textContent = statusText;
  dom.checkTransferButton.hidden = !(state.transferWaitingTooLong && state.transfer.status === "queued" && !state.sending);
  renderTransferSteps();
}

function renderCurrentBook() {
  if (!state.book) {
    dom.currentBook.hidden = true;
    dom.currentBook.textContent = "";
    dom.currentDownloadButton.hidden = true;
    return;
  }
  dom.currentBook.hidden = false;
  dom.currentBook.textContent = `${state.book.filename || state.book.title} · ${formatBytes(state.book.size)}`;
  dom.currentDownloadButton.hidden = false;
}

function renderPreview() {
  if (!state.draft) return;
  const chapters = Array.isArray(state.draft.chapters) ? state.draft.chapters : [];
  if (!chapters.length) return;
  const index = Math.min(state.previewChapterIndex, chapters.length - 1);
  const chapter = chapters[index];

  dom.previewTitle.textContent = dom.titleInput.value || state.draft.title || "未命名书稿";
  dom.previewAuthor.textContent = dom.authorInput.value.trim() || "佚名";

  dom.chapterList.replaceChildren();
  chapters.forEach((item, itemIndex) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chapter-button";
    button.textContent = item.title || `章节 ${itemIndex + 1}`;
    button.setAttribute("aria-current", itemIndex === index ? "true" : "false");
    button.dataset.chapterIndex = String(itemIndex);
    dom.chapterList.append(button);
  });

  dom.previewBody.replaceChildren();
  if (chapter.title) {
    const heading = document.createElement("h5");
    heading.className = "preview-chapter-title";
    heading.textContent = chapter.title;
    dom.previewBody.append(heading);
  }
  const paragraphs = Array.isArray(chapter.paragraphs) ? chapter.paragraphs : [];
  paragraphs.forEach((paragraph) => {
    const element = document.createElement("p");
    element.textContent = paragraph.trim();
    dom.previewBody.append(element);
  });

  dom.previewBody.classList.toggle("compact", selectedValue(dom.styleRadios) === "compact");
}

function renderDraft() {
  if (!state.draft) return;
  const chapters = Array.isArray(state.draft.chapters) ? state.draft.chapters : [];
  const stats = [
    ["编码", state.draft.encoding || "自动识别"],
    ["大小", formatBytes(state.draft.size)],
    ["章节", `${state.draft.chapter_count} 章`],
    ["段落", `${state.draft.paragraph_count} 段`]
  ];
  dom.draftStats.replaceChildren();
  stats.forEach(([label, value]) => {
    const wrapper = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    wrapper.append(term, detail);
    dom.draftStats.append(wrapper);
  });

  dom.draftMeta.textContent = `预览为服务返回的前 ${chapters.length} 章，转换时会处理全部章节。`;
  renderPreview();
}

function renderSenderAccess() {
  const links = state.info && Array.isArray(state.info.sender_urls) ? state.info.sender_urls.filter((item) => typeof item === "string" && item) : [];
  state.senderUrls = links;
  dom.senderSelect.replaceChildren();
  links.forEach((url, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = url;
    dom.senderSelect.append(option);
  });
  const hasAccess = links.length > 0 && Boolean(state.key);
  dom.senderSelect.disabled = !hasAccess || links.length === 1;
  dom.senderLinkInput.disabled = !hasAccess;
  dom.copySenderButton.disabled = !hasAccess;
  if (!hasAccess) {
    dom.senderLinkInput.value = "";
    setLiveMessage(dom.copyMessage, "");
    return;
  }
  state.senderIndex = Math.min(state.senderIndex, links.length - 1);
  dom.senderSelect.value = String(state.senderIndex);
  const baseUrl = links[state.senderIndex].replace(/\/+$/, "");
  dom.senderLinkInput.value = `${baseUrl}/#key=${encodeURIComponent(state.key)}`;
}

function renderInfo() {
  renderConnection();
  renderSenderAccess();
  const available = state.info.formats;
  dom.formatRadios.forEach((radio) => {
    radio.disabled = !available.includes(radio.value);
  });
  if (!available.includes(selectedValue(dom.formatRadios))) {
    dom.formatRadios.forEach((radio) => { radio.checked = radio.value === available[0]; });
  }
  renderReceiverAddress();
  renderSendTarget();
}

function renderReceiverAddress() {
  const receivers = state.info ? state.info.receiver_urls : [];
  dom.receiverLinkInput.value = receivers[state.senderIndex] || receivers[0] || "";
  const needsWifi = androidBridge && state.info && !receivers.length;
  setLiveMessage(dom.receiverNetworkNote, needsWifi ? "手机连上 Wi‑Fi 后，这里会显示收件地址。" : "");
}

function collectConvertPayload() {
  return {
    draft_id: state.draft.id,
    title: dom.titleInput.value.trim() || state.draft.title || "未命名书稿",
    author: dom.authorInput.value.trim() || "佚名",
    style: selectedValue(dom.styleRadios),
    chapter_mode: selectedValue(dom.chapterRadios),
    format: selectedValue(dom.formatRadios)
  };
}

async function convertBook() {
  const format = selectedValue(dom.formatRadios);
  if (!state.draft || !state.info || !state.info.formats.includes(format)) {
    throw new ApiError("转换服务未就绪。", 0);
  }
  state.converting = true;
  renderSendTarget();
  setLiveMessage(dom.bookError, "");
  setLiveMessage(dom.bookStatus, "正在转换，请稍候。");
  try {
    const payload = collectConvertPayload();
    const result = await requestJson("/api/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    state.book = result;
    renderCurrentBook();
    setLiveMessage(dom.bookStatus, `转换完成：${result.filename || result.title}`);
    return result;
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) setLiveMessage(dom.bookError, error.message);
    throw error;
  } finally {
    state.converting = false;
    renderSendTarget();
  }
}
async function saveBlob(blob, filename) {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 3000);
}

async function downloadCurrentBook(book) {
  const target = book || state.book;
  if (!target) return;
  setLiveMessage(dom.bookError, "");
  if (androidBridge) {
    if (!androidBridge.saveBook(target.id, target.filename)) {
      setLiveMessage(dom.bookError, "无法打开保存窗口，请重试。");
    } else {
      setLiveMessage(dom.bookStatus, "请选择电子书的保存位置。");
    }
    return;
  }
  setLiveMessage(dom.bookStatus, "正在下载转换文件。");
  try {
    const response = await fetch(`/api/books/${encodeURIComponent(target.id)}`, {
      headers: authHeaders()
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const message = errorMessage(response.status, payload);
      if (response.status === 401) invalidateKey("管理密钥无效或已过期，请重新输入。");
      throw new ApiError(message, response.status);
    }
    const blob = await response.blob();
    const filename = target.filename || `${target.title || "雁书书稿"}.${target.format || "mobi"}`;
    await saveBlob(blob, filename);
    setLiveMessage(dom.bookStatus, `已保存 ${filename}。`);
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) setLiveMessage(dom.bookError, error.message || "下载失败，请重试。");
  }
}

function stopTransferPolling() {
  if (state.transferController) {
    state.transferController.abort();
    state.transferController = null;
  }
}

async function pollTransfer(transferId, signal) {
  const deadline = Date.now() + 8 * 60 * 1000;
  state.transferWaitingTooLong = false;
  while (!signal.aborted) {
    const result = await requestJson(`/api/transfers/${encodeURIComponent(transferId)}`, { signal });
    if (signal.aborted) return;
    if (state.transfer && state.transfer.id === transferId) {
      state.transfer = { ...state.transfer, status: result.status };
      renderTransfer();
    }
    if (result.status === "downloaded") return;
    if (Date.now() >= deadline) {
      state.transferWaitingTooLong = true;
      renderTransfer();
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 2500));
  }
}

async function sendToKindle() {
  if (!state.draft || state.sending || state.converting) return;
  const deviceId = state.selectedDeviceId;
  const device = state.devices.find((item) => item.id === deviceId);
  if (!device) {
    dom.deviceError.hidden = false;
    dom.deviceError.textContent = "请先选择或配对一台 Kindle。";
    document.getElementById("devicesSection").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  stopTransferPolling();
  state.sending = true;
  state.transferWaitingTooLong = false;
  state.transfer = { phase: "converting", status: "pending", deviceId, deviceName: device.name };
  renderSendTarget();
  setLiveMessage(dom.transferError, "");
  setLiveMessage(dom.bookError, "");
  renderTransfer();

  const controller = new AbortController();
  state.transferController = controller;
  try {
    const book = await convertBook();
    const result = await requestJson("/api/transfer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_id: book.id, device_id: deviceId })
    });
    state.transfer = { ...state.transfer, id: result.id, status: result.status || "queued", phase: "watching" };
    renderTransfer();
    pollTransfer(result.id, controller.signal).catch((error) => {
      if (controller.signal.aborted) return;
      state.transferWaitingTooLong = true;
      setLiveMessage(dom.transferError, error.message || "无法查询下载状态，请稍后重试。");
      renderTransfer();
    });
  } catch (error) {
    state.transfer.phase = "failed";
    if (error.name !== "AbortError" && !(error instanceof ApiError && error.status === 401)) {
      setLiveMessage(dom.transferError, error.message || "发送失败，请重试。");
    }
  } finally {
    state.sending = false;
    renderSendTarget();
    renderTransfer();
  }
}

async function loadDevices(showLoading = true) {
  if (!state.key || state.loadingDevices || state.mutatingDevices) return;
  state.loadingDevices = true;
  const revision = state.deviceRevision;
  if (showLoading) {
    dom.deviceSkeleton.hidden = false;
    setLiveMessage(dom.deviceError, "");
  }
  try {
    const payload = await requestJson("/api/devices");
    if (revision !== state.deviceRevision) return;
    const devices = Array.isArray(payload.devices) ? payload.devices : [];
    const available = Array.isArray(payload.available) ? payload.available : [];
    const devicesChanged = JSON.stringify(state.devices) !== JSON.stringify(devices);
    const availableChanged = JSON.stringify(state.available) !== JSON.stringify(available);
    state.devices = devices;
    state.available = available;
    if (state.selectedDeviceId && !state.devices.some((device) => device.id === state.selectedDeviceId)) {
      state.selectedDeviceId = "";
      localStorage.removeItem(SELECTED_DEVICE_NAME);
    }
    if (devicesChanged || showLoading) renderDevices();
    if (availableChanged || showLoading) renderDiscovery();
  } catch (error) {
    if (showLoading && !(error instanceof ApiError && error.status === 401)) {
      setLiveMessage(dom.deviceError, error.message || "设备列表加载失败。");
    }
  } finally {
    state.loadingDevices = false;
    dom.deviceSkeleton.hidden = true;
  }
}

function pairByCode(event) {
  event.preventDefault();
  const code = dom.pairCodeInput.value.replace(/\D/g, "");
  const name = dom.pairNameInput.value.trim() || "我的 Kindle";
  if (!/^\d{6}$/.test(code)) {
    setLiveMessage(dom.deviceSuccess, "");
    setLiveMessage(dom.deviceError, "请输入 Kindle 页面显示的六位数字编码。");
    dom.pairCodeInput.focus();
    return;
  }
  pairDevice({ code, name }, dom.pairSubmit);
}

async function pairDevice(payload, button) {
  if (state.mutatingDevices) return;
  state.mutatingDevices = true;
  state.deviceRevision += 1;
  setLiveMessage(dom.deviceError, "");
  setLiveMessage(dom.deviceSuccess, "");
  setBusy(button, true, "配对中");
  try {
    const device = await requestJson("/api/devices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const existingIndex = state.devices.findIndex((item) => item.id === device.id);
    if (existingIndex >= 0) state.devices.splice(existingIndex, 1, device);
    else state.devices.push(device);
    state.selectedDeviceId = device.id;
    localStorage.setItem(SELECTED_DEVICE_NAME, device.id);
    state.deviceConfirmId = "";
    if (payload.receiver_id) {
      state.available = state.available.filter((candidate) => candidate.id !== payload.receiver_id);
      renderDiscovery();
    }
    renderDevices();
    if (payload.receiver_id) {
      [...dom.deviceList.querySelectorAll("[data-device-id]")]
        .find((select) => select.dataset.deviceId === device.id)?.focus();
    }
    dom.pairCodeInput.value = "";
    dom.pairNameInput.value = "我的 Kindle";
    setLiveMessage(dom.deviceSuccess, `已配对 ${device.name || "我的 Kindle"}。`);
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) setLiveMessage(dom.deviceError, error.message || "配对失败，请刷新 Kindle 收件页后重试。");
  } finally {
    state.mutatingDevices = false;
    setBusy(button, false);
    loadDevices(false);
  }
}

async function deleteDevice(deviceId) {
  if (state.mutatingDevices) return;
  state.mutatingDevices = true;
  state.deviceRevision += 1;
  setLiveMessage(dom.deviceError, "");
  setLiveMessage(dom.deviceSuccess, "");
  try {
    await requestJson(`/api/devices/${encodeURIComponent(deviceId)}`, { method: "DELETE" });
    state.devices = state.devices.filter((device) => device.id !== deviceId);
    if (state.selectedDeviceId === deviceId) {
      state.selectedDeviceId = "";
      localStorage.removeItem(SELECTED_DEVICE_NAME);
    }
    state.deviceConfirmId = "";
    renderDevices();
    setLiveMessage(dom.deviceSuccess, "设备已删除。");
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) setLiveMessage(dom.deviceError, error.message || "删除设备失败。");
  } finally {
    state.mutatingDevices = false;
  }
}

async function checkTransferOnce() {
  if (!state.transfer || !state.transfer.id || state.transferCheckBusy) return;
  const transferId = state.transfer.id;
  state.transferCheckBusy = true;
  dom.checkTransferButton.disabled = true;
  try {
    const result = await requestJson(`/api/transfers/${encodeURIComponent(transferId)}`);
    if (state.transfer?.id !== transferId) return;
    state.transfer = { ...state.transfer, status: result.status };
    state.transferWaitingTooLong = result.status === "queued";
    setLiveMessage(dom.transferError, "");
  } catch (error) {
    if (state.transfer?.id === transferId && !(error instanceof ApiError && error.status === 401)) {
      setLiveMessage(dom.transferError, error.message || "状态查询失败。");
    }
  } finally {
    state.transferCheckBusy = false;
    dom.checkTransferButton.disabled = false;
    renderTransfer();
  }
}
async function analyzeFile(file) {
  if (!file || state.analyzing || state.converting || state.sending) return;
  setLiveMessage(dom.uploadError, "");
  const maxSize = state.info ? state.info.max_upload_bytes : null;
  const isTxt = file.name.toLowerCase().endsWith(".txt");
  if (!isTxt) {
    setLiveMessage(dom.uploadError, "请选择 TXT 文件。");
    return;
  }
  if (maxSize && file.size > maxSize) {
    setLiveMessage(dom.uploadError, `文件超过 ${formatBytes(maxSize)} 上限，请拆分后再上传。`);
    return;
  }

  state.analyzing = true;
  state.draft = null;
  state.book = null;
  state.transfer = null;
  state.previewChapterIndex = 0;
  stopTransferPolling();
  dom.uploadSkeleton.hidden = false;
  dom.draftSection.hidden = true;
  dom.chooseButton.disabled = true;
  dom.chooseButton.textContent = "正在解析";
  renderCurrentBook();
  renderTransfer();
  renderSendTarget();

  try {
    const buffer = await file.arrayBuffer();
    const result = await requestJson("/api/analyze", {
      method: "POST",
      headers: {
        "X-Filename": encodeURIComponent(file.name),
        "X-Encoding": dom.encodingSelect.value
      },
      body: buffer
    });
    state.draft = result;
    dom.titleInput.value = result.title || file.name.replace(/\.txt$/i, "");
    dom.authorInput.value = result.author || "";
    dom.draftSection.hidden = false;
    renderDraft();
    setLiveMessage(dom.uploadHint, `已解析 ${file.name}。`);
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) setLiveMessage(dom.uploadError, error.message || "解析失败，请确认文件后重试。");
    dom.draftSection.hidden = true;
  } finally {
    state.analyzing = false;
    dom.uploadSkeleton.hidden = true;
    dom.chooseButton.disabled = false;
    dom.chooseButton.textContent = "选择 TXT 文件";
    dom.fileInput.value = "";
    renderSendTarget();
  }
}

async function loadInfo() {
  try {
    const payload = await requestJson("/api/info");
    state.info = payload;
    setLiveMessage(dom.uploadError, "");
    renderInfo();
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) {
      dom.connectionChip.textContent = "服务连接失败";
      setLiveMessage(dom.uploadError, error.message || "无法读取服务信息，请确认本地服务已运行。");
    }
  }
}

async function copySenderLink() {
  const value = dom.senderLinkInput.value;
  if (!value) return;
  let copied = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      copied = true;
    }
  } catch (error) {
    copied = false;
  }

  if (!copied) {
    const helper = document.createElement("textarea");
    helper.value = value;
    helper.readOnly = true;
    helper.setAttribute("aria-hidden", "true");
    helper.className = "clipboard-helper";
    document.body.append(helper);
    helper.select();
    helper.setSelectionRange(0, value.length);
    try {
      copied = document.execCommand("copy");
    } catch (error) {
      copied = false;
    }
    helper.remove();
  }

  if (copied) setLiveMessage(dom.copyMessage, "链接已复制。只分享给可信局域网中的设备。");
  else setLiveMessage(dom.copyMessage, "复制失败，请在输入框中长按并全选复制。");
}

async function bootstrap() {
  await loadInfo();
  await loadDevices();
  renderConnection();
  renderSendTarget();
  renderTransferSteps();
}

async function unlock(event) {
  event.preventDefault();
  const value = dom.unlockInput.value.trim();
  if (!value) {
    setLiveMessage(dom.unlockMessage, "请输入管理密钥。");
    dom.unlockInput.focus();
    return;
  }
  state.key = value;
  sessionStorage.setItem(SESSION_KEY_NAME, value);
  dom.unlock.hidden = true;
  dom.app.hidden = false;
  setLiveMessage(dom.unlockMessage, "");
  dom.unlockInput.value = "";
  await bootstrap();
}

function bindEvents() {
  dom.uploadForm.addEventListener("submit", (event) => event.preventDefault());
  dom.chooseButton.addEventListener("click", () => dom.fileInput.click());
  dom.dropzone.addEventListener("click", () => dom.fileInput.click());
  dom.fileInput.addEventListener("change", () => {
    const file = dom.fileInput.files && dom.fileInput.files[0];
    if (file) analyzeFile(file);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    dom.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dom.dropzone.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dom.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dom.dropzone.classList.remove("is-dragging");
    });
  });
  dom.dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) analyzeFile(file);
  });

  dom.titleInput.addEventListener("input", renderPreview);
  dom.authorInput.addEventListener("input", renderPreview);
  dom.chapterList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-chapter-index]");
    if (!button) return;
    state.previewChapterIndex = Number(button.dataset.chapterIndex);
    renderPreview();
    dom.chapterList.children[state.previewChapterIndex].focus();
  });
  dom.styleRadios.forEach((radio) => radio.addEventListener("change", renderPreview));
  dom.chapterRadios.forEach((radio) => radio.addEventListener("change", renderPreview));
  dom.formatRadios.forEach((radio) => radio.addEventListener("change", () => {
    renderSendTarget();
    if (state.book) setLiveMessage(dom.bookStatus, `已改为 ${radio.value.toUpperCase()}。重新转换后才会更新下载文件。`);
  }));

  dom.sendButton.addEventListener("click", sendToKindle);
  dom.convertButton.addEventListener("click", async () => {
    try {
      const book = await convertBook();
      await downloadCurrentBook(book);
    } catch (error) {
      // convertBook already displays the actionable message.
    }
  });
  dom.currentDownloadButton.addEventListener("click", () => downloadCurrentBook());
  dom.checkTransferButton.addEventListener("click", checkTransferOnce);

  dom.deviceList.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.classList.contains("device-select")) {
      state.selectedDeviceId = button.dataset.deviceId;
      localStorage.setItem(SELECTED_DEVICE_NAME, state.selectedDeviceId);
      state.deviceConfirmId = "";
      renderDevices();
      const selected = state.devices.find((device) => device.id === state.selectedDeviceId);
      setLiveMessage(dom.deviceSuccess, `已选择 ${selected ? selected.name : "设备"}。`);
      return;
    }
    if (button.dataset.deleteId) {
      state.deviceConfirmId = button.dataset.deleteId;
      renderDevices();
      return;
    }
    if (button.dataset.confirmId) deleteDevice(button.dataset.confirmId);
    if (button.dataset.cancelId) {
      state.deviceConfirmId = "";
      renderDevices();
    }
  });
  dom.pairForm.addEventListener("submit", pairByCode);
  dom.discoveryList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-receiver-id]");
    if (button) pairDevice({ receiver_id: button.dataset.receiverId }, button);
  });
  const refreshDevices = () => {
    if (!document.hidden) loadDevices(false);
  };
  window.setInterval(refreshDevices, 3000);
  document.addEventListener("visibilitychange", refreshDevices);

  dom.senderSelect.addEventListener("change", () => {
    state.senderIndex = Number(dom.senderSelect.value) || 0;
    renderSenderAccess();
    renderReceiverAddress();
  });
  dom.receiverLinkInput.addEventListener("focus", () => dom.receiverLinkInput.select());
  dom.copySenderButton.addEventListener("click", copySenderLink);

  dom.unlockForm.addEventListener("submit", unlock);
  window.addEventListener("pagehide", stopTransferPolling);
}

async function init() {
  if (androidBridge) {
    dom.senderAccessSection.hidden = true;
    dom.runtimeHint.textContent = "收书时保持安卓上的雁书打开，让手机与 Kindle 连同一个 Wi‑Fi。";
    window.inkFerryAndroidNetworkChanged = () => {
      if (state.key) loadInfo();
    };
    window.inkFerryExportResult = (status, message) => {
      setLiveMessage(dom.bookError, status === "error" ? message : "");
      setLiveMessage(dom.bookStatus, status === "error" ? "" : message);
    };
  }
  bindEvents();
  const hashKey = readHashKey();
  const savedKey = sessionStorage.getItem(SESSION_KEY_NAME);
  state.key = hashKey || savedKey || "";
  state.selectedDeviceId = localStorage.getItem(SELECTED_DEVICE_NAME) || "";
  if (hashKey) {
    sessionStorage.setItem(SESSION_KEY_NAME, hashKey);
    history.replaceState(null, "", location.pathname + location.search);
  }
  if (!state.key) {
    dom.app.hidden = true;
    dom.unlock.hidden = false;
    dom.unlockInput.focus();
    return;
  }
  dom.app.hidden = false;
  await bootstrap();
}

init();
