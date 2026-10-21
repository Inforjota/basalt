# Login com a Google (clientes, contas automáticas)

Para um Basalt de clientes — em que cada pessoa entra com a própria conta Google e não há senhas do Basalt a gerir —
põe-se um proxy de SSO (**oauth2-proxy**) à frente, em modo `[auth] modo = "cabecalho"`, com `criar_contas = true`:
a primeira pessoa que entra com um e-mail novo fica com conta própria, sem ninguém ter de correr `basalt utilizador novo`.

Isto é diferente do **🔌 Ligar a tua IA** (OAuth 2.1 do `/mcp`, documentado em [MCP.md](MCP.md)): aqui é a *pessoa* que
entra no browser; lá é o Claude/ChatGPT que liga à conta dela depois de entrar. As duas coisas coexistem sem conflito.

## 1. Consola da Google Cloud — criar o ecrã de consentimento e o cliente OAuth

1. [console.cloud.google.com](https://console.cloud.google.com/) → cria (ou escolhe) um projecto.
2. **APIs e serviços → Ecrã de consentimento OAuth**: tipo **Externo**; nome da aplicação, e-mail de suporte, logótipo
   (opcional). Âmbitos: só `openid`, `email`, `profile` (o Basalt só precisa do e-mail e, se quiseres confiança extra,
   do `sub`).
3. **Estado de publicação: muda de «Em teste» para «Em produção».** Em «teste» a Google só deixa entrar até 100
   utilizadores de teste explicitamente adicionados à lista, e os tokens expiram em 7 dias — nada disto serve para
   clientes reais. Passar a produção com âmbitos destes (não sensíveis) não exige revisão da Google.
4. **Credenciais → Criar credenciais → ID de cliente OAuth**, tipo **Aplicação Web**. Em **URIs de redirecionamento
   autorizados**, põe `https://basalt.exemplo.pt/oauth2/callback` (o caminho de callback do oauth2-proxy, não um
   caminho do Basalt). Guarda o `client_id` e o `client_secret` gerados — são para o oauth2-proxy, nunca para o
   `basalt.toml`.

## 2. oauth2-proxy — a peça que fala com a Google

O Basalt **não** sabe falar OAuth com a Google (de propósito: menos código de autenticação a auditar). Quem faz login
é o [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/), que fica entre o nginx e o Basalt e, depois de
confirmar a pessoa com a Google, deixa passar o pedido com um cabeçalho a dizer quem é.

```bash
oauth2-proxy \
  --provider=google \
  --client-id="SEU_CLIENT_ID.apps.googleusercontent.com" \
  --client-secret="SEU_CLIENT_SECRET" \
  --email-domain="*" \
  --cookie-secret="$(python3 -c 'import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')" \
  --cookie-secure=true \
  --http-address="127.0.0.1:4180" \
  --upstream="static://202" \
  --set-xauthrequest=true \
  --pass-access-token=false \
  --pass-authorization-header=false \
  --skip-provider-button=true
```

- `--email-domain="*"` deixa entrar qualquer conta Google; para restringir a um domínio de empresa (Google Workspace),
  põe o domínio (ex. `--email-domain=exemplo.pt`) — e, do lado do Basalt, `[auth] dominios = ["exemplo.pt"]` faz a
  mesma recusa outra vez, por garantia (ver §4).
- `--upstream="static://202"`: neste desenho é o **nginx** que fala com o Basalt (§3); o oauth2-proxy só responde ao
  `auth_request` e às rotas `/oauth2/*`. Um upstream «estático» garante que o oauth2-proxy **nunca** encaminha pedidos
  ao Basalt por conta própria — se alguém um dia apontar uma rota directamente ao 4180, não há nada atrás.
- `--set-xauthrequest=true` é o que faz o oauth2-proxy expor `X-Auth-Request-Email` (e `-User`, `-Groups`) nas respostas
  do `auth_request` do nginx — é daí que o nginx tira o e-mail verdadeiro para o injectar como `X-Basalt-Email`.
  **Nunca** apontar `proxy_set_header X-Basalt-Email` directamente a um cabeçalho que o cliente possa mandar.
- `--cookie-secret` é só do oauth2-proxy (a sessão dele); não tem nada a ver com o `BASALT_SEGREDO_PROXY` do passo 4.
- Com Docker Compose, a imagem oficial é `quay.io/oauth2-proxy/oauth2-proxy`; os mesmos argumentos como `command:`.

## 3. nginx — `auth_request` + stripping/injecção de cabeçalhos

Esta é a parte que decide se o sistema é seguro: o nginx tem de **apagar** qualquer `X-Basalt-Email` e
`X-Basalt-Proxy-Segredo` que o visitante tente mandar, e só depois pôr os valores verdadeiros (o `auth_request` já
correu e confirmou a pessoa).

```nginx
server {
    listen 443 ssl http2;
    server_name basalt.exemplo.pt;
    client_max_body_size 50m;

    location = /oauth2/auth {
        internal;
        proxy_pass http://127.0.0.1:4180;
        proxy_pass_request_body off;                                          # o subpedido de autenticação vai sem corpo…
        proxy_set_header Content-Length "";                                   # …e sem o Content-Length do pedido original (um auth que leia o corpo ficava pendurado)
        proxy_set_header Host                    $host;
        proxy_set_header X-Real-IP                $remote_addr;
        proxy_set_header X-Forwarded-For          $remote_addr;
        proxy_set_header X-Forwarded-Proto        $scheme;
    }

    location /oauth2/ {
        proxy_pass http://127.0.0.1:4180;
        proxy_set_header Host             $host;
        proxy_set_header X-Real-IP        $remote_addr;
        proxy_set_header X-Forwarded-For  $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # rotas que a IA do cliente (claude.ai, ChatGPT) chama SEM browser: não podem pedir login Google. O /mcp tem o
    # seu próprio Bearer; aqui o e-mail vai SEMPRE vazio, por isso ninguém entra como pessoa por estas rotas.
    # (/oauth/authorize fica de fora de propósito: é o browser do cliente, e passa pelo login Google em baixo.)
    location ~ ^/(\.well-known/|oauth/register$|oauth/token$|mcp$|api/saude$) {
        proxy_pass http://127.0.0.1:8121;
        proxy_set_header Host                       $host;
        proxy_set_header X-Forwarded-Proto           https;
        proxy_set_header X-Forwarded-For             $remote_addr;
        proxy_set_header X-Basalt-Email              "";
        proxy_set_header X-Basalt-Sub                "";
        proxy_buffering off;                                                  # o /mcp responde em stream
    }

    location / {
        auth_request /oauth2/auth;
        error_page 401 = /oauth2/sign_in;
        auth_request_set $email $upstream_http_x_auth_request_email;

        proxy_pass http://127.0.0.1:8121;
        proxy_set_header Host                       $host;
        proxy_set_header X-Forwarded-Proto           https;
        proxy_set_header X-Forwarded-For             $remote_addr;
        proxy_set_header X-Basalt-Sub                "";                      # ou $sub — ver o fim desta secção
        # o e-mail verdadeiro E o segredo vêm os dois do include (nota abaixo): não há maneira de pôr um sem o outro.
        # proxy_set_header SUBSTITUI o cabeçalho com o mesmo nome que o visitante tenha mandado (o dele nunca passa)
        include /etc/nginx/basalt-segredo.conf;
    }
}
```

O segredo fica num ficheiro à parte, só legível pelo root (`chmod 600`), nunca no repositório nem no ficheiro do site:

```nginx
# /etc/nginx/basalt-segredo.conf — o e-mail confirmado pelo auth_request E o segredo (o mesmo valor do BASALT_SEGREDO_PROXY
# do Basalt, passo 4), SEMPRE juntos: um location que inclua isto sem `auth_request` manda o e-mail vazio e o Basalt recusa
proxy_set_header X-Basalt-Email         $email;
proxy_set_header X-Basalt-Proxy-Segredo "COLA_AQUI_O_MESMO_VALOR";
```

Em **todos** os `location` que vão para o Basalt: ou `auth_request` + o `include` (que traz o e-mail verdadeiro e o segredo
juntos), ou `proxy_set_header X-Basalt-Email "";` **sem** o `include`, como no das rotas da IA. Um `location` que se esqueça do
`include` deixa simplesmente de entrar (falta o segredo) — falha fechada, não aberta. O que **abre** a porta é um `location`
com o segredo mas sem a substituição do e-mail — por isso as duas linhas vivem no mesmo ficheiro — ou um `[auth] cabecalho`
no `basalt.toml` com **outro nome** que não o que o nginx escreve (ex. `X-Auth-Request-Email`, o nome natural do
oauth2-proxy): o nginx só substitui o cabeçalho que lhe mandam substituir, e o que o visitante escrever com o outro nome
passa intacto, com o segredo colado (reproduzido no ensaio de 21/10/2026: `/api/eu` devolvia a conta forjada). O
`cabecalho` fica `X-Basalt-Email`, como no passo 4, e a prova do §7 apanha os dois casos.

**O `sub` da Google (recomendado).** Confere, além do e-mail, o identificador estável da conta Google — impede que
um e-mail *gmail.com*/Workspace apagado e recriado por outra pessoa volte a coincidir com a conta antiga sem dar por nada
(ver §4). Com `--provider=google` e `--set-xauthrequest=true`, o oauth2-proxy expõe esse `sub` em
`X-Auth-Request-User` — é o código dele, não uma suposição: em `providers/google.go`, o `Redeem` guarda
`User: c.Subject` (o claim `sub` do ID token) e `Email: c.Email`, e recusa ID tokens com `email_verified = false`
(revisto 20/10/2026 na versão em `master`). No nginx, no `location /` em cima:

```nginx
        auth_request_set $sub   $upstream_http_x_auth_request_user;
        proxy_set_header X-Basalt-Sub                $sub;                    # em vez de ""
```

e no `basalt.toml` `[auth] cabecalho_sub = "X-Basalt-Sub"`. Duas notas:
- Isto é específico do `--provider=google`. Com `--provider=oidc` (genérico), o `X-Auth-Request-User` vem do claim
  escolhido por `--user-id-claim` (omissão: `email`) — aí seria preciso `--user-id-claim=sub`, e confirmar no ensaio.
- O Basalt é **fechado por omissão**: com `cabecalho_sub` configurado, uma conta nova só se cria se o `sub` chegar, e uma
  conta que já tem `sub` gravado só entra se o cabeçalho trouxer o mesmo valor. Por isso, se puseres `cabecalho_sub` no
  toml, as duas linhas do `$sub` em cima são **obrigatórias**, não opcionais: com o nginx a mandar `X-Basalt-Sub ""` (o
  bloco tal como está em cima, ou o `--set-xauthrequest` desligado) ninguém cria conta e essas contas ficam de fora até o
  `sub` voltar a chegar — é esse o comportamento certo (nunca abre), mas convém saber (ensaio de 21/10/2026: 403 a toda
  a gente nova). As contas criadas à mão sem `sub` (ex. o admin) entram só pelo e-mail até terem um.

**Sem proxy na mesma máquina** (oauth2-proxy e nginx em hosts diferentes do Basalt): acrescenta o IP/rede deles a
`[auth] proxies_de_confianca` no `basalt.toml` (ver [INSTALAR.md §4](INSTALAR.md#4-na-rede-proxy-com-https)), senão o
limite de tentativas de login por IP conta tudo para o IP do proxy.

## 4. `basalt.toml` — as chaves deste modo

```toml
[basalt]
anfitrioes = ["basalt.exemplo.pt"]        # confere Host/Origin; outro nome dá 421

[auth]
modo = "cabecalho"
cabecalho = "X-Basalt-Email"              # o que o nginx injecta (passo 3)
segredo_proxy_env = "BASALT_SEGREDO_PROXY" # o Basalt lê o segredo desta variável de ambiente — NUNCA do toml
cabecalho_segredo = "X-Basalt-Proxy-Segredo"
cabecalho_sub = "X-Basalt-Sub"            # opcional: confere também o `sub` da Google (recomendado)
criar_contas = true                        # conta nova no 1.º login de um e-mail autenticado pelo proxy
dominios = ["exemplo.pt"]                  # opcional: só estes domínios criam conta (vazio = qualquer)
max_contas = 200                           # opcional: tecto de contas (0 = sem tecto); as que já existem continuam a entrar
                                           # (tecto, domínios, criar_contas e espaco_max_mb são relidos do toml em execução, como
                                           # os [[utilizadores]]; mudar o modo, os cabeçalhos ou o segredo é reiniciar o serviço)
espaco_max_mb = 500                        # r14: tecto de disco por conta — notas + anexos + cópia + dados (0 = sem tecto)

[ia]
pedidos_por_hora = 200                     # tecto da IA para TODAS as contas juntas
pedidos_por_hora_por_conta = 20            # r14: além desse, um tecto por conta — uma pessoa não esgota a IA de toda a gente
```

A variável de ambiente é obrigatória com `criar_contas = true` e tem de ter **pelo menos 32 caracteres**
(`basalt.toml` falha a validar sem isso — ver `basalt/config.py`, `AuthConfig.criar_contas`). Gera um valor com
`python3 -c "import secrets; print(secrets.token_urlsafe(32))"` e põe-no no serviço systemd
(`Environment=BASALT_SEGREDO_PROXY=...` em `sistema/basalt.service`, ou `.env` no Docker Compose) — **nunca** no
`basalt.toml`, que pode ir para um repositório ou cópia de segurança partilhada. Se o segredo acabar escrito no toml
por engano, `basalt estado`/o arranque recusa com `ConfigInvalida` a avisar para o tirar de lá.

`espaco_max_mb` e `pedidos_por_hora_por_conta` são as duas defesas deste pacote contra uma conta de cliente (criada
sozinha, sem ninguém a vigiar) encher o disco ou esgotar o tecto da IA para todos os outros — úteis sobretudo aqui,
porque em modo `senha`/sem `criar_contas` quem cria a conta já está a decidir confiar nessa pessoa. O que cada um cobre:
- `espaco_max_mb` conta notas (`memoria/`, com os anexos), a cópia editável e `dados/` (Entrada, índice, cópias offline)
  — e vale por **todas** as portas de escrita: criar/gravar nota na página, `/api/sincronizar` (edição sem internet),
  anexos, a Entrada (`/api/guarda`) e as ferramentas de escrita do `/mcp`. Uma nota pode sempre **encolher**, mesmo com
  o espaço cheio. Conta **tudo** o que está na pasta do espaço — também o histórico git da memória (`memoria/.git`) e a
  cópia editável (`copia/`, que repete cada nota): na prática 1 MB de tecto dá para menos de 500 kB de notas, e um espaço
  com muitas versões enche mais depressa. É de propósito (excluir o git deixava encher o disco só com versões); dimensiona
  o `espaco_max_mb` a contar com isso (medido a 21/10/2026: uma nota de 600 kB ocupou 1,2 MB do tecto logo ao ser gravada).
- `pedidos_por_hora_por_conta` conta os pedidos à IA feitos por uma conta: o editor (`/api/ia`), a pergunta
  (`/api/pergunta`) e a fusão (`/api/unir`) partilham o mesmo balde. As propostas da IA na sincronização offline
  ficam sob o tecto do servidor `[ia] propostas_por_hora`, e a Entrada automática não é pedida pelo cliente.

## 5. Terminar sessão (sign-out)

Em modo `cabecalho`, o Basalt não tem formulário de login nem senha — a sessão «é» a cookie do oauth2-proxy. O botão
**Terminar sessão** da interface do Basalt chama `POST /api/sair`, que neste modo não tem sessão nenhuma do Basalt para
acabar (não há cookie do Basalt; a única sessão é a do oauth2-proxy) e por isso **não** termina nada — nem a sessão da
Google nem a do oauth2-proxy: sem mais nada, a pessoa seria autenticada outra vez no
pedido seguinte, sem ver um ecrã de login. Para um sign-out completo, o link de "sair" tem de apontar para o
oauth2-proxy, não para o Basalt:

```
https://basalt.exemplo.pt/oauth2/sign_out?rd=https%3A%2F%2Fbasalt.exemplo.pt%2F
```

(`/oauth2/sign_out` apaga a cookie do oauth2-proxy e redirecciona para `rd`; para também desligar a sessão da conta
Google nesse browser, o `rd` pode apontar para o endpoint de logout da própria Google, mas isso desliga a Google
inteira, não só este sítio — normalmente não vale a pena.) Isto é uma troca manual a fazer na instalação (não vem
automático do `basalt utilizador apagar`, que é para a conta deixar de poder entrar, não para terminar a sessão de
quem a esteja a usar nesse instante no browser — ver §6).

## 6. Apagar e exportar uma conta

`basalt utilizador apagar EMAIL` tira a conta do `basalt.toml`, revoga as sessões do Basalt e as ligações OAuth do
`/mcp`, e **move** (nunca apaga) a pasta de dados para `<dados>/apagados/<espaco>-<data>/`. Com login pela Google,
isto é o que corresponde a "a pessoa deixa de poder entrar": se ela voltar a autenticar-se com a mesma conta Google,
e `criar_contas` continuar ligado, fica com uma conta **nova** (espaço vazio) — os dados antigos continuam em
`apagados/`, por se precisares de os recuperar ou entregar. `basalt utilizador exportar EMAIL --para dados.zip`
tira uma cópia das notas e anexos dessa conta (nunca o `basalt.toml` nem tokens) — útil para responder a um pedido de
portabilidade de dados antes ou depois de apagar a conta. Se o espaço for **partilhado** com outra conta
(`[basalt] espaco_partilhado = true`), a pasta fica onde está — é também da outra pessoa — e o comando di-lo.

```bash
python3 -m basalt --config /var/lib/basalt/basalt.toml utilizador exportar ana@gmail.com --para /tmp/ana.zip
python3 -m basalt --config /var/lib/basalt/basalt.toml utilizador apagar ana@gmail.com      # pede para confirmar o e-mail
```

## 7. Prova do ataque — o visitante não consegue escrever o próprio e-mail

A defesa inteira deste modo está em duas coisas diferentes — o nginx **substituir** o cabeçalho do visitante pelo verdadeiro,
e o Basalt **exigir o segredo** do proxy — e cada uma prova-se no sítio certo. Um `curl https://basalt.exemplo.pt/api/eu`
sem sessão devolve `302` para o login: é o `auth_request` a funcionar, e não prova nenhuma das duas (dava 302 na mesma com
o nginx a deixar passar o cabeçalho do visitante — ensaio de 21/10/2026).

**1. O segredo do Basalt** — no servidor, directamente à porta do Basalt, como se fosses o nginx (sem oauth2-proxy nem segredo):

```bash
# sem o segredo do proxy — mesmo com um e-mail válido, tem de ser recusado
curl -i http://127.0.0.1:8121/api/eu -H "X-Basalt-Email: admin@exemplo.pt"
# esperado: 401 ou 403 (nunca 200 com a conta do admin)

# com o segredo errado — recusado da mesma forma
curl -i http://127.0.0.1:8121/api/eu -H "X-Basalt-Email: admin@exemplo.pt" -H "X-Basalt-Proxy-Segredo: qualquer-coisa-errada"
# esperado: 401 ou 403
```

Se der 200, o Basalt arrancou sem o `BASALT_SEGREDO_PROXY` (passo 4): com `criar_contas = true` nem arranca; sem
`criar_contas` arranca, mas escreve um `aviso:` no arranque — vê o `journalctl -u basalt`.

**2. A substituição no nginx** — pelo sítio público, COM a tua sessão (entra pela Google primeiro), a tentar passar por outra
pessoa. A cookie `_oauth2_proxy` é a tua sessão: copia-a do browser (ferramentas de programador → Armazenamento/Application
→ Cookies) e nunca a mandes a ninguém.

```bash
curl -s https://basalt.exemplo.pt/api/eu -b "_oauth2_proxy=COLA_AQUI_A_TUA_COOKIE" \
     -H "X-Basalt-Email: outra-pessoa@exemplo.pt" -H "X-Basalt-Proxy-Segredo: qualquer-coisa"
# esperado: 200 com o TEU e-mail (nunca o da outra pessoa; e nunca 401 — isso seria a tua própria sessão a falhar)
```

ou, sem sair do browser, na consola (F12) da página do Basalt já aberta:

```js
fetch("/api/eu", {headers: {"X-Basalt-Email": "outra-pessoa@exemplo.pt", "X-Basalt-Proxy-Segredo": "x"}}).then(r => r.json()).then(console.log)
```

Se aparecer o e-mail da outra pessoa, **o nginx está a deixar passar o cabeçalho do visitante**: há um `location` com o
segredo mas sem a substituição do e-mail (o `include` do passo 3 traz as duas linhas juntas precisamente para isto não
acontecer), ou o `[auth] cabecalho` do `basalt.toml` tem outro nome que não o que o nginx escreve — corrige antes de pôr
isto em produção. O teste 1 está provado em `testes/test_login_google.py::test_sem_o_segredo_do_proxy_ninguem_entra` (o
mesmo ataque, directo ao servidor Basalt); a forma do bloco nginx deste guia em `testes/test_guia_google.py` (o include
leva o e-mail e o segredo, as rotas da IA apagam o e-mail, o `/oauth2/auth` vai sem corpo, e o bloco passa num `nginx -t`
real quando há nginx na máquina).

## 8. RGPD e privacidade

- Login pela Google só transmite o e-mail (e, opcionalmente, o `sub`) ao Basalt — não o perfil, as fotos nem os
  contactos da conta Google; o Basalt nunca pede âmbitos (`scopes`) além de `openid`, `email`, `profile`.
- O que o Basalt guarda sobre cada conta e como protege os dados de cada uma (isolamento por espaço, cópias CAS,
  histórico git, sem apagar nunca por fora de `utilizador apagar`) está descrito em [SEGURANCA.md](SEGURANCA.md) —
  em particular a secção "Revogar sem reiniciar" (relevante aqui: apagar a conta no toml invalida sessões e tokens no
  pedido seguinte, sem reiniciar o servidor) e "O que NÃO está protegido" (o modo `cabecalho` confia cegamente no
  cabeçalho do proxy — só é seguro atrás de um proxy que o substitua sempre, como mostrado no §3 acima).
- Os direitos da pessoa (acesso, portabilidade, apagar) e como os cumprir com os comandos `utilizador exportar`/
  `utilizador apagar` deste documento estão detalhados em [PRIVACIDADE.md](PRIVACIDADE.md) — lê esse documento antes
  de pôr este modo ao serviço de clientes reais (quem é o responsável pelo tratamento, prazos de resposta, etc.).
- Guarda o `client_secret` da Google e o `BASALT_SEGREDO_PROXY` como segredos de produção (gestor de segredos ou
  ficheiro 0600 fora do git) — nenhum dos dois entra no `basalt.toml` nem em cópias de segurança partilhadas em claro.
