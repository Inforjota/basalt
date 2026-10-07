# Alterações

Formato: [Keep a Changelog](https://keepachangelog.com/pt-PT/1.1.0/); versões [SemVer](https://semver.org/lang/pt-BR/).

## [Por lançar]

### 1.0.0b1 — primeira versão pública (beta)
- Porte do Basalt de produção (Inforjota, 09/2026) para um pacote só com a biblioteca-padrão do Python:
  `basalt/config.py` (um `basalt.toml`, vários utilizadores, um espaço por utilizador), `auth.py` (cabecalho / senha / aberto;
  tokens Bearer para agentes), `cas.py` (escrita atómica compare-and-swap, nada se perde numa corrida), `duasvias.py`
  (sincronização real ↔ cópia editável), `sig.py` (pesquisa por significado com recurso lexical BM25), `ia.py` (OpenAI-compatível,
  Anthropic, `claude` CLI, Ollama; chave só por variável/ficheiro 0600), `lexical.py`, `web.py` (interface), `mcp.py`.
- MCP (Model Context Protocol): 7 ferramentas (`procurar`, `ler_nota`, `escrever_nota` com `base_hash`, `acrescentar_nota`,
  `criar_nota`, `listar_notas`, `guardar_ideia`) + recursos `basalt://nota/<nome>`; stdio e HTTP (`POST /mcp`, só Bearer).
- Interface offline (service worker + IndexedDB) com trancas por sessão, diagramas Mermaid e fórmulas KaTeX compatíveis com
  o Obsidian, editor CodeMirror 6 com IA.
- Instalação: `python3 -m basalt iniciar`, `serve`; `docker/` (Dockerfile + compose) e `sistema/` (systemd).
- Obsidian: `basalt importar PASTA [--simular]` (nomes, ligações e anexos tratados; nunca sobrepõe) e `basalt exportar PASTA`.
- Editor: colar ou largar ficheiros guarda-os em `memoria/anexos/` e insere `![[nome]]` (`POST /api/anexos?nome=`, `GET /anexos/<nome>`;
  máximo 25 MiB; o mesmo conteúdo fica com o mesmo nome, conteúdo diferente ganha `_2`); autocompletar de `#etiquetas` a partir das
  notas (`GET /api/etiquetas`); o pedido à IA pode ser cancelado a meio (o texto fica como estava).
- Interface em inglês (`[basalt] idioma = "pt" | "en" | "auto"`, ou `BASALT_IDIOMA`): o português fica byte a byte igual; em `auto` o
  servidor escolhe pelo `Accept-Language` (com `Vary: Accept-Language`). A página traduz tudo o que mostra (dicionário `EN`, `t()`/`tf()`);
  depois de mudar o `idioma` é preciso recarregar a página (service worker). O ecrã de entrada (`/entrar`) também sai em inglês. Limitações: as mensagens de erro da API do
  servidor e o prefixo `guarda:` da barra de comandos ficam em português. Testes em `testes/test_idioma.py`.
- Sem rasto do autor original: nenhum nome, domínio, IP ou caminho da instalação de origem no código (teste `test_sem_rasto`).
