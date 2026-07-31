# Recebimento de Atestados - MVP

MVP local para capturar manualmente um atestado aberto no WhatsApp Web, extrair
campos com a API Gemini, salvar em SQLite e permitir conferencia humana antes
da exportacao para XLSX.

## Fluxo

1. WhatsApp Business aberto e logado no navegador.
2. O analista abre a mensagem que contem o atestado.
3. A extensao e ativada e o arquivo e selecionado.
4. O servidor local recebe o arquivo.
5. O Gemini extrai os dados.
6. Os dados ficam armazenados no SQLite.
7. O analista revisa e confirma no painel local.

## Requisitos

- `uv` (gerenciador gratuito de Python; ja disponivel neste computador).
- Chave da API Gemini.
- Google Chrome ou Microsoft Edge.

## Instalacao do servidor

```powershell
uv venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env
```

Abra o arquivo `.env` e cole a chave depois do sinal de igual:

```env
GEMINI_API_KEY=sua_chave_aqui
GEMINI_MODEL=gemini-2.5-flash
```

Nao use aspas nem espacos ao redor do sinal de igual. Depois execute:

```powershell
.\iniciar.ps1
```

Se o PowerShell bloquear o script, execute diretamente:

```powershell
.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```

Abra `http://127.0.0.1:8000`.

## Instalacao da extensao

1. Acesse `chrome://extensions` ou `edge://extensions`.
2. Ative o modo de desenvolvedor.
3. Clique em **Carregar sem compactacao**.
4. Selecione a pasta `extension` deste projeto.
5. Abra a conversa no WhatsApp Web, clique na extensao e escolha o arquivo.

## Custos

SQLite e a geracao de XLSX sao locais e gratuitas. O projeto nao depende da
API do Google Sheets nem do Microsoft Graph. O unico servico externo do MVP e
a API Gemini ja disponibilizada pela empresa.
