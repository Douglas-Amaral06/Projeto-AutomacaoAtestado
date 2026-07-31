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

## Teste do monitoramento automatico

1. Inicie o servidor com `.\iniciar.ps1`.
2. Em `chrome://extensions`, clique em **Atualizar** no cartao da extensao.
3. Atualize a pagina do WhatsApp Web.
4. Abra a conversa que sera monitorada.
5. Abra a extensao e ative **Monitoramento automatico**.
6. Aguarde aparecer `Monitorando: nome da conversa`.
7. Envie de outro telefone uma nova imagem de atestado para essa conversa.
8. Aguarde o selo `OK` no icone da extensao e confira o registro no painel.

Se o anexo chegou antes de o monitoramento ser ativado, clique em **Processar
ultimo anexo da conversa**. O botao **Enviar arquivo selecionado** continua
sendo exclusivo para arquivos baixados manualmente.

O monitoramento atual cobre novas imagens e PDFs expostos pelo WhatsApp Web na
conversa aberta. Anexos antigos sao ignorados. Ao trocar de conversa, o
monitoramento pausa e precisa ser ativado novamente. O envio manual permanece
disponivel como contingencia.

## Custos

SQLite e a geracao de XLSX sao locais e gratuitas. O projeto nao depende da
API do Google Sheets nem do Microsoft Graph. O unico servico externo do MVP e
a API Gemini ja disponibilizada pela empresa.
