# Prova real — «Traz a tua IA» com as IAs a sério

Os testes automáticos (`testes/test_oauth.py`, `testes/test_ligar.py`) provam o protocolo contra um cliente simulado e a página
num Chromium. O que só se prova com as IAs verdadeiras está aqui. Corre isto numa **instância de teste** (nunca na tua memória
real), num browser real com a sessão normal — não com `curl`.

## Preparar

1. Uma instância nova: `python3 -m basalt iniciar --dados ~/basalt-prova --email prova@exemplo.pt`.
2. Um endereço https público até ela (proxy com certificado ou túnel). No `basalt.toml`:
   `url_publico = "https://prova.exemplo.pt"` e `anfitrioes = ["prova.exemplo.pt"]`. Proxy como em [INSTALAR.md](INSTALAR.md).
3. Umas notas de exemplo (uma com uma frase única, para reconhecer a resposta). Escreve-a como um facto («o código da prova é
   X»), **nunca** como instrução à IA («se uma IA disser X…»): o ChatGPT trata isso como *prompt injection* e bloqueia as escritas.
4. Abre `https://prova.exemplo.pt`, entra, carrega em 🔌 **Ligar a tua IA** → Claude → **Verificar ligação**: tudo com ✓.
5. Deixa o registo do servidor à vista (`journalctl -fu basalt` ou o terminal do `serve`): cada passo deixa uma linha `oauth:`.

## Casos

Para cada um: liga, pede «procura nas minhas notas …» (leitura) e «acrescenta à nota X que …» (escrita), confirma na interface,
depois **Revogar** na página e pede outra vez (tem de falhar ou pedir para voltar a ligar).

| # | Cliente | Como | Ver |
|---|---|---|---|
| 1 | claude.ai, plano Free | Personalizar → Conectores → Adicionar conector personalizado | consentimento do Basalt; `ligacao_aprovada para=claude.ai`; «último uso» na lista |
| 2 | claude.ai, Pro/Max | idem | idem; a renovação (`token_emitido via=renovacao`) depois de 1 h — ✅ 19/10/2026 |
| 3 | Claude Desktop | o mesmo conector (vem da conta) | funciona sem voltar a ligar |
| 4 | Claude no telemóvel | idem | idem |
| 5 | Claude Code, OAuth | `claude mcp add --transport http basalt https://prova.exemplo.pt/mcp` → `/mcp` → Autenticar | regresso a loopback (`para=localhost` ou `127.0.0.1`) |
| 6 | Claude Code, token | «Criar token» na página → `--header "Authorization: Bearer bslt_…"` | o token só se mostra uma vez; revogar corta |
| 7 | ChatGPT, Plus | Definições → Apps → Criar (Modo de programador, se preciso) → OAuth | `para=chatgpt.com`; ✅ 19/10/2026: leitura e escrita (escrita numa conversa sem texto dirigido à IA) |
| 8 | ChatGPT, Business/Pro (se houver) | idem | registar o que cada plano deixa fazer (leitura/escrita) |
| 9 | Codex | «Criar token para o Codex» → bloco no `~/.codex/config.toml` + `BASALT_TOKEN` | `codex` lista as 7 ferramentas |

## Também verificar

- Mudar a senha da conta de prova → todas as ligações desaparecem da lista e as IAs pedem para voltar a ligar.
- Abrir o pedido de ligação noutra conta (outro browser) → recusado («aberto noutra conta»).
- Actualizar o Basalt com o conector do ChatGPT já criado → se faltar uma ferramenta, remover e voltar a adicionar (anotar).
- Versão do protocolo: o Basalt negoceia até `2025-06-18`; anotar se o claude.ai ou o ChatGPT recusam ou avisam.

## Registar

No fim, actualiza a tabela «Traz a tua IA» do [MCP.md](MCP.md) com o que se viu (planos, nomes dos menus, avisos) e a data.
O que falhar abre um problema com a linha `oauth:` do registo (nunca com tokens).
