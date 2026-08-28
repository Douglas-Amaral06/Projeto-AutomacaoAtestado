# Segurança e publicação

## Controles implementados

- Senhas com Argon2id, salt individual e sem armazenamento reversível.
- Login do painel com usuário e senha, protegido por Argon2id e limitação de tentativas.
- Sessões aleatórias armazenadas no banco somente pelo hash SHA-256.
- Cookies `HttpOnly`, `SameSite=Strict` e `Secure` quando HTTPS estiver ativo.
- CSRF nas alterações do painel e queries parametrizadas contra SQL injection.
- Bloqueio combinado por usuário/IP e por IP global, impedindo contorno pela troca de nomes de usuário.
- Comparação de senha com custo constante mesmo quando o usuário não existe.
- Pareamento da extensão com código aleatório de seis dígitos, uso único e validade de dez minutos.
- Token revogável e com validade de 90 dias, guardado no servidor somente como hash.
- CSP, proteção contra iframe, MIME sniffing e vazamento por Referer.
- Logs autenticados com CPF, e-mail, tokens, senhas e chaves mascarados.
- Exportacoes XLSX geradas somente em memoria, sem copia persistente no projeto.
- Caminhos das fontes do pipeline somente no `.env`, sem nomes ou acessos expostos no codigo.
- Cruzamento executado localmente, com logs apenas agregados e sem dados pessoais.
- Escrita da planilha serializada, atomica e com identificador de idempotencia oculto.
- `.env`, banco, anexos, backups, QR Codes e exportacoes excluidos do Git.
- Disjuntor LGPD bloqueando chamadas ao Gemini sem aprovacao contratual explicita e regiao declarada.
- Orçamento local do Gemini com teto por resposta, documento, tentativas, chamadas e tokens de saída diários.

Use criptografia de disco corporativa (por exemplo, BitLocker) no computador que
armazena banco, anexos e backups. O `.env` e a chave interna devem permanecer
acessiveis somente ao usuario de servico autorizado.

## Cloudflare

A ativação exige domínio, conta e credenciais da empresa. Crie um Cloudflare
Tunnel apontando para `127.0.0.1:8000`, use `cloudflare/config.yml.example` e
proteja o hostname com Cloudflare Access. Depois configure:

```env
COOKIE_SECURE=true
TRUST_CLOUDFLARE=true
TRUSTED_PROXY_IPS=127.0.0.1,::1
ALLOWED_HOSTS=atestados.suaempresa.com.br
```

Não publique a porta 8000 diretamente. Restrinja o firewall para que somente o
`cloudflared` alcance a origem. No Access, exija identidade corporativa e MFA.
