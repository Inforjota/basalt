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
  --upstream="http://127.0.0.1:8121" \
  --set-xauthrequest=true \
  --pass-access-token=false \
  --pass-authorization-header=false \
  --skip-provider-button=true
```

- `--email-domain="*"` deixa entrar qualquer conta Google; para restringir a um domínio de empresa (Google Workspace),
  põe o domínio (ex. `--email-domain=exemplo.pt`) — e, do lado do Basalt, `[auth] dominios = ["exemplo.pt"]` faz a
  mesma recusa outra vez, por garantia (ver §4).
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
        # proxy_set_header SUBSTITUI o cabeçalho com o mesmo nome que o visitante tenha mandado (o dele nunca passa)
        proxy_set_header X-Basalt-Email              $email;
        proxy_set_header X-Basalt-Sub                "";                      # ou $sub — ver o fim desta secção
        include /etc/nginx/basalt-segredo.conf;                               # o segredo — ver nota abaixo
    }
}
```

O segredo fica num ficheiro à parte, só legível pelo root (`chmod 600`), nunca no repositório nem no ficheiro do site:

```nginx
# /etc/nginx/basalt-segredo.conf — o mesmo valor do BASALT_SEGREDO_PROXY do Basalt (passo 4)
proxy_set_header X-Basalt-Proxy-Segredo "COLA_AQUI_O_MESMO_VALOR";
```

Em **todos** os `location` que vão para o Basalt põe a linha do `X-Basalt-Email`: com `$email` (vindo do
`auth_request`) junto com o `include` do segredo, ou `""` sem o `include`, como no das rotas da IA. Um `location` que se esqueça do `include` deixa simplesmente de entrar
(falta o segredo) — falha fechada, não aberta.

Se quiseres o `sub` da Google como confirmação extra (impede que um e-mail *gmail.com* mudado de dono volte a
coincidir com a conta antiga sem dar por nada — ver §4), o oauth2-proxy também expõe
`X-Auth-Request-User` como subject id com certos provedores; confirma com `--set-xauthrequest=true` e
`auth_request_set $sub $upstream_http_x_auth_request_user;`, e injecta-o como `X-Basalt-Sub` da mesma forma
(substitui o do visitante, como os outros). ⚠️ Confirma no ensaio que esse valor é mesmo o `sub` da Google e não o e-mail; se for o e-mail, deixa `cabecalho_sub` vazio.

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
porque em modo `senha`/sem `criar_contas` quem cria a conta já está a decidir confiar nessa pessoa.

## 5. Terminar sessão (sign-out)

Em modo `cabecalho`, o Basalt não tem formulário de login nem senha — a sessão «é» a cookie do oauth2-proxy. O botão
**Terminar sessão** da interface do Basalt chama `POST /api/sair`, que acaba a sessão do Basalt (o cookie dele), mas
**não** termina a sessão da Google nem a do oauth2-proxy: sem mais nada, a pessoa seria autenticada outra vez no
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
portabilidade de dados antes ou depois de apagar a conta.

```bash
python3 -m basalt --config /var/lib/basalt/basalt.toml utilizador exportar ana@gmail.com --para /tmp/ana.zip
python3 -m basalt --config /var/lib/basalt/basalt.toml utilizador apagar ana@gmail.com      # pede para confirmar o e-mail
```

## 7. Prova do ataque (curl) — o visitante não consegue escrever o próprio e-mail

A defesa inteira deste modo está em o nginx apagar o cabeçalho do visitante antes de pôr o verdadeiro, e o Basalt
exigir o segredo do proxy. Para provar que funciona, dois testes com `curl`, direccionados ao Basalt como se fosses o
nginx (sem passar pelo oauth2-proxy nem pelo segredo):

```bash
# 1. sem o segredo do proxy — mesmo mandando um e-mail válido, tem de ser recusado
curl -i https://basalt.exemplo.pt/api/eu -H "X-Basalt-Email: admin@exemplo.pt"
# esperado: 401 ou 403 (nunca 200 com a conta do admin)

# 2. com o segredo errado — recusado da mesma forma
curl -i https://basalt.exemplo.pt/api/eu \
     -H "X-Basalt-Email: admin@exemplo.pt" \
     -H "X-Basalt-Proxy-Segredo: qualquer-coisa-errada"
# esperado: 401 ou 403
```

Se qualquer um destes devolver 200, **o nginx está a deixar passar o cabeçalho do cliente** (há um `location`
com o `include` do segredo mas sem a linha `proxy_set_header X-Basalt-Email $email;` — o segredo só prova que o
pedido passou pelo nginx, não que o e-mail é verdadeiro; o `include` e a linha do e-mail andam SEMPRE juntos) — corrige o nginx antes de pôr isto em produção. Este é exactamente o
comportamento provado em `testes/test_login_google.py::test_sem_o_segredo_do_proxy_ninguem_entra`, que faz o mesmo
ataque directamente ao servidor Basalt (sem precisar de um nginx real para correr a bateria de testes).

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
