# Alterações

Formato: [Keep a Changelog](https://keepachangelog.com/pt-PT/1.1.0/); versões [SemVer](https://semver.org/lang/pt-BR/).

## [Por lançar]

### 1.0.0b1 — primeira versão pública (beta)
- Porte da instalação de origem (Inforjota, 09/2026) para um pacote só com a biblioteca-padrão do Python:
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
- Robustez (auditorias externas, rondas 5 e 6, da instalação de origem, 07/10/2026): o recuo da fusão («Unir») espera a graça do CAS antes de
  apagar o que saiu e diz, nota a nota, o que não voltou; um erro de disco a meio também recua; a nota mantida é relida depois
  da troca; a geração da cópia local é persistente (IndexedDB + localStorage) e comparada dentro de cada transacção que
  escreve; o service worker (cache `basalt-v8`) ordena pedidos, trancas e lápides por relógio monótono e pela fotografia das
  trancas que existiam ao partir; 404/410 deixam lápide na página e só apagam da cache o que partiu antes; «senha: 2026-10-06»
  conta como password (a excepção data/hora só com contexto de validade). Testes: `testes/test_ronda5.py`, `testes/test_ronda6.py`.
- Robustez, ronda 7 (07/10/2026): uma escrita feita durante o recuo de uma fusão fica em `cas-salvos/` e o erro diz onde; um
  erro de disco ao confirmar o recuo dá o relato nota a nota e não deixa temporários; a gravação sem internet compara a geração
  persistente da cópia, por isso uma edição descartada por outro separador não volta à cópia nova; o service worker (cache
  `basalt-v9`) não guarda respostas que partiram antes de apagar e religar a cópia, não apaga trancas novas com a mesma chave,
  faz a limpeza dentro da fila por endereço e mede a idade das lápides no relógio real; um 404/410 atrasado ou um pacote antigo
  não apagam nem repõem uma nota recriada depois. Testes: `testes/test_ronda7.py`.
- Segurança e instalação, ronda 8 (auditoria externa do pacote, 07/10/2026):
  - **Contas no mesmo browser:** a cópia local e a fila sem internet têm dono (`/api/eu` → `dono`; cabeçalho `X-Basalt-Dono`).
    O servidor recusa com 403, sem trancar a cópia, um pedido de outra conta. A fila de quem saiu fica guardada à parte e é
    enviada quando essa conta volta. «Terminar sessão» tira a cópia do browser. O service worker (cache `basalt-v10`) esvazia a
    cópia da API ao entrar e ao sair. Os anexos revalidam sempre e a etiqueta (ETag) depende do dono.
  - **Um espaço por utilizador:** o `basalt.toml` com dois utilizadores no mesmo espaço é recusado (partilhar só com
    `[basalt] espaco_partilhado = true`); `basalt utilizador novo` nunca reutiliza o espaço de outro.
  - **Revogar sem reiniciar:** os utilizadores relêem-se do `basalt.toml` quando ele muda; apagar um utilizador ou um token vale
    no pedido seguinte e mudar a senha termina as sessões dessa conta. Uma sessão só conta depois de gravada.
  - **DNS rebinding:** em modo `aberto` (e com `[basalt] anfitrioes`) o `Host` e o `Origin` conferem-se; um nome estranho recebe
    421. O MCP confere o `Origin` contra a mesma lista.
  - **Segredos:** um só guarda (`basalt.segredos`) em todas as entradas (interface, MCP, duas vias, `sig guarda`, importação,
    anexos de texto). Notas retidas não vão para a IA em nenhum caminho (índice, pergunta, categorias, arrumação, fusão, Entrada).
  - **Erros:** a chave da IA, o texto das notas e caminhos internos não aparecem nos erros nem nos registos; um redirect para
    outro endereço não leva o `Authorization`.
  - **Escrita:** a Entrada tem um trinco partilhado por todos os processos e carimbos únicos; a importação e a arrumação
    automática escrevem com CAS; ligações simbólicas não saem do espaço; o MCP não esvazia notas.
  - **Configuração:** `--config` vale para tudo no mesmo processo (notas, IA, índice); o `basalt.toml` só é substituído
    depois de validado; `X-Forwarded-For` só de `[auth] proxies_de_confianca` (loopback conta sempre), lido da direita.
  - **Instalação:** `/api/saude` pública para o healthcheck; Docker cria o `basalt.toml` antes do `up` e não anuncia o modo
    aberto; systemd com `/opt/basalt` e `PYTHONPATH`; o timer corre `arrumar --auto` com o mesmo endurecimento; os exemplos MCP
    põem `--config` antes de `mcp`; os recursos estáticos vão todos no pacote instalável; a importação trata anexos em
    subpastas, nomes repetidos e tamanho.
  - Testes: `testes/test_ronda8_*.py`, `testes/test_segredos.py`, `testes/test_instalacao.py`.
- Segurança, ronda 9 (auditoria externa do pacote, ronda 2):
  - **O dono em todos os pedidos da página:** `/api/…` e `/anexos/…` sem `X-Basalt-Dono` recebem 428 (`semDono`,
    `X-Basalt-Sem-Dono: 1`) e nada se lê nem se escreve; só `/api/eu` fica de fora. As imagens dos anexos levam o dono em `?d=`.
    A resposta servida ao dono certo leva-o de volta (`X-Basalt-Dono`). Os agentes (Bearer) não precisam de dono.
    **Muda para scripts:** quem usava a sessão do browser ou o cabeçalho do proxy tem de mandar o `X-Basalt-Dono` de
    `GET /api/eu` — ou passar a um token (`python3 -m basalt utilizador mcp-token EMAIL`; vale também para `/api/`), o caminho recomendado.
  - **Service worker** (cache `basalt-v12`): uma cópia da API por dono; guarda só a resposta que traz o eco do mesmo dono e
    serve-a só a esse dono; um pedido sem dono não guarda nem recebe nada; um 401/403 apaga as cópias de todos os donos.
  - **Página:** sem saber o dono não escreve nada — nem na fila sem internet: o texto fica no editor e a página diz porquê; o
    envio de anexos leva o dono; uma resposta do service worker com outro dono não é aceite; «ficar com a do servidor» tira
    da fila só a revisão que o conflito guardou como «a tua» (o texto igual não chega); uma cópia sem dono gravado (de antes
    da ronda 8) só fica de quem entra se estiver vazia — com edições por enviar, elas ficam à parte, em `pendentes-de:?`, e
    nunca sobem em nome de quem entrou.
  - **Actualizar de uma versão anterior:** abrir a página com rede em cada aparelho até a fila sem internet ficar vazia antes de
    actualizar; o que ficar por enviar numa cópia sem dono não sobe sozinho.
  - **Revogar vale sempre:** cada sessão guarda uma impressão da senha com que abriu e só vale com ela (mudar a senha com o
    servidor parado também termina as sessões); um `basalt.toml` que mudou e não carrega — erro, sem utilizadores, apagado —
    fecha a porta a todos, também aos tokens, até voltar a carregar (antes ficavam os utilizadores anteriores: apagar o último
    utilizador não revogava nada); um pedido a meio da releitura espera por ela. **Ao actualizar:** as sessões abertas antes
    desta versão deixam de valer — cada pessoa entra outra vez uma vez.
  - **Ligações simbólicas:** o que o Basalt abre, lista, serve, importa ou exporta não atravessa uma ligação simbólica em nenhum
    componente do caminho, nem se ela aparecer entre a verificação e a abertura (`basalt.caminhos`: abre pasta a pasta com
    `O_NOFOLLOW`). Uma pasta de anexos que seja uma ligação não se serve, não se lista e não se escreve.
  - **Importar do Obsidian:** os anexos passam pelo mesmo caminho da página (detector de segredos; um anexo com uma password não
    entra e o embed fica como estava); o texto validado é o FINAL, depois de reescrever as ligações; os anexos gravam-se antes
    das notas; `--simular` diz o mesmo que a gravação e não escreve nada. Um nome sozinho resolve para o anexo da pasta da nota
    e um caminho para esse ficheiro; um nome ambíguo salta e deixa o embed. Um anexo com o nome de outro e conteúdo diferente
    entra com outro nome (`foto_2.png`) e a nota aponta para ele.
  - **Anexos sem sobreposição:** a gravação vai por um temporário e uma ligação dura (`link`, que falha se o nome já existir);
    um anexo que apareceu entretanto fica intacto.
  - **Carimbos da Entrada:** nunca se repetem, nem depois de apagar todas as notas nem com o relógio a andar para trás (o último
    fica gravado ao lado da Entrada, com escrita atómica); a ordem é numérica.
  - Testes: `testes/test_ronda9_donos.py`, `testes/test_ronda9_pagina_donos.py`, `testes/test_sw.py` (`TestUmaCachePorDono`),
    `testes/test_ronda9_ficheiros.py`, `testes/test_ronda9_revogar.py`.
