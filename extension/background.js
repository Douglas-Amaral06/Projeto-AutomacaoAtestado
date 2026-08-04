const API_URL = "http://127.0.0.1:8000/api/atestados";
const LOG_URL = "http://127.0.0.1:8000/api/logs";

function base64ToBlob(base64, mimeType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return new Blob([bytes], { type: mimeType });
}

async function saveStatus(message, error = false) {
  await chrome.storage.local.set({ lastStatus: { message, error, at: Date.now() } });
  await chrome.action.setBadgeBackgroundColor({ color: error ? "#b3261e" : "#128c7e" });
  await chrome.action.setBadgeText({ text: error ? "!" : "OK" });
}

async function recordLog(nivel, evento, mensagem, detalhes = null) {
  const protectedValues = [detalhes?.conversa, detalhes?.conversation, detalhes?.arquivo, detalhes?.filename].filter(Boolean);
  let safeMessage = String(mensagem || "");
  protectedValues.forEach((value) => { safeMessage = safeMessage.split(String(value)).join("[DADO PROTEGIDO]"); });
  const safeDetails = detalhes ? { ...detalhes } : null;
  ["conversa", "conversation", "arquivo", "filename", "cpf", "nome", "token", "senha", "secret", "codigo"].forEach((key) => {
    if (safeDetails && key in safeDetails) safeDetails[key] = "[DADO PROTEGIDO]";
  });
  const entry = { nivel, evento, mensagem: safeMessage, detalhes: safeDetails, criadoEm: new Date().toISOString() };
  const { actionLogs = [] } = await chrome.storage.local.get("actionLogs");
  await chrome.storage.local.set({ actionLogs: [entry, ...actionLogs].slice(0, 300) });
  try {
    const { apiToken = "" } = await chrome.storage.local.get("apiToken");
    await fetch(LOG_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Token": apiToken },
      body: JSON.stringify({ nivel, evento, mensagem: safeMessage, detalhes: safeDetails })
    });
  } catch (_error) {
    // O log local continua disponivel mesmo se o servidor estiver desligado.
  }
}

async function uploadAttachment(payload) {
  await recordLog("info", "upload_iniciado", "Enviando documento para analise.", { chave: payload.key });
  const formData = new FormData();
  formData.append("file", base64ToBlob(payload.base64, payload.mimeType), payload.filename);
  const { apiToken = "" } = await chrome.storage.local.get("apiToken");
  if (!apiToken) throw new Error("Token da extensao nao configurado.");
  const response = await fetch(API_URL, { method: "POST", body: formData, headers: { "X-API-Token": apiToken } });
  const result = await response.json();
  if (!response.ok) {
    const detail = result.detail;
    const error = new Error(
      typeof detail === "object" ? detail.mensagem : detail || "Falha ao processar o atestado."
    );
    if (response.status === 429 && detail?.codigo === "gemini_quota_exceeded") {
      error.quotaExceeded = true;
      error.retryAfter = detail.aguarde_segundos;
    }
    throw error;
  }
  if (result.status === "ignorado") {
    await saveStatus(`Arquivo ignorado: ${result.motivo || "nao foi identificado como atestado."}`);
    await recordLog("info", "arquivo_ignorado", `${payload.filename}: ${result.motivo || "nao identificado como atestado"}`, { tipo_documento: result.tipo_documento });
  } else if (result.status === "duplicado") {
    await saveStatus(`Arquivo ja processado anteriormente como atestado #${result.id}.`);
    await recordLog("aviso", "arquivo_duplicado", `${payload.filename} ja corresponde ao atestado #${result.id}.`);
  } else {
    await saveStatus(`Atestado #${result.id} recebido automaticamente.`);
    await recordLog("info", "atestado_salvo", `Atestado #${result.id} salvo para conferencia.`);
  }
  return result;
}

async function trustedClick(tabId, x, y, conversation) {
  const target = { tabId };
  await recordLog("info", "clique_real_iniciado", `Executando clique real em ${conversation}.`, { x, y, tabId });
  try {
    await chrome.debugger.attach(target, "1.3");
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseMoved", x, y
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mousePressed", x, y, button: "left", clickCount: 1
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseReleased", x, y, button: "left", clickCount: 1
    });
    await recordLog("info", "clique_real_enviado", `Clique enviado para ${conversation}.`, { x, y });
    return { ok: true };
  } catch (error) {
    await recordLog("erro", "clique_real_falhou", error.message, { conversation, x, y });
    return { ok: false, error: error.message };
  } finally {
    try { await chrome.debugger.detach(target); } catch (_error) { /* ja desconectado */ }
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "TRUSTED_CLICK") {
    if (!_sender.tab?.id) {
      sendResponse({ ok: false, error: "A aba do WhatsApp nao foi identificada." });
      return false;
    }
    trustedClick(_sender.tab.id, message.x, message.y, message.conversation)
      .then(sendResponse);
    return true;
  }
  if (message.type === "UPLOAD_ATTACHMENT") {
    uploadAttachment(message.payload)
      .then((result) => sendResponse({ ok: true, id: result.id, status: result.status, motivo: result.motivo, tipo_documento: result.tipo_documento }))
      .catch(async (error) => {
        const waitText = error.retryAfter ? ` Tente novamente em cerca de ${error.retryAfter} segundos.` : "";
        const messageText = error.quotaExceeded
          ? `Tarefa pausada: limite da API Gemini atingido.${waitText}`
          : error.message;
        await saveStatus(messageText, true);
        await recordLog(error.quotaExceeded ? "aviso" : "erro", error.quotaExceeded ? "tarefa_pausada_quota" : "upload_falhou", messageText, { arquivo: message.payload?.filename, aguarde_segundos: error.retryAfter });
        sendResponse({ ok: false, error: messageText, quotaExceeded: Boolean(error.quotaExceeded), retryAfter: error.retryAfter });
      });
    return true;
  }
  if (message.type === "MONITOR_ERROR") saveStatus(message.message, true);
  if (message.type === "MONITOR_ERROR") recordLog("erro", message.evento || "monitoramento", message.message, message.detalhes);
  if (message.type === "MONITOR_STATUS") {
    saveStatus(message.message, false);
    recordLog("info", message.evento || "monitoramento", message.message, message.detalhes);
  }
  if (message.type === "MONITOR_LOG") recordLog(message.nivel || "info", message.evento, message.message, message.detalhes);
  return false;
});
