# Basalt por MCP — os teus agentes de IA a ler e escrever nas tuas notas

O Basalt é um servidor **MCP** (Model Context Protocol): o Claude Code, o Codex, o Cursor, o claude.ai e qualquer outro cliente
MCP podem procurar, ler, criar e corrigir notas — **com as mesmas regras da interface**: trinco, escrita atómica (CAS), recusa de
segredos em claro, histórico git. Um agente nunca sobrepõe o que outro (ou tu) gravou no mesmo instante, e **não há ferramenta para
apagar** notas: apaga-se na interface, com histórico.

## Dois transportes

| | stdio (local) | HTTP (remoto) |
|---|---|---|
| Quem lança | o cliente MCP lança `python3 -m basalt mcp` | o servidor web já está a correr; o cliente fala com `https://…/mcp` |
| Autenticação | nenhuma — é o teu utilizador do sistema | **só** `Authorization: Bearer <token>` (nunca a cookie da sessão) |
| Para quê | Claude Code / Codex / Cursor na mesma máquina ou por SSH | claude.ai, agentes noutra máquina, equipa |

### stdio

```bash
# Claude Code
claude mcp add basalt -- python3 -m basalt mcp --config ~/.config/basalt/basalt.toml --utilizador ana@exemplo.pt

# Codex (~/.codex/config.toml)
[mcp_servers.basalt]
command = "python3"
args = ["-m", "basalt", "mcp", "--config", "~/.config/basalt/basalt.toml", "--utilizador", "ana@exemplo.pt"]

# Cursor / Windsurf / outros (.cursor/mcp.json)
{ "mcpServers": { "basalt": { "command": "python3",
  "args": ["-m", "basalt", "mcp", "--config", "~/.config/basalt/basalt.toml", "--utilizador", "ana@exemplo.pt"] } } }
```

`--utilizador` só é preciso quando o `basalt.toml` tem mais do que um utilizador. O registo vai para stderr; stdout é só protocolo.

### HTTP

1. Cria um token para o utilizador (só se guarda o sha256 no `basalt.toml`; o token mostra-se **uma** vez):
   ```bash
   python3 -m basalt utilizador mcp-token ana@exemplo.pt
   ```
2. No cliente:
   ```bash
   claude mcp add --transport http basalt https://basalt.exemplo.pt/mcp --header "Authorization: Bearer bslt_…"
   ```
   No claude.ai: Definições → Conectores → «Adicionar conector personalizado» com o mesmo URL e cabeçalho.
3. Se o Basalt está atrás de um proxy com login (SSO), `location /mcp` **não** pode exigir esse login — o agente não tem
   browser. Exemplo nginx:
   ```nginx
   location /mcp {                       # sem auth_request: o Basalt valida o Bearer
       proxy_pass http://127.0.0.1:8121;
       proxy_set_header Host $host;
       proxy_set_header Authorization $http_authorization;
       client_max_body_size 1m;
   }
   ```

Regras do `/mcp`: só `POST`; cabeçalho `Origin` (se vier) tem de ser o próprio sítio ou `localhost` (defesa contra DNS rebinding);
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
- Para o revogar, gera outro (`mcp-token`) ou apaga `mcp_token_hash` do utilizador no `basalt.toml`.
- Cada utilizador só vê e escreve no seu espaço (`[[utilizadores]] espaco`), também por MCP.
- O servidor nunca executa o conteúdo das notas; devolve texto. O que um agente lê numa nota é **dado**, não instrução — isso
  é regra do cliente, e vale a pena lembrá-la no prompt do sistema do agente.
- O MCP por HTTP sem TLS só faz sentido em `127.0.0.1`; em rede, sempre `https://` (o proxy à frente trata dos certificados).
