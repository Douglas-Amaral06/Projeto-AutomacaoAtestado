# Respostas da seção 13 — contrato de atestados

Estas respostas consolidam as definições levantadas com a operação para o
piloto de São Paulo.

## 1. Lista de valores de `tipo_documento`

No escopo atual são recebidos somente:

- `Atestado`
- `Comprovante de horas`

Não são processados outros tipos de documento neste momento.

## 2. Significado do campo `Data`

Os dados são armazenados separadamente:

- `documento.data_emissao`: dia em que o documento foi emitido, correspondente
  ao dia do atendimento médico;
- `documento.dias_afastamento`: quantidade de dias de afastamento indicada no
  documento.

Caso futuramente seja necessário representar uma data de início do afastamento
diferente da data de emissão, será preciso acrescentar esse campo em uma nova
versão do contrato.

## 3. Identificador da mensagem

A extensão captura o identificador disponibilizado pelo WhatsApp Web no
atributo `data-id` e o envia em `origem.id_mensagem`. Quando o identificador não
estiver disponível, será enviado `null`.

Como o WhatsApp Web não oferece um contrato público e estável para seu DOM, a
captura desse identificador deverá ser acompanhada durante o piloto.

## 4. Códigos das unidades

A extensão permite configurar a unidade. `UNI001` é utilizado somente nos
testes atuais. Ainda falta confirmar o código oficial da unidade de São Paulo e
a fonte oficial da lista de unidades para a futura expansão nacional.

Atualmente existe um único número corporativo receptor na unidade de São Paulo.
Para a expansão nacional será mantida uma relação oficial entre cada unidade e
seu número corporativo receptor. O número de São Paulo no formato E.164 ainda
precisa ser confirmado.

## 5. CRM e UF

Sim. A extração devolve `documento.crm` e `documento.crm_uf` separadamente. Se
algum valor não puder ser lido com segurança, o campo será enviado como `null` e
seu nome será registrado em `extracao.campos_ausentes`.

## 6. Volume estimado

Para São Paulo:

- média de 120 documentos por mês;
- maior pico informado de 192 documentos em um mês;
- maior pico informado de 43 documentos em um dia;
- arquivos entre aproximadamente 900 KB e 6–7 MB, dependendo principalmente da
  qualidade e resolução da foto.

O backend aceita documentos de até 15 MB. O limite atual de entrada do Gemini é
8 MB por documento, cobrindo a faixa observada.

## 7. Ambiente de teste

Não existe número, unidade ou Volume separado para homologação. Foi autorizado
o uso do Volume específico já criado para a integração. Os testes devem usar
somente documentos e dados integralmente fictícios, claramente identificados,
com registro dos IDs gerados e limpeza controlada quando necessária.

## Responsabilidade operacional complementar

Os analistas da área de frequência que já recebem os atestados serão treinados
para utilizar a extensão. Cada analista utilizará seu próprio login, permitindo
identificar o responsável por cada operação e revisão.

## Pendências restantes

- confirmar o código oficial da unidade de São Paulo;
- confirmar o número corporativo de São Paulo no formato E.164.
