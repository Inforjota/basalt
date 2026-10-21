# Privacidade (RGPD) — modelo para quem instala o Basalt

O Basalt corre na **tua** máquina: não há conta em lado nenhum, não há telemetria, não há servidor nosso. Quem instala é o
responsável pelo tratamento dos dados que lá põe. Este texto é um **modelo** para adaptares se deres acesso a outras pessoas
(família, equipa, clientes). Se fores só tu, não precisas dele.

## O que o Basalt guarda, e onde

No servidor (`<dados>` é a pasta do `basalt.toml`; cada utilizador tem `<dados>/<espaco>/`):

| dados | onde | porquê | quanto tempo |
|---|---|---|---|
| as notas (Markdown) e o seu histórico git | `<espaco>/memoria/` | é o serviço | até as apagares (o git guarda versões antigas: `git log`) |
| anexos (imagens, PDFs, ficheiros colados no editor) | `<espaco>/memoria/anexos/` | é o serviço | idem |
| a cópia editável | `<espaco>/copia/` | edição e sincronização | idem |
| índice de pesquisa: **títulos e excertos das notas em claro** + vectores | `<espaco>/dados/semantica.sqlite` | pesquisa por palavras e por significado | reconstruível com `basalt indexar` (sem as notas apagadas) |
| Entrada (ideias soltas) e o registo das apagadas | `<espaco>/dados/entrada.jsonl`, `entrada-apagadas.jsonl` | arrumação | as apagadas ficam no registo para desfazer, até as tirares |
| versões gravadas sem internet que chocaram com outra | `<espaco>/dados/offline-versoes/` | nada se perde num conflito | até as apagares |
| fusões («Unir») e relatórios da arrumação automática | `<espaco>/dados/unir/`, `auto-arrumar/` | desfazer e explicar | até as apagares |
| cópias de segurança do CAS | `<espaco>/dados/cas-salvos/` | nada se perde numa escrita em simultâneo | 60 dias (`BASALT_CAS_SALVOS_DIAS`) e no máximo 500 ficheiros (`BASALT_CAS_SALVOS_MAX`); a idade conta desde que a cópia foi guardada e as que passaram o prazo apagam-se quando se guarda uma nova |
| nomes das notas usadas (sem conteúdo) | `<espaco>/dados/toques.jsonl` | vistas «ao vivo» | roda aos 2 MB (fica a volta anterior) |
| e-mail, nome, hash da senha, hash do token MCP, `conta_id` (aleatório) | `basalt.toml` | autenticação | enquanto o utilizador existir |
| a pasta de uma conta apagada (notas, anexos, índice…) | `<dados>/apagados/<espaco>-<data>/` | desfazer um apagamento por engano, entregar os dados a quem os pediu | 30 dias: elimina-se de vez com `basalt apagados eliminar --sim` (o timer `basalt-apagados` corre-o todos os dias) |
| registo das contas apagadas: sha256 do e-mail, `conta_id`, datas e, até à eliminação, o e-mail, o espaço e a pasta | `<dados>/contas-apagadas.json` (`0600`) | cumprir o prazo de 30 dias e não deixar uma cópia de segurança reposta trazer a conta de volta | o e-mail, o espaço e a pasta saem ao eliminar (30 dias); o resto fica enquanto houver cópias que possam trazer a conta de volta (tira-o à mão quando já não houver) |
| sessões: hash do cookie, e-mail, hora de criação e de expiração, e uma impressão (não reversível) do hash da senha com que abriram | `<dados>/sessoes.json` | manter o login | até expirarem (`[auth] sessao_horas`), saíres, o utilizador deixar de existir ou mudar de senha; as que já não valem saem do ficheiro ao arrancar o servidor e, durante o uso, no máximo de hora a hora (mesmo que ninguém volte com o cookie delas) |
| tentativas de login falhadas por IP | memória do processo | travar força bruta | esquecem-se as mais velhas que `[auth] janela_minutos` (a cada tentativa, de todos os IPs); perdem-se ao reiniciar |
| registos do servidor (stdout/journal) | onde o systemd/Docker os puser | diagnóstico | rotação do sistema; método, caminho (sem os parâmetros; um caminho com o que parece uma chave sai mascarado ou omitido) e hora de cada pedido; **não** contêm texto de notas nem chaves |

No browser de cada utilizador (para funcionar sem internet):

| dados | onde | quanto tempo |
|---|---|---|
| cópia das notas, fila de edições sem internet e conflitos | IndexedDB do site | até «Terminar sessão», «Apagar cópia deste aparelho», ou entrar outra conta no mesmo browser |
| respostas da API (texto das notas) | cache do service worker | idem |
| anexos já vistos | cache HTTP do browser | o browser decide; antes de mostrar um, pergunta sempre ao servidor se ainda pode |
| fila sem internet de uma conta que saiu (e edições abertas com ela que o separador já não pôde gravar, porque entretanto entrou outra conta) | IndexedDB, guardada à parte em nome dessa conta | até essa conta voltar a entrar no mesmo browser (aí é enviada) ou se limparem os dados do site |
| edições sem internet de que não se sabe a conta (de uma versão anterior da página, ou feitas sem sessão iniciada) | IndexedDB, guardadas à parte | até quem está no browser as enviar em seu nome ou se limparem os dados do site; a página avisa, mostra-as e deixa descarregá-las; «Apagar cópia deste aparelho» não as apaga |

A tranca da cópia no browser depende de respostas do servidor (sessão expirada, conta removida). Um dispositivo que nunca mais
se liga continua a poder ler a cópia que tem. Em computadores partilhados, diz aos utilizadores para usarem «Terminar sessão».

## Para onde vão os dados

- **Para lado nenhum**, por omissão. Sem IA configurada, nada sai da máquina.
- **Com IA configurada** (`[ia]`), o texto das notas envolvidas em cada pedido (resumir, arrumar, pergunta com fontes,
  embeddings) vai para o fornecedor que escolheste. Ollama local: fica em casa. OpenAI/Anthropic/OpenRouter/Mistral: sai
  para esse fornecedor, ao abrigo das condições dele — diz-o aos teus utilizadores e, se forem dados de terceiros, confirma
  que tens base legal e um contrato de subcontratação (art. 28.º RGPD) com o fornecedor.
- **Agentes de IA por MCP**: o cliente (Claude Code, Cursor…) lê e escreve notas em nome do utilizador cujo token usa;
  o que ele faz com o texto é regido pelo fornecedor desse cliente.
- **Verificador de segredos e avisos** (`[segredos] verificador`, `[avisos] comando`): são comandos teus, que correm na
  máquina do servidor. O verificador recebe o texto das notas e ideias a verificar (em ficheiros temporários privados,
  apagados no fim); o comando de avisos recebe só a mensagem do alerta, com o que pareça uma password ou chave tapado. Se
  algum deles enviar alguma coisa para fora (um serviço de notificações, uma API), essa saída é tua e tem de entrar no aviso
  aos utilizadores.

## Direitos das pessoas

Tudo é ficheiro: **acesso** e **portabilidade** = copiar a pasta `memoria/` (Markdown, formato aberto); **rectificação** =
editar; **apagamento** = apagar a nota e os anexos (e, para apagar do histórico, `git filter-repo` ou recriar o repositório),
apagar a Entrada e o seu registo de apagadas, `basalt indexar` para refazer o índice, e apagar as cópias em `cas-salvos/`,
`offline-versoes/` e `unir/`. No browser: «Apagar cópia deste aparelho».

Uma **conta** apaga-se com `basalt utilizador apagar EMAIL`: fica desligada logo (as sessões e as IAs ligadas terminam), a pasta
vai para `apagados/` e elimina-se de vez aos 30 dias (`basalt apagados eliminar --sim`, ou o timer `basalt-apagados`). Nas
cópias de segurança, os dados desaparecem à medida que as cópias rodam; ao repor uma cópia antiga, `basalt apagados reaplicar`
volta a apagar a conta antes de o serviço abrir (ver [INSTALAR.md](INSTALAR.md) §§ 4 e 6). Para a portabilidade, antes de
apagar: `basalt utilizador exportar EMAIL --para dados.zip`. O único admin não se apaga (cria outro antes); se é para desligar
tudo de vez, pára também o servidor.

## Modelo de aviso (adapta e põe onde os teus utilizadores o vejam)

> **Quem trata os teus dados:** [nome / organização, contacto].
> **O quê:** as notas que escreves, o teu e-mail e nome, a hora das sessões. **Para quê:** guardar e procurar as tuas notas.
> **Onde:** num servidor [nosso / alojado em …], na União Europeia. [Com IA: «Para resumir e pesquisar, o texto das notas é
> enviado a … (fornecedor), com quem temos contrato.»]
> **Quanto tempo:** enquanto tiveres conta. Ao pedires o apagamento, a conta é desativada logo e as sessões terminam; os
> dados são eliminados de vez em 30 dias e, nas cópias de segurança, desaparecem à medida que estas rodam (até [N] dias). Se
> tivermos de repor uma cópia antiga, a tua conta apagada não é reposta.
> **Os teus direitos:** acesso, cópia, correcção e apagamento — pede a [contacto]. Podes queixar-te à CNPD (cnpd.pt).
