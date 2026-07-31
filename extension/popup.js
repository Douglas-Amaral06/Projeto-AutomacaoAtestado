const fileInput = document.getElementById("file");
const sendButton = document.getElementById("send");
const statusBox = document.getElementById("status");

sendButton.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    statusBox.textContent = "Selecione um arquivo primeiro.";
    return;
  }

  sendButton.disabled = true;
  statusBox.textContent = "Enviando e extraindo dados...";
  const formData = new FormData();
  formData.append("file", file);

  try {
    const response = await fetch("http://127.0.0.1:8000/api/atestados", {
      method: "POST",
      body: formData
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Falha no processamento");
    statusBox.innerHTML = `Atestado #${result.id} recebido.<br><a href="http://127.0.0.1:8000" target="_blank">Abrir conferencia</a>`;
  } catch (error) {
    statusBox.textContent = `Erro: ${error.message}. Verifique se o servidor local esta ativo.`;
  } finally {
    sendButton.disabled = false;
  }
});

