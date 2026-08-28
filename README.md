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
PROCESSOR_CONTRACT_APPROVED=false
PROCESSOR_REGION=configure_a_regiao_aprovada
GEMINI_TIMEOUT_SECONDS=60
GEMINI_MAX_ATTEMPTS=2
GEMINI_MAX_OUTPUT_TOKENS=1024
GEMINI_MAX_DOCUMENT_MB=8
GEMINI_DAILY_REQUEST_LIMIT=50
GEMINI_DAILY_OUTPUT_TOKEN_BUDGET=50000
```

O envio de documentos ao Gemini permanece bloqueado enquanto
`PROCESSOR_CONTRACT_APPROVED` nao for alterado para `true` por uma pessoa
autorizada e `PROCESSOR_REGION` nao declarar a regiao aprovada. Essa declaracao
de configuracao nao substitui a verificacao contratual nem, por si so, altera o
endpoint ou garante residencia regional no provedor.

O orcamento local do Gemini e conservador: cada tentativa reserva o teto de
tokens de saida antes da chamada, mesmo quando a resposta real for menor. Isso
evita que retries ou varias extensoes ultrapassem silenciosamente o limite
diario. Ajuste os valores somente depois de conferir a cota da conta usada.

Nao use aspas nem espacos ao redor do sinal de igual. Depois execute:

```powershell
.\iniciar.ps1
```

Se o PowerShell bloquear o script, execute diretamente:

```powershell
.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload --no-proxy-headers
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

O comando gera a chave interna e solicita os dados do administrador. O acesso
ao painel utiliza usuário e senha; a senha é armazenada somente como hash.

Para conectar a extensao, entre no painel como administrador, abra
**Conectar extensao**, gere o codigo temporario de 6 digitos e informe-o na
extensao. O codigo expira em 10 minutos e funciona uma unica vez. A credencial
definitiva expira em 90 dias e fica armazenada no servidor somente como hash.

Consulte `SECURITY.md` antes de publicar com Cloudflare.

## Enriquecimento automatico

Configure uma unica vez os dois arquivos locais sem colocar caminhos no codigo:

```powershell
.venv\Scripts\python.exe scripts\configurar_pipeline.py --atestados "caminho-local.xlsx" --base-geral "caminho-local.xlsx"
```

Os caminhos ficam somente no `.env`, que nao entra no Git. Em cada recebimento,
o Gemini extrai os dados do documento e o sistema
localiza a pessoa na Base Geral por CPF + nome e acrescenta uma linha completa
na planilha de atestados configurada. A gravacao usa arquivo temporario e troca
atomica para reduzir risco de corrupcao. Mantenha a planilha fechada no Excel
durante o processamento; se estiver bloqueada, o item permanece na fila para
nova tentativa. Um identificador tecnico oculto impede linhas duplicadas. Nao
existe execucao manual no painel: esse cruzamento e parte obrigatoria do fluxo.

## Leitura de imagens de documentos

Para imagens JPG, PNG e WEBP, o sistema envia ao motor a foto original e uma
copia auxiliar em memoria com orientacao EXIF corrigida, contraste moderado e
nitidez leve. O arquivo original salvo nunca e modificado e continua sendo a
fonte do SHA-256. A extracao tambem identifica CRM/CRO, UF, assinatura e carimbo;
quando a qualidade nao permite certeza, o valor permanece nulo para revisao
humana. Para desativar somente a copia auxiliar, use:

```env
GEMINI_IMAGE_ENHANCEMENT=false
```

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
As decisoes, pendencias da secao 13 e etapas especificas do Databricks estao em
`docs\INTEGRACAO_DATABRICKS_V2.md`.

## Endereco do backend na extensao

A extensao usa `http://127.0.0.1:8000` por padrao para desenvolvimento local.
O campo **Endereco do backend** no popup permite apontar futuramente para um
servidor central. Enderecos remotos aceitam somente HTTPS e o Chrome solicita
permissao apenas para o host informado. Ao trocar o servidor, o token anterior e
removido e um novo pareamento passa a ser obrigatorio.

## Custos

SQLite e a geracao de XLSX sao locais e gratuitas. O projeto nao depende da
API do Google Sheets nem do Microsoft Graph. O unico servico externo do MVP e
a API Gemini ja disponibilizada pela empresa.

## Preparacao da entrega Databricks

A entrega segue desacoplada por `DeliveryService -> StorageClient`. O modo
padrao permanece desabilitado. O `DatabricksStorageClient` implementa a Files
API e OAuth M2M da especificacao v2, mas nenhuma chamada real ocorre enquanto
a ativacao dupla abaixo nao for configurada explicitamente.

Antes de qualquer escrita, a entrega valida integralmente o contrato v2: chaves,
tipos, datas com fuso, telefones E.164, CPF, CRM/UF, CID, caminhos, tamanho e
SHA-256. Qualquer divergencia interrompe a operacao antes de gravar o documento.

Para homologar localmente o fluxo completo com documentos exclusivamente
ficticios, configure no `.env`:

```env
DELIVERY_MODE=fake
DELIVERY_FAKE_ROOT=C:\caminho\seguro\para\homologacao
DELIVERY_UNIT=UNI001
DELIVERY_WHATSAPP_DESTINATION=+5511988887777
```

No modo `fake`, cada atestado reconhecido pelo fluxo normal gera o documento
original e o JSON contratual lado a lado no diretorio informado. O documento e
gravado e relido para validar o SHA-256 antes da criacao do JSON. Para desligar:

```env
DELIVERY_MODE=disabled
```

O modo real preparado usa o destino oficial
`/Volumes/renapsi_prd/bronze_atestados/atestado`, cria a pasta, grava o documento,
confirma a integridade e somente entao grava o JSON. As credenciais nunca ficam
no codigo. Quando o engenheiro fornecer valores ficticios/de homologacao, a
configuracao sera:

```env
DELIVERY_MODE=databricks
DATABRICKS_UPLOAD_ENABLED=true
DATABRICKS_HOST=https://dbc-32044e02-fb27.cloud.databricks.com
DATABRICKS_VOLUME_ROOT=/Volumes/renapsi_prd/bronze_atestados/atestado
DATABRICKS_CLIENT_ID=configure_externamente
DATABRICKS_CLIENT_SECRET=configure_externamente
```

Definir apenas `DELIVERY_MODE=databricks` nao basta: sem
`DATABRICKS_UPLOAD_ENABLED=true`, o sistema falha fechado antes de abrir qualquer
conexao. Ate a homologacao controlada, mantenha `DELIVERY_MODE=fake` ou
`disabled` e `DATABRICKS_UPLOAD_ENABLED=false`.

Depois de receber as credenciais, a primeira verificacao pode ser somente
leitura (autentica e lista o Volume, sem gravar arquivos):

```powershell
.venv\Scripts\python.exe scripts\homologar_databricks.py --check-access
```

O primeiro upload deve usar somente dados sinteticos e exige tanto a chave de
ambiente quanto a confirmacao literal do destino:

```powershell
.venv\Scripts\python.exe scripts\homologar_databricks.py --upload-fictitious --confirm-volume "/Volumes/renapsi_prd/bronze_atestados/atestado"
```

Esse comando nao deve ser executado antes de o engenheiro confirmar se o destino
e de homologacao ou producao.

Limitacao conhecida antes da ativacao real: o fluxo operacional atual bloqueia
somente a repeticao da mesma mensagem pelo `id_mensagem`. O mesmo binario
recebido em mensagens diferentes e preservado como eventos historicos distintos,
conforme o contrato Bronze. Uploads manuais sem identificador tambem sao tratados
como novas ocorrencias.

Para executar uma homologacao isolada, somente com dados ficticios:

```powershell
.venv\Scripts\python.exe scripts\homologar_entrega_fake.py
```

O comando grava um PDF sintetico e seu JSON em `data\homologacao_fake`, confirma
o pareamento dos nomes e recalcula o SHA-256. Nenhum acesso de rede e realizado.
