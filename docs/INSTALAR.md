# Instalar o Basalt

## 1. À mão (qualquer Linux com Python ≥ 3.11 e git)

Windows ou Mac, ou só um portátil? Ver a [secção 8](#8-só-com-um-portátil).

```bash
git clone https://github.com/inforjota/basalt && cd basalt
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

O `idioma` (como todas as opções que não são utilizadores) só muda ao reiniciar o servidor; depois de o reiniciares, recarrega a página (a interface guarda-se no service worker, por isso o idioma novo só se vê depois de a recarregares). As mensagens de erro do servidor continuam em português.

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
sudo cp sistema/basalt-apagados.* /etc/systemd/system/ && sudo systemctl enable --now basalt-apagados.timer # opcional; com várias contas, sim
```

O código fica em `/opt/basalt` (sem pip) e as unidades põem `PYTHONPATH=/opt/basalt`; o serviço corre em `/var/lib/basalt`
com o `basalt.toml` de lá. Se puseres o código noutro sítio, ajusta o `PYTHONPATH` nas duas unidades. O timer corre
`basalt arrumar --auto` às 04:10, com o mesmo utilizador e o mesmo endurecimento do serviço. Por cada espaço faz o que o servidor faz à Entrada
(se `[entrada] automatica` estiver ligada), arruma as notas parecidas, reindexa e refaz as categorias; a Entrada e as notas parecidas
precisam de uma IA configurada (`[ia]`, ou a de cada tarefa — § 5); sem ela só reindexa e refaz as categorias. Para ver o que faria sem escrever nada: `basalt arrumar --auto --simular`.

O timer `basalt-apagados` corre `basalt apagados eliminar --dias 30 --sim` todos os dias às 04:40 (com um atraso ao acaso até
15 min; `Persistent=true` faz a corrida perdida se a máquina esteve desligada), com o mesmo utilizador e o mesmo endurecimento,
e **sem rede**. É ele que elimina de vez, passados 30 dias, as contas apagadas com `basalt utilizador apagar` (§ 4). O resultado
vê-se com `journalctl -u basalt-apagados`; a última linha é sempre `resumo: …` (§ 6) e o serviço falha (rc 1) se houver uma
entrada que não se conseguiu eliminar ou uma conta apagada que voltou ao `basalt.toml`. No Docker não há timers: põe no `cron`
da máquina `docker compose exec -T basalt python3 -m basalt apagados eliminar --sim` (uma vez por dia).

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

**Apagar uma conta:** `basalt utilizador apagar EMAIL` desliga-a logo — sai do `basalt.toml`, as sessões e as IAs ligadas caem
— e move a pasta para `<dados>/apagados/<espaco>-<data>/`. Antes de a mover, regista o apagamento em
`<dados>/contas-apagadas.json` (só o dono lê). Passados **30 dias**, `basalt apagados eliminar --sim` (o timer da § 3 fá-lo
sozinho) apaga essa pasta de vez, tira da base OAuth tudo o que era dessa conta e deixa no registo só o resumo do e-mail
(sha256), o `conta_id` e as datas: o e-mail, o espaço e a pasta passam a `null`. `basalt apagados listar` mostra o que está à
espera e quando; sem `--sim`, `eliminar` só diz o que faria (`--dias N` muda o prazo). O `eliminar` só apaga pastas dentro de
`apagados/` com um registo que lhes corresponda; as que lá estejam sem registo (de antes da r17, ou postas à mão) ficam, e o
comando di-lo.

Cada conta tem um `conta_id` aleatório, escrito por `iniciar`, `utilizador novo` e pelo 1.º login com `criar_contas`. Uma conta
**nova** com o mesmo e-mail, criada depois do apagamento, tem outro `conta_id` e nunca é tocada (nem a pasta, nem as sessões,
nem as ligações das IAs). As contas criadas **antes da r17** não têm `conta_id`: para essas, o mesmo e-mail chega para as
reconhecer, por isso uma conta antiga apagada e outra pessoa com o mesmo e-mail num bloco antigo sem `conta_id` confundem-se.
Se escreveres blocos `[[utilizadores]]` à mão, põe-lhes `conta_id = "…"` (por exemplo
`python3 -c "import secrets; print(secrets.token_hex(8))"`).

**Mudar a senha:** cada utilizador muda a sua na página **🔌 Ligar a tua IA → A tua senha** (senha actual + nova). As outras
sessões e as IAs ligadas caem e voltam a pedir «Permitir». Esqueceste-a? No servidor: `python3 -m basalt utilizador senha
O_TEU_EMAIL` — na instalação com systemd, como o utilizador do serviço: `sudo -u basalt env PYTHONPATH=/opt/basalt python3 -m basalt
--config /var/lib/basalt/basalt.toml utilizador senha O_TEU_EMAIL`. O serviço precisa de poder escrever na pasta do `basalt.toml`
(no `sistema/basalt.service` já pode); a página e a linha de comandos escrevem-no à vez (trinco `basalt.toml.trinco`).

**Já tens login (SSO) no proxy?** Põe `[auth] modo = "cabecalho"` e faz o proxy **substituir** `X-Basalt-Email` pelo e-mail
autenticado (p. ex. `proxy_set_header X-Basalt-Email $upstream_http_x_auth_request_email;` com o oauth2-proxy). A rota `/mcp`
não pode exigir esse login (ver [MCP.md](MCP.md)).

**O segredo do proxy é obrigatório neste modo** (desde a r16): o servidor só arranca com a variável `BASALT_SEGREDO_PROXY`
(o nome muda com `[auth] segredo_proxy_env`) com **pelo menos 32 caracteres** — gera-a com
`python3 -c "import secrets; print(secrets.token_urlsafe(32))"` e põe-na no serviço (`Environment=BASALT_SEGREDO_PROXY=...` em
`sistema/basalt.service`, ou `.env` no Docker Compose), nunca no `basalt.toml`. O proxy manda o **mesmo valor** no cabeçalho
`X-Basalt-Proxy-Segredo`, junto com o e-mail, num ficheiro só do root (`proxy_set_header X-Basalt-Proxy-Segredo "…";` — ver
[GOOGLE.md](GOOGLE.md) §3). Sem a variável, ou com menos de 32 caracteres, `basalt serve` sai com a razão (até à r15 só
avisava, e qualquer processo que chegasse à porta entrava com o e-mail que escrevesse); os comandos de linha (`utilizador …`,
`entrada`, `mcp` por stdio) não precisam dela. **A actualizar de uma versão anterior? Põe a variável e o proxy ANTES.**

### Traz a tua IA (Claude e ChatGPT pelo OAuth)

Para ligar o Claude (site, Desktop, telemóvel) e o ChatGPT, o Basalt precisa de saber o seu endereço público **https**:

```toml
[basalt]
url_publico = "https://basalt.exemplo.pt"   # sem caminho nem barra no fim; liga o OAuth do /mcp
anfitrioes = ["basalt.exemplo.pt"]          # obrigatório: o mesmo nome
```

(ou `BASALT_URL_PUBLICO`). É este endereço — e não o `X-Forwarded-Proto` nem o `Host` de cada pedido — que entra nos metadados e
nos tokens, por isso tem de ser exactamente o que vais colar no Claude/ChatGPT (mais `/mcp`). Em modo `aberto` o OAuth não existe
(não há contas a quem pedir licença): usa o Claude Code ou o Codex com um token.

O Claude e o ChatGPT chamam o Basalt **a partir da nuvem deles**, não do teu computador: o endereço tem de se ver na internet
(um proxy público, ou um túnel como o Cloudflare Tunnel ou o Tailscale Funnel se o Basalt está em casa). Se o proxy tem login (SSO),
estas rotas ficam **sem** ele — a protecção é o token. Cada uma leva `auth_request off;`: se o login estiver definido no bloco
`server` (como é costume), as `location` herdam-no, e não pôr a directiva **não** o desliga — a nuvem da IA ficava a bater no login:

```nginx
location ~ ^/(mcp|\.well-known/oauth-|oauth/(register|token)$) {   # chamadas pela nuvem da IA
    auth_request off;
    proxy_pass http://127.0.0.1:8121;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Basalt-Email "";
    client_max_body_size 1m;
}
# /oauth/authorize — a 1.ª chamada (vinda da IA, com client_id=…&redirect_uri=…&state=…) também fica SEM login: muitos
# proxies de SSO (oauth2-proxy com `rd=$request_uri`, por exemplo) não codificam o endereço de regresso e perdem tudo depois do
# 1.º «&» — a IA mostra «client_id ?». Esta fase não precisa de conta: o Basalt só guarda o pedido e recarrega para
# ?pedido=<id>, e é ESSA que tem de passar pelo login (é lá que se vê a conta e se aprova).
location = /oauth/authorize {
    if ($arg_pedido = "") { rewrite ^ /__basalt_oauth_inicio last; }
    # … o mesmo auth_request / X-Basalt-Email do resto do sítio …
    proxy_pass http://127.0.0.1:8121;
}
location = /__basalt_oauth_inicio {
    internal;
    auth_request off;           # também aqui: sem isto herdava o login do server
    proxy_pass http://127.0.0.1:8121/oauth/authorize$is_args$args;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Basalt-Email "";
}
# /oauth/autorizar (o botão «Permitir») fica atrás do login, como o resto.
```

Depois, na interface, **🔌 Ligar a tua IA** → escolhe a IA → «Verificar ligação» (abre a página pelo `url_publico`). A prova
final é a ligação aparecer na lista com «último uso». A base das ligações fica em `<dados>/oauth.sqlite` (entra nas cópias de
segurança com o resto da pasta de dados; sem ela, as IAs têm de voltar a ligar-se).

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

**Uma IA por tarefa (opcional).** `[ia.unir]` (fundir notas: o botão Unir e o `arrumar --auto`), `[ia.arrumar]` (decidir os
pares de notas parecidas do `arrumar --auto`) e `[ia.entrada]` (classificar as ideias da Entrada, no servidor e no `arrumar --auto`)
podem usar outro fornecedor ou modelo — por exemplo um modelo local barato para a Entrada e um mais forte para fundir. Sem
`[ia.entrada]`, a Entrada usa a de `[ia.arrumar]` e, sem essa, a de `[ia]`. Cada uma só muda o que declara (`fornecedor`, `url`, `modelo_chat`, `chave_env`, `chave_ficheiro`,
`tempo_limite`); o resto vem de `[ia]`. A chave de `[ia]` só passa para a tarefa se o fornecedor e o url forem os mesmos (a
chave de um serviço nunca vai para outro endereço). Os limites por hora continuam os de `[ia]`. `basalt estado` mostra a IA de
cada tarefa e de onde vem. Uma tarefa desligada não leva as outras: `[ia.unir] fornecedor = "nenhum"` desliga só a fusão (o
`arrumar --auto` passa a ligar as notas parecidas em vez de as fundir); `[ia.arrumar] fornecedor = "nenhum"` desliga só os pares
(a Entrada continua a ser classificada pela de `[ia.entrada]`, e o índice e as categorias continuam a ser refeitos).

## 6. Cópias de segurança

Copia `<dados>` inteiro (notas + git + índice + sessões). O índice reconstrói-se com `basalt indexar`; as notas são
ficheiros Markdown com histórico git — `git -C <dados>/<espaco>/memoria log` mostra tudo o que mudou.

**Contas apagadas e cópias antigas.** Uma cópia de antes de um apagamento ainda tem a conta: o `basalt.toml` com o bloco dela, a
pasta e as ligações das IAs. Nas cópias, os dados de uma conta apagada desaparecem à medida que as cópias rodam; até lá, ao
repor uma cópia, a conta **não pode voltar**. Para isso:

1. **Depois de cada apagamento**, guarda a lista das contas apagadas **fora** das cópias de segurança (outro disco, outra
   máquina). A lista não tem e-mails, só o resumo de cada um, o `conta_id` e a data:

   ```bash
   sudo -u basalt env PYTHONPATH=/opt/basalt python3 -m basalt --config /var/lib/basalt/basalt.toml apagados exportar > contas-apagadas.jsonl
   ```

2. **Depois de repor uma cópia, ANTES de abrir o serviço** (`systemctl start basalt`), volta a aplicar essa lista e elimina o
   que já passou do prazo:

   ```bash
   python3 -m basalt --config /var/lib/basalt/basalt.toml apagados reaplicar --lista contas-apagadas.jsonl         # o que faria
   python3 -m basalt --config /var/lib/basalt/basalt.toml apagados reaplicar --lista contas-apagadas.jsonl --sim
   python3 -m basalt --config /var/lib/basalt/basalt.toml apagados eliminar --sim
   ```

   (na instalação com systemd, como o utilizador do serviço: `sudo -u basalt env PYTHONPATH=/opt/basalt …`.) O `reaplicar` volta
   a apagar cada conta da lista que a cópia trouxe de volta (o mesmo e-mail **e** o mesmo `conta_id`), pelo mesmo caminho do
   `utilizador apagar` e com a data do apagamento original, por isso o `eliminar` a seguir apaga logo a pasta se os 30 dias já
   passaram. Uma conta com o mesmo e-mail mas outro `conta_id` foi criada depois e fica («recriada depois do apagamento — não
   mexo»). Uma linha que falte no registo reposto, e cuja conta não esteja no `basalt.toml`, acrescenta-se já como eliminada. O
   único admin nunca se apaga sozinho: o comando conta-o como `protegidas` e sai com rc 1 (cria outro admin e repete). Pode
   correr-se as vezes que for preciso: da segunda vez não faz nada.

**A linha do resumo.** `apagados eliminar` e `apagados reaplicar` acabam **sempre** com uma linha fixa, a última do stdout — com
ou sem `--sim`, também quando não há nada a fazer ou quando falham —, sem e-mails, com os números por esta ordem:

| comando | última linha |
|---|---|
| `apagados eliminar` | `resumo: a_eliminar=N` (entradas com mais de `--dias` ainda por eliminar) |
| `apagados eliminar --sim` | `resumo: eliminadas=N falhas=N voltaram=N` |
| `apagados reaplicar --lista F` | `resumo: reapagaria=N recriadas=N acrescentaria=N protegidas=N` |
| `apagados reaplicar --lista F --sim` | `resumo: reapagadas=N recriadas=N acrescentadas=N protegidas=N` |

O rc é 1 se alguma coisa falhou (um registo ou um `basalt.toml` que não se lêem, uma pasta recusada, a base ocupada), e também
com `--sim` quando ficou trabalho por fazer à mão (`voltaram` ou `protegidas` acima de 0); senão 0. Quem vigia o servidor pode
ler só esta linha e o rc.

## 7. Actualizar

`git pull` e reiniciar o serviço (systemd: voltar a copiar `basalt/` para `/opt/basalt`; Docker: `docker compose build`).
As versões seguem SemVer; o [CHANGELOG](../CHANGELOG.md) diz o que muda e o que é preciso fazer.

**Programas que escrevem na Entrada** (um bot de mensagens, um atalho, um script antigo) devem usar o comando, não acrescentar
linhas ao `entrada.jsonl` à mão: a página reescreve esse ficheiro quando arquivas ou arrumas uma ideia, e uma linha acrescentada
nesse instante perdia-se. O comando espera pelo mesmo trinco da página e segue as mesmas regras (corte aos 4000 caracteres,
password em claro → recusa com código de saída 2, nada fica gravado):

```bash
python3 -m basalt --config ~/basalt-dados/basalt.toml entrada "comprar velas" --de telegram -u ana@exemplo.pt
printf '%s' "$TEXTO" | python3 -m basalt entrada - --de telegram     # «-»: o texto vem do stdin (não aparece na lista de processos)
python3 -m basalt entrada --de telegram -- "-urgente"               # um texto que comece por «-» vai depois de --
python3 -m basalt entrada                                            # sem texto: lista as ideias por arrumar (--json)
```

Uma fonte que não esteja em `[entrada] fontes_de_confianca` grava na mesma, mas a ideia fica na Entrada à espera de aprovação
(o comando avisa). As categorias de `[fontes_extra]` (`<fonte>:<ref>` em `categorias.json`) mantêm-se quando o `arrumar` refaz
as categorias; só saem as de notas apagadas e as que a fonte deixou de dar ao índice.
**Edições feitas sem internet numa versão anterior da página.** Se uma pessoa tinha edições por enviar quando a página
mudou de versão, e não se sabe de que conta eram, elas não sobem sozinhas: ficam guardadas à parte no browser e a página
mostra «Há N edições guardadas neste aparelho que não chegaram a ser enviadas». Daí pode vê-las, enviá-las em seu nome (com
a versão de base que tinham: se a nota mudou entretanto, fica um conflito para resolver, nunca por cima) ou descarregá-las
(`.md`/`.zip`, sem rede). Esconder o aviso não apaga nada; o Início continua a mostrar quantas são.

**Voltar a uma versão anterior.** A página de uma versão anterior não conhece o que esta guardou à parte: não o mostra, e o
«Apagar cópia deste aparelho» dela apaga-o. Antes de recuar, pede a quem tem esse aviso que envie ou descarregue as edições.

## 8. Só com um portátil

Não é preciso servidor. O que muda é **onde** corre e **quando** a IA da nuvem chega lá.

**Sistema.** O Basalt precisa de Linux: as escritas seguras usam trocas atómicas do Linux (`renameat2`), e o arranque
(`iniciar`, `serve`, `mcp`) prova-as na pasta dos dados — se não servirem, pára com uma mensagem a dizer porquê.

| portátil | como |
|---|---|
| **Linux** | a secção 1, tal e qual |
| **Windows** | no **WSL2** (`wsl --install`, depois a secção 1 dentro do Ubuntu). Os dados ficam no Linux (`~/basalt-dados`), **nunca** em `/mnt/c/…` — as pastas do Windows não têm as trocas atómicas. O Obsidian do Windows abre a pasta por `\\wsl$\Ubuntu\home\<tu>\basalt-dados\<espaco>\memoria`. |
| **Mac** | com o **Docker Desktop** e os dados num volume do Docker (não numa pasta do Mac): `cd docker && docker compose -f compose.yml -f compose.volume.yml run --rm basalt iniciar --dados /dados --email tu@exemplo.pt --modo senha --escutar 0.0.0.0` e depois `… up -d`. Para ver as notas no Obsidian, `docker compose cp basalt:/dados/. ./copia-dados` (cópia, não a pasta viva). *Ainda não provado num Mac real — conta-nos como correu.* |

**Sem internet para o Basalt** (só este computador): a interface em http://127.0.0.1:8121, o Obsidian na mesma pasta, e os
agentes do terminal — Claude Code e Codex — ligados por stdio (ver [MCP.md](MCP.md)). Nada sai do portátil.

**Claude (site, app, telemóvel) e ChatGPT.** Chamam o Basalt a partir da nuvem deles, por isso o portátil precisa de um
endereço **https fixo** na internet. Sem servidor nem portas abertas no router, um túnel faz isso:

- **Tailscale Funnel** (grátis, sem domínio): instala o Tailscale, entra, e `tailscale funnel --bg 8121` dá um endereço fixo do
  tipo `https://portatil.nome-da-rede.ts.net`.
- **Cloudflare Tunnel com um domínio teu**: `cloudflared tunnel create basalt`, `cloudflared tunnel route dns basalt notas.exemplo.pt`
  e `cloudflared tunnel run --url http://127.0.0.1:8121 basalt`.
- ⚠️ O túnel rápido sem conta (`cloudflared tunnel --url …`, endereço `*.trycloudflare.com`) **não serve**: o endereço muda a cada
  arranque e as IAs ligadas deixam de o encontrar.

Depois põe esse endereço no `basalt.toml` (secção 4, «Traz a tua IA»), reinicia e liga a IA pela página **🔌 Ligar a tua IA**:

```toml
[basalt]
url_publico = "https://portatil.nome-da-rede.ts.net"
anfitrioes = ["portatil.nome-da-rede.ts.net"]
```

Precisa de um utilizador com senha (`iniciar --email …`): em modo `aberto` não há OAuth. O Basalt continua a escutar só em
`127.0.0.1` — quem chega de fora é o túnel.

**O preço de ser um portátil:** a IA só chega às notas **enquanto o portátil está ligado, acordado e com o Basalt a correr**. Com
a tampa fechada, o Claude no telemóvel diz que não consegue ligar-se — as notas não se perdem, só não estão lá. Para estar
sempre disponível é preciso uma máquina sempre ligada (um mini-PC, um Raspberry Pi 5, ou uma VPS pequena): a secção 3 (systemd)
e a 4 (proxy) servem para isso.
