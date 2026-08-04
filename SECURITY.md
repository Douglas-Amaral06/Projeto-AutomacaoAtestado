# Segurança e publicação

## Controles implementados

- Senhas com Argon2id, salt individual e sem armazenamento reversível.
- 2FA TOTP obrigatório para as contas.
- Sessões aleatórias armazenadas no banco somente pelo hash SHA-256.
- Cookies `HttpOnly`, `SameSite=Strict` e `Secure` quando HTTPS estiver ativo.
- CSRF nas alterações do painel e queries parametrizadas contra SQL injection.
- Bloqueio após cinco falhas de login em quinze minutos por usuário/IP.
- Token revogável, guardado somente como hash, para a extensão.
- CSP, proteção contra iframe, MIME sniffing e vazamento por Referer.
- Logs autenticados e CPF mascarado em mensagens de auditoria.
- Exportacoes XLSX geradas somente em memoria, sem copia persistente no projeto.
- `.env`, banco, anexos, backups, QR Codes e exportacoes excluidos do Git.

Use criptografia de disco corporativa (por exemplo, BitLocker) no computador que
armazena banco, anexos e backups. O `.env` e a chave do 2FA devem permanecer
acessiveis somente ao usuario de servico autorizado.

## Cloudflare

A ativação exige domínio, conta e credenciais da empresa. Crie um Cloudflare
Tunnel apontando para `127.0.0.1:8000`, use `cloudflare/config.yml.example` e
proteja o hostname com Cloudflare Access. Depois configure:

```env
COOKIE_SECURE=true
TRUST_CLOUDFLARE=true
ALLOWED_HOSTS=atestados.suaempresa.com.br
```

Não publique a porta 8000 diretamente. Restrinja o firewall para que somente o
`cloudflared` alcance a origem. No Access, exija identidade corporativa e MFA.
