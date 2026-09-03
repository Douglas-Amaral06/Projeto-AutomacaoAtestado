# Integração Databricks v2 — decisões e homologação

Este documento registra o estado verificável do projeto em relação à
especificação de entrega de atestados. Ele não substitui a especificação do
engenheiro; dúvidas contratuais permanecem abertas até aprovação formal.

## Destino e fluxo implementados

- Host informado: `https://dbc-32044e02-fb27.cloud.databricks.com`.
- Raiz informada: `/Volumes/renapsi_prd/bronze_atestados/atestado`.
- Identidade operacional: `origem.operador_id` usa um identificador opaco no
  formato `opr_<uuid>`, resolvido no backend a partir do token de pareamento.
  Reparear ou trocar de máquina não altera esse valor. Contas desligadas são
  desativadas, não removidas, e o identificador nunca é reaproveitado.
- Diretório de tradução: a relação entre `operador_id` e a pessoa permanece na
  tabela `usuarios` do banco da aplicação. O dono funcional deve ser a área de
  Frequência/RH; a custódia técnica deve ser atribuída nominalmente à equipe que
  administra o backend. Enquanto não houver operador pareado, o JSON envia null.
- Sequência: criar diretório, gravar documento, confirmar conteúdo e somente
  depois gravar o JSON.
- Produção automatizada: OAuth M2M por uma Service Principal da aplicação.
- A extensão nunca recebe credenciais ou tokens do Databricks.
- O modo real exige `DELIVERY_MODE=databricks` e
  `DATABRICKS_UPLOAD_ENABLED=true`; o padrão permanece bloqueado.

## Pendências da seção 13

| Item | Estado atual | Responsável pela confirmação |
|---|---|---|
| Tipos de documento | Confirmado para o escopo atual: somente `Atestado` e `Comprovante de horas`. | Concluído para o piloto de São Paulo |
| Significado de `Data` | Confirmado: armazenar separadamente a data de emissão/atendimento (`documento.data_emissao`) e a quantidade de dias de afastamento (`documento.dias_afastamento`). Se no futuro o início do afastamento for diferente da emissão, será necessária decisão sobre um campo adicional. | Concluído para o contrato atual; exceção futura pendente |
| ID da mensagem | A extensão usa o `data-id` exposto pelo WhatsApp Web. Quando ausente, envia `null`. O seletor depende do DOM não oficial do WhatsApp Web. | Engenharia de dados deve aceitar `null`; produto deve validar estabilidade em piloto |
| Códigos das unidades | A unidade é configurável na extensão. `UNI001` é apenas valor de teste. Ainda falta confirmar o código oficial de São Paulo; a lista nacional será necessária antes da expansão. A pergunta trata do identificador mestre da unidade, não do sistema que armazena atestados. | Operação/RH |
| CRM e UF | Implementado. `crm` e `crm_uf` são extraídos separadamente; falhas viram `null` e entram em `campos_ausentes`. | Concluído tecnicamente; validar qualidade no piloto |
| Volume estimado | São Paulo recebe em média 120 documentos por mês. O maior pico informado foi de 192 documentos no mês e 43 em um dia. Os arquivos observados variam aproximadamente de 900 KB a 6–7 MB. Os limites atuais de 15 MB no backend e 8 MB no Gemini cobrem a faixa informada. | Concluído para o piloto de São Paulo; demais unidades serão levantadas na expansão |
| Ambiente de teste | Confirmado que não existe número nem unidade de homologação. Qualquer teste no Volume de produção exige autorização explícita, documento integralmente fictício, identificação clara do teste e procedimento de limpeza previamente combinado. | Concluído como restrição operacional; autorização de cada teste cabe à engenharia de dados |

### Dependência adicional: números corporativos

`origem.whatsapp_destinatario` é obrigatório e deve estar em E.164. Hoje o
valor vem de `DELIVERY_WHATSAPP_DESTINATION`; ainda falta a relação oficial
unidade → número corporativo. A extensão não possui uma fonte confiável para
descobrir automaticamente remetente e destinatário no DOM do WhatsApp Web.

Para o piloto existe somente um número corporativo, na unidade de São Paulo.
Antes da expansão nacional, o projeto deve manter uma relação oficial entre
unidade e número receptor. A opção mais confiável é atribuir essa relação no
pareamento/configuração da extensão; detectar automaticamente pelo DOM do
WhatsApp Web só deve ser adotado se houver uma fonte estável e verificável.

### Validação humana no piloto

Os analistas da área de frequência que já recebem os atestados serão treinados
para utilizar a extensão e validar os resultados durante o piloto. A
responsabilidade será identificada pelo login individual da extensão: cada
analista utilizará sua própria conta, permitindo atribuir as operações ao
usuário responsável. O aceite operacional cabe ao analista autenticado da área
de frequência, conforme a definição interna da equipe.

## Decisão pendente: colisão do `id_documento`

A fórmula contratual é:

```text
<UNIDADE>_<AAAAMMDD>T<HHMMSS>_<sha8>
```

Dois recebimentos do mesmo binário, na mesma unidade e no mesmo segundo,
produzem o mesmo `id_documento`. Como `overwrite=true`, o segundo JSON pode
substituir o primeiro e alterar `origem.id_mensagem`, contrariando a intenção de
preservar os dois eventos históricos.

O projeto mantém a fórmula original até decisão do engenheiro. Alternativas a
serem aprovadas no contrato:

1. acrescentar microssegundos ao timestamp;
2. acrescentar um sufixo derivado de `id_mensagem`;
3. aceitar a sobrescrita como comportamento contratual documentado.

Não implementar uma alternativa unilateralmente: qualquer mudança altera a
chave da linha Bronze e a convenção de nomes.

### Pergunta pronta para a engenharia de dados

> Identificamos um caso-limite na fórmula atual do `id_documento`: dois
> recebimentos do mesmo binário, pela mesma unidade e no mesmo segundo, geram o
> mesmo ID. Como a Files API usa `overwrite=true`, o segundo JSON pode substituir
> o primeiro, embora os `id_mensagem` sejam diferentes. Devemos manter esse
> comportamento ou versionar o contrato para incluir microssegundos ou um sufixo
> derivado da mensagem? Até sua decisão, manteremos exatamente a fórmula da spec.

## Solicitação pronta para Operação/RH

> Para fechar as pendências restantes do piloto, precisamos confirmar somente:
> (1) qual é o código oficial da unidade de São Paulo e quem mantém a lista
> oficial para a futura expansão nacional; e (2) qual é o número corporativo de
> São Paulo no formato E.164. O volume informado é de 120 documentos por mês em
> média, com pico de 192 no mês e 43 no dia. A validação operacional será
> atribuída ao analista da área de frequência por seu login individual na
> extensão. Não existe ambiente de homologação.

## Roteiro de homologação por etapas

### Etapa 0 — validação local, sem rede

```powershell
.venv\Scripts\python.exe scripts\homologar_entrega_fake.py
.venv\Scripts\python.exe -m pytest -q
```

Critérios: PDF e JSON pareados, JSON UTF-8 sem BOM, SHA-256 e tamanho corretos,
documento escrito antes do JSON e todos os testes aprovados.

### Etapa 1 — acesso pessoal, somente leitura

Usar OAuth U2M do usuário corporativo. Confirmar que o workspace, catálogo,
schema e Volume podem ser visualizados. Não criar diretórios e não enviar
arquivos nesta etapa.

Critérios: identidade correta, workspace Sagres correto e listagem autorizada de
`/Volumes/renapsi_prd/bronze_atestados/atestado`.

**Evidência de 26/08/2026:** concluída pela interface corporativa. O usuário
autenticado visualizou o workspace Sagres, o catálogo `renapsi_prd`, o schema
`bronze_atestados` e o Volume `atestado`. A plataforma exibiu o caminho exato
`/Volumes/renapsi_prd/bronze_atestados/atestado`. Nenhum botão de upload,
criação de diretório ou alteração de permissão foi acionado. Essa evidência
comprova leitura pela identidade pessoal. A interface também apresentou
habilitados os controles **Upload to this volume** e **Create directory**, o que
indica permissão pessoal de escrita, mas nenhum deles foi acionado. A evidência
não comprova ainda o OAuth M2M da Service Principal.

### Etapa 2 — acesso da Service Principal, somente leitura

Configurar as credenciais por canal seguro. Primeiro validar o `.env` sem rede:

```powershell
.venv\Scripts\python.exe scripts\homologar_databricks.py --check-config
```

O resultado mostra host, Volume, ambiente inferido e presença da configuração,
mas nunca imprime `client_id` ou `client_secret`. Depois executar a leitura:

```powershell
.venv\Scripts\python.exe scripts\homologar_databricks.py --check-access
```

Critérios: OAuth M2M aprovado e listagem do Volume sem revelar o segredo nos
logs. `DATABRICKS_UPLOAD_ENABLED` pode permanecer `false`.

### Etapa 3 — única gravação fictícia controlada

Pré-requisitos: autorização explícita do engenheiro, destino confirmado como
homologação ou pasta de teste autorizada, unidade fictícia aprovada e limpeza
combinada previamente.

```powershell
.venv\Scripts\python.exe scripts\homologar_databricks.py `
  --upload-fictitious `
  --confirm-volume "/Volumes/renapsi_prd/bronze_atestados/atestado"
```

Critérios: diretório criado, PDF completo, SHA-256 conferido, JSON ao lado com o
mesmo nome-base e nenhuma informação real.

Após registrar as evidências e somente quando a engenharia confirmar que a
eventual linha Bronze pode ser tratada separadamente, os dois arquivos podem ser
removidos com o caminho relativo e o ID exatos retornados pelo upload:

```powershell
.venv\Scripts\python.exe scripts\homologar_databricks.py `
  --cleanup-fictitious `
  --document-relative-path "UNI001/AAAA/MM/DD/ID_DO_TESTE.pdf" `
  --confirm-id "ID_DO_TESTE"
```

A limpeza lê e valida primeiro o JSON remoto. Ela só prossegue quando o ID, o
caminho e o motor `HOMOLOGACAO-CONTROLADA` coincidem; remove o JSON antes do PDF
e nunca apaga diretórios. A operação remove os arquivos do Volume, mas não
remove automaticamente uma linha que a Bronze já tenha ingerido.

**Evidência de 28/08/2026:** primeira entrega fictícia concluída com OAuth U2M
do usuário corporativo, usando a CLI oficial do Databricks e o destino
autorizado. O PDF foi enviado antes do JSON.

- Diretório: `/Volumes/renapsi_prd/bronze_atestados/atestado/UNI001/2026/08/28/`
- ID: `UNI001_20260828T091116_304b38fa`
- PDF: 630 bytes local e 630 bytes remoto.
- JSON: 1.373 bytes local e 1.373 bytes remoto.
- SHA-256 do PDF: `304b38fab6f8201b4ba4928b00250e5f250b3f5cb8bd05f76a70801e96608468`.
- Conteúdo: integralmente fictício, motor `HOMOLOGACAO-LOCAL`.
- Credenciais: token OAuth armazenado no cofre seguro do sistema operacional;
  nenhum token ou segredo foi colocado no projeto.

Os arquivos permanecem disponíveis para análise da engenharia de dados. Não
executar a limpeza antes de o engenheiro conferir o JSON e a eventual ingestão
na Bronze.

### Etapa 4 — ingestão Bronze

O engenheiro confirma que o JSON foi observado e virou exatamente uma linha na
Bronze. Conferir `id_documento`, caminho, tipos, nulos e ausência de campos não
previstos. Registrar o identificador fictício e a evidência do resultado.

### Etapa 5 — piloto da extensão

Somente após as etapas anteriores: apontar uma extensão para o backend de
homologação, parear um token revogável, selecionar uma unidade oficial de teste
e enviar um documento fictício. Confirmar rastreabilidade extensão → fila →
Volume → Bronze.

## Evidências obrigatórias

- Data, ambiente, host e Volume usados.
- Identidade utilizada (usuário ou nome da Service Principal, nunca o segredo).
- `id_documento` fictício.
- Status HTTP das operações, sem cabeçalhos de autenticação.
- SHA-256 e tamanho local/remoto.
- Confirmação da linha Bronze pelo engenheiro.
- Resultado da limpeza dos dados fictícios, quando autorizada.
