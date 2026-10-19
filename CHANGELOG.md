# Alterações

Formato: [Keep a Changelog](https://keepachangelog.com/pt-PT/1.1.0/); versões [SemVer](https://semver.org/lang/pt-BR/).

## [Por lançar]

### Portátil, Windows e Mac (19/10/2026)
- O arranque (`iniciar`, `serve`, `mcp`) prova as trocas atómicas na pasta dos dados (`cas.troca_suportada`) e pára com uma mensagem
  clara se não servirem (macOS/Windows sem Linux, `/mnt/c` no WSL, pastas partilhadas) — antes só falhava na 1.ª gravação, e no
  macOS o `import` rebentava. `docs/INSTALAR.md` § 8 «Só com um portátil» (WSL2, Docker com volume, Tailscale Funnel/Cloudflare
  Tunnel, o que muda com a tampa fechada); `docker/compose.volume.yml`; a imagem cria `/dados` do utilizador 1000.

### 1.0.0b1 — primeira versão pública (beta)
- **Traz a tua IA** (OAuth 2.1 para o MCP remoto): o Claude (site, Desktop, telemóvel) e o ChatGPT ligam-se ao `/mcp` sem cabeçalhos,
  e o Claude Code também por OAuth. `basalt/oauth.py` (só stdlib): metadados do recurso e do servidor (RFC 9728/8414), registo
  dinâmico (RFC 7591) com lista fechada de `redirect_uri` (Claude, ChatGPT, loopback, `[oauth] redirects_extra`), PKCE S256, `iss`
  (RFC 9207), `resource` (RFC 8707); tokens opacos em `<dados>/oauth.sqlite` (só o sha256), acesso 1 h, renovação rodada 30 dias,
  reutilização de código ou renovação revoga a ligação; os tokens OAuth só valem no `/mcp`; mudar a senha ou apagar a conta revoga.
  Liga-se com `[basalt] url_publico = "https://…"` (novo, também `BASALT_URL_PUBLICO`; tem de estar em `anfitrioes`) fora do modo
  aberto; sem isso nada muda. O `/mcp` passa a responder `401` com `resource_metadata`; `?access_token=` → `400`.
  Página **Ligar a tua IA** (botão 🔌, `#/ligar`, PT/EN): passos por IA, copiar, «Verificar ligação», tokens pessoais `bslt_`
  (mostrados uma vez, revogáveis, para o Codex), lista de ligações com «Revogar». Testes: `testes/test_oauth.py`, `testes/test_ligar.py`.
  Documentação: `docs/MCP.md`, `docs/SEGURANCA.md`, `docs/INSTALAR.md` (proxy sem SSO nas rotas da IA), `docs/PROVA-REAL.md`.
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
  - **Retidas, mais três caminhos:** uma chave no NOME do ficheiro também retém a nota (índice, categorias, fusão); a pergunta
    com fontes olha para a nota tal como está agora (os pedaços antigos de uma nota que ganhou um segredo, ou que já não
    existe, não servem até à próxima indexação); a proposta da IA num conflito sem internet leva as três versões — com um
    segredo em qualquer delas (ou a nota retida) não se pede, fica só o conflito.
  - **Erros da IA sem o que lhe mandámos:** de um erro HTTP do fornecedor diz-se o código, o tipo (`invalid_request_error`…) e
    uma dica nossa — a mensagem dele nunca, porque pode repetir o texto da nota ou um bocado da chave; o mesmo para o `claude`
    (só se dizem os erros que se reconhecem: sem saldo, sem sessão). O registo de uma proposta recusada já não cita linhas.
  - **Passwords só de letras, falha fechada:** uma maiúscula no meio do valor conta sempre; «senha: X» só fica como texto se X
    for uma palavra corrente da lista (`criptografada`, `sincronizada`…) ou numa descrição explícita («Estado/Tipo/Formato da
    senha: …») com uma palavra até 16 letras — `senha: minhapalavracomplicada` volta a ser uma password.
  - **Juntar uma ideia a uma nota, de uma só vez:** confirmar que a ideia ainda está na Entrada, escrever a nota e arquivá-la corre
    sob o mesmo trinco; duas juntas da mesma ideia a notas diferentes dão um 200 e um 409, e só uma nota muda. Se a ideia não se
    deixar arquivar, a nota volta ao que era e o pedido responde erro. Uma ideia que já não está pendente passa a dar 409 (era 404).
  - **Sessões e tentativas sem restos:** as sessões expiradas, de quem já não existe ou de quem mudou de senha saem do
    `sessoes.json` ao arrancar e, durante o uso, no máximo de hora a hora — mesmo que ninguém volte com o cookie delas. Um disco que
    não deixa gravar não derruba o pedido. As tentativas de login falhadas esquecem-se ao fim da janela, de todos os IPs. As cópias
    de segurança do CAS já saíam aos 60 dias; `docs/PRIVACIDADE.md` diz agora exactamente isso.
  - **`arrumar --auto` como o servidor:** o timer nocturno trata também a Entrada, pelas mesmas funções do vigia do servidor, e depois
    arruma, reindexa e refaz as categorias. Com `--simular` não escreve nada nem chama a IA.
  - **Nomes de anfitrião:** `basalt.exemplo.pt.` (com ponto final) e `Basalt.Exemplo.PT` são a mesma máquina, no `Host`, no `Origin`
    e em `[basalt] anfitrioes`. Com `escutar = "::1"` (ou `"::"`) o servidor escuta mesmo em IPv6, e o endereço que o `iniciar`,
    o `estado` e o arranque mostram vem com [ ] (`http://[::1]:8121`).
  - **Documentação:** o MCP aceita como `Origin` o próprio `Host` quando não há `anfitrioes` — seguro porque o Bearer é obrigatório —
    e recomenda-se definir `anfitrioes` num servidor exposto; o `idioma` e as outras opções que não são utilizadores só mudam ao
    reiniciar.
  - **Cópia sem internet depois de «Apagar a cópia»:** o service worker volta a verificar DEPOIS de guardar uma resposta se a
    cópia foi desligada, se a geração mudou ou se a nota entretanto deu 404/410 — se sim, tira a entrada que acabou de guardar
    (uma nota apagada já não «ressuscita» por um 200 que vinha a caminho); não serve uma entrada mais velha que a lápide da
    nota; e relê a marca de desligada e a geração depois de ler da cache. A página recusa uma resposta da cópia que chegue
    depois de a cópia ter sido apagada («precisa de rede — a cópia deste aparelho foi apagada»). As gravações deste aparelho
    tiram a hora do mesmo contador dos pacotes, na mesma transacção — com o relógio local atrasado, um pacote antigo já não
    repõe texto velho. «Voltar a ligar a cópia» só com um service worker do MESMO protocolo (um mais novo também se recusa).
  - Testes: `testes/test_ronda9_donos.py`, `testes/test_ronda9_pagina_donos.py`, `testes/test_sw.py` (`TestUmaCachePorDono`),
    `testes/test_ronda9_ficheiros.py`, `testes/test_ronda9_revogar.py`, `testes/test_ronda9_ia.py`, `testes/test_ronda9_menores.py`,
    `testes/test_ronda9_copia_apagada.py`.
- Segurança, ronda 10 (auditoria externa do pacote, ronda 3):
  - **A edição fica presa à conta com que a nota abriu:** se o separador passar a ser de outra conta entre abrir e gravar (abriu
    sem internet com a cópia de A e entretanto B entrou), a edição não sobe nem entra na fila da cópia de B — nem com o dono
    novo, nem com o antigo. Fica guardada à parte neste aparelho, em `pendentes-de:<A>`, e sobe em nome de A quando A voltar a
    entrar; a página diz isso e oferece «copiar o meu texto». Sem conta conhecida não se guarda (não se sabe de quem é).
  - **Leitura sem internet numa só transacção:** as notas da cópia lêem-se na mesma transacção da IndexedDB em que se confirma
    o dono, a geração, a tranca e a cópia ligada; a fila sem internet também, e cada envio leva esse dono. Uma troca de conta
    entre as duas leituras já não mostra (nem envia) as notas da outra conta.
  - **Resposta da cópia verificada depois de ler o corpo:** uma resposta da cópia do service worker volta a ver tranca, dono,
    geração e cópia apagada DEPOIS de `res.json()`, antes de chegar à página.
  - **Lápides do service worker por conta:** um 404/410 de uma conta já não tira, sem internet, a cópia de outra conta do mesmo
    URL. As lápides de pedidos sem dono (e as de versões anteriores) continuam a valer para todas.
  - **Revogar sem janela:** ao reler o `basalt.toml`, a lista de utilizadores é substituída ANTES de se publicar a marca que
    dispensa o trinco — nenhum pedido vê a marca nova com a lista antiga (Bearer e cabeçalho do proxy).
  - **Ligações simbólicas em qualquer ancestral:** a âncora de confiança é a raiz de dados configurada (`dados`) — o que está
    por baixo (espaços, notas, anexos, Entrada) abre-se pasta a pasta com `O_NOFOLLOW` a partir dela, também as listagens e as
    leituras (antes partia da pasta do espaço, que podia ser alcançada por uma ligação). Um ancestral que seja uma ligação
    recusa a leitura, a listagem e a escrita. A própria raiz de dados pode ser uma ligação (é escolha de quem instala).
  - **Retenção decide pelo conteúdo actual:** a cache de «nota retida» guarda-se pelo sha256 do texto, não pela hora/tamanho;
    trocar a nota por outra com o mesmo tamanho e a mesma hora já não herda a decisão antiga.
  - **Nomes de anexo com chaves:** um anexo cujo nome tenha o que parece uma chave ou password é recusado pelo guarda (envio
    pela página e importação do Obsidian); o registo HTTP omite os parâmetros e mascara (ou omite) caminhos com o que parece uma chave, também
    codificada (`%5F`); o relatório da importação mascara os nomes saltados.
  - **Sessões expiradas saem do ficheiro ao arrancar** (antes ficavam até à primeira poda durante o uso).
  - **`importar --simular` não fica preso:** uma pasta de anexos inacessível ou que é uma ligação recusa a simulação (como a
    gravação) em vez de procurar `nome_2`, `nome_3`… para sempre; e há um limite de nomes.
  - **Documentação:** `/api/saude` é a única rota da API sem login nem dono (o healthcheck: responde `{"ok": true}` e mais nada); fora da API
    só são públicos a página de entrar, o manifesto e os ícones da PWA.
  - Testes: `testes/test_ronda10.py` (servidor, SW), `testes/test_ronda10_pagina.py` (página, Chromium). Cada um falha em cbf5489.
