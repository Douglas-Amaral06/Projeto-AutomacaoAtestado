# Recebimento de Atestados - MVP

MVP local para capturar manualmente um atestado aberto no WhatsApp Web, extrair
campos com a API Gemini, salvar em SQLite e permitir conferencia humana antes
da exportacao para XLSX.

## Estrutura do projeto

- `app`: servidor, seguranca, banco e interface web.
- `extension`: extensao do Chrome/Edge.
- `scripts`: administracao, backup, atualizacao e testes.
- `tests`: testes automatizados de seguranca e fluxo.
- `docs`: roteiro de homologacao.
- `data` e `backups`: dados locais ignorados pelo Git.

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

Instalacao automatizada recomendada:

```powershell
.\instalar.ps1
.\scripts\configurar_seguranca.ps1
```

Instalacao manual:

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

### Monitoramento de varias conversas

O modo **Monitorar conversas nao lidas** percorre as conversas com contador de
nao lidas, abre cada uma e verifica o ultimo anexo. Ele altera a conversa
visivel no WhatsApp Web enquanto trabalha, portanto deve ser usado quando o
analista nao estiver navegando manualmente. Arquivos repetidos sao detectados
pelo hash e imagens que nao forem atestados sao ignoradas pelo Gemini.

O andamento aparece em um painel no canto inferior direito do WhatsApp Web.
Fechar o popup da extensao nao encerra o monitoramento; a aba do WhatsApp deve
permanecer aberta. O navegador pode estar minimizado.

O botao **Parar tarefa** interrompe todos os modos. Os eventos ficam registrados
em `http://127.0.0.1:8000/logs`, incluindo tentativas de abertura, conversas sem
anexo, erros, arquivos ignorados, duplicidades e atestados salvos.

Para abrir as conversas automaticamente, a extensao usa temporariamente a
permissao de depuracao do Chrome para produzir um clique real. O Chrome pode
exibir um aviso de permissao ao atualizar a extensao. A conexao de depuracao e
encerrada imediatamente depois de cada clique.

Ao receber `429 RESOURCE_EXHAUSTED` do Gemini, a tarefa inteira e pausada e os
dois modos de monitoramento sao desligados. O painel e o log mostram o tempo de
espera informado pela API, quando disponivel. O analista deve reativar a tarefa
depois desse periodo.

## Primeira configuracao segura

Depois de instalar as dependencias, execute uma unica vez:

```powershell
.\scripts\configurar_seguranca.ps1
```

O comando gera a chave interna, solicita o administrador e exibe o endereco do
2FA. Guarde o QR Code/segredo em local seguro.

Para conectar a extensao, entre no painel como administrador, abra
**Conectar extensao**, gere o codigo temporario de 6 digitos e informe-o na
extensao. O codigo expira em 10 minutos e funciona uma unica vez. A credencial
definitiva e criada e armazenada internamente, sem ser exibida ou colada pelo
usuario.

Consulte `SECURITY.md` antes de publicar com Cloudflare.

## Backup, retencao e atualizacao

Enquanto o servidor estiver ativo, um backup verificado e criado a cada 24
horas em `backups`. Para criar um imediatamente, execute
`scripts\backup.ps1`. Para executar backup, retencao e limpeza de backups
antigos, use `scripts\manutencao.ps1`.

A exclusao por retencao vem desativada. Depois da aprovacao de RH, juridico e
LGPD, defina `RETENTION_ENABLED=true` no `.env` e ajuste
`DOCUMENT_RETENTION_DAYS`. Somente documentos ja confirmados ou rejeitados e
vencidos sao removidos; pendencias nao sao apagadas. Os backups permanecem pelo
prazo independente de `BACKUP_RETENTION_DAYS`.

Para atualizar dependencias e validar o projeto, execute
`scripts\atualizar.ps1`. Esse
script cria backup antes da atualizacao e roda toda a suite de testes. A
restauracao usa `scripts\restaurar_backup.ps1 -Arquivo caminho-do-zip` e exige o
servidor parado.

O roteiro completo de homologacao esta em `docs\PILOTO.md`.

## Custos

SQLite e a geracao de XLSX sao locais e gratuitas. O projeto nao depende da
API do Google Sheets nem do Microsoft Graph. O unico servico externo do MVP e
a API Gemini ja disponibilizada pela empresa.
