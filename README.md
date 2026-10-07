# Basalt

**As tuas notas, com memória.** Um cofre de Markdown (compatível com o Obsidian) que procura por significado, responde com fontes,
arruma sozinho as ideias soltas e deixa os teus agentes de IA (Claude Code, Codex, Cursor, claude.ai…) ler e escrever **sem
nunca sobrepor** o que tu escreveste. Funciona sem internet, num só ficheiro Python, na tua máquina.

[English](README.en.md)

## Porquê

- **Ficheiros teus, em Markdown.** A pasta `memoria/` é um cofre Obsidian normal: abres com o Obsidian, com o `vim`, com o que
  quiseres. Nada de bases de dados proprietárias; o histórico é `git`. Já tens um cofre? `basalt importar` traz as notas e os
  anexos sem sobrepor nada ([docs/OBSIDIAN.md](docs/OBSIDIAN.md)).
- **Pesquisa por significado, offline.** Com um modelo de embeddings local (Ollama) procuras «aquela nota sobre o barco das 8h»
  mesmo sem acertar nas palavras. Sem embeddings, a pesquisa é por palavras (BM25) — continua a funcionar.
- **Agentes de IA que não estragam nada.** O servidor MCP dá às IAs sete ferramentas; cada escrita exige o `hash` da versão que o
  agente leu. Se a nota mudou entretanto, o agente recebe o texto actual e junta — nunca sobrepõe. Não há ferramenta para apagar.
- **Nada se perde numa corrida.** Escrita atómica (compare-and-swap com `renameat2`), trinco entre processos, cópias de
  segurança do que sair numa troca. Auditado por uma IA adversarial de outra família durante o desenvolvimento.
- **Segredos nunca entram.** Uma password ou chave em claro numa nota é recusada na gravação.
- **Offline a sério.** A interface guarda uma cópia local (service worker + IndexedDB) com trancas por sessão: editas no
  telemóvel sem rede e sincroniza quando voltar; se a sessão expirar, a cópia local fica trancada.
- **Diagramas e fórmulas** como no Obsidian (Mermaid, KaTeX), editor CodeMirror 6 com IA (resumir, reescrever, continuar —
  e cancelar a meio). Colas ou largas uma imagem ou um PDF no editor e fica em `memoria/anexos/` como `![[nome]]`;
  `[[ligações]]` e `#etiquetas` autocompletam-se a partir das tuas notas.
- **Só a biblioteca-padrão do Python.** Zero dependências obrigatórias; `numpy` opcional para o índice semântico.

## Instalar em 2 minutos

```bash
git clone https://github.com/REPO_POR_DEFINIR/basalt && cd basalt
python3 -m basalt iniciar --dados ~/basalt-dados --email tu@exemplo.pt   # pede a senha; cria basalt.toml e as pastas
export BASALT_CONFIG=~/basalt-dados/basalt.toml                          # os comandos seguintes encontram o ficheiro
python3 -m basalt serve                                                  # http://127.0.0.1:8121
```

Com Docker: primeiro `iniciar` e só depois `docker compose up -d` (o contentor precisa do `basalt.toml`). Como serviço systemd:
`sistema/basalt.service`. Os passos exactos, o proxy com HTTPS e a IA local estão em [docs/INSTALAR.md](docs/INSTALAR.md).

Requisitos: Python ≥ 3.11, `git` (histórico), Linux (o CAS usa `renameat2`). Opcional: Ollama para embeddings e IA local.

## Configurar

Tudo num `basalt.toml` ([exemplo comentado](basalt/basalt.toml.exemplo)): pasta dos dados, porta, modo de autenticação
(`senha` · `cabecalho` para quem já tem SSO num proxy · `aberto` só em localhost), utilizadores (cada um com o seu espaço),
IA (`openai`-compatível como Ollama/OpenRouter/Mistral, `anthropic`, ou o `claude` CLI), embeddings, categorias, Entrada.
As chaves de API **nunca** vão para o ficheiro: só por variável de ambiente ou ficheiro `0600`.
A interface está em português e também em inglês: `[basalt] idioma = "pt" | "en" | "auto"` (ou `BASALT_IDIOMA`); depois de mudar, recarrega a página.

```bash
python3 -m basalt utilizador novo ana@exemplo.pt --nome Ana        # + senha
python3 -m basalt utilizador mcp-token ana@exemplo.pt              # token para os agentes (mostra-se uma vez)
python3 -m basalt indexar                                          # (re)constrói o índice semântico
python3 -m basalt importar ~/O-meu-cofre --simular                # trazer um cofre Obsidian (primeiro só ver o que faria)
python3 -m basalt exportar ~/cofre-exportado                       # levar a memória para uma pasta (abre-se no Obsidian)
python3 -m basalt arrumar                                          # Entrada → notas, ligações, categorias (de noite, por timer)
```

## Os teus agentes de IA

```bash
claude mcp add basalt -- python3 -m basalt --config ~/basalt-dados/basalt.toml mcp --utilizador ana@exemplo.pt
```

Depois, no Claude Code: «procura nas minhas notas o que decidi sobre o servidor», «acrescenta à nota `projecto_x` que …».
Codex, Cursor, claude.ai (por HTTP com token) e o protocolo de conflito: [docs/MCP.md](docs/MCP.md).

## Como está arrumado

```
basalt/        config · auth · cas · duasvias · sig (pesquisa) · ia · lexical · unir · arrumar · web · mcp
static/        interface (um HTML, service worker)
testes/        pytest (+ Chromium para a interface)
docs/          INSTALAR · MCP · SEGURANCA
docker/  sistema/
```

Cada espaço de utilizador: `memoria/` (o cofre, git), `copia/` (a cópia editável que a interface usa), `dados/` (índice,
Entrada, ledger, cópias do CAS). A sincronização `memoria ↔ copia` corre nos dois sentidos com CAS; conflitos ficam
marcados, nunca resolvidos às cegas.

## Segurança, em duas linhas

Escuta em `127.0.0.1` por omissão; para a rede, um proxy com HTTPS à frente. Sessões HttpOnly/SameSite=Strict, scrypt,
limite de tentativas por IP. O MCP por HTTP só aceita `Authorization: Bearer` (nunca a cookie — um site alheio no teu browser
não escreve nas tuas notas). Mais em [docs/SEGURANCA.md](docs/SEGURANCA.md); dás acesso a outras pessoas? [docs/PRIVACIDADE.md](docs/PRIVACIDADE.md) (RGPD, modelo de aviso). Encontraste algo? Abre um *issue* privado.

## Licença

MIT — ver [LICENCA](LICENCA). Feito nos Açores por [Inforjota](https://inforjota.com).
