# Privacidade (RGPD) — modelo para quem instala o Basalt

O Basalt corre na **tua** máquina: não há conta em lado nenhum, não há telemetria, não há servidor nosso. Quem instala é o
responsável pelo tratamento dos dados que lá põe. Este texto é um **modelo** para adaptares se deres acesso a outras pessoas
(família, equipa, clientes). Se fores só tu, não precisas dele.

## O que o Basalt guarda, e onde

| dados | onde | porquê | quanto tempo |
|---|---|---|---|
| as notas (Markdown) e o seu histórico git | `<dados>/<espaco>/memoria/` | é o serviço | até as apagares (o git guarda versões antigas: `git log`) |
| a cópia editável | `<dados>/<espaco>/copia/` | edição e sincronização | idem |
| índice semântico (vectores) | `<dados>/<espaco>/dados/` | pesquisa por significado | reconstruível; apaga-se com `basalt indexar` |
| Entrada (ideias soltas) e o registo das apagadas | `<dados>/<espaco>/dados/` | arrumação | as apagadas ficam num registo para desfazer |
| cópias de segurança do CAS | `<dados>/<espaco>/dados/cas-salvos/` | nada se perde numa escrita em simultâneo | 60 dias / 500 ficheiros (configurável) |
| e-mail, nome, hash da senha, hash do token MCP | `basalt.toml` | autenticação | enquanto o utilizador existir |
| sessões (hash do cookie, validade) | `<dados>/sessoes.json` | manter o login | até expirarem (`[auth] sessao_horas`) |
| tentativas de login falhadas por IP | memória do processo | travar força bruta | `[auth] janela_minutos`; perde-se ao reiniciar |
| registos do servidor (stdout/journal) | onde o systemd/Docker os puser | diagnóstico | rotação do sistema; **não** contêm texto de notas nem chaves |
| cópia local no browser (IndexedDB) | o dispositivo de cada utilizador | funcionar sem rede | até o utilizador apagar a cópia (botão na interface) ou a sessão expirar (fica trancada) |

## Para onde vão os dados

- **Para lado nenhum**, por omissão. Sem IA configurada, nada sai da máquina.
- **Com IA configurada** (`[ia]`), o texto das notas envolvidas em cada pedido (resumir, arrumar, pergunta com fontes,
  embeddings) vai para o fornecedor que escolheste. Ollama local: fica em casa. OpenAI/Anthropic/OpenRouter/Mistral: sai
  para esse fornecedor, ao abrigo das condições dele — diz-o aos teus utilizadores e, se forem dados de terceiros, confirma
  que tens base legal e um contrato de subcontratação (art. 28.º RGPD) com o fornecedor.
- **Agentes de IA por MCP**: o cliente (Claude Code, Cursor…) lê e escreve notas em nome do utilizador cujo token usa;
  o que ele faz com o texto é regido pelo fornecedor desse cliente.

## Direitos das pessoas

Tudo é ficheiro: **acesso** e **portabilidade** = copiar a pasta `memoria/` (Markdown, formato aberto); **rectificação** =
editar; **apagamento** = apagar a nota (e, para apagar do histórico, `git filter-repo` ou recriar o repositório), apagar a
Entrada, `basalt indexar` para refazer o índice, e apagar as cópias em `cas-salvos/`. Um utilizador apaga-se removendo o
bloco `[[utilizadores]]` e a pasta do espaço.

## Modelo de aviso (adapta e põe onde os teus utilizadores o vejam)

> **Quem trata os teus dados:** [nome / organização, contacto].
> **O quê:** as notas que escreves, o teu e-mail e nome, a hora das sessões. **Para quê:** guardar e procurar as tuas notas.
> **Onde:** num servidor [nosso / alojado em …], na União Europeia. [Com IA: «Para resumir e pesquisar, o texto das notas é
> enviado a … (fornecedor), com quem temos contrato.»]
> **Quanto tempo:** enquanto tiveres conta; cópias de segurança até [N] dias.
> **Os teus direitos:** acesso, cópia, correcção e apagamento — pede a [contacto]. Podes queixar-te à CNPD (cnpd.pt).
