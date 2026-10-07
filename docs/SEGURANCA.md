# Segurança

## O que o Basalt protege

- **As tuas notas contra perdas.** Escrita atómica compare-and-swap (`renameat2 RENAME_EXCHANGE`): troca-se o ficheiro inteiro
  e verifica-se o que saiu; se outro processo escreveu no mesmo instante, a escrita dele é reposta e a nossa fica «adiada» —
  nunca se sobrepõe. Um trinco entre processos e um período de graça apanham quem já tinha o ficheiro aberto. O que sai numa
  troca falhada fica em `dados/cas-salvos/`. Histórico git de cada alteração.
- **Contra segredos em claro.** Padrões de chaves (Anthropic, OpenAI, GitHub, AWS, Telegram…), passwords `x = …` e afins
  são recusados ao gravar, na interface, por MCP e na sincronização (ficam «retidos», nunca entram na memória).
- **Contra agentes de IA desastrados.** Sem ferramenta de apagar; escrita só com `base_hash`; cada utilizador no seu espaço.
- **Contra CSRF e DNS rebinding.** Cookies `HttpOnly; SameSite=Strict`; `/mcp` só por `Authorization: Bearer` e com o
  `Origin` verificado; o servidor escuta em `127.0.0.1` por omissão e `validar()` recusa `aberto` fora de localhost.
- **Contra força bruta.** scrypt (n=2¹⁴) nas senhas; limite de tentativas por IP; tempo e mensagem iguais para e-mail
  inexistente e senha errada; sessões com prazo, guardadas só por hash.
- **Contra fugas de chave.** As chaves de API só entram por variável de ambiente ou ficheiro `0600`; nunca aparecem em erros
  nem registos.

## O que NÃO protege (e o que fazer)

- **TLS**: não faz. Um proxy (nginx/Caddy) com HTTPS à frente — ver [INSTALAR.md](INSTALAR.md).
- **Modo `cabecalho`**: confia cegamente no cabeçalho de e-mail. Só com um proxy que o **substitui** sempre; sem isso, qualquer
  visitante escolhe quem é.
- **Token MCP** = os poderes de escrita do utilizador. Guarda-o no cliente; para revogar, gera outro ou apaga o hash do toml.
- **Conteúdo das notas**: o Basalt devolve texto; o que uma IA lê numa nota é dado, não instrução — isso é regra do cliente
  MCP. Não guardes nas notas instruções que não queiras que um agente siga.
- **Disco cifrado, cópias de segurança, acesso físico**: do sistema, não do Basalt.

## Reportar

Encontraste uma falha? Abre um *security advisory* privado no repositório (ou escreve ao autor). Não a publiques antes de
haver correcção; respondemos em poucos dias.
