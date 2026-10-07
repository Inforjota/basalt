# Instalar o Basalt

## 1. À mão (qualquer Linux com Python ≥ 3.11 e git)

```bash
git clone https://github.com/REPO_POR_DEFINIR/basalt && cd basalt
python3 -m basalt iniciar --dados ~/basalt-dados --email tu@exemplo.pt --nome "O teu nome"
export BASALT_CONFIG=~/basalt-dados/basalt.toml      # os comandos seguintes (serve, indexar, utilizador…) encontram-no
python3 -m basalt serve
```

Abre http://127.0.0.1:8121. Sem `--email` fica em modo `aberto` (sem login) — só é aceite a escutar em `127.0.0.1`.
O `iniciar` guarda o `basalt.toml` na pasta dos dados; os outros comandos encontram-no por `BASALT_CONFIG` ou `--config`
(`python3 -m basalt --config ~/basalt-dados/basalt.toml serve`). O modelo comentado com todas as opções é o
`basalt/basalt.toml.exemplo` (vai dentro do pacote: o `iniciar` parte dele).

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
cd docker
mkdir -p dados && sudo chown 1000:1000 dados     # a imagem corre como o utilizador 1000
docker compose run --rm basalt iniciar --dados /dados --email tu@exemplo.pt --modo senha --escutar 0.0.0.0
docker compose up -d                              # http://127.0.0.1:8121; dados em docker/dados/
docker compose exec basalt python3 -m basalt utilizador novo ana@exemplo.pt --nome Ana
```

O `iniciar` pede a senha no terminal e cria `/dados/basalt.toml` (`BASALT_CONFIG` já aponta para lá); sem ele o `up` não
tem configuração e o contentor reinicia em ciclo. Na imagem o servidor escuta em `0.0.0.0` (a porta só é publicada em
`127.0.0.1`), por isso o login é obrigatório: o modo `aberto` é recusado. Sem `sudo`: põe `user: "UID:GID"` (o teu, de
`id -u` e `id -g`) no serviço do `compose.yml`.

A imagem é `python:3.12-slim` + `git`; corre como utilizador sem privilégios; só `/dados` é escrito. O estado de saúde vem de
`/api/saude` (pública, sem login).

## 3. Serviço systemd

```bash
sudo useradd --system --create-home --home-dir /var/lib/basalt basalt
sudo mkdir -p /opt/basalt /var/lib/basalt
sudo cp -r basalt /opt/basalt/ && sudo chown -R basalt:basalt /opt/basalt
sudo -u basalt env PYTHONPATH=/opt/basalt python3 -m basalt iniciar --dados /var/lib/basalt --email tu@exemplo.pt
sudo cp sistema/basalt.service /etc/systemd/system/ && sudo systemctl enable --now basalt
sudo cp sistema/basalt-arrumar.* /etc/systemd/system/ && sudo systemctl enable --now basalt-arrumar.timer   # opcional
```

O código fica em `/opt/basalt` (sem pip) e as unidades põem `PYTHONPATH=/opt/basalt`; o serviço corre em `/var/lib/basalt`
com o `basalt.toml` de lá. Se puseres o código noutro sítio, ajusta o `PYTHONPATH` nas duas unidades. O timer corre
`basalt arrumar --auto` às 04:10, com o mesmo utilizador e o mesmo endurecimento do serviço.

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

No `basalt.toml`, diz ao servidor por que nome é chamado e quem é o proxy:

```toml
[basalt]
anfitrioes = ["basalt.exemplo.pt"]       # o Host e o Origin conferem-se; outro nome recebe 421 (contra DNS rebinding)

[auth]
proxies_de_confianca = ["203.0.113.5"]   # só se o proxy NÃO estiver na mesma máquina (IP ou rede, ex.: "198.51.100.0/24")
```

O limite de tentativas de login conta por IP. Com o proxy na mesma máquina (loopback) o Basalt já lê o `X-Forwarded-For`.
Com o proxy noutra máquina, ou com o Basalt no Docker, a ligação chega de outro IP: põe esse IP (ou a rede da bridge do
Docker, que vês com `docker network inspect bridge`) em `proxies_de_confianca`. Sem isso, todos os visitantes partilham o
limite do IP do proxy. O cabeçalho lê-se da direita, por isso um visitante não consegue escolher o próprio IP.

**Vários utilizadores:** cada `[[utilizadores]]` tem o seu `espaco` (pasta própria); `basalt utilizador novo` escolhe um livre.
Dois utilizadores no mesmo espaço são recusados ao arrancar, a não ser que ponhas `[basalt] espaco_partilhado = true` de
propósito (aí vêem e editam as mesmas notas). Os utilizadores, as senhas e os tokens MCP relêem-se do `basalt.toml` sem
reiniciar; as outras opções só mudam ao reiniciar.

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

O `basalt/basalt.toml.exemplo` já aponta ao Ollama local. Qualquer API compatível com a OpenAI serve (`[ia] url`); Anthropic e o
`claude` CLI também. A chave entra por `BASALT_IA_CHAVE` (ou `chave_ficheiro` 0600) — nunca no toml.

Sem IA nenhuma: a pesquisa é por palavras (BM25), a Entrada arruma-se à mão, o editor não tem os botões de IA. Tudo o resto
funciona.

## 6. Cópias de segurança

Copia `<dados>` inteiro (notas + git + índice + sessões). O índice reconstrói-se com `basalt indexar`; as notas são
ficheiros Markdown com histórico git — `git -C <dados>/<espaco>/memoria log` mostra tudo o que mudou.

## 7. Actualizar

`git pull` e reiniciar o serviço (systemd: voltar a copiar `basalt/` para `/opt/basalt`; Docker: `docker compose build`).
As versões seguem SemVer; o [CHANGELOG](../CHANGELOG.md) diz o que muda e o que é preciso fazer.
