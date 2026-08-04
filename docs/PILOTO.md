# Roteiro do piloto interno

Use somente documentos ficticios durante a homologacao. Execute os cenarios
abaixo com 1 ou 2 analistas e registre qualquer falha na pagina **Logs**.

## Antes do teste

1. Execute `instalar.ps1` e `scripts\configurar_seguranca.ps1`.
2. Configure a chave Gemini no `.env`.
3. Inicie com `iniciar.ps1` e conecte a extensao por codigo temporario.
4. Execute `scripts\executar_testes.ps1`; todos os testes devem passar.
5. Execute `scripts\backup.ps1` e confirme a criacao do ZIP em `backups`.

## Cenarios obrigatorios

- Imagem de atestado valida: deve chegar como pendente.
- PDF de atestado valido: deve chegar como pendente.
- Receita ou foto comum: deve ser ignorada e registrada no log.
- Mesmo arquivo duas vezes: deve ser classificado como duplicado.
- Conversas nao lidas em diferentes posicoes: todas devem ser percorridas.
- Uma conversa sem anexo: deve ser ignorada e a fila deve continuar.
- Botao parar: deve interromper a tarefa.
- Limite 429 do Gemini: deve pausar e preservar o item na fila.
- Revisao: aprovar e rejeitar, confirmando o nome do analista no historico.
- Exportacao XLSX: conferir totais e campos aprovados.
- Fechar o popup: o monitor deve continuar com a aba do WhatsApp aberta.
- Revogar a extensao: novos uploads devem ser recusados ate novo pareamento.

## Criterio para aprovar o piloto

- Nenhum documento perdido.
- Nenhum arquivo nao-atestado registrado como aprovado sem revisao humana.
- Duplicidades identificadas.
- Backup criado e restaurado em uma copia de homologacao.
- Todos os erros possuem registro suficiente para diagnostico.

Nao restaure um backup sobre o ambiente principal durante o piloto. Primeiro
copie o projeto para homologacao e valide a restauracao nessa copia.
