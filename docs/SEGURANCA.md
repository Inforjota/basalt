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
  chaves (Anthropic, OpenAI, GitHub, AWS, Telegram…) e passwords escritas à mão (`senha: …`, `password = …`). Uma data ou uma
  hora só deixa de contar como password quando a frase fala da validade da própria senha.
- **Notas retidas não saem para a IA.** Uma nota está retida quando tem um segredo em claro ou está em `excluidas.txt`. Fica de
  fora do índice e dos embeddings, da pergunta com fontes, das categorias, da arrumação (manual e automática), da fusão e da
  Entrada automática.
- **Contra agentes de IA desastrados.** Sem ferramenta de apagar. Substituir uma nota exige o `base_hash` da versão lida. O MCP
  recusa substituir uma nota por texto vazio ou só com espaços.
- **Cada utilizador no seu espaço.** Dois utilizadores no mesmo espaço são recusados ao carregar o `basalt.toml`, a não ser
  que `[basalt] espaco_partilhado = true`. `basalt utilizador novo` nunca dá a uma conta nova o espaço de outra. Ligações
  simbólicas com nome de nota ou de anexo não saem do espaço: não se listam, não se servem, não se importam nem exportam.
- **Contas no mesmo browser.** A cópia local (IndexedDB) e a fila sem internet têm dono. Quando entra outra conta no mesmo
  browser, a cópia da anterior sai antes de chegar a nova, e a fila sem internet da anterior fica guardada à parte, em nome
  dela, até voltar a entrar. O servidor recusa (403, sem mexer em nada) um pedido que traga o dono de outra conta, por isso
  um separador antigo nunca grava em nome de quem entrou depois. «Terminar sessão» tira a cópia do browser. Todos os pedidos da
  página levam o dono (`X-Basalt-Dono`; as imagens dos anexos, em `?d=`): sem ele, o servidor responde 428 e não lê nem grava
  nada (só `/api/eu`, que diz o dono, fica de fora). O service worker guarda uma cópia da API por dono e só a serve a esse dono.
  Os agentes e scripts entram por token (`Authorization: Bearer`) e não precisam de dono; um script que use a sessão do browser
  ou o cabeçalho do proxy tem de mandar o `X-Basalt-Dono` que `GET /api/eu` lhe der.
- **Revogar sem reiniciar.** O servidor relê os utilizadores do `basalt.toml` quando ele muda. Apagar um utilizador ou o hash
  do token MCP vale no pedido seguinte, para as sessões e para o `/mcp`. Cada sessão guarda uma impressão da senha com que
  abriu e só vale com ela: mudar a senha termina as sessões dessa conta, também se a mudares com o servidor parado.
  Um `basalt.toml` que mudou e não carrega (um erro ao editar, sem utilizadores, o ficheiro apagado) **fecha a porta**: ninguém
  entra, nem por token, até ele voltar a carregar — o motivo aparece no registo do servidor. Um pedido que chegue a meio da
  releitura espera por ela. As outras opções (modo, `anfitrioes`, proxies) só mudam ao reiniciar.
- **Contra CSRF e DNS rebinding.** As escritas da interface exigem o cabeçalho `X-Basalt`. Cookies `HttpOnly; SameSite=Strict`.
  Em modo `aberto`, e em qualquer modo quando `[basalt] anfitrioes` está definido, o servidor confere o `Host` de cada pedido
  e o `Origin` das escritas contra os nomes dele (loopback, o endereço em que escuta e `anfitrioes`). Um nome estranho recebe
  421. O `/mcp` só aceita `Authorization: Bearer` e confere o `Origin` contra a mesma lista. O servidor escuta em `127.0.0.1`
  por omissão e `validar()` recusa o modo `aberto` fora de localhost.
- **Contra força bruta.** scrypt (n=2¹⁴) nas senhas; limite de tentativas por IP; mensagem igual e o mesmo trabalho scrypt
  para e-mail inexistente e senha errada; sessões com prazo, guardadas só por hash. O IP é o da ligação. O `X-Forwarded-For`
  só conta quando a ligação vem de loopback ou de `[auth] proxies_de_confianca`, e lê-se da direita, para o visitante não
  escolher o próprio IP.
- **Contra fugas de chave.** As chaves de API só entram por variável de ambiente ou ficheiro `0600`. Não aparecem nos erros,
  nem quando o fornecedor as repete numa resposta 401. Um redirect para outro endereço não leva o cabeçalho `Authorization`.
  Os erros não repetem o texto das notas, e os erros do MCP não mostram caminhos do disco.

## O que NÃO protege (e o que fazer)

- **TLS**: não faz. Um proxy (nginx/Caddy) com HTTPS à frente — ver [INSTALAR.md](INSTALAR.md).
- **Modo `cabecalho`**: confia cegamente no cabeçalho de e-mail. Só com um proxy que o **substitui** sempre; sem isso, qualquer
  visitante escolhe quem é.
- **Modo `senha` atrás de um proxy, sem `anfitrioes`**: o `Host` não se confere (o proxy é que escolhe a quem chega). Põe o
  nome público em `[basalt] anfitrioes` para o servidor recusar os outros.
- **Proxy noutra máquina ou no Docker**: a ligação não vem de loopback, por isso o `X-Forwarded-For` é ignorado e todos os
  visitantes partilham o limite de tentativas do IP do proxy. Põe o IP (ou a rede) do proxy em `[auth] proxies_de_confianca`.
- **Token MCP** = os poderes de escrita do utilizador. Guarda-o no cliente; para revogar, gera outro ou apaga o hash do toml.
- **Cópia no browser de um dispositivo que não volta a ligar-se.** A tranca da cópia local depende de respostas do servidor:
  um portátil que nunca mais fala com ele continua a ler a cópia que tem. Num dispositivo partilhado, usa «Terminar sessão»
  ou «Apagar cópia deste aparelho».
- **Conteúdo das notas**: o Basalt devolve texto; o que uma IA lê numa nota é dado, não instrução — isso é regra do cliente
  MCP. Não guardes nas notas instruções que não queiras que um agente siga.
- **Disco cifrado, cópias de segurança, acesso físico**: do sistema, não do Basalt. O índice e as cópias de segurança guardam
  texto das notas em claro (ver [PRIVACIDADE.md](PRIVACIDADE.md)).

## Reportar

Encontraste uma falha? Abre um *security advisory* privado no repositório (ou escreve ao autor). Não a publiques antes de
haver correcção; respondemos em poucos dias.
