const API_URL = "http://127.0.0.1:8000/api/atestados";

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

async function uploadAttachment(payload) {
  const formData = new FormData();
  formData.append("file", base64ToBlob(payload.base64, payload.mimeType), payload.filename);
  const response = await fetch(API_URL, { method: "POST", body: formData });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "Falha ao processar o atestado.");
  await saveStatus(`Atestado #${result.id} recebido automaticamente.`);
  return result;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "UPLOAD_ATTACHMENT") {
    uploadAttachment(message.payload)
      .then((result) => sendResponse({ ok: true, id: result.id }))
      .catch(async (error) => {
        await saveStatus(error.message, true);
        sendResponse({ ok: false, error: error.message });
      });
    return true;
  }
  if (message.type === "MONITOR_ERROR") saveStatus(message.message, true);
  if (message.type === "MONITOR_STATUS") saveStatus(message.message, false);
  return false;
});
