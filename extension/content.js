let observer = null;
let monitoring = false;
let armed = false;
let conversationIdentity = null;
const seenMessages = new Set();
const pendingMessages = new Set();

function messageKey(container, media) {
  const dataId = container.getAttribute("data-id");
  if (dataId) return dataId;
  const metadata = container.querySelector("[data-pre-plain-text]")?.getAttribute("data-pre-plain-text");
  return metadata ? `${metadata}|${media.src || media.href}` : media.src || media.href;
}

function currentConversationIdentity() {
  const titles = [...document.querySelectorAll("#main header span[title], #main header [dir='auto']")]
    .map((item) => item.getAttribute("title") || item.textContent?.trim())
    .filter(Boolean);
  return titles[0] || null;
}

function isIncomingMessage(container) {
  if (container.closest(".message-out")) return false;
  return Boolean(container.closest(".message-in") || container.classList.contains("message-in"));
}

function findMessageContainers(root = document) {
  const containers = new Set();
  if (root.nodeType === Node.ELEMENT_NODE && root.matches?.(".message-in, [data-testid='msg-container']")) {
    containers.add(root);
  }
  root.querySelectorAll?.(".message-in, [data-testid='msg-container']").forEach((item) => containers.add(item));
  return [...containers].filter(isIncomingMessage);
}

function mediaUrl(element) {
  return element.currentSrc || element.src || element.href || "";
}

function isConversationMedia(element) {
  if (element.closest("header, footer")) return false;
  const url = mediaUrl(element);
  if (!url || (!url.startsWith("blob:") && !url.startsWith("data:image/"))) return false;
  if (element.tagName === "IMG") {
    const rectangle = element.getBoundingClientRect();
    const width = element.naturalWidth || element.width || rectangle.width;
    const height = element.naturalHeight || element.height || rectangle.height;
    return width >= 120 && height >= 120 && rectangle.width >= 80 && rectangle.height >= 80;
  }
  return true;
}

function findSupportedMedia(container) {
  const candidates = [...container.querySelectorAll("img[src], a[href]")];
  return candidates.find((element) => {
    if (!isConversationMedia(element)) return false;
    if (element.tagName === "IMG") return true;
    const filename = element.getAttribute("download") || "";
    return /\.pdf$/i.test(filename) || filename === "";
  });
}

function findAllConversationMedia() {
  const main = document.querySelector("#main") || document.querySelector("main");
  if (!main) return [];
  return [...main.querySelectorAll("img[src], a[href]")].filter(isConversationMedia);
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

async function processContainer(container, force = false, selectedMedia = null) {
  const media = selectedMedia || findSupportedMedia(container);
  if (!media) return;

  const key = messageKey(container, media);
  if (!armed && !force) {
    if (key) seenMessages.add(key);
    return;
  }
  if (!key || (!force && seenMessages.has(key)) || pendingMessages.has(key)) return;
  pendingMessages.add(key);

  try {
    const url = media.src || media.href;
    const response = await fetch(url);
    if (!response.ok) throw new Error("Nao foi possivel ler o anexo no WhatsApp.");
    const blob = await response.blob();
    const supportedType = ["image/jpeg", "image/png", "image/webp", "application/pdf"].includes(blob.type);
    if (!supportedType) return;
    if (blob.size > 15 * 1024 * 1024) throw new Error("Anexo acima do limite de 15 MB.");

    const filename = media.getAttribute("download")
      || `atestado-${Date.now()}.${blob.type === "application/pdf" ? "pdf" : blob.type.split("/")[1] || "jpg"}`;
    const bytes = new Uint8Array(await blob.arrayBuffer());

    chrome.runtime.sendMessage({
      type: "UPLOAD_ATTACHMENT",
      payload: { base64: bytesToBase64(bytes), mimeType: blob.type, filename, key }
    }, (result) => {
      pendingMessages.delete(key);
      if (chrome.runtime.lastError || !result?.ok) return;
      seenMessages.add(key);
    });
  } catch (error) {
    pendingMessages.delete(key);
    chrome.runtime.sendMessage({ type: "MONITOR_ERROR", message: error.message });
  }
}

function processLatestAttachment() {
  const containers = findMessageContainers();
  for (let index = containers.length - 1; index >= 0; index -= 1) {
    if (!findSupportedMedia(containers[index])) continue;
    processContainer(containers[index], true);
    return { ok: true };
  }

  // Fallback para versoes do WhatsApp que nao expoem mais .message-in.
  const mediaItems = findAllConversationMedia();
  if (mediaItems.length) {
    const media = mediaItems[mediaItems.length - 1];
    const container = media.closest("[data-id], [role='row']") || media.parentElement;
    processContainer(container, true, media);
    return { ok: true };
  }

  const main = document.querySelector("#main") || document.querySelector("main");
  const imageCount = main?.querySelectorAll("img[src]").length || 0;
  return {
    ok: false,
    error: `Nenhum anexo compativel encontrado. Diagnostico: ${containers.length} mensagens e ${imageCount} imagens visiveis.`
  };
}

function baselineVisibleMessages() {
  findMessageContainers().forEach((container) => {
    const media = findSupportedMedia(container);
    if (media) seenMessages.add(messageKey(container, media));
  });
}

function startMonitoring() {
  if (monitoring) return;
  monitoring = true;
  armed = false;
  conversationIdentity = currentConversationIdentity();
  if (!conversationIdentity) {
    monitoring = false;
    chrome.runtime.sendMessage({ type: "MONITOR_ERROR", message: "Abra uma conversa antes de ativar o monitoramento." });
    return;
  }
  baselineVisibleMessages();
  observer = new MutationObserver((mutations) => {
    if (!monitoring) return;
    if (currentConversationIdentity() !== conversationIdentity) {
      stopMonitoring();
      chrome.storage.local.set({ monitoringEnabled: false });
      chrome.runtime.sendMessage({ type: "MONITOR_ERROR", message: "A conversa mudou. Ative o monitoramento novamente na conversa desejada." });
      return;
    }
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node.nodeType !== Node.ELEMENT_NODE) continue;
        findMessageContainers(node).forEach(processContainer);
        const parentMessage = node.closest?.(".message-in, [data-testid='msg-container']");
        if (parentMessage && isIncomingMessage(parentMessage)) processContainer(parentMessage);

        const mediaCandidates = [];
        if (node.matches?.("img[src], a[href]")) mediaCandidates.push(node);
        node.querySelectorAll?.("img[src], a[href]").forEach((item) => mediaCandidates.push(item));
        mediaCandidates.filter(isConversationMedia).forEach((media) => {
          if (media.closest(".message-out")) return;
          const container = media.closest(".message-in, [data-id], [role='row']") || media.parentElement;
          processContainer(container, false, media);
        });
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
  setTimeout(() => {
    if (!monitoring) return;
    baselineVisibleMessages();
    armed = true;
    chrome.runtime.sendMessage({ type: "MONITOR_STATUS", message: `Monitorando: ${conversationIdentity}` });
  }, 1500);
}

function stopMonitoring() {
  monitoring = false;
  armed = false;
  observer?.disconnect();
  observer = null;
  chrome.runtime.sendMessage({ type: "MONITOR_STATUS", message: "Monitoramento pausado." });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "START_MONITORING") startMonitoring();
  if (message.type === "STOP_MONITORING") stopMonitoring();
  if (message.type === "PROCESS_LATEST_ATTACHMENT") {
    sendResponse(processLatestAttachment());
    return;
  }
  sendResponse({ ok: true });
});

chrome.storage.local.get("monitoringEnabled", ({ monitoringEnabled }) => {
  if (monitoringEnabled) startMonitoring();
});
