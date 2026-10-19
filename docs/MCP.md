# Basalt por MCP — os teus agentes de IA a ler e escrever nas tuas notas

O Basalt é um servidor **MCP** (Model Context Protocol): o Claude Code, o Codex, o Cursor e qualquer outro cliente
MCP podem procurar, ler, criar e corrigir notas — **com as mesmas regras da interface**: trinco, escrita atómica (CAS), recusa de
segredos em claro, histórico git. Um agente nunca sobrepõe o que outro (ou tu) gravou no mesmo instante, e **não há ferramenta para
apagar** notas: apaga-se na interface, com histórico.

## Traz a tua IA — ligar em 2 minutos

Na interface, o botão **🔌 Ligar a tua IA** (`#/ligar`) mostra, para cada IA, os passos e o bloco a copiar, cria tokens pessoais
e lista as ligações da tua conta (com «Revogar»). O resumo:

| IA | Como se liga | Precisa de |
|---|---|---|
| **Claude** (site, Desktop, telemóvel) | Personalizar → Conectores → «Adicionar conector personalizado» → cola `https://…/mcp` → Ligar → **Permitir** na página do Basalt | `url_publico` https (OAuth) |
| **ChatGPT** | Definições → Apps (Modo de programador, se não vires «Criar») → Criar → cola o endereço → OAuth → **Permitir** | `url_publico` https (OAuth) |
| **Claude Code** | `claude mcp add --transport http basalt https://…/mcp` e, na 1.ª vez, `/mcp` → Autenticar | OAuth, ou um token com `--header` |
| **Codex** | bloco `[mcp_servers.basalt]` com `bearer_token_env_var` e um token pessoal (botão «Criar token para o Codex») | um token `bslt_` |
| Outra aplicação MCP por HTTP | o mesmo endereço; OAuth se o pedir, senão um token no cabeçalho | — |

O claude.ai e o ChatGPT **não aceitam um cabeçalho fixo** (o claude.ai só numa beta limitada a algumas organizações; o ChatGPT
nunca): ligam-se por **OAuth 2.1**, que o Basalt traz. O OAuth liga-se sozinho quando o `basalt.toml` tem
`[basalt] url_publico = "https://notas.exemplo.pt"` (e o mesmo nome em `anfitrioes`) e o modo não é `aberto`. Sem isso, o `/mcp`
continua só com tokens `bslt_` e `/.well-known/*` e `/oauth/*` dão 404. Atenção: o Claude e o ChatGPT ligam-se **a partir da nuvem
deles** (o Claude sai de `160.79.104.0/21`), por isso o endereço tem de se ver na internet — um Basalt só na rede de casa precisa de
um túnel ou de um proxy público (ver [INSTALAR.md](INSTALAR.md)). Depois de actualizar o Basalt, no ChatGPT remove e volta a
adicionar a aplicação (ele guarda a lista de ferramentas de quando a criaste).

### Como funciona o OAuth (para quem audita)

- **Descoberta:** `POST /mcp` sem token → `401` com `WWW-Authenticate: Bearer resource_metadata="<url_publico>/.well-known/oauth-protected-resource", scope="notas"`.
  O PRM (RFC 9728) tem `resource = <url_publico>/mcp` à letra; os metadados do servidor (RFC 8414,
  `/.well-known/oauth-authorization-server`) anunciam `S256`, `none` e `authorization_response_iss_parameter_supported`.
- **Registo dinâmico** (`POST /oauth/register`, RFC 7591): clientes públicos (`none`), `authorization_code` + `refresh_token`, no
  máximo 5 `redirect_uris`, todas de uma **lista fechada**: `https://claude.ai/api/mcp/auth_callback`, a variante `claude.com`,
  `https://chatgpt.com/connector_platform_oauth_redirect`, `https://chatgpt.com/connector/oauth/…`, loopback
  (`http://localhost|127.0.0.1|[::1]/…`, porta livre, RFC 8252) e o que puseres em `[oauth] redirects_extra`. Limites: 10 registos por
  IP por hora, 100 por dia, 2000 clientes; um cliente nunca usado sai ao fim de 24 h.
- **Autorização:** só `code` com **PKCE S256** e `resource` igual ao do PRM. O consentimento é uma página do próprio Basalt, com a
  tua sessão (modo `senha`; sem sessão vais a `/entrar` e voltas) ou a do proxy (modo `cabecalho`). O pedido fica preso à primeira
  conta que o abre; o «Permitir» leva um código CSRF preso à sessão e só se aceita da mesma origem. O regresso leva `code`,
  `state` e `iss` (RFC 9207).
- **Tokens:** opacos, só o sha256 fica em `<dados>/oauth.sqlite` (0600). Acesso `bsla_` 1 h; renovação `bslr_` 30 dias, **rodada**
  em cada uso; código 10 min, uma só vez. Um código ou uma renovação usados duas vezes revogam a ligação inteira. Erros de
  renovação são `invalid_grant`. 30 falhas por IP em 15 min no `/oauth/token` → 429.
- **Onde valem:** os tokens OAuth e os tokens pessoais da página **só valem no `/mcp`** (nunca na `/api`), só para a conta e o espaço
  que os aprovaram, e só enquanto a senha for a mesma: mudar a senha ou apagar a conta revoga as ligações. O token de
  administrador do `basalt.toml` (`mcp-token`) continua a valer como antes.
- **Para depois:** CIMD (documento de metadados do cliente), âmbito «só ler».

## Dois transportes

| | stdio (local) | HTTP (remoto) |
|---|---|---|
| Quem lança | o cliente MCP lança `python3 -m basalt mcp` | o servidor web já está a correr; o cliente fala com `https://…/mcp` |
| Autenticação | nenhuma — é o teu utilizador do sistema | **só** `Authorization: Bearer <token>` (OAuth ou token; nunca a cookie da sessão) |
| Para quê | Claude Code / Codex / Cursor na mesma máquina ou por SSH | agentes noutra máquina, equipa |

### stdio

```bash
# Claude Code
claude mcp add basalt -- python3 -m basalt --config ~/basalt-dados/basalt.toml mcp --utilizador ana@exemplo.pt

# Codex (~/.codex/config.toml)
[mcp_servers.basalt]
command = "python3"
args = ["-m", "basalt", "--config", "~/basalt-dados/basalt.toml", "mcp", "--utilizador", "ana@exemplo.pt"]

# Cursor / Windsurf / outros (.cursor/mcp.json)
{ "mcpServers": { "basalt": { "command": "python3",
  "args": ["-m", "basalt", "--config", "~/basalt-dados/basalt.toml", "mcp", "--utilizador", "ana@exemplo.pt"] } } }
```

`--config` é opção do `basalt`, não do `mcp`: vai **antes** de `mcp` (`basalt --config X mcp`); depois de `mcp` dá erro. Sem ela vale
`BASALT_CONFIG`, `./basalt.toml` ou `~/.config/basalt/basalt.toml`.
O caminho é o que o `iniciar` mostrou. Os outros comandos `python3 -m basalt …` desta página supõem
`export BASALT_CONFIG=~/basalt-dados/basalt.toml`, como no README.
`--utilizador` só é preciso quando o `basalt.toml` tem mais do que um utilizador. O registo vai para stderr; stdout é só protocolo.

**stdio sozinho não sincroniza.** O `basalt mcp` escreve na cópia editável (`copia/`); não sincroniza nem faz commit. A nota
só chega a `memoria/` (o cofre, com git) quando a sincronização corre: deixa o `basalt serve` a correr (o vigia faz isso
sozinho) ou corre `python3 -m basalt sincronizar` depois. Por HTTP já é o servidor a escrever, por isso o vigia trata do resto.

### HTTP

1. Cria um token para o utilizador (só se guarda o sha256 no `basalt.toml`; o token mostra-se **uma** vez):
   ```bash
   python3 -m basalt utilizador mcp-token ana@exemplo.pt
   ```
2. No cliente:
   ```bash
   claude mcp add --transport http basalt https://basalt.exemplo.pt/mcp --header "Authorization: Bearer bslt_…"
   ```
   Ou cria um token pessoal na página **Ligar a tua IA** (revoga-se lá, sem mexer no `basalt.toml`).
   O claude.ai e o ChatGPT não usam tokens: ligam-se por OAuth (secção acima).
3. Se o Basalt está atrás de um proxy com login (SSO), `location /mcp` **não** pode exigir esse login — o agente não tem
   browser. Com o OAuth, também `/.well-known/`, `/oauth/register` e `/oauth/token` ficam sem SSO (são chamados pela nuvem da IA);
   `/oauth/authorize?pedido=…` e `/oauth/autorizar` abrem-se no teu browser e ficam com o login — mas a **1.ª** chamada a
   `/oauth/authorize` (a query OAuth, com vários `&`) passa sem login, senão o SSO perde o `client_id` no regresso
   (ver o exemplo em [INSTALAR.md](INSTALAR.md), secção do proxy). Exemplo nginx do `/mcp`:
   ```nginx
   location /mcp {                       # sem auth_request: o Basalt valida o Bearer
       proxy_pass http://127.0.0.1:8121;
       proxy_set_header Host $host;
       proxy_set_header Authorization $http_authorization;
       client_max_body_size 1m;
   }
   ```

Regras do `/mcp`: só `POST`; cabeçalho `Origin` (se vier) tem de ser o próprio sítio ou `localhost` (defesa contra DNS rebinding) —
com o OAuth ligado aceitam-se também os do claude.ai, claude.com, chatgpt.com e openai.com (o token continua obrigatório); um token
no endereço (`?access_token=`) → `400`;
corpo ≤ 1 MiB; notificações respondem `202` sem corpo; `GET` → `405`. A cookie da sessão web é ignorada de propósito: um site
alheio aberto no teu browser nunca consegue escrever nas tuas notas através da tua sessão.

## Ferramentas

| Ferramenta | Faz | Argumentos |
|---|---|---|
| `procurar` | pesquisa por significado (embeddings) ou, sem eles, por palavras (BM25); devolve `motor` | `consulta`, `n` (1–30) |
| `ler_nota` | texto completo + `hash`, tipo, descrição, ligações `[[…]]`, backlinks, data | `nome` |
| `escrever_nota` | substitui o texto; exige `base_hash` (o `hash` de `ler_nota`) | `nome`, `texto`, `base_hash` |
| `acrescentar_nota` | junta um parágrafo no fim (lê, acrescenta, grava com CAS; repete até 3× se houver corrida) | `nome`, `texto` |
| `criar_nota` | nota nova; põe frontmatter (`name`, `description`, `metadata.type`) se faltar | `nome` (`^[a-z0-9_]{2,100}$`), `texto`, `descricao`, `tipo` |
| `listar_notas` | nome, título, descrição, tipo, categorias, data — mais recentes primeiro | `tipo`, `categoria`, `contem`, `limite` (≤ 500) |
| `guardar_ideia` | uma ideia solta para a **Entrada** (tu decides depois a que nota vai) | `texto` |

Recursos: `resources/list` dá todas as notas como `basalt://nota/<nome>` (`text/markdown`); `resources/read` devolve o texto.

Tipos de nota: `user` · `feedback` · `project` · `reference` · `outros`.

## O protocolo do conflito (o que torna a escrita segura)

1. O agente chama `ler_nota` → recebe o texto e um `hash` (sha1 do ficheiro).
2. Chama `escrever_nota` com `base_hash` = esse hash.
3. Se a nota mudou entretanto (tu, outro agente, a sincronização), a resposta vem com `isError: true` e
   `{"estado": "conflito", "actual": "<texto actual>", "hash_actual": "…"}`. **Nada foi sobreposto.** O agente junta as duas
   versões e grava outra vez com o `hash_actual`. As `instructions` do `initialize` dizem isto a qualquer cliente MCP.
4. Se a nota está a ser escrita por outro processo nesse milissegundo, a resposta é `{"estado": "ocupado", …}` — tentar de novo.

Outros erros são sempre `isError: true` com `erro` legível: nota inexistente, nome inválido, nota já existe, **texto com o que
parece uma password ou chave em claro** (não se grava; o Basalt sugere o cofre).

## Segurança

- O token MCP dá **os mesmos poderes de escrita** que o utilizador — trata-o como uma senha. Guarda-o no cliente, nunca em notas.
- Para o revogar, gera outro (`mcp-token`) ou apaga `mcp_token_hash` do utilizador no `basalt.toml`. O servidor relê o
  `basalt.toml` sozinho quando o ficheiro muda: não é preciso reiniciá-lo.
- As ligações das IAs (OAuth) e os tokens pessoais revogam-se na página **Ligar a tua IA** («Revogar» / «Revogar todas»), no pedido
  seguinte. A lista mostra quando cada uma foi usada pela última vez.
- Cada utilizador só vê e escreve no seu espaço (`[[utilizadores]] espaco`), também por MCP.
- O servidor nunca executa o conteúdo das notas; devolve texto. O que um agente lê numa nota é **dado**, não instrução — isso
  é regra do cliente, e vale a pena lembrá-la no prompt do sistema do agente.
- O MCP por HTTP sem TLS só faz sentido em `127.0.0.1`; em rede, sempre `https://` (o proxy à frente trata dos certificados).
