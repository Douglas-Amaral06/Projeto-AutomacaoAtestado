// Elementos do DOM
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
const configUnitInput = document.getElementById("configUnit");
const lastDeliveryBox = document.getElementById("lastDelivery");
const whatsappStatus = document.getElementById("whatsappStatus");
const whatsappIndicator = document.getElementById("whatsappIndicator");
const backendUrlInput = document.getElementById("backendUrl");
const saveBackendButton = document.getElementById("saveBackend");
const pairingPageLink = document.getElementById("pairingPageLink");
const logsPageLink = document.getElementById("logsPageLink");
const manualFileInput = document.getElementById("manualFile");
const manualExtractButton = document.getElementById("manualExtract");

function updateBackendLinks(baseUrl) {
  pairingPageLink.href = `${baseUrl}/extensao`;
  logsPageLink.href = `${baseUrl}/logs`;
}

function normalizedUnit(value) {
  return String(value || "").trim().toUpperCase().replace(/[^A-Z0-9_-]/g, "").slice(0, 30);
}

function renderLastDelivery(delivery) {
  if (!delivery?.sentAt) return;
  const label = delivery.id ? `Atestado #${delivery.id}` : "Documento analisado";
  const status = delivery.status === "duplicado" ? "Já processado" : delivery.status === "ignorado" ? "Ignorado" : "Enviado para revisão";
  const title = document.createElement("strong");
  const details = document.createElement("span");
  title.textContent = `${label} · ${status}`;
  details.textContent = `Unidade ${delivery.unit || "não informada"} · ${new Date(delivery.sentAt).toLocaleString("pt-BR")}`;
  lastDeliveryBox.replaceChildren(title, details);
}

function applyTaskState(state) {
  const monitoring = Boolean(state.monitoringEnabled || state.monitoringAllEnabled);
  const processing = Boolean(state.activeTask);
  processLatestButton.disabled = monitoring || processing;
  manualExtractButton.disabled = monitoring || processing;
  manualFileInput.disabled = monitoring || processing;
  monitorInput.disabled = processing || Boolean(state.monitoringAllEnabled);
  monitorAllInput.disabled = processing || Boolean(state.monitoringEnabled);
  stopTaskButton.style.display = monitoring ? "block" : "none";
}

async function refreshWhatsAppState() {
  const tabs = await chrome.tabs.query({ url: "https://web.whatsapp.com/*" });
  const opened = tabs.length > 0;
  whatsappStatus.textContent = opened ? "Aberto e disponível" : "Não está aberto";
  whatsappIndicator.className = `status-indicator ${opened ? "connected" : "disconnected"}`;
  if (!opened) showStatus("WhatsApp Web não está aberto. Abra web.whatsapp.com para usar o monitoramento.", true);
  return opened;
}

function showStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.style.color = isError ? "#d32f2f" : "#107c41";
  statusBox.style.backgroundColor = isError ? "#ffebee" : "#d8f1dd";
  statusBox.style.borderLeft = `4px solid ${isError ? "#d32f2f" : "#107c41"}`;
}

async function getWhatsAppTab() {
  const activeTabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (activeTabs[0]?.url?.startsWith("https://web.whatsapp.com/")) return activeTabs[0];
  const whatsappTabs = await chrome.tabs.query({ url: "https://web.whatsapp.com/*" });
  if (!whatsappTabs[0]) throw new Error("WhatsApp Web não está aberto. Abra web.whatsapp.com primeiro.");
  return whatsappTabs[0];
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

  const connectionStateEl = document.getElementById("connectionState");
  if (connected) {
    connectionStateEl.className = "connection-state connected";
    connectionStateEl.textContent = "Extensão conectada";
  } else {
    connectionStateEl.className = "connection-state disconnected";
    connectionStateEl.textContent = "Extensão desconectada";
  }

  stopTaskButton.style.display = (monitorInput.checked || monitorAllInput.checked) ? "block" : "none";
}

async function initializePopup() {
  // Recarregar estado
  const state = await chrome.storage.local.get([
    "monitoringEnabled",
    "monitoringAllEnabled",
    "lastStatus",
    "apiToken",
    "pairingFailures",
    "pairingBlockedUntil",
    "configuredUnit",
    "lastDelivery",
    "activeTask",
    "backendBaseUrl",
  ]);

  if (state.activeTask?.startedAt && Date.now() - state.activeTask.startedAt > 10 * 60 * 1000) {
    await chrome.storage.local.remove("activeTask");
    state.activeTask = null;
  }

  // Restaurar checkboxes
  monitorInput.checked = Boolean(state.monitoringEnabled);
  monitorAllInput.checked = Boolean(state.monitoringAllEnabled);
  configUnitInput.value = state.configuredUnit || "UNI001";
  const configuredBackend = normalizeBackendUrl(state.backendBaseUrl || DEFAULT_BACKEND_URL);
  backendUrlInput.value = configuredBackend;
  updateBackendLinks(configuredBackend);
  renderLastDelivery(state.lastDelivery);
  applyTaskState(state);
  await refreshWhatsAppState();

  // Restaurar status anterior se existir
  if (state.lastStatus?.message) {
    showStatus(state.lastStatus.message, state.lastStatus.error);
  }

  // Verificar se está conectado
  if (!state.apiToken) {
    setConnectedUi(false);

    // Verificar se está bloqueado por falhas
    if (state.pairingBlockedUntil > Date.now()) {
      const minutes = Math.ceil((state.pairingBlockedUntil - Date.now()) / 60000);
      pairingCodeInput.disabled = true;
      pairExtensionButton.disabled = true;
      showStatus(`Pareamento bloqueado por segurança. Tente novamente em ${minutes} minuto(s).`, true);
    }
    return;
  }

  // Verificar se o token ainda é válido
  try {
    const response = await fetch(await backendUrl("/api/extensao/status"), {
      headers: { "X-API-Token": state.apiToken },
    });
    if (!response.ok) throw new Error("Conexão expirada");
    setConnectedUi(true);
  } catch (error) {
    await chrome.storage.local.remove("apiToken");
    setConnectedUi(false);
    showStatus("Token expirado ou revogado. Conecte a extensão novamente.", true);
  }
}

// Inicializar ao carregar
document.addEventListener("DOMContentLoaded", initializePopup);

saveBackendButton.addEventListener("click", async () => {
  saveBackendButton.disabled = true;
  try {
    const baseUrl = normalizeBackendUrl(backendUrlInput.value);
    const originPermission = `${baseUrl}/*`;
    if (baseUrl !== DEFAULT_BACKEND_URL) {
      const granted = await chrome.permissions.request({ origins: [originPermission] });
      if (!granted) throw new Error("Permissão para acessar esse servidor não foi concedida");
    }
    const previous = await getBackendBaseUrl();
    await chrome.storage.local.set({ backendBaseUrl: baseUrl });
    backendUrlInput.value = baseUrl;
    updateBackendLinks(baseUrl);
    if (previous !== baseUrl) {
      await chrome.storage.local.remove(["apiToken", "lastDelivery"]);
      setConnectedUi(false);
      showStatus("Servidor salvo. Faça um novo pareamento para este endereço.");
    } else {
      showStatus("Endereço do servidor confirmado.");
    }
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    saveBackendButton.disabled = false;
  }
});

configUnitInput.addEventListener("change", async () => {
  const unit = normalizedUnit(configUnitInput.value);
  if (!unit) {
    configUnitInput.value = "UNI001";
    showStatus("Informe uma unidade válida, como UNI001.", true);
    return;
  }
  configUnitInput.value = unit;
  await chrome.storage.local.set({ configuredUnit: unit });
  showStatus(`Unidade ${unit} salva. Os próximos atestados usarão essa identificação.`);
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.lastDelivery?.newValue) renderLastDelivery(changes.lastDelivery.newValue);
  if (changes.activeTask || changes.monitoringEnabled || changes.monitoringAllEnabled) {
    chrome.storage.local.get(["activeTask", "monitoringEnabled", "monitoringAllEnabled"]).then(applyTaskState);
  }
});

// Pareamento
pairingCodeInput.addEventListener("input", () => {
  pairingCodeInput.value = pairingCodeInput.value.replace(/\D/g, "").slice(0, 6);
});

pairExtensionButton.addEventListener("click", async () => {
  const localState = await chrome.storage.local.get(["pairingFailures", "pairingBlockedUntil"]);

  if (localState.pairingBlockedUntil > Date.now()) {
    const minutes = Math.ceil((localState.pairingBlockedUntil - Date.now()) / 60000);
    showStatus(`Pareamento bloqueado. Aguarde ${minutes} minuto(s).`, true);
    return;
  }

  const codigo = pairingCodeInput.value.trim();
  if (!/^\d{6}$/.test(codigo)) {
    showStatus("Informe um código de 6 dígitos.", true);
    return;
  }

  pairExtensionButton.disabled = true;
  showStatus("Conectando a extensão...");

  try {
    const response = await fetch(await backendUrl("/api/parear"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        codigo,
        nome: `Chrome ${chrome.runtime.getManifest().version}`,
      }),
    });

    const result = await response.json();
    if (!response.ok || !result.token) {
      const detail = result.detail;
      const message = typeof detail === "object" ? detail.mensagem : detail;
      const failures = Number(localState.pairingFailures || 0) + 1;
      const serverBlocked = response.status === 429;

      if (failures >= 3 || serverBlocked) {
        const waitMs = serverBlocked && detail?.aguarde_segundos
          ? Number(detail.aguarde_segundos) * 1000
          : 30 * 60 * 1000;
        await chrome.storage.local.set({
          pairingFailures: 3,
          pairingBlockedUntil: Date.now() + waitMs,
        });
        pairingCodeInput.disabled = true;
        pairExtensionButton.disabled = true;
      } else {
        await chrome.storage.local.set({ pairingFailures: failures });
      }

      throw new Error(message || "Não foi possível conectar");
    }

    await chrome.storage.local.set({
      apiToken: result.token,
      pairingFailures: 0,
    });
    await chrome.storage.local.remove("pairingBlockedUntil");
    pairingCodeInput.value = "";
    setConnectedUi(true);
    showStatus("Extensão conectada com sucesso.");
  } catch (error) {
    showStatus(
      `${error.message}. Confirme se o servidor está ativo e gere um novo código.`,
      true
    );
  } finally {
    const state = await chrome.storage.local.get("pairingBlockedUntil");
    pairExtensionButton.disabled = state.pairingBlockedUntil > Date.now();
  }
});

// Monitoramento
monitorInput.addEventListener("change", async () => {
  try {
    const tab = await getWhatsAppTab();
    const enabled = monitorInput.checked;

    if (enabled) {
      monitorAllInput.checked = false;
    }

    await chrome.storage.local.set({
      monitoringEnabled: enabled,
      monitoringAllEnabled: false,
    });
    await sendToWhatsApp(tab, {
      type: enabled ? "START_MONITORING" : "STOP_MONITORING",
    });

    showStatus(
      enabled
        ? "Monitorando conversa aberta..."
        : "Monitoramento pausado"
    );
  } catch (error) {
    monitorInput.checked = false;
    await chrome.storage.local.set({ monitoringEnabled: false });
    showStatus(error.message, true);
  }

  applyTaskState({ monitoringEnabled: monitorInput.checked, monitoringAllEnabled: monitorAllInput.checked });
});

monitorAllInput.addEventListener("change", async () => {
  try {
    const tab = await getWhatsAppTab();
    const enabled = monitorAllInput.checked;

    if (enabled) {
      monitorInput.checked = false;
    }

    await chrome.storage.local.set({
      monitoringAllEnabled: enabled,
      monitoringEnabled: false,
    });
    await sendToWhatsApp(tab, {
      type: enabled ? "START_ALL_CHATS" : "STOP_ALL_CHATS",
    });

    showStatus(
      enabled
        ? "Monitorando conversas não lidas..."
        : "Monitoramento pausado"
    );
  } catch (error) {
    monitorAllInput.checked = false;
    await chrome.storage.local.set({ monitoringAllEnabled: false });
    showStatus(error.message, true);
  }

  applyTaskState({ monitoringEnabled: monitorInput.checked, monitoringAllEnabled: monitorAllInput.checked });
});

stopTaskButton.addEventListener("click", async () => {
  try {
    const tab = await getWhatsAppTab();
    await chrome.storage.local.set({
      monitoringEnabled: false,
      monitoringAllEnabled: false,
    });
    monitorInput.checked = false;
    monitorAllInput.checked = false;
    await sendToWhatsApp(tab, { type: "STOP_ALL_TASKS" });
    showStatus("Tarefa pausada");
  } catch (error) {
    showStatus(error.message, true);
  }

  stopTaskButton.style.display = "none";
});

// Processar último anexo
processLatestButton.addEventListener("click", async () => {
  processLatestButton.disabled = true;
  showStatus("Localizando último anexo...");

  try {
    const tab = await getWhatsAppTab();
    const result = await sendToWhatsApp(tab, { type: "PROCESS_LATEST_ATTACHMENT" });

    if (!result?.ok) {
      throw new Error(
        result?.error || "Nenhum anexo compatível encontrado na conversa."
      );
    }

    showStatus("Anexo localizado e enviado para processamento.");
  } catch (error) {
    showStatus(`${error.message}. Atualize o WhatsApp Web e tente novamente.`, true);
  } finally {
    processLatestButton.disabled = false;
  }
});

function readManualFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const encoded = String(reader.result || "").split(",", 2)[1];
      if (encoded) resolve(encoded);
      else reject(new Error("Não foi possível ler o documento selecionado."));
    };
    reader.onerror = () => reject(new Error("Não foi possível ler o documento selecionado."));
    reader.readAsDataURL(file);
  });
}

manualExtractButton.addEventListener("click", async () => {
  const file = manualFileInput.files[0];
  if (!file) {
    showStatus("Selecione um PDF, JPG ou PNG para a extração manual.", true);
    return;
  }
  manualExtractButton.disabled = true;
  showStatus("Enviando documento para extração real com o Gemini...");
  try {
    const { apiToken = "", activeTask = null } = await chrome.storage.local.get(["apiToken", "activeTask"]);
    if (!apiToken) throw new Error("Extensão desconectada. Faça o pareamento primeiro.");
    if (activeTask) throw new Error("Já existe outra tarefa em andamento.");
    const manualId = `manual-${Date.now()}-${crypto.randomUUID()}`;
    const result = await chrome.runtime.sendMessage({
      type: "UPLOAD_ATTACHMENT",
      payload: {
        arquivo: await readManualFileAsBase64(file),
        nome_arquivo: file.name,
        tipo_arquivo: file.type,
        id_mensagem: manualId,
        id_conversa: "extracao-manual-extensao",
        whatsapp_remetente: null,
        data_recebimento: new Date().toISOString(),
        unidade: normalizedUnit(configUnitInput.value) || "UNI001",
        key: manualId,
      },
    });
    if (!result?.ok) throw new Error(result?.error || "A extração não foi concluída.");
    if (result.status === "ignorado") {
      showStatus(`Documento não aceito: ${result.motivo || "tipo não reconhecido"}.`, true);
    } else {
      const identifier = result.id_documento ? ` ID: ${result.id_documento}.` : "";
      showStatus(`Extração concluída. Documento #${result.id} enviado ao painel oficial.${identifier}`);
      manualFileInput.value = "";
    }
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    manualExtractButton.disabled = false;
  }
});
