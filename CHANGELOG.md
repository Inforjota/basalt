# Alterações

Formato: [Keep a Changelog](https://keepachangelog.com/pt-PT/1.1.0/); versões [SemVer](https://semver.org/lang/pt-BR/).

## [Por lançar]

### Correcções da ronda 13 (20/10/2026)
- **Mudar a senha só diz que correu bem com o corte das IAs já gravado:** na página e com `basalt utilizador senha`/`novo`,
  as ligações e os tokens pessoais da conta revogam-se na base antes da resposta (a CLI fá-lo antes de mexer no `basalt.toml`;
  se não conseguir, não mexe). Reiniciar e repor o `basalt.toml` antigo já não faz voltar a valer os acessos antigos.
- **Criar um token pessoal ou carregar «Permitir» confere a conta no momento de gravar:** se foi apagada ou mudou entretanto,
  nada se cria, e um token assim nunca passa a valer quando a conta é recriada.
- **Os trincos já não seguem ligações simbólicas:** o da fusão (`.basalt-unir.lock`, botão Unir e auto-arrumar) e o do
  `basalt.toml` abrem-se sem seguir ligações nem truncar; uma ligação no lugar deles faz a operação recusar sem tocar no
  ficheiro para onde aponta (antes, a fusão esvaziava-o).
- **A nota que resulta de uma fusão passa pelo guarda comum de segredos** (passwords em claro e o `[segredos] verificador`);
  verificador falhado = não grava. As notas de entrada também passam pelo guarda antes de irem à IA.
- **Um `[segredos] verificador` lento ou partido já não prende o servidor:** em cada ciclo de sincronização há no máximo uma
  chamada falhada e um ALERTA; as outras notas ficam para o ciclo seguinte. O servidor atende logo no arranque, com a 1.ª
  sincronização em segundo plano.
- **A página já não mostra dados da conta anterior:** as respostas da rede passam pela mesma verificação final que as da cópia
  (conta, «Sair», tranca, geração, época), e a nota, o início, o índice, a entrada e «Unir» voltam a verificar depois de cada
  espera, antes de desenhar. As caches em memória (embeds, pré-visualizações, grafo, salto rápido) e a árvore das notas deixam
  de passar de uma conta para outra.
- **Categorias das fontes externas:** uma ref com `#` (p. ex. `repo#12`) fica sempre com a chave exacta, mesmo sozinha; uma
  chave antiga sem `#` deixa de impedir que os itens novos ganhem categoria.
- **Service worker `basalt-v15`:** a cópia sem rede deixa de servir a página antiga (o protocolo continua 5).

### «Mudar a senha» na interface (20/10/2026)
- Na página **🔌 Ligar a tua IA**, secção «A tua senha» (só no modo `senha`): senha actual + nova (pelo menos 8 caracteres)
  + repetir. O hash novo grava-se no `basalt.toml` de uma vez (temporário 0600, só substitui se carregar) e, ao mudar, **as outras
  sessões, as IAs ligadas por OAuth e os tokens pessoais caem** (voltam a pedir «Permitir»); este browser continua dentro. Senha
  actual errada conta para o mesmo limite de tentativas do «Entrar». Um token de IA não muda a senha (403). No modo `cabecalho`
  (SSO) a secção não aparece. Esqueceste a senha? Continua a ser no terminal: `python3 -m basalt utilizador senha O_TEU_EMAIL`.
- A linha de comandos e a interface escrevem o `basalt.toml` com o mesmo trinco (`basalt.toml.trinco`) e um temporário único.
- Revisão antes de publicar: **a CLI (`utilizador novo/senha/mcp-token`) lê, edita e grava com o trinco tomado** — lia o ficheiro,
  pedia a senha no terminal e gravava o texto velho, desfazendo uma senha mudada na página entretanto (e matando a sessão nova dessa
  pessoa na releitura). **A página só troca a senha se a do ficheiro ainda for a conferida** (compara-e-troca; se o admin a mudou a
  meio, diz-o e manda entrar com a nova). **O ficheiro novo mantém o dono e o grupo do anterior** e o trinco abre-se só para leitura
  quando não se pode escrever — a CLI corrida como root deixava o `basalt.toml` root:root 0600 e o serviço (`User=basalt`), ao
  relê-lo, fechava a porta a toda a gente. Os editores do toml vivem em `basalt/auth.py` (o `__main__` importa-os de lá; o servidor
  já não importa `basalt.__main__`, que sob `python -m basalt` carregava o ficheiro uma segunda vez).

### Segurança, ronda 13 (OAuth) (20/10/2026)
- **Uma ligação ao Claude/ChatGPT não volta a valer quando a conta reaparece:** apagar uma conta (ou mudar-lhe o espaço ou a
  senha) revoga de vez as ligações e os tokens pessoais dela, gravado na base. Recriar a conta com o mesmo email já não os
  ressuscita: é preciso ligar de novo. Também vale para quem apaga a conta com o Basalt parado (o arranque seguinte revoga).
- **Um endereço retirado de `redirects_extra` deixa logo de receber ligações:** os pedidos que já estavam abertos para esse
  endereço deixam de mostrar a página de consentimento e não recebem código (antes só os pedidos novos eram recusados).
- **Uma conta já não consegue encher a base das ligações das outras:** cada ligação renova no máximo 60 vezes por hora (a
  seguinte responde «tenta depois», sem perder a ligação), cada conta tem no máximo 50 ligações activas (a 51.ª pede para
  desligar alguma em 🔌 «Ligar a tua IA»), os tokens antigos de cada ligação são apagados a cada renovação e as ligações
  revogadas antigas deixam de se acumular.
- **Os tokens pessoais da senha antiga já não ocupam a quota de 20:** depois de mudar a senha, a lista vazia já não responde
  «já tens 20 tokens».
- **Com o `basalt.toml` estragado, a IA já não pensa que perdeu a ligação:** a renovação responde «indisponível, tenta depois»
  (503) em vez de «autorização inválida»; depois de corrigires o ficheiro, a ligação continua a funcionar sem a refazer.
- **Proxy com login (nginx):** o exemplo de `docs/INSTALAR.md` § 4 leva agora `auth_request off;` nas rotas que a nuvem da IA
  chama; sem isso, um login definido no bloco `server` continuava a aplicar-se a elas.

### Ronda 13 (servidor)
- **O teu verificador de segredos:** com `[segredos] verificador` no `basalt.toml`, um comando teu também decide o que é
  segredo — ao gravar pela página, pelo MCP ou pelo terminal, na Entrada, na sincronização, na importação, nos anexos de
  texto e nas listas de notas retidas. Recebe só caminhos de ficheiros temporários privados, até 50 de cada vez. Se falhar ou
  demorar, nada se grava: a página explica porquê e a sincronização tenta outra vez no ciclo seguinte. Desligado por omissão.
- **Alertas onde os vês:** com `[avisos] comando`, cada alerta corre também um comando teu (uma notificação no telemóvel, um
  email…), em fundo, com as passwords e chaves tapadas na mensagem. Desligado por omissão.
- **Mais passwords reconhecidas, menos falsos alarmes:** uma password só de letras entre parênteses, chavetas ou colada ao
  «;» seguinte já é recusada; uma linha terminada só em CR (notas antigas do Mac) já não deixa passar a data da linha de
  baixo; «senha: desnecessária (já não usamos)» deixa de ser recusada; os tokens do OAuth contam como segredos.
- **Fontes externas:** uma referência com um segredo não se abre pelos pedaços vizinhos; as categorias ficam presas à
  referência exacta, uma fonte que respondeu vazia perde as dela e os itens novos passam a ser categorizados.
- **Claude CLI sem as tuas chaves:** o `claude` corre com um ambiente mínimo; a chave só lhe chega se a tarefa a declarar.
- **Registos sem segredos:** um erro a meio de um pedido ou o relatório da importação já não mostram chaves nem passwords
  que estejam no caminho ou no nome de um ficheiro.
- **Cada espaço com os seus dados:** a Entrada, os trabalhos do «Unir», as categorias, o índice da pesquisa, o registo da
  sincronização e os trincos deixam de seguir ligações simbólicas — uma ligação posta na pasta de dados de uma conta já não
  mostra nem altera os dados de outra.
- **Documentação:** `docs/SEGURANCA.md` dizia que `/api/saude` era a única rota sem login; `POST /api/entrar` e
  `POST /api/sair` (ambas com `X-Basalt: 1`) também são atendidas antes do login, e agora está escrito o que cada uma faz.

### Actualizar sem perder nada (20/10/2026)
- **As categorias das fontes externas já não se perdem:** o `arrumar` (e `python -m basalt.categorias`) reescrevia o
  `categorias.json` só com as notas do espaço e as chaves `<fonte>:<ref>` de `[fontes_extra]` caíam. Agora mantêm-se: com o índice,
  ficam as que a fonte ainda lá tem (e as de fontes que o índice não conhece); sem índice, ficam todas as que têm `<fonte>:`. As de
  notas apagadas continuam a sair. As fontes externas não são categorizadas pela IA (só preservadas).
- **Uma IA por tarefa:** `[ia.unir]` (fundir notas: o botão Unir e o `arrumar --auto`), `[ia.arrumar]` (decidir os pares de notas
  parecidas do `arrumar --auto`) e `[ia.entrada]` (classificar as ideias da Entrada: o servidor de 3 em 3 minutos e o
  `arrumar --auto`), opcionais, mudam `fornecedor`, `url`, `modelo_chat`, `chave_env`, `chave_ficheiro` e `tempo_limite`; o resto vem
  de `[ia]`. Sem `[ia.entrada]`, a Entrada usa a de `[ia.arrumar]` e, sem essa, a de `[ia]` (como até aqui). Validadas como `[ia]` (uma secção ou um campo desconhecido é recusado no arranque). A chave de `[ia]` só passa para a
  tarefa com o mesmo fornecedor e o mesmo url. Os limites por hora continuam os de `[ia]`. `basalt estado` e a página (`/api/eu`)
  dizem que IA cada tarefa usa e de onde vem. Se a fusão falhar no `arrumar --auto` (IA de fundir desligada ou em baixo), as notas
  ficam ligadas em vez de fundidas. Uma tarefa desligada não leva as outras: com `[ia.arrumar] fornecedor = "nenhum"` e a Entrada
  com IA, o `arrumar --auto` classifica a Entrada, não decide pares e continua a reindexar e a refazer as categorias (antes saltava
  também a Entrada). Sem as sub-tabelas tudo fica como estava.
- **`basalt entrada "texto" [--de FONTE]`:** guarda uma ideia na Entrada a partir de outro programa pelo mesmo trinco da página
  (um programa que acrescentasse linhas ao `entrada.jsonl` à mão perdia-as se a página o reescrevesse no mesmo instante). As mesmas
  regras: corte aos 4000 caracteres, password em claro → código de saída 2 e nada gravado, fonte fora de
  `[entrada] fontes_de_confianca` → fica à espera de aprovação (com aviso). `-` lê o texto do stdin; sem texto lista as ideias por
  arrumar. Um texto que comece por `-` vai depois de `--` (`basalt entrada --de bot -- "-urgente"`). Quem tem um bot ou um script a escrever directamente no ficheiro deve passar a usar este comando (`docs/INSTALAR.md` § 7).
- **As edições por enviar de uma versão anterior da página já não ficam escondidas:** a página anterior não gravava de que
  conta era a cópia sem internet; ao abrir esta versão, o que estava na fila ficava guardado à parte (para não subir em nome
  de outra conta) e não se via em lado nenhum. Agora aparece um aviso «Há N edições guardadas neste aparelho que não chegaram
  a ser enviadas» com **Ver** (a lista, sem enviar nada), **Enviar em meu nome** (sobem como uma gravação normal, com a mesma
  versão de base: se a nota mudou entretanto fica um conflito para resolveres e a tua versão guardada em
  `offline-versoes/`, nunca por cima), **Descarregar** (`.md`, ou `.zip` com várias, feito na página, sem rede) e
  **Esconder aviso** (nada se apaga: o Início continua a mostrar «Edições guardadas à parte neste aparelho (N)»).
  O mesmo vale para o que ficou na fila depois de «Terminar sessão» (antes perdia-se quando entrava uma conta).
  As edições de outra conta conhecida continuam a voltar só quando ela entrar no mesmo browser; o Início diz quantas são,
  nunca o texto. «Apagar cópia deste aparelho» não as apaga.
- **Os resultados de `[fontes_extra]` abrem-se, só para ler:** na procura, nas citações e nas fontes da pergunta, um texto
  vindo de um comando externo abre-se numa vista com o título, a fonte e «só leitura», todos os pedaços que o índice tem
  dessa referência pela ordem, sem editar, apagar nem unir (antes não se abria). Usa o mesmo filtro da procura: um pedaço com
  uma password ou chave em claro não se mostra (só se diz quantos ficaram de fora) e uma referência só com pedaços desses não
  se abre. Novo `python -m basalt.sig fonte FONTE REF [--json]` e `GET /api/fonte?fonte=…&ref=…`; `sig pergunta --json`
  passa a dizer a `fonte` de cada excerto. Os comandos de `[fontes_extra]` não mudam.
- **Título ou referência com um segredo em claro numa fonte externa também não entram:** o filtro só olhava para o texto; agora
  um item de `[fontes_extra]` cujo título ou referência pareça uma password ou chave não é indexado (aviso no ecrã com a fonte e
  a contagem, nunca o conteúdo) e, num índice feito antes, também não sai na procura, nas citações nem na vista só de leitura (404).
- Se voltares a uma versão anterior do Basalt depois de abrir esta: o que ficou guardado à parte não se vê na página anterior,
  e o «Apagar cópia deste aparelho» dela apaga-o. Envia ou descarrega primeiro (ver [INSTALAR.md § 7](docs/INSTALAR.md)).

### Ronda 13 (página)
- **Uma nota apagada ou uma sessão expirada já não aparece sem internet, nem com o pedido a meio:** se a página já tinha
  a nota da cópia nas mãos (a ler o texto ou a fazer as últimas verificações) quando o servidor respondeu «apagada» (404/410) ou
  «sessão expirada» (401) — neste separador ou noutro —, mostrava-a na mesma. Agora o service worker avisa todas as páginas
  abertas antes de acabar de apagar ou trancar, e a página só mostra a cópia se nada mudou desde que a pediu (senão tenta outra
  vez e, sem rede, diz que precisa de rede). O mesmo quando carregas em «Apagar cópia deste aparelho», «Terminar sessão» ou entra
  outra conta.
- **Os separadores abertos com a versão anterior acertam-se sozinhos:** quando esta versão entra, cada separador que ainda tem a
  página anterior recarrega uma vez (a fila por enviar fica onde estava). Se tiveres alterações por gravar no editor, não
  recarrega: aparece «a app foi actualizada — grava o que tens e recarrega a página.» e, até recarregares, esse separador já não
  recebe a cópia sem internet (o que escreves continua lá).
- **Anexos e notas novas ficam na conta com que começaste:** um ficheiro largado ou colado no editor, ou uma nota nova criada na
  janela «Nova nota», subia em nome da conta que tivesse entrado entretanto neste browser (noutro separador). Agora não sobe e
  diz «esta nota foi aberta com outra conta, e entretanto entrou outra neste browser — não gravei em nome da conta nova.»
- **A nota aberta não mostra edições nem conflitos de outra conta:** se outra conta entrasse noutro separador no instante em que
  abrias uma nota, podia aparecer a edição por enviar (ou o conflito) dela com o mesmo nome. Agora só se mostra o que é da conta
  da nota.
- **Uma edição gravada depois de «Apagar cópia deste aparelho» noutro separador não volta à cópia:** o editor diz «a cópia deste
  aparelho foi apagada entretanto — não guardei esta edição nela. Copia o teu texto.»
- **Edições de outra conta deste browser podem descarregar-se:** no Início, ao lado de «N edições de outra conta (conta)», há
  **Descarregar**, que pergunta antes «São edições de outra conta (conta). Descarregar para este aparelho?» e guarda um `.md` (ou um
  `.zip` com várias) feito na página, sem rede. Não as mostra, não as apaga e nunca as envia em nome de quem está.
- **Edições de conta desconhecida: enviar pede confirmação.** Muda o aviso descrito acima: passa a ter só a contagem,
  **Descarregar** e **Esconder aviso**. «Ver» e «Enviar em meu nome» ficam na lista («Edições guardadas à parte neste aparelho
  (N)», no Início), e «Enviar em meu nome» mostra primeiro o título e as primeiras linhas de cada edição — só entram na tua fila
  as que viste ao carregar em «Confirmar e enviar» (uma que chegue entretanto fica à parte).
- **Os textos das fontes externas abrem-se sem internet:** a vista só de leitura de `[fontes_extra]` passa a ficar na cópia deste
  aparelho, marcada «cópia de <data>», com as mesmas regras das notas (apagado na fonte → sai da cópia; sessão expirada → a cópia
  tranca).
- **Telemóvel:** a pesquisa da barra de cima ficava sem espaço (por baixo do 🧭) em ecrãs estreitos; passa a ter uma linha só dela
  e, abaixo de 440 px, o logótipo fica só com a pedra.
- O service worker passa a `basalt-v14` (a cache `basalt-v13` é apagada sozinha na primeira visita e volta a encher-se com o uso);
  as edições por enviar e as notas guardadas pela página ficam, não é preciso fazer nada.

### Segurança: passwords, notas apagadas e cópia sem internet (19/10/2026)
- **Passwords só de letras entre aspas, crases ou com uma observação a seguir:** `senha: "minhapalavracomplicada"`,
  `` senha: `minhaSenhaSecreta` ``, `senha: minhapalavrasecreta (router)` ou `senha: minhapalavrasecreta; utilizador: ana`
  gravavam-se como texto. Agora são recusadas como qualquer outra password — também com «», “”, '', negrito, `,`, `|`, `—` ou
  ` - ` a seguir, e também numa nota com fins de linha do Windows (CR LF), que antes deixava passar qualquer password só de
  letras. «senha: está no cofre», «senha: "criptografada"» e as outras frases que só falam da senha continuam a gravar-se.
- **Uma nota apagada no servidor não volta à cópia sem internet:** com respostas a chegar fora de ordem (rede lenta, vários
  pedidos ao mesmo tempo), a marca «esta nota foi apagada» podia recuar e uma versão antiga da nota ficava guardada e aparecia
  sem internet. Cada resposta 404/410 deixa agora a sua própria marca e vale sempre a mais recente; as marcas que outra mais
  recente da mesma nota já cobre são arrumadas sozinhas (as de mais de 31 dias também, como antes).
- **A cópia sem internet relê a nota antes de a mostrar:** se a nota fosse apagada, a sessão expirasse ou carregasses em
  «Apagar a cópia» enquanto o service worker ainda a estava a ir buscar, ela podia aparecer na mesma. Agora só se mostra se
  continuar exactamente a mesma no fim de todas as verificações.
- **A página decide no fim:** uma nota vinda da cópia sem internet não se mostra se, enquanto a página fazia as últimas
  verificações, a cópia foi trancada (sessão expirada, neste separador ou noutro), apagada ou passou para outra conta.
- O service worker passa a `basalt-v13` (a cache `basalt-v12` é apagada sozinha na primeira visita); o formato da cópia não
  muda, não é preciso fazer nada.

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
