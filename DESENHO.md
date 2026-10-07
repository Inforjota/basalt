# Basalt — desenho do pacote público

O Basalt nasceu como a memória de trabalho de uma pessoa e dos seus agentes de IA, em produção desde 2026. Este pacote é a
mesma coisa, limpa de tudo o que era só dessa instalação, para qualquer pessoa a instalar na sua máquina: **grátis e
auto-alojável** (código aberto). Uma nuvem paga não é condição deste pacote e não faz parte dele.

## O que é
«A memória que se arruma sozinha, partilhada entre ti e os teus agentes de IA — em Markdown que abres no Obsidian.»
Uma pasta de notas `.md` (um cofre Obsidian) + interface web + os agentes a escrever por MCP, com escrita segura (CAS),
cópia editável sem rede (PWA), pesquisa por significado, arrumação automática, categorias, fusão de notas validada.

## Princípios
1. **Um só código** para a instalação de origem e para o público: a instalação de origem passa a correr este pacote com um
   `basalt.toml` (com recuo possível). O que era só dela (fontes externas por ssh, outras bases de notas, canais de
   mensagens) saiu do núcleo e fica como **extensão por configuração** (`[fontes_extra]`, `[entrada.*]`), nunca no código.
2. **Só stdlib** no servidor. `numpy` opcional (índice semântico); sem embeddings a pesquisa é lexical (BM25 em Python
   puro) — o produto funciona com ZERO IA.
3. **Nada da instalação de origem no pacote**: nem nomes, nem e-mails, nem domínios, nem caminhos, nem endereços de rede.
   Verificado por um teste (`testes/test_sem_rasto.py`) que corre com a suite.
4. **Espaço** (`Espaco`) = tudo o que eram globais de caminhos: `memoria/` (real), `copia/` (editável), `dados/`
   (ledger, trinco, índice sqlite, entrada.jsonl, categorias.json, versões offline, cas-salvos, unir/). Um espaço por
   utilizador: `[[utilizadores]] email = "…", espaco = "nome"`. O servidor resolve o espaço pelo utilizador autenticado
   (thread-local por pedido). V1 corre bem com UM utilizador; vários são só mais linhas no toml.
5. **Autenticação** (`[auth] modo`): `cabecalho` (confia no `X-Basalt-Email` posto por um proxy com SSO),
   `senha` (formulário próprio: e-mail + palavra-passe com scrypt, cookie de sessão HttpOnly/SameSite=Strict, limite de
   tentativas), `aberto` (só para `127.0.0.1`). O CSRF `X-Basalt: 1` mantém-se em todos.
6. **IA por fornecedor** (`[ia]`): `nenhum` · `openai` (qualquer endpoint compatível: Ollama, OpenRouter, Mistral, LM Studio)
   · `anthropic` (chave) · `claude-cli` (o `claude -p` isolado). Papéis: `chat` (pergunta, IA no editor, propostas
   de junção, arrumar, fusão) e `embeddings` (índice). Sem `chat`, a interface esconde o que precisa dele (opções
   contextuais, nunca um «modo avançado»). Chaves só por variável de ambiente ou ficheiro 0600 referido no toml, nunca no toml.
7. **Escrita de agentes por MCP** (`basalt mcp`): JSON-RPC 2.0 por stdio (Claude Code, Codex, Cursor) e por HTTP
   (`POST /mcp`, token de portador por utilizador; `GET /mcp` → 405). Ferramentas: `procurar`, `ler_nota`, `escrever_nota`
   (CAS com `base_hash` → conflito em vez de perder), `acrescentar_nota`, `criar_nota`, `listar_notas`, `guardar_ideia`.
   Recursos `basalt://nota/<nome>`. Não há ferramenta para apagar. OAuth para clientes remotos: fase posterior (documentado).
8. **Obsidian**: a pasta `memoria/` É um cofre. `basalt importar PASTA` e `basalt exportar PASTA`. Anexos em
   `memoria/anexos/`, servidos só-leitura em `/anexos/<nome>`; colar ou largar um ficheiro no editor guarda-o lá e insere
   `![[nome]]`; `[[ligações]]` e `#etiquetas` autocompletam-se a partir das notas; o pedido à IA pode ser cancelado a meio.
9. **Testes antes de tudo**: a suite pytest vive em `testes/` e tem de ficar verde no pacote; cada correcção nova traz um
   teste que falha no código antigo. Um auditor adversarial (uma IA de outra família) revê o pacote antes de ser divulgado.
10. **Corre em qualquer Linux**, em Docker ou systemd; não depende de nenhuma máquina nem de nenhum serviço da
    instalação de origem.

## Disposição
```
basalt/                          (git; o repositório público)
  README.md · README.en.md · LICENCA · basalt.toml.exemplo · CHANGELOG.md · DESENHO.md
  basalt/                        pacote Python (python3 -m basalt …)
    __init__.py · __main__.py    CLI: iniciar, serve, mcp, utilizador, importar, exportar, indexar
    config.py                    toml + ambiente → Config, Espaco, utilizadores, ia, auth
    web.py                       servidor web (interface + API + /anexos + /mcp por ROTAS_EXTRA)
    cas.py · duasvias.py · sig.py · categorias.py · unir.py · arrumar.py · auto_arrumar.py
    ia.py                        fornecedores (nenhum/openai/anthropic/claude-cli) + limites
    auth.py                      cabecalho/senha/aberto, sessões, tokens Bearer
    mcp.py                       servidor MCP (stdio + HTTP)
    lexical.py                   BM25 (pesquisa sem embeddings)
    obsidian.py · anexos.py · etiquetas.py
    static/                      index.html, sw.js, entrar.html, manifest, ícones, vendor/ (MIT/ISC, sem internet)
  editor-src/                    fonte do editor (CodeMirror 6, esbuild)
  testes/                        pytest (conftest aponta ao pacote), test_sem_rasto.py, testes de página (Chromium)
  docker/                        Dockerfile, docker-compose.yml
  sistema/                       basalt.service (systemd)
  docs/                          INSTALAR.md, MCP.md, OBSIDIAN.md, PRIVACIDADE.md, SEGURANCA.md
  site/                          página de entrada PT/EN (estática; só se publica com autorização do autor)
```

## Configuração (`basalt.toml`)
```toml
[basalt]
dados = "/var/lib/basalt"            # raiz: <dados>/<espaco>/{memoria,copia,dados}
porta = 8121
escutar = "127.0.0.1"
lingua = "pt"

[auth]
modo = "senha"                        # cabecalho | senha | aberto
cabecalho = "X-Basalt-Email"          # só no modo cabecalho

[[utilizadores]]
email = "eu@exemplo.pt"
espaco = "eu"
senha_hash = "scrypt$…"               # gerado por `basalt utilizador senha eu@exemplo.pt`
mcp_token_hash = "…"                  # `basalt utilizador mcp-token …`

[ia]
fornecedor = "openai"                 # nenhum | openai | anthropic | claude-cli
url = "http://127.0.0.1:11434/v1"     # openai: Ollama/OpenRouter/Mistral/LM Studio
chave_env = "BASALT_IA_CHAVE"         # nome da variável com a chave (nunca a chave)
modelo_chat = "qwen2.5:7b"
modelo_embeddings = "nomic-embed-text"
propostas_por_hora = 12
pedidos_ia_por_hora = 40

[fontes_extra]                        # opcional: comandos que imprimem JSON [{fonte, ref, titulo, texto}] para o índice
# comandos = ["/usr/local/bin/as-minhas-tarefas"]
```

## Fases (cada uma deixa o pacote a funcionar e testado)
- **A — pacote**: esqueleto, `config/Espaco/auth/ia`, mover o código com caminhos por configuração, suite verde, CLI,
  Docker/systemd, README PT/EN.
- **B — MCP**: servidor + testes (stdio e HTTP), docs para Claude Code/Codex/Cursor e clientes remotos.
- **C — Obsidian e pequenos**: importar/exportar, anexos (colar/largar), #etiquetas com autocompletar, cancelar a IA a
  meio; interface em inglês.
- **D — antes de divulgar**: `test_sem_rasto`, auditoria adversarial externa do pacote, página de entrada PT/EN,
  modelos de privacidade, relatório ao autor → **ele autoriza** (ou não) a divulgação; a instalação de origem migra para
  o pacote com recuo.
