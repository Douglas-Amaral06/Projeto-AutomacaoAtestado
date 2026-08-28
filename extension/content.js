let observer = null;
let monitoring = false;
let armed = false;
let conversationIdentity = null;
let allChatsTimer = null;
let allChatsBusy = false;
let allChatsRunId = 0;
let lastReportedStatus = "";
const seenMessages = new Set();
const pendingMessages = new Set();
const unreadCounts = new WeakMap();
const chatsMarkedUnreadThisRun = new Set();
const CHAT_INSPECTION_DELAY_MS = 10000;

function ensureStatusPanel() {
  let panel = document.getElementById("atestados-monitor-status");
  if (panel) return panel;
  panel = document.createElement("div");
  panel.id = "atestados-monitor-status";
  Object.assign(panel.style, {
    position: "fixed", right: "18px", bottom: "18px", zIndex: "999999",
    maxWidth: "340px", padding: "11px 14px", borderRadius: "8px",
    background: "#075e54", color: "white", fontFamily: "Arial, sans-serif",
    fontSize: "13px", boxShadow: "0 3px 14px rgba(0,0,0,.28)", display: "none"
  });
  document.body.appendChild(panel);
  return panel;
}

function reportStatus(message, error = false, evento = "monitoramento", detalhes = null) {
  const panel = ensureStatusPanel();
  panel.textContent = message;
  panel.style.background = error ? "#b3261e" : "#075e54";
  panel.style.display = "block";
  if (lastReportedStatus === `${error}|${message}`) return;
  lastReportedStatus = `${error}|${message}`;
  chrome.runtime.sendMessage({ type: error ? "MONITOR_ERROR" : "MONITOR_STATUS", message, evento, detalhes });
}

function messageKey(container, media) {
  const dataId = container.getAttribute("data-id");
  if (dataId) return `${dataId}|${mediaUrl(media)}`;
  const metadata = container.querySelector("[data-pre-plain-text]")?.getAttribute("data-pre-plain-text");
  return metadata ? `${metadata}|${media.src || media.href}` : media.src || media.href;
}

function currentConversationIdentity() {
  const titles = [...document.querySelectorAll("#main header span[title], #main header [dir='auto']")]
    .map((item) => item.getAttribute("title") || item.textContent?.trim())
    .filter(Boolean);
  if (titles[0]) return titles[0];
  const headerText = document.querySelector("#main header")?.textContent?.replace(/\s+/g, " ").trim();
  return headerText || null;
}

function isIncomingMessage(container) {
  if (container.closest(".message-out")) return false;
  if (container.closest(".message-in") || container.classList.contains("message-in")) return true;
  // O WhatsApp altera essas classes com frequencia. Elementos sem direcao
  // conhecida dentro da area de mensagens sao tratados como recebidos.
  return Boolean(container.closest("#main, main"));
}

function findMessageContainers(root = document, includeOutgoing = false) {
  const containers = new Set();
  if (root.nodeType === Node.ELEMENT_NODE && root.matches?.(".message-in, .message-out, [data-testid='msg-container']")) {
    containers.add(root);
  }
  root.querySelectorAll?.(".message-in, .message-out, [data-testid='msg-container']").forEach((item) => containers.add(item));
  // Fallback para versoes que removeram message-in/msg-container.
  const mediaRoot = root === document ? (document.querySelector("#main") || document.querySelector("main")) : root;
  mediaRoot?.querySelectorAll?.("img[src], a[href]").forEach((media) => {
    if (!isConversationMedia(media)) return;
    const container = media.closest(".message-in, .message-out, [data-id], [role='row']");
    if (container) containers.add(container);
  });
  return includeOutgoing ? [...containers] : [...containers].filter(isIncomingMessage);
}

function mediaUrl(element) {
  return element.currentSrc || element.src || element.href || "";
}

function isConversationMedia(element) {
  if (element.closest("header, footer")) return false;
  const url = mediaUrl(element);
  if (!url || url.startsWith("data:image/svg")) return false;
  if (element.tagName === "IMG") {
    const rectangle = element.getBoundingClientRect();
    const width = element.naturalWidth || element.width || rectangle.width;
    const height = element.naturalHeight || element.height || rectangle.height;
    return width >= 120 && height >= 120 && rectangle.width >= 80 && rectangle.height >= 80
      && rectangle.bottom > 0 && rectangle.right > 0;
  }
  const label = `${element.getAttribute("download") || ""} ${element.getAttribute("aria-label") || ""}`;
  return url.startsWith("blob:") || /\.pdf(?:$|\?)/i.test(url) || /pdf|documento|document/i.test(label);
}

function findSupportedMediaItems(container) {
  const candidates = [...container.querySelectorAll("img[src], a[href]")];
  return candidates.filter((element) => {
    if (!isConversationMedia(element)) return false;
    if (element.tagName === "IMG") return true;
    const filename = element.getAttribute("download") || "";
    return /\.pdf$/i.test(filename) || filename === "";
  });
}

function findSupportedMedia(container) {
  return findSupportedMediaItems(container)[0];
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

function messageId(container) {
  return container.getAttribute("data-id")
    || container.closest?.("[data-id]")?.getAttribute("data-id")
    || null;
}

function buildAttachmentPayload({ bytes, blob, filename, container, key }) {
  // Campos de WhatsApp (remetente/destinatario) serao incluidos em uma etapa
  // futura, quando houver uma fonte confiavel para esses dados.
  return {
    arquivo: bytesToBase64(bytes),
    nome_arquivo: filename,
    tipo_arquivo: blob.type,
    id_mensagem: messageId(container),
    data_recebimento: new Date().toISOString(),
    key,
  };
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
    const url = mediaUrl(media);
    const response = await fetch(url);
    if (!response.ok) throw new Error("Nao foi possivel ler o anexo no WhatsApp.");
    const blob = await response.blob();
    const supportedType = ["image/jpeg", "image/png", "image/webp", "application/pdf"].includes(blob.type);
    if (!supportedType) {
      pendingMessages.delete(key);
      return;
    }
    if (blob.size > 15 * 1024 * 1024) throw new Error("Anexo acima do limite de 15 MB.");

    const filename = media.getAttribute("download")
      || `atestado-${Date.now()}.${blob.type === "application/pdf" ? "pdf" : blob.type.split("/")[1] || "jpg"}`;
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const payload = buildAttachmentPayload({ bytes, blob, filename, container, key });

    // Nao registra o conteudo do arquivo: apenas os metadados necessarios para
    // validar a estrutura de captura.
    console.info("[Atestado] Payload preparado:", {
      nome_arquivo: payload.nome_arquivo,
      tipo_arquivo: payload.tipo_arquivo,
      id_mensagem: payload.id_mensagem,
      data_recebimento: payload.data_recebimento,
    });

    const result = await chrome.runtime.sendMessage({
      type: "UPLOAD_ATTACHMENT",
      payload
    });
    if (!result?.ok) {
      if (result?.quotaExceeded) {
        stopMonitoring();
        stopAllChatsMonitoring();
        await chrome.storage.local.set({ monitoringEnabled: false, monitoringAllEnabled: false });
        const waitText = result.retryAfter ? ` Aguarde aproximadamente ${result.retryAfter} segundos.` : "";
        reportStatus(`Tarefa pausada: limite da API Gemini atingido.${waitText}`, true, "tarefa_pausada_quota", { aguarde_segundos: result.retryAfter });
        return result;
      }
      reportStatus(result?.error || "Falha ao enviar o anexo.", true, "upload_falhou");
      return result;
    }
    seenMessages.add(key);
    if (result.status === "ignorado") reportStatus(`Tarefa finalizada: arquivo ignorado (${result.motivo || "nao identificado como atestado"}).`, false, "arquivo_ignorado", { tipo_documento: result.tipo_documento });
    else if (result.status === "duplicado") reportStatus(`Tarefa finalizada: atestado #${result.id} ja havia sido processado.`, false, "arquivo_duplicado");
    else reportStatus(`Tarefa finalizada: atestado #${result.id} enviado para conferencia.`, false, "atestado_salvo");
    return result;
  } catch (error) {
    reportStatus(error.message, true, "processamento_falhou", { chave: key });
    return { ok: false, error: error.message };
  } finally {
    pendingMessages.delete(key);
  }
}

function processLatestAttachment() {
  if (!document.querySelector("#main") && !document.querySelector("main")) {
    return { ok: false, error: "Abra uma conversa para processar o ultimo anexo. Para buscar sozinho, use Monitorar conversas nao lidas." };
  }
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

function isSelfConversation(identity) {
  return /(^|\s)(voc[eê]|you)(\s|$)/i.test(identity || "");
}

async function processUnreadAttachments(unreadCount = 1, includeOutgoing = false) {
  const items = [];
  const mediaSeen = new Set();
  const containers = findMessageContainers(document, includeOutgoing);
  containers.slice(-Math.min(Math.max(unreadCount, 1), 20)).forEach((container) => {
    findSupportedMediaItems(container).forEach((media) => {
      if (!mediaSeen.has(media)) items.push({ container, media });
      mediaSeen.add(media);
    });
  });
  // Ultimo recurso: usa as midias grandes visiveis diretamente no painel do
  // chat. Isso cobre o DOM atual que nao expoe mais um container por mensagem.
  if (!items.length) {
    const visibleMedia = findAllConversationMedia().filter((media) => {
      return includeOutgoing || !media.closest(".message-out");
    });
    visibleMedia.slice(-Math.min(Math.max(unreadCount, 1), 5)).forEach((media) => {
      const container = media.closest(".message-in, .message-out, [data-id], [role='row']") || media.parentElement;
      if (container && !mediaSeen.has(media)) items.push({ container, media });
      mediaSeen.add(media);
    });
  }
  const results = [];
  for (const item of items) results.push(await processContainer(item.container, true, item.media));
  return {
    found: items.length,
    hasAttestation: results.some((result) => result?.ok && result.status !== "ignorado"),
    failed: results.some((result) => result && !result.ok),
  };
}

function attachmentDiagnostics(includeOutgoing = false) {
  const main = document.querySelector("#main") || document.querySelector("main");
  const images = [...(main?.querySelectorAll("img[src]") || [])];
  const media = findAllConversationMedia().filter((item) => includeOutgoing || !item.closest(".message-out"));
  const schemes = [...new Set(media.map((item) => {
    const url = mediaUrl(item);
    return url.includes(":") ? url.split(":", 1)[0] : "sem-esquema";
  }))];
  return { imagens_no_chat: images.length, midias_compativeis: media.length, containers: findMessageContainers(document, includeOutgoing).length, esquemas: schemes };
}

function baselineVisibleMessages() {
  findMessageContainers().forEach((container) => {
    const media = findSupportedMedia(container);
    if (media) seenMessages.add(messageKey(container, media));
  });
}

function startMonitoring() {
  if (monitoring) return;
  if (allChatsTimer) clearInterval(allChatsTimer);
  allChatsTimer = null;
  allChatsBusy = false;
  monitoring = true;
  armed = false;
  conversationIdentity = currentConversationIdentity();
  if (!conversationIdentity) {
    monitoring = false;
    reportStatus("Abra uma conversa antes de ativar o monitoramento.", true);
    return;
  }
  baselineVisibleMessages();
  observer = new MutationObserver((mutations) => {
    if (!monitoring) return;
    if (currentConversationIdentity() !== conversationIdentity) {
      stopMonitoring();
      chrome.storage.local.set({ monitoringEnabled: false });
      reportStatus("A conversa mudou. Ative o monitoramento novamente na conversa desejada.", true);
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
    reportStatus(`Monitorando: ${conversationIdentity}`);
  }, 1500);
}

function stopMonitoring() {
  monitoring = false;
  armed = false;
  observer?.disconnect();
  observer = null;
  reportStatus("Monitoramento pausado.");
}

function unreadChatRows() {
  const sidebar = document.querySelector("#pane-side");
  if (!sidebar) return [];
  const badges = sidebar.querySelectorAll(
    "[aria-label*='não lida' i], [aria-label*='nao lida' i], [aria-label*='unread' i], [data-testid='icon-unread-count'], [data-icon='unread-count'], [data-icon='status-unread']"
  );
  const rows = new Set();
  badges.forEach((badge) => {
    let row = badge.closest("[role='listitem'], [role='row']");
    if (!row) {
      let candidate = badge.parentElement;
      for (let level = 0; candidate && level < 8; level += 1, candidate = candidate.parentElement) {
        if (candidate.querySelector("span[title]") && candidate.getBoundingClientRect().height >= 45) {
          row = candidate;
          break;
        }
      }
    }
    if (row) {
      const description = `${badge.getAttribute("aria-label") || ""} ${badge.textContent || ""}`;
      const parsedCount = Number(description.match(/\d+/)?.[0] || 1);
      unreadCounts.set(row, Math.max(unreadCounts.get(row) || 1, parsedCount));
      rows.add(row);
    }
  });
  return [...rows];
}

function chatRowName(row) {
  return row.querySelector("span[title]")?.getAttribute("title")
    || row.querySelector("[dir='auto']")?.textContent?.trim()
    || row.getAttribute("aria-label")
    || "conversa sem nome";
}

function findChatRowByName(name) {
  const sidebar = document.querySelector("#pane-side");
  if (!sidebar) return null;
  const candidates = sidebar.querySelectorAll("[role='listitem'], [role='row'], [data-testid='cell-frame-container']");
  return [...candidates].find((candidate) => chatRowName(candidate) === name) || null;
}

async function clickChatRow(row, conversation) {
  row.scrollIntoView({ block: "center", inline: "nearest" });
  await new Promise((resolve) => setTimeout(resolve, 250));
  const rectangle = row.getBoundingClientRect();
  if (rectangle.width < 30 || rectangle.height < 30 || rectangle.bottom < 0 || rectangle.top > window.innerHeight) {
    return { ok: false, error: "A conversa foi encontrada, mas esta fora da area clicavel." };
  }
  const x = Math.round(rectangle.left + Math.min(rectangle.width * 0.55, rectangle.width - 20));
  const y = Math.round(rectangle.top + rectangle.height / 2);
  return chrome.runtime.sendMessage({ type: "TRUSTED_CLICK", x, y, conversation });
}

async function markChatAsUnread(conversation, runId) {
  if (!allChatsTimer || runId !== allChatsRunId) return false;
  const row = findChatRowByName(conversation);
  if (!row) return false;
  row.scrollIntoView({ block: "center", inline: "nearest" });
  const rectangle = row.getBoundingClientRect();
  const x = Math.round(rectangle.left + Math.min(rectangle.width * 0.55, rectangle.width - 20));
  const y = Math.round(rectangle.top + rectangle.height / 2);
  const opened = await chrome.runtime.sendMessage({ type: "TRUSTED_CONTEXT_CLICK", x, y, conversation });
  if (!opened?.ok) return false;
  await new Promise((resolve) => setTimeout(resolve, 300));
  const menuItem = [...document.querySelectorAll("[role='menuitem'], [data-testid*='menuitem']")].find((item) => {
    const text = `${item.textContent || ""} ${item.getAttribute("aria-label") || ""}`;
    return /marcar como n\u00e3o lida|mark as unread/i.test(text);
  });
  if (!menuItem) return false;
  const menuRectangle = menuItem.getBoundingClientRect();
  const clicked = await chrome.runtime.sendMessage({
    type: "TRUSTED_CLICK",
    x: Math.round(menuRectangle.left + menuRectangle.width / 2),
    y: Math.round(menuRectangle.top + menuRectangle.height / 2),
    conversation,
  });
  return Boolean(clicked?.ok);
}

function waitForConversation(conversation, previousIdentity, hadMain, timeout = 10000) {
  return new Promise((resolve) => {
    const startedAt = Date.now();
    const timer = setInterval(() => {
      const identity = currentConversationIdentity();
      const mainExists = Boolean(document.querySelector("#main"));
      const currentRow = findChatRowByName(conversation);
      const selected = currentRow?.getAttribute("aria-selected") === "true"
        || Boolean(currentRow?.querySelector("[aria-selected='true']"));
      if (mainExists && (selected || !hadMain || (identity && identity !== previousIdentity))) {
        clearInterval(timer);
        resolve(identity || conversation);
      } else if (Date.now() - startedAt >= timeout) {
        clearInterval(timer);
        resolve(null);
      }
    }, 250);
  });
}

async function waitForChatInspection(identity, runId) {
  reportStatus(`Conversa aberta. Aguardando 10 segundos para carregar fotos e documentos...`, false, "aguardando_anexos", { conversa: identity, aguarde_segundos: 10 });
  const steps = CHAT_INSPECTION_DELAY_MS / 500;
  for (let step = 0; step < steps; step += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    if (!allChatsTimer || runId !== allChatsRunId) return false;
    const current = currentConversationIdentity();
    if (current && identity && current !== identity) return false;
  }
  return true;
}

async function scanUnreadChats() {
  if (allChatsBusy) return;
  const queue = unreadChatRows().map((row) => ({
    name: chatRowName(row),
    unreadCount: unreadCounts.get(row) || 1,
  })).filter((item) => !chatsMarkedUnreadThisRun.has(item.name));
  if (!queue.length) {
    reportStatus("Verificacao finalizada: nenhuma conversa nao lida pendente.");
    return;
  }
  allChatsBusy = true;
  const runId = allChatsRunId;
  try {
    reportStatus(`Fila inicializada com ${queue.length} conversa(s) nao lida(s).`, false, "fila_iniciada", { quantidade: queue.length });
    for (let index = 0; index < queue.length; index += 1) {
      if (!allChatsTimer || runId !== allChatsRunId) break;
      const rowName = queue[index].name;
      const unreadCount = queue[index].unreadCount;
      const row = findChatRowByName(rowName);
      if (!row) {
        reportStatus(`A conversa ${rowName} mudou de posicao e nao foi reencontrada. Seguindo.`, true, "conversa_nao_reencontrada", { conversa: rowName });
        continue;
      }
      const previousIdentity = currentConversationIdentity();
      const hadMain = Boolean(document.querySelector("#main"));
      reportStatus(`Abrindo ${index + 1} de ${queue.length}: ${rowName}...`, false, "abrindo_conversa", { conversa: rowName, tentativa: index + 1 });
      const clickResult = await clickChatRow(row, rowName);
      if (!clickResult?.ok) {
        reportStatus(`Falha no clique de ${rowName}: ${clickResult?.error || "erro desconhecido"}. Seguindo.`, true, "clique_conversa_falhou", { conversa: rowName });
        continue;
      }
      const openedIdentity = await waitForConversation(rowName, previousIdentity, hadMain);
      if (!allChatsTimer || runId !== allChatsRunId) break;
      if (!openedIdentity) {
        reportStatus(`Falha ao abrir ${rowName}. Seguindo para a proxima.`, true, "conversa_nao_aberta", { conversa: rowName });
        continue;
      }
      reportStatus(`Conversa aberta: ${openedIdentity}. Preparando inspecao detalhada...`, false, "conversa_aberta", { conversa: openedIdentity });
      const ready = await waitForChatInspection(openedIdentity, runId);
      if (!allChatsTimer || runId !== allChatsRunId) break;
      if (!ready) {
        reportStatus(`A conversa mudou durante a espera. Seguindo para a proxima.`, true, "conversa_mudou_durante_inspecao", { conversa: openedIdentity });
        continue;
      }
      reportStatus(`Analisando fotos e documentos recentes em ${openedIdentity}...`, false, "analisando_anexos", { conversa: openedIdentity });
      const processed = await processUnreadAttachments(unreadCount, isSelfConversation(openedIdentity));
      if (!processed.found) {
        const diagnostico = attachmentDiagnostics(isSelfConversation(openedIdentity));
        const marked = await markChatAsUnread(rowName, runId);
        if (marked) chatsMarkedUnreadThisRun.add(rowName);
        reportStatus(`Nenhuma foto ou documento recente em ${openedIdentity}.${marked ? " Conversa marcada como nao lida." : ""} Seguindo para a proxima.`, !marked, marked ? "conversa_marcada_nao_lida" : "marcacao_nao_lida_falhou", { conversa: openedIdentity, diagnostico });
        continue;
      }
      if (!processed.hasAttestation && !processed.failed) {
        const marked = await markChatAsUnread(rowName, runId);
        if (marked) chatsMarkedUnreadThisRun.add(rowName);
        reportStatus(`${processed.found} anexo(s) verificado(s) em ${openedIdentity}; nenhum atestado identificado.${marked ? " Conversa marcada como nao lida." : ""}`, !marked, marked ? "conversa_marcada_nao_lida" : "marcacao_nao_lida_falhou", { conversa: openedIdentity, quantidade: processed.found });
        continue;
      }
      reportStatus(`${processed.found} anexo(s) nao lido(s) verificado(s) em ${openedIdentity}.`, false, "anexos_verificados", { conversa: openedIdentity, quantidade: processed.found, mensagens_nao_lidas: unreadCount });
    }
    if (allChatsTimer && runId === allChatsRunId) reportStatus("Fila finalizada. Aguardando novas mensagens nao lidas.", false, "fila_finalizada");
  } catch (error) {
    reportStatus(`${error.message} A fila continuara na proxima verificacao.`, true, "fila_erro");
  } finally {
    allChatsBusy = false;
  }
}

function startAllChatsMonitoring() {
  stopMonitoring();
  if (allChatsTimer) clearInterval(allChatsTimer);
  chatsMarkedUnreadThisRun.clear();
  allChatsRunId += 1;
  allChatsTimer = setInterval(scanUnreadChats, 8000);
  reportStatus("Tarefa inicializando: procurando conversas nao lidas...");
  scanUnreadChats();
}

function stopAllChatsMonitoring() {
  allChatsRunId += 1;
  if (allChatsTimer) clearInterval(allChatsTimer);
  allChatsTimer = null;
  allChatsBusy = false;
  chatsMarkedUnreadThisRun.clear();
  reportStatus("Monitoramento geral pausado.");
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "PING") {
    sendResponse({ ok: true });
    return;
  }
  if (message.type === "START_MONITORING") startMonitoring();
  if (message.type === "STOP_MONITORING") stopMonitoring();
  if (message.type === "PROCESS_LATEST_ATTACHMENT") {
    sendResponse(processLatestAttachment());
    return;
  }
  if (message.type === "START_ALL_CHATS") startAllChatsMonitoring();
  if (message.type === "STOP_ALL_CHATS") stopAllChatsMonitoring();
  if (message.type === "STOP_ALL_TASKS") {
    stopMonitoring();
    stopAllChatsMonitoring();
    chrome.storage.local.set({ monitoringEnabled: false, monitoringAllEnabled: false });
  }
  sendResponse({ ok: true });
});

chrome.storage.local.get(["monitoringEnabled", "monitoringAllEnabled"], ({ monitoringEnabled, monitoringAllEnabled }) => {
  if (monitoringAllEnabled) startAllChatsMonitoring();
  else if (monitoringEnabled) startMonitoring();
});
