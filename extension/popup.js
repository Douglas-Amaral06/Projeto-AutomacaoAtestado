const fileInput = document.getElementById("file");
const sendButton = document.getElementById("send");
const statusBox = document.getElementById("status");
const monitorInput = document.getElementById("monitor");
const processLatestButton = document.getElementById("processLatest");

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

chrome.storage.local.get(["monitoringEnabled", "lastStatus"], (state) => {
  monitorInput.checked = Boolean(state.monitoringEnabled);
  if (state.lastStatus?.message) showStatus(state.lastStatus.message, state.lastStatus.error);
});

monitorInput.addEventListener("change", async () => {
  try {
    const tab = await getWhatsAppTab();
    const enabled = monitorInput.checked;
    await chrome.storage.local.set({ monitoringEnabled: enabled });
    await chrome.tabs.sendMessage(tab.id, { type: enabled ? "START_MONITORING" : "STOP_MONITORING" });
    showStatus(enabled ? "Monitoramento ativo nesta conversa." : "Monitoramento pausado.");
  } catch (error) {
    monitorInput.checked = false;
    await chrome.storage.local.set({ monitoringEnabled: false });
    showStatus(error.message, true);
  }
});

processLatestButton.addEventListener("click", async () => {
  processLatestButton.disabled = true;
  showStatus("Localizando o ultimo anexo recebido...");
  try {
    const tab = await getWhatsAppTab();
    const result = await chrome.tabs.sendMessage(tab.id, { type: "PROCESS_LATEST_ATTACHMENT" });
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
    const response = await fetch("http://127.0.0.1:8000/api/atestados", {
      method: "POST",
      body: formData
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Falha no processamento");
    showStatus(`Atestado #${result.id} recebido. Abra o painel para conferir.`);
  } catch (error) {
    showStatus(`Erro: ${error.message}. Verifique se o servidor local esta ativo.`, true);
  } finally {
    sendButton.disabled = false;
  }
});
