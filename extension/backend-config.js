const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

function normalizeBackendUrl(value) {
  let parsed;
  try {
    parsed = new URL(String(value || "").trim());
  } catch (_error) {
    throw new Error("Informe uma URL válida para o servidor");
  }
  const localHttp = parsed.protocol === "http:" && parsed.hostname === "127.0.0.1";
  if (parsed.protocol !== "https:" && !localHttp) {
    throw new Error("Servidores remotos devem utilizar HTTPS");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash || (parsed.pathname && parsed.pathname !== "/")) {
    throw new Error("Informe somente o endereço principal do servidor, sem caminho ou credenciais");
  }
  return parsed.origin;
}

async function getBackendBaseUrl() {
  const state = await chrome.storage.local.get("backendBaseUrl");
  return normalizeBackendUrl(state.backendBaseUrl || DEFAULT_BACKEND_URL);
}

async function backendUrl(path) {
  if (!/^\/[a-z0-9/_-]*$/i.test(path)) throw new Error("Caminho interno do backend inválido");
  return `${await getBackendBaseUrl()}${path}`;
}

if (typeof module !== "undefined") {
  module.exports = { DEFAULT_BACKEND_URL, normalizeBackendUrl };
}
