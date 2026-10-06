# Basalt — desenho do pacote público (06/10/2026)

Pedido do José (06/10): «corrigir tudo da minha produção até fazer produto final, para depois divulgar gratuitamente,
com a minha autorização». Logo: o produto é **grátis e auto-alojável** (código aberto). A nuvem paga do PLANO.md
(~/propostas/basalt-publico/PLANO.md) fica para depois e NÃO é condição deste pacote. A divulgação é decisão do José.

## O que é
«A memória que se arruma sozinha, partilhada entre ti e os teus agentes de IA — em Markdown que abres no Obsidian.»
Uma pasta de notas `.md` (um cofre Obsidian) + interface web + os agentes a escrever por MCP, com escrita segura (CAS),
cópia editável sem rede (PWA), pesquisa por significado, arrumação automática, categorias, fusão de notas validada.

## Princípios
1. **Um só código** para a produção do José e para o público: a produção passa a correr o pacote com um `basalt.toml`
   (mais tarde, com recuo). As coisas só do José (Farol por ssh, memórias de outros projectos, Telegram) saem do núcleo e
   ficam como **extensões por configuração** (`[fontes_extra]`, `[entrada.telegram]`), nunca no código.
2. **Só stdlib** no servidor (como hoje). `numpy` opcional (índice semântico); sem embeddings a pesquisa é lexical
   (BM25 em Python puro) — o produto funciona com ZERO IA.
3. **Nada do José no pacote**: nem nomes, nem e-mails, nem domínios, nem caminhos. Verificado por um teste
   (`testes/test_sem_rasto.py`: grep a «lacerda», «inforjota», «José», «geral@», «192.168.», `/home/`).
4. **Espaço** (`Espaco`) = tudo o que hoje são globais de caminhos: `memoria/` (real), `copia/` (editável), `dados/`
   (ledger, trinco, índice sqlite, entrada.jsonl, categorias.json, versões offline, cas-salvos, unir/). Um espaço por
   utilizador: `[[utilizadores]] email = "…", espaco = "nome"`. O servidor resolve o espaço pelo utilizador autenticado
   (thread-local por pedido). V1 corre bem com UM utilizador; vários são só mais linhas no toml.
5. **Autenticação** (`[auth] modo`): `cabecalho` (confia no `X-Basalt-Email` posto por um proxy com SSO — o modo de hoje),
   `senha` (formulário próprio: e-mail + palavra-passe com scrypt, cookie de sessão HttpOnly/SameSite=Strict, limite de
   tentativas), `aberto` (só para `127.0.0.1`). O CSRF `X-Basalt: 1` mantém-se em todos.
6. **IA por fornecedor** (`[ia]`): `nenhum` · `openai` (qualquer endpoint compatível: Ollama, OpenRouter, Mistral, LM Studio)
   · `anthropic` (chave) · `claude-cli` (o `claude -p` isolado de hoje). Papéis: `chat` (pergunta, IA no editor, propostas
   de junção, arrumar, fusão) e `embeddings` (índice). Sem `chat`, a interface esconde o que precisa dele (opções
   contextuais, regra do José). Chaves só por variável de ambiente ou ficheiro 0600 referido no toml, nunca no toml.
7. **Escrita de agentes por MCP** (`basalt mcp`): JSON-RPC 2.0 por stdio (Claude Code, Codex, Cursor) e por HTTP
   (`POST /mcp`, token de portador por utilizador). Ferramentas: `procurar`, `ler_nota`, `escrever_nota` (CAS com
   `base_hash` → conflito em vez de perder), `criar_nota`, `listar_notas`, `guardar_ideia`. Recursos `basalt://nota/<nome>`.
   OAuth para o claude.ai/ChatGPT remoto: fase posterior (documentado).
8. **Obsidian**: a pasta `memoria/` É um cofre. `basalt importar pasta|zip` e `basalt exportar zip`. Anexos (`anexos/`)
   servidos só-leitura; carregar imagens no editor — fase C.
9. **Testes antes de tudo**: a suite pytest (260) muda-se para `testes/` e tem de ficar verde no pacote; cada correcção
   nova traz um teste que falha no antigo. Astra audita o pacote antes de o José o divulgar.
10. **Nunca no 104 nem na GPU do 101** como serviço público. O pacote corre em Docker ou systemd em qualquer Linux.

## Disposição
```
basalt-publico/                  (git; futuro repositório público)
  README.md · README.en.md · LICENCA (MIT — decisão do José) · basalt.toml.exemplo · CHANGELOG.md
  basalt/                        pacote Python (python3 -m basalt …)
    __init__.py · __main__.py · cli.py
    config.py                    toml + ambiente → Config, Espaco, utilizadores, ia, auth
    web.py                       servidor (de basalt_web.py)
    cas.py · duasvias.py · sig.py · categorias.py · unir.py · arrumar.py · auto_arrumar.py
    ia.py                        fornecedores (nenhum/openai/anthropic/claude-cli) + limites
    auth.py                      cabecalho/senha/aberto, sessões, utilizadores
    mcp.py                       servidor MCP (stdio + HTTP)
    lexical.py                   BM25 (pesquisa sem embeddings)
    entrada_telegram.py          opcional
  static/                        index.html, sw.js, manifest, ícones, vendor/ (MIT/ISC, sem internet)
  editor-src/                    fonte do editor (CodeMirror 6, esbuild)
  testes/                        pytest (conftest aponta ao pacote), test_sem_rasto.py
  docker/                        Dockerfile, docker-compose.yml (opcional: ollama)
  sistema/                       basalt-web.service, basalt-vigia.service/.timer (modelos com ${BASALT_DIR})
  docs/                          CONTRATO.md, TESTES.md, PRIVACIDADE.md, TERMOS.md (modelos), MCP.md, OBSIDIAN.md
  site/                          página de entrada PT/EN (estática; só se publica com autorização)
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
- **A — pacote** (06-07/10): esqueleto, `config/Espaco/auth/ia`, mover o código com caminhos por configuração
  (mecânico → `mecanico`), suite verde, CLI, Docker/systemd, README PT/EN.
- **B — MCP**: servidor + testes (stdio e HTTP), docs para Claude Code/Codex/Cursor/claude.ai.
- **C — Obsidian e pequenos**: importar/exportar, anexos, #etiquetas com autocompletar, cancelar a IA a meio, EN na interface.
- **D — antes de divulgar**: `test_sem_rasto`, Astra ao pacote, página de entrada PT/EN, modelos RGPD, relatório ao José
  → **ele autoriza** (ou não) a divulgação; a produção do José migra para o pacote com recuo.
