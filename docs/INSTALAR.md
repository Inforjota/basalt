# Instalar o Basalt

## 1. À mão (qualquer Linux com Python ≥ 3.11 e git)

```bash
git clone https://github.com/…/basalt && cd basalt
python3 -m basalt iniciar --dados ~/basalt-dados --email tu@exemplo.pt --nome "O teu nome"
BASALT_CONFIG=~/basalt-dados/basalt.toml python3 -m basalt serve
```

Abre http://127.0.0.1:8121. Sem `--email` fica em modo `aberto` (sem login) — só é aceite a escutar em `127.0.0.1`.

**Interface em inglês?** No `basalt.toml` (ou com `BASALT_IDIOMA`, que vence o ficheiro):

```toml
[basalt]
idioma = "en"      # "pt" (omissão) · "en" · "auto" (escolhe pelo Accept-Language do browser)
```

Depois de mudar o `idioma`, recarrega a página (a interface guarda-se no service worker, por isso o idioma novo só se vê depois de a recarregares). As mensagens de erro do servidor continuam em português.

Onde fica cada coisa (por utilizador, em `<dados>/<espaco>/`):

| pasta | o quê | podes mexer? |
|---|---|---|
| `memoria/` | **as tuas notas** — um cofre Obsidian com histórico git | sim: é teu; o Basalt detecta e sincroniza |
| `copia/` | a cópia editável que a interface usa | de preferência não (é sincronizada com `memoria/`) |
| `dados/` | índice, Entrada, ledger da sincronização, cópias de segurança do CAS, sessões | não |

## 2. Docker

```bash
cd docker && docker compose up -d        # http://127.0.0.1:8121; dados em docker/dados/
docker compose exec basalt python3 -m basalt utilizador novo tu@exemplo.pt --nome Tu
```

A imagem é `python:3.12-slim` + `git`; corre como utilizador sem privilégios; só `/dados` é escrito.

## 3. Serviço systemd

```bash
sudo useradd --system --create-home --home-dir /var/lib/basalt basalt
sudo cp -r basalt basalt.toml.exemplo /opt/basalt/ && sudo chown -R basalt:basalt /opt/basalt
sudo -u basalt python3 -m basalt iniciar --dados /var/lib/basalt --email tu@exemplo.pt
sudo cp sistema/basalt.service /etc/systemd/system/ && sudo systemctl enable --now basalt
sudo cp sistema/basalt-arrumar.* /etc/systemd/system/ && sudo systemctl enable --now basalt-arrumar.timer   # opcional
```

(`sistema/basalt.service` corre a partir de `/var/lib/basalt`; se puseres o código noutro sítio, ajusta `WorkingDirectory`
ou `PYTHONPATH`.)

## 4. Na rede: proxy com HTTPS

O Basalt escuta em `127.0.0.1` e **não** faz TLS. À frente, um proxy:

```nginx
server {
    listen 443 ssl http2;
    server_name basalt.exemplo.pt;
    # ssl_certificate … (certbot / Caddy tratam disto sozinhos)
    client_max_body_size 50m;
    location / {
        proxy_pass http://127.0.0.1:8121;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Basalt-Email "";      # NUNCA deixar o visitante mandar este cabeçalho (modo cabecalho)
    }
}
```

Caddy: `basalt.exemplo.pt { reverse_proxy 127.0.0.1:8121 }` e pronto.

**Já tens login (SSO) no proxy?** Põe `[auth] modo = "cabecalho"` e faz o proxy **substituir** `X-Basalt-Email` pelo e-mail
autenticado (p. ex. `proxy_set_header X-Basalt-Email $upstream_http_x_auth_request_email;` com o oauth2-proxy). A rota `/mcp`
não pode exigir esse login (ver [MCP.md](MCP.md)).

## 5. IA local (opcional, recomendado)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull nomic-embed-text        # embeddings: pesquisa por significado (~270 MB)
ollama pull qwen2.5:7b              # chat: resumir, arrumar, pergunta com fontes (~4,7 GB; qwen2.5:3b se tiveres pouca RAM)
python3 -m basalt indexar
```

O `basalt.toml.exemplo` já aponta ao Ollama local. Qualquer API compatível com a OpenAI serve (`[ia] url`); Anthropic e o
`claude` CLI também. A chave entra por `BASALT_IA_CHAVE` (ou `chave_ficheiro` 0600) — nunca no toml.

Sem IA nenhuma: a pesquisa é por palavras (BM25), a Entrada arruma-se à mão, o editor não tem os botões de IA. Tudo o resto
funciona.

## 6. Cópias de segurança

Copia `<dados>` inteiro (notas + git + índice + sessões). O índice reconstrói-se com `basalt indexar`; as notas são
ficheiros Markdown com histórico git — `git -C <dados>/<espaco>/memoria log` mostra tudo o que mudou.

## 7. Actualizar

`git pull` e reiniciar o serviço. As versões seguem SemVer; o [CHANGELOG](../CHANGELOG.md) diz o que muda e o que é preciso fazer.
