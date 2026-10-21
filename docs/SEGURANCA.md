# Segurança

## O que o Basalt protege

- **As tuas notas contra perdas.** A interface, o MCP, a importação Obsidian e a arrumação automática escrevem por troca
  atómica compare-and-swap (`renameat2 RENAME_EXCHANGE`): troca-se o ficheiro inteiro e verifica-se o que saiu. Se outro
  processo escreveu no mesmo instante, a escrita dele é reposta e a nossa fica «adiada»; nunca se sobrepõe. Um trinco entre
  processos e um período de graça apanham quem já tinha o ficheiro aberto. O que sai numa troca falhada fica em
  `dados/cas-salvos/`. A Entrada (ideias soltas) tem um trinco próprio, partilhado pela web, pelo MCP, pela CLI e pela arrumação.
- **Histórico.** Com `git = true`, a memória tem fotografias git a cada ciclo de sincronização e em cada fusão,
  arrumação automática e importação. Não é um
  commit por gravação: várias gravações seguidas entre dois ciclos ficam na mesma fotografia. O MCP por stdio escreve na
  cópia e não sincroniza sozinho (ver [MCP.md](MCP.md)).
- **Contra segredos em claro.** Um só guarda (`basalt.segredos`) para todas as entradas: interface, MCP (incluindo
  `guardar_ideia`), sincronização em duas vias, `basalt.sig guarda`, importação Obsidian e anexos de texto. Recusa padrões de
  chaves (Anthropic, OpenAI, GitHub, AWS, Telegram…) e passwords escritas à mão (`senha: …`, `password = …`), também
  entre aspas ou crases, a negrito ou com uma observação a seguir (`senha: "…" (router)`), e em notas com fins de linha
  do Windows (CR LF). Uma data ou uma hora só deixa de contar como password quando a frase fala da validade da própria senha.
  Os tokens de acesso e de renovação do OAuth contam como segredos, como o token pessoal.
- **O teu verificador de segredos, além deste.** Com `[segredos] verificador` no `basalt.toml`, um comando teu também decide,
  em todos os caminhos de entrada e nas listas de notas retidas. Recebe só caminhos de ficheiros (privados, `0600`, numa
  pasta temporária apagada no fim; o texto nunca vai como argumento), até 50 de cada vez, com um ambiente mínimo. Sai `0` =
  limpo, `1` = há segredo (o Basalt divide o lote para saber qual), outra coisa ou passar o `tempo_limite` = não se sabe, e
  aí **não se grava**: a página e a API respondem 503, a Entrada automática volta a tentar, a sincronização adia a nota
  para o ciclo seguinte e as listas tratam como retido o que ainda não tinha sido verificado. As respostas `0`/`1` ficam em
  memória pelo conteúdo, para não verificar duas vezes o mesmo texto.
- **Alertas para onde os vês.** Cada `ALERTA:` (sincronização, verificador que falha…) sai no stderr e, com
  `[avisos] comando`, corre também esse comando em fundo, com a mensagem como último argumento — a mesma do alerta, com o
  que pareça uma password ou chave tapado —, um ambiente mínimo e um tempo limite curto. O registo do servidor tapa
  também os tokens que apareçam nos caminhos e nas mensagens de erro.
- **Notas retidas não saem para a IA.** Uma nota está retida quando tem um segredo em claro — no texto ou no nome do ficheiro
  — ou está em `excluidas.txt`. Fica de fora do índice e dos embeddings, da pergunta com fontes (que olha para a nota tal como
  está agora, não para o índice: uma nota que ganhou um segredo depois de indexada já não dá pedaços), das categorias, da
  arrumação (manual e automática), da fusão, da Entrada automática e da proposta da IA num conflito sem internet (que leva as
  três versões: se alguma tem um segredo, fica só o conflito, para escolheres).
- **Fontes externas só para ler.** Os textos que os comandos de `[fontes_extra]` trazem para o índice abrem-se na página
  (`GET /api/fonte`, `basalt.sig fonte`) sem editar, apagar nem unir, e com o mesmo filtro da procura e da pergunta: um pedaço
  com um segredo em claro não se mostra e uma referência só com pedaços desses dá «não encontrado». Um item cujo título ou referência
  tenha um segredo em claro também não entra no índice (nem se mostra, se já lá estava).
- **Contra agentes de IA desastrados.** Sem ferramenta de apagar. Substituir uma nota exige o `base_hash` da versão lida. O MCP
  recusa substituir uma nota por texto vazio ou só com espaços.
- **Cada utilizador no seu espaço.** Dois utilizadores no mesmo espaço são recusados ao carregar o `basalt.toml`, a não ser
  que `[basalt] espaco_partilhado = true`. `basalt utilizador novo` nunca dá a uma conta nova o espaço de outra. Ligações
  simbólicas com nome de nota ou de anexo não saem do espaço: não se listam, não se servem, não se importam nem exportam.
  A âncora de confiança é a raiz de dados configurada (`dados`, e os caminhos que o administrador põe à mão no `basalt.toml` ou no
  ambiente): tudo o que está por baixo dela abre-se pasta a pasta com `O_NOFOLLOW`, e um ancestral que seja uma ligação recusa a
  leitura, a listagem e a escrita. As escritas CAS conferem a pasta imediatamente antes de trocar o ficheiro; trocar uma pasta
  por uma ligação nesse instante exige escrever na pasta de dados do próprio servidor — quem pode isso já tem as notas todas.
  O mesmo vale para os ficheiros de dados de cada espaço: a Entrada e o seu trinco, os trabalhos do «Unir», as categorias,
  a lista de exclusão, o registo e o trinco da sincronização e o índice da pesquisa não se lêem nem escrevem através de
  uma ligação simbólica (o pedido falha em vez de mostrar os dados de outro espaço).
- **Contas no mesmo browser.** A cópia local (IndexedDB) e a fila sem internet têm dono. Quando entra outra conta no mesmo
  browser, a cópia da anterior sai antes de chegar a nova, e a fila sem internet da anterior fica guardada à parte, em nome
  dela, até voltar a entrar. O servidor recusa (403, sem mexer em nada) um pedido que traga o dono de outra conta, por isso
  um separador antigo nunca grava em nome de quem entrou depois. «Terminar sessão» tira a cópia do browser. Todos os pedidos da
  página levam o dono (`X-Basalt-Dono`; as imagens dos anexos, em `?d=`): sem ele, o servidor responde 428 e não lê nem grava
  nada (só `/api/eu`, que diz o dono, fica de fora). O service worker guarda uma cópia da API por dono e só a serve a esse dono;
  as lápides (notas que deram 404/410) também são por dono, e nunca recuam com respostas fora de ordem (vale a mais recente). Uma edição fica presa à conta com que a nota abriu: se o separador
  passar a ser de outra conta antes de gravar, nada sobe nem entra na fila em nome da nova — a edição fica à parte, em nome da
  conta original, e sobe quando ela voltar. As leituras sem internet confirmam dono, geração e cópia na mesma transacção em que
  lêem as notas. O service worker relê a nota da cópia como última leitura e só a entrega se for a mesma; a página volta a
  verificá-la depois de lido o corpo e decide no fim, sem mais esperas, se a cópia foi entretanto trancada, apagada ou
  passou para outra conta.
  Os agentes e scripts entram por token (`Authorization: Bearer`) e não precisam de dono; um script que use a sessão do browser
  ou o cabeçalho do proxy tem de mandar o `X-Basalt-Dono` que `GET /api/eu` lhe der. Na API, só três pedidos são atendidos
  antes do login e sem dono:
  - `GET /api/saude` (o healthcheck do Docker e da monitorização): responde `{"ok": true}` e mais nada;
  - `POST /api/entrar` (o formulário de entrada, só no modo `senha`): exige o cabeçalho `X-Basalt: 1`, tem um limite de
    tentativas falhadas por IP e só devolve o cookie de sessão a quem acertar no e-mail e na senha;
  - `POST /api/sair`: exige `X-Basalt: 1`, termina a sessão do cookie que vier no pedido (se for válido) e manda o browser
    esquecê-lo — sem uma sessão válida não muda nada.

  Nenhum dos três devolve dados das notas, das contas ou da configuração. Fora da API só são públicos a página de entrar, o
  manifesto e os ícones da PWA.
- **Revogar sem reiniciar.** O servidor relê os utilizadores do `basalt.toml` quando ele muda. Apagar um utilizador ou o hash
  do token MCP vale no pedido seguinte, para as sessões e para o `/mcp`. Cada sessão guarda uma impressão da senha com que
  abriu e só vale com ela: mudar a senha termina as sessões dessa conta, também se a mudares com o servidor parado.
  Um `basalt.toml` que mudou e não carrega (um erro ao editar, sem utilizadores, o ficheiro apagado) **fecha a porta**: ninguém
  entra, nem por token, até ele voltar a carregar — o motivo aparece no registo do servidor. Um pedido que chegue a meio da
  releitura espera por ela. Só os utilizadores (e-mails, senhas, tokens MCP) se relêem; todas as outras opções (modo, `anfitrioes`, proxies,
  `idioma`…) só mudam ao reiniciar o servidor.
- **Apagar uma conta é de vez, aos 30 dias, e uma cópia antiga não a traz de volta** (r17). `basalt utilizador apagar` desliga a
  conta logo (sessões e IAs ligadas caem) e, ANTES de mover a pasta para `apagados/`, regista o apagamento em
  `<dados>/contas-apagadas.json` (`0600`, escrita atómica com `fsync`, trinco próprio; uma ligação simbólica no lugar dele é
  recusada). `basalt apagados eliminar --sim` (o timer `basalt-apagados`, sem rede) apaga de vez o que passou de 30 dias: só
  pastas dentro de `apagados/` com o nome que o registo diz, nunca através de ligações simbólicas, nunca pastas sem registo; tira
  da base OAuth as ligações e os pedidos dessa conta e as sessões que ainda lá estejam; e deixa no registo só o sha256 do e-mail
  (com um prefixo fixo), o `conta_id` e as datas. Cada entrada trata-se à parte: uma que falhe não impede as outras e volta a
  tentar-se no dia seguinte. Uma conta nova com o mesmo e-mail tem outro `conta_id` e não se lhe mexe. O `eliminar` e um
  apagamento (`utilizador apagar`, `reaplicar`) não se cruzam: a pasta move-se e cada entrada decide-se com o trinco do
  `basalt.toml` tomado; uma pasta que não chegou a sair de `<dados>/<espaco>/` fica por eliminar (rc 1) até a moverem para onde
  o registo diz; e `apagados` não pode ser nome de espaço. Depois de repor uma cópia,
  `basalt apagados reaplicar --lista F --sim` volta a apagar as contas que ela trouxe, com a data original (ver
  [INSTALAR.md](INSTALAR.md) § 6). Nenhum dos dois escreve e-mails no terminal nem no registo do sistema: a última linha é sempre
  uma destas, para quem vigia o servidor a ler sem depender do texto (rc 1 se alguma coisa falhou):
  `resumo: a_eliminar=N` (`eliminar` sem `--sim`) · `resumo: eliminadas=N falhas=N voltaram=N` (`eliminar --sim`) ·
  `resumo: reapagaria=N recriadas=N acrescentaria=N protegidas=N` (`reaplicar` sem `--sim`) ·
  `resumo: reapagadas=N recriadas=N acrescentadas=N protegidas=N` (`reaplicar --sim`).
- **Contra CSRF e DNS rebinding.** As escritas da interface exigem o cabeçalho `X-Basalt`. Cookies `HttpOnly; SameSite=Strict`.
  Em modo `aberto`, e em qualquer modo quando `[basalt] anfitrioes` está definido, o servidor confere o `Host` de cada pedido
  e o `Origin` das escritas contra os nomes dele (loopback, o endereço em que escuta e `anfitrioes`). Um nome estranho recebe
  421. O `/mcp` só aceita `Authorization: Bearer` e confere o `Origin` (quando o pedido o traz) contra a mesma lista, com uma diferença:
  sem `[basalt] anfitrioes` e fora do modo `aberto` (modo `senha` ou `cabecalho`, em que o servidor não sabe o seu nome público), o MCP
  aceita também como `Origin` o próprio `Host` que recebeu nesse pedido. Isto não deixa entrar ninguém: o Bearer continua obrigatório,
  e uma página de um site estranho, aberta no browser de alguém, não tem o token MCP (não vai em cookie nem no cabeçalho do proxy), por
  isso o pedido dela recebe 401 mesmo com o `Host` e o `Origin` a combinar. Em modo `aberto` o `Host` do pedido não conta: só loopback e o
  endereço em que escuta. Se o servidor está exposto à rede, define `[basalt] anfitrioes` com o nome público: aí o `Host` e o `Origin`
  têm de estar na lista, em todos os pedidos e em todos os modos. O servidor escuta em `127.0.0.1` por omissão e `validar()` recusa o
  modo `aberto` fora de localhost.
- **Ligações das IAs (OAuth, «Traz a tua IA»).** Só existe com `[basalt] url_publico` https e fora do modo `aberto`; sem isso as
  rotas `/.well-known/*` e `/oauth/*` dão 404. Os endereços de regresso (`redirect_uri`) são uma lista fechada (Claude, ChatGPT,
  loopback e `[oauth] redirects_extra`): um site de phishing não consegue registar-se como cliente para receber o código. Um cliente
  com nome de Claude/ChatGPT/OpenAI/Anthropic que volte para outro sítio é recusado antes do consentimento. PKCE S256 obrigatório, `resource`
  e `iss` conferidos. O consentimento é uma página do Basalt, com a sessão do Basalt (ou do proxy): leva um CSRF preso ao pedido, à
  conta e à sessão; o «Permitir» só se aceita da mesma origem (`Origin`, `Sec-Fetch-Site`), sem `Authorization`; a página tem
  `Content-Security-Policy` estrita (`default-src 'none'`, sem scripts, `frame-ancestors 'none'`; sem `form-action`, que o Chrome aplicaria
  também às redirecções que o claude.ai/ChatGPT fazem a seguir ao «Permitir» e partia a ligação). Um pedido
  fica preso à primeira conta que o abre. Tokens opacos (só o sha256 em `oauth.sqlite`, 0600), presos à conta, ao espaço e à
  senha: mudar a senha ou apagar a conta revoga-os; um `basalt.toml` que não carrega fecha também o `/mcp` sem gastar a renovação.
  Código e renovação são de uso único — o segundo uso revoga a ligação inteira (quem roubou um deles fica sem nada). Os tokens OAuth
  e os pessoais da página só valem no `/mcp`, nunca na `/api`; a página das ligações não aceita tokens (um token não cria nem revoga
  tokens). Limites por IP no registo, nos pedidos pendentes e no `/oauth/token`. O registo do servidor diz quem ligou o quê
  (cliente, anfitrião de regresso, os primeiros caracteres da ligação) e nunca um token.
- **Contra força bruta.** scrypt (n=2¹⁴) nas senhas; limite de tentativas por IP; mensagem igual e o mesmo trabalho scrypt
  para e-mail inexistente e senha errada; sessões com prazo, guardadas só por hash. O IP é o da ligação. O `X-Forwarded-For`
  só conta quando a ligação vem de loopback ou de `[auth] proxies_de_confianca`, e lê-se da direita, para o visitante não
  escolher o próprio IP.
- **Contra fugas de chave.** As chaves de API só entram por variável de ambiente ou ficheiro `0600`. Não aparecem nos erros,
  nem quando o fornecedor as repete numa resposta 401. Um redirect para outro endereço não leva o cabeçalho `Authorization`.
  Os erros não repetem o texto das notas (de um erro do fornecedor de IA só se diz o código, o tipo e uma dica — a mensagem
  dele pode citar o que lhe mandámos), e os erros do MCP não mostram caminhos do disco. O registo HTTP não leva os parâmetros
  do pedido e mascara (ou omite) um caminho com o que parece uma chave, também codificada; um anexo cujo nome tenha o que parece
  uma chave é recusado.

## O que NÃO protege (e o que fazer)

- **TLS**: não faz. Um proxy (nginx/Caddy) com HTTPS à frente — ver [INSTALAR.md](INSTALAR.md).
- **Modo `cabecalho`**: confia no cabeçalho de e-mail que chegue com o segredo do proxy (`X-Basalt-Proxy-Segredo` =
  `BASALT_SEGREDO_PROXY`, obrigatório desde a r16: sem ele, ou com menos de 32 caracteres, o servidor não arranca). O proxy tem
  de **substituir** sempre esse cabeçalho e guardar o segredo num ficheiro só dele; quem souber o segredo e chegar à porta
  escolhe quem é.
- **Modo `senha` atrás de um proxy, sem `anfitrioes`**: o `Host` não se confere (o proxy é que escolhe a quem chega). Põe o
  nome público em `[basalt] anfitrioes` para o servidor recusar os outros.
- **Proxy noutra máquina ou no Docker**: a ligação não vem de loopback, por isso o `X-Forwarded-For` é ignorado e todos os
  visitantes partilham o limite de tentativas do IP do proxy. Põe o IP (ou a rede) do proxy em `[auth] proxies_de_confianca`.
- **Token MCP** = os poderes de escrita do utilizador. Guarda-o no cliente; para revogar, gera outro ou apaga o hash do toml.
- **Uma IA ligada** tem os mesmos poderes que um token: lê e escreve todas as notas da conta (não há âmbito «só ler» na v1). O nome que
  um cliente dá a si próprio no registo dinâmico não se prova — o consentimento mostra o endereço de regresso, que é o que conta.
  Se não pediste a ligação, não carregues em Permitir. Revoga o que não usas na página **Ligar a tua IA**.
- **O Claude e o ChatGPT chegam pela internet.** O `/mcp`, `/.well-known/` e `/oauth/register|token` têm de estar abertos ao mundo (sem
  SSO); a protecção é o token. Se não precisas deles, não ponhas `url_publico`.
- **Cópia no browser de um dispositivo que não volta a ligar-se.** A tranca da cópia local depende de respostas do servidor:
  um portátil que nunca mais fala com ele continua a ler a cópia que tem. Num dispositivo partilhado, usa «Terminar sessão»
  ou «Apagar cópia deste aparelho».
- **Conteúdo das notas**: o Basalt devolve texto; o que uma IA lê numa nota é dado, não instrução — isso é regra do cliente
  MCP. Não guardes nas notas instruções que não queiras que um agente siga.
- **Disco cifrado, cópias de segurança, acesso físico**: do sistema, não do Basalt. O índice e as cópias de segurança guardam
  texto das notas em claro (ver [PRIVACIDADE.md](PRIVACIDADE.md)).
- **Cópias de segurança de contas apagadas.** O Basalt não mexe nas tuas cópias: uma conta apagada continua nelas até rodarem.
  Uma cópia de antes do apagamento também tem o registo de antes, sem essa conta: repô-la sem correr `apagados reaplicar` com a
  lista guardada FORA das cópias traz a conta de volta, e nada dá por isso. (Se só o `basalt.toml` voltar atrás, o `eliminar` dá
  por isso — `voltaram=1`, rc 1 — mas não a apaga sozinho.)
- **O resumo do e-mail não é anónimo.** O registo e a lista do `exportar` guardam o sha256 do e-mail: quem tiver um e-mail em
  mente consegue confirmar se essa conta foi apagada. Trata a lista como um dado pessoal.
- **Contas sem `conta_id`** (criadas antes da r17): o mesmo e-mail chega para as reconhecer, por isso uma conta antiga reposta e
  outra pessoa com o mesmo e-mail num bloco antigo sem `conta_id` confundem-se. Dá um `conta_id` aos blocos que escreveres à mão.

## Reportar

Encontraste uma falha? Abre um *security advisory* privado no repositório (ou escreve ao autor). Não a publiques antes de
haver correcção; respondemos em poucos dias.
