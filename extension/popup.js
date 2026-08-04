const fileInput = document.getElementById("file");
const sendButton = document.getElementById("send");
const statusBox = document.getElementById("status");
const monitorInput = document.getElementById("monitor");
const processLatestButton = document.getElementById("processLatest");
const monitorAllInput = document.getElementById("monitorAll");
const stopTaskButton = document.getElementById("stopTask");
const pairingCodeInput = document.getElementById("pairingCode");
const pairExtensionButton = document.getElementById("pairExtension");
const connectionState = document.getElementById("connectionState");
const setupArea = document.getElementById("setupArea");
const operationalArea = document.getElementById("operationalArea");

function showStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.style.color = isError ? "#b3261e" : "#075e54";
}

async function getWhatsAppTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  if (!tab || !tab.url?.startsWith("https://web.whatsapp.com/")) {
    throw new Error("Abra o WhatsApp Web nesta aba primeiro.");
  }
  return tab;
}

async function sendToWhatsApp(tab, message) {
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "PING" });
  } catch (_error) {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  }
  return chrome.tabs.sendMessage(tab.id, message);
}

function setConnectedUi(connected) {
  setupArea.hidden = connected;
  operationalArea.hidden = !connected;
  connectionState.textContent = connected ? "Conectada" : "Não conectada";
  connectionState.style.color = connected ? "#075e54" : "#b3261e";
}

async function initializePopup() {
  const state = await chrome.storage.local.get(["monitoringEnabled", "monitoringAllEnabled", "lastStatus", "apiToken", "pairingFailures", "pairingBlockedUntil"]);
  monitorInput.checked = Boolean(state.monitoringEnabled);
  monitorAllInput.checked = Boolean(state.monitoringAllEnabled);
  if (state.lastStatus?.message) showStatus(state.lastStatus.message, state.lastStatus.error);
  if (!state.apiToken) {
    setConnectedUi(false);
    if (state.pairingBlockedUntil > Date.now()) {
      const minutes = Math.ceil((state.pairingBlockedUntil - Date.now()) / 60000);
      pairingCodeInput.disabled = true;
      pairExtensionButton.disabled = true;
      showStatus(`Pareamento bloqueado por segurança. Tente novamente em ${minutes} minuto(s).`, true);
    } else if (state.pairingBlockedUntil) {
      await chrome.storage.local.remove(["pairingFailures", "pairingBlockedUntil"]);
    }
    return;
  }
  try {
    const response = await fetch("http://127.0.0.1:8000/api/extensao/status", {
      headers: { "X-API-Token": state.apiToken }
    });
    if (!response.ok) throw new Error("Conexão expirada ou revogada");
    setConnectedUi(true);
  } catch (error) {
    await chrome.storage.local.remove("apiToken");
    setConnectedUi(false);
    showStatus(`${error.message}. Conecte a extensão novamente.`, true);
  }
}

initializePopup();

pairingCodeInput.addEventListener("input", () => {
  pairingCodeInput.value = pairingCodeInput.value.replace(/\D/g, "").slice(0, 6);
});

pairExtensionButton.addEventListener("click", async () => {
  const localState = await chrome.storage.local.get(["pairingFailures", "pairingBlockedUntil"]);
  if (localState.pairingBlockedUntil > Date.now()) {
    const minutes = Math.ceil((localState.pairingBlockedUntil - Date.now()) / 60000);
    return showStatus(`Pareamento bloqueado. Aguarde ${minutes} minuto(s).`, true);
  }
  const codigo = pairingCodeInput.value.trim();
  if (!/^\d{6}$/.test(codigo)) return showStatus("Informe o código de 6 dígitos.", true);
  pairExtensionButton.disabled = true;
  showStatus("Conectando a extensão...");
  try {
    const response = await fetch("http://127.0.0.1:8000/api/parear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codigo, nome: `Chrome ${chrome.runtime.getManifest().version}` })
    });
    const result = await response.json();
    if (!response.ok || !result.token) {
      const detail = result.detail;
      const message = typeof detail === "object" ? detail.mensagem : detail;
      const failures = Number(localState.pairingFailures || 0) + 1;
      const serverBlocked = response.status === 429;
      if (failures >= 3 || serverBlocked) {
        const waitMilliseconds = serverBlocked && detail?.aguarde_segundos
          ? Number(detail.aguarde_segundos) * 1000 : 30 * 60 * 1000;
        await chrome.storage.local.set({ pairingFailures: 3, pairingBlockedUntil: Date.now() + waitMilliseconds });
        pairingCodeInput.disabled = true;
        pairExtensionButton.disabled = true;
      } else {
        await chrome.storage.local.set({ pairingFailures: failures });
      }
      throw new Error(message || "Não foi possível conectar");
    }
    await chrome.storage.local.set({ apiToken: result.token, pairingFailures: 0 });
    await chrome.storage.local.remove("pairingBlockedUntil");
    pairingCodeInput.value = "";
    setConnectedUi(true);
    showStatus("Extensão conectada com segurança.");
  } catch (error) {
    showStatus(`${error.message}. Confirme se o servidor está ativo e gere um novo código.`, true);
  } finally {
    const state = await chrome.storage.local.get("pairingBlockedUntil");
    pairExtensionButton.disabled = state.pairingBlockedUntil > Date.now();
  }
});

monitorInput.addEventListener("change", async () => {
  try {
    const tab = await getWhatsAppTab();
    const enabled = monitorInput.checked;
    if (enabled) monitorAllInput.checked = false;
    await chrome.storage.local.set({ monitoringEnabled: enabled, monitoringAllEnabled: false });
    await sendToWhatsApp(tab, { type: enabled ? "START_MONITORING" : "STOP_MONITORING" });
    showStatus(enabled ? "Monitoramento ativo nesta conversa." : "Monitoramento pausado.");
  } catch (error) {
    monitorInput.checked = false;
    await chrome.storage.local.set({ monitoringEnabled: false });
    showStatus(error.message, true);
  }
});

monitorAllInput.addEventListener("change", async () => {
  try {
    const tab = await getWhatsAppTab();
    const enabled = monitorAllInput.checked;
    if (enabled) monitorInput.checked = false;
    await chrome.storage.local.set({ monitoringAllEnabled: enabled, monitoringEnabled: false });
    await sendToWhatsApp(tab, { type: enabled ? "START_ALL_CHATS" : "STOP_ALL_CHATS" });
    showStatus(enabled ? "Monitorando conversas nao lidas." : "Monitoramento geral pausado.");
  } catch (error) {
    monitorAllInput.checked = false;
    await chrome.storage.local.set({ monitoringAllEnabled: false });
    showStatus(error.message, true);
  }
});

stopTaskButton.addEventListener("click", async () => {
  try {
    const tab = await getWhatsAppTab();
    await chrome.storage.local.set({ monitoringEnabled: false, monitoringAllEnabled: false });
    monitorInput.checked = false;
    monitorAllInput.checked = false;
    await sendToWhatsApp(tab, { type: "STOP_ALL_TASKS" });
    showStatus("Tarefa interrompida pelo analista.");
  } catch (error) {
    showStatus(error.message, true);
  }
});

processLatestButton.addEventListener("click", async () => {
  processLatestButton.disabled = true;
  showStatus("Localizando o ultimo anexo recebido...");
  try {
    const tab = await getWhatsAppTab();
    const result = await sendToWhatsApp(tab, { type: "PROCESS_LATEST_ATTACHMENT" });
    if (!result?.ok) throw new Error(result?.error || "Nenhum anexo compativel encontrado na conversa.");
    showStatus("Anexo encontrado. Enviando para extracao...");
  } catch (error) {
    showStatus(`${error.message} Atualize a pagina do WhatsApp Web e tente novamente.`, true);
  } finally {
    processLatestButton.disabled = false;
  }
});

sendButton.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    showStatus("Selecione um arquivo primeiro.", true);
    return;
  }

  sendButton.disabled = true;
  showStatus("Enviando e extraindo dados...");
  const formData = new FormData();
  formData.append("file", file);

  try {
    const { apiToken = "" } = await chrome.storage.local.get("apiToken");
    if (!apiToken) throw new Error("Extensão não conectada. Faça o pareamento primeiro");
    const response = await fetch("http://127.0.0.1:8000/api/atestados", {
      method: "POST",
      body: formData,
      headers: { "X-API-Token": apiToken }
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Falha no processamento");
    if (result.status === "ignorado") showStatus("Arquivo ignorado: nao foi identificado como atestado.");
    else if (result.status === "duplicado") showStatus(`Arquivo ja processado como atestado #${result.id}.`);
    else showStatus(`Atestado #${result.id} recebido. Abra o painel para conferir.`);
  } catch (error) {
    showStatus(`Erro: ${error.message}. Verifique se o servidor local esta ativo.`, true);
  } finally {
    sendButton.disabled = false;
  }
});
