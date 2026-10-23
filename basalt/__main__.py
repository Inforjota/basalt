"""python3 -m basalt — a linha de comandos do Basalt.

  iniciar      cria o basalt.toml, as pastas e o primeiro utilizador
  serve        o servidor web (+ /mcp)
  utilizador   novo · senha · mcp-token · listar · apagar · exportar  (edita o basalt.toml no sítio: só a chave pedida, o resto fica igual)
  apagados     listar · eliminar · exportar · reaplicar  (as contas apagadas: eliminar de vez aos 30 dias, cópias de segurança)
  indexar      (re)constrói o índice semântico do(s) espaço(s)
  procurar     pesquisa por significado (ou por palavras)
  pergunta     resposta com fontes (precisa de IA)
  sincronizar  memória ↔ cópia editável, nos dois sentidos (CAS)
  arrumar      Entrada → notas, ligações, categorias
  entrada      guardar uma ideia na Entrada (o mesmo trinco e as mesmas regras da página) · sem texto: listá-las
  importar     trazer um cofre Obsidian / pasta de Markdown (nunca sobrepõe)
  exportar     copiar a memória para uma pasta (abre-se no Obsidian)
  mcp          servidor MCP por stdio (Claude Code, Codex, Cursor…)
  estado       o que está configurado e quantas notas há
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from . import __version__, caminhos, config
from .auth import (_BLOCO_RX, _bloco_de, _toml_str, acrescentar_utilizador, bloco_utilizador,   # noqa: F401 (re-exportados: testes e
                   definir_chave_utilizador, editar_toml, gravar_toml, remover_utilizador,         #  quem importava daqui)
                   revogar_sessoes, trinco_toml)
from .config import Config, Utilizador

EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _falha(msg):
    raise SystemExit(f"basalt: {msg}")


# ───────────────────────────── editar o basalt.toml sem o reescrever todo ─────────────────────────────
# ronda 13: os editores (definir_chave_utilizador, bloco_utilizador, acrescentar_utilizador, trinco_toml, gravar_toml, editar_toml)
# vivem em basalt/auth.py — a página («Mudar a senha») também os usa, e importar este ficheiro de dentro do servidor duplicava-o.


def _ler_toml(caminho: Path) -> str:
    try:
        return caminho.read_text(encoding="utf-8")
    except OSError as e:
        _falha(f"não consigo ler {caminho}: {e.strerror}")


def _escrever_toml(caminho: Path, texto: str):
    """o ficheiro INTEIRO (só o `iniciar`, que o cria). Para mudar uma chave num ficheiro que já existe é `_editar_toml`."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    try:
        with trinco_toml(caminho):    # a interface («Mudar a senha») também escreve: um de cada vez
            gravar_toml(caminho, texto)   # tem de carregar ANTES de substituir o verdadeiro; senão ficava um toml inválido no sítio
    except (config.ConfigInvalida, ValueError, OSError) as e:   # ValueError = toml mal formado
        _falha(f"o basalt.toml ficaria inválido ({e}) — não mexi em {caminho}")


def _editar_toml(caminho: Path, editar, cortadas=(), depois=None):
    """ronda 13: ler → editar → gravar com o trinco tomado (ver auth.editar_toml). Antes lia-se o texto no início do comando e, se a
    página mudasse uma senha enquanto o terminal esperava pela nossa, a gravação do texto velho desfazia-a.
    ronda 13c: `cortadas` = os e-mails cujas ligações das IAs o `editar` já revogou na base (ronda 13b: antes de o toml mudar). Se a
    gravação falhar depois disso, o corte fica (é a direcção segura) e o aviso di-lo — antes dizia só «não mexi no basalt.toml».
    r17: `depois()` corre com o trinco ainda tomado, depois de o toml estar gravado (não pode lançar: o toml JÁ mudou)."""
    try:
        editar_toml(caminho, editar, depois)
    except KeyError as e:
        _falha(f"utilizador desconhecido: {e.args[0] if e.args else ''}")
    except (config.ConfigInvalida, ValueError, OSError) as e:   # ValueError = toml mal formado
        corte = (f"; atenção: as ligações das IAs de {', '.join(cortadas)} já ficaram cortadas na base (isso fica — voltam a "
                 "pedir «Permitir»), o resto não mudou — repete o comando" if cortadas else "")
        _falha(f"o basalt.toml ficaria inválido ({e}) — não mexi em {caminho}{corte}")


def _revogar_ligacoes(dados, email: str) -> int:
    """ronda 13b: dentro do trinco do basalt.toml, ANTES de ele mudar — as ligações das IAs desta conta (OAuth e tokens pessoais)
    ficam revogadas já na base do servidor. Se não der, o toml não muda (o comando falha e diz porquê)."""
    import sqlite3
    from . import oauth
    try:
        return oauth.revogar_na_base(dados, email)
    except (oauth.Ocupado, sqlite3.Error, OSError) as e:
        _falha(f"não consegui revogar as ligações das IAs de {email} na base ({e}) — não mexi no basalt.toml "
               "(tenta outra vez, com o utilizador do serviço)")


def _cfg(a):
    """a configuração de --config/BASALT_CONFIG, ACTIVADA para todo o processo (ambiente dos filhos + IA de [ia]). Ronda 8 (--config para tudo): cada
    subcomando lia as notas de --config mas a IA, o sig e as fontes_extra reliam a configuração pelo ambiente — podia ser outra."""
    return config.activar(config.carregar(_caminho_config(a)))


def _slug_espaco(email: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", email.split("@")[0].lower()).strip("_") or "utilizador"


def _espaco_para(cfg, email: str, pedido: str | None) -> str:
    """o espaço de um utilizador novo: o pedido (--espaco) ou um derivado do e-mail — e NUNCA o de outro utilizador por acaso (ronda 8, um espaço por utilizador):
    «ana@a.pt» e «ana@b.pt» não podem cair os dois em «ana». Se o derivado já é de alguém leva _2, _3…; um --espaco de outro
    recusa-se (partilhar é explícito: [basalt] espaco_partilhado = true)."""
    donos = {u.espaco: u.email for u in cfg.utilizadores if u.email.lower() != email.lower()}
    if pedido:
        if pedido in donos and not cfg.espaco_partilhado:
            _falha(f"o espaço «{pedido}» já é de {donos[pedido]} — cada utilizador tem o seu (partilhar só com [basalt] espaco_partilhado = true)")
        return pedido
    base = _slug_espaco(email)
    n, i = base, 1
    while n in donos or n in config.ESPACOS_RESERVADOS:     # r17: «apagados@…» não pode cair na pasta das contas apagadas
        i += 1
        n = f"{base}_{i}"
    return n


def _caminho_config(a) -> Path:
    p = config._ficheiro(a.config) if hasattr(config, "_ficheiro") else (Path(a.config) if a.config else None)
    if p is None or not Path(p).exists():
        _falha("não encontro o basalt.toml (usa --config, BASALT_CONFIG, ./basalt.toml ou ~/.config/basalt/basalt.toml; ou `basalt iniciar`)")
    return Path(p)


def _senha(a, confirmar=True) -> str:
    s = a.senha or os.environ.get("BASALT_SENHA") or ""
    if not s:
        if not sys.stdin.isatty():
            _falha("sem terminal: passa --senha ou BASALT_SENHA")
        s = getpass.getpass("senha (mín. 8): ")
        if confirmar and getpass.getpass("outra vez: ") != s:
            _falha("as senhas não coincidem")
    if len(s) < 8:
        _falha("a senha tem de ter pelo menos 8 caracteres")
    return s


def _email(e: str) -> str:
    e = (e or "").strip().lower()
    if not EMAIL_RX.match(e):
        _falha(f"e-mail inválido: {e!r}")
    return e


def _espacos(cfg: Config, a):
    """os espaços em que um comando trabalha: --utilizador, senão todos os utilizadores (ou o espaço de omissão)."""
    if getattr(a, "utilizador", ""):
        u = cfg.utilizador(_email(a.utilizador)) or _falha(f"utilizador desconhecido: {a.utilizador}")
        return [cfg.espaco_de(u)]
    return [cfg.espaco_de(u) for u in cfg.utilizadores] or [cfg.espaco()]


def _git_init(esp):
    if (esp.real / ".git").exists():
        return
    try:
        subprocess.run(["git", "init", "-q", str(esp.real)], check=True, capture_output=True)
        subprocess.run(["git", *config.git_c(), "-C", str(esp.real), "commit", "-q", "--allow-empty", "-m", "basalt: início"],
                       check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"aviso: sem git em {esp.real} ({e}); o histórico fica desligado até instalares o git", file=sys.stderr)


# ───────────────────────────── comandos ─────────────────────────────
def _verificar_trocas(cfg: Config, a):
    """as escritas seguras (CAS) precisam de trocas atómicas do Linux na pasta dos dados — provar antes de arrancar."""
    from . import cas
    for esp in _espacos(cfg, a):
        for pasta in (esp.real, esp.copia):
            pasta = pasta if pasta.exists() else pasta.parent
            motivo = cas.troca_suportada(pasta)
            if motivo:
                _falha(f"a pasta {pasta} não serve para os dados: {motivo}.\n"
                       "  O Basalt precisa de Linux e de um disco Linux (ext4, xfs, btrfs, zfs, tmpfs…).\n"
                       "  Windows: dentro do WSL2, com os dados no Linux (~/basalt-dados), nunca em /mnt/c.\n"
                       "  Mac: com o Docker, num volume do Docker (docker/compose.yml), não numa pasta partilhada do Mac.\n"
                       "  Ver docs/INSTALAR.md, «Só com um portátil».")


def cmd_iniciar(a):
    from . import auth
    dados = Path(a.dados or "./basalt-dados").expanduser().resolve()
    caminho = Path(a.config).expanduser().resolve() if a.config else dados / "basalt.toml"
    if caminho.exists() and not a.forcar:
        _falha(f"{caminho} já existe (usa --forcar para o substituir — os teus dados não são tocados)")
    email = _email(a.email) if a.email else ""
    modo = a.modo or ("senha" if email else "aberto")
    texto = config.exemplo()
    texto = re.sub(r'(?m)^dados\s*=\s*"[^"]*"', f"dados = {_toml_str(str(dados))}", texto, count=1)
    texto = re.sub(r'(?m)^modo\s*=\s*"[^"]*"', f"modo = {_toml_str(modo)}", texto, count=1)
    texto = re.sub(r'(?m)^escutar\s*=\s*"[^"]*"', f"escutar = {_toml_str(a.escutar)}", texto, count=1)
    texto = re.sub(r'(?m)^porta\s*=\s*\d+', f"porta = {int(a.porta)}", texto, count=1)
    if a.sem_ia:
        texto = re.sub(r'(?m)^fornecedor\s*=\s*"[^"]*"', 'fornecedor = "nenhum"', texto)
    # o bloco de exemplo sai; entra o utilizador pedido (ou nenhum, em modo aberto)
    texto = _BLOCO_RX.sub("", texto, count=1)
    if email:
        h = auth.hash_senha(_senha(a)) if modo == "senha" else ""
        texto = acrescentar_utilizador(texto, bloco_utilizador(email, a.espaco or _slug_espaco(email),
                                                               a.nome or email.split("@")[0], True, h))
    dados.mkdir(parents=True, exist_ok=True)
    os.chmod(dados, 0o700)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    _escrever_toml(caminho, texto)
    cfg = config.carregar(caminho)
    for esp in _espacos(cfg, argparse.Namespace(utilizador="")):
        esp.criar()
        if cfg.git:
            _git_init(esp)
    _verificar_trocas(cfg, argparse.Namespace(utilizador=""))
    print(f"✓ {caminho}\n✓ dados em {dados}" + (f"\n✓ utilizador {email} ({modo})" if email else f"\n· modo {modo}, sem utilizadores (só localhost)"))
    print(f"\nA seguir:\n  python3 -m basalt --config {caminho} serve   → http://{config.endereco(a.escutar, a.porta)}\n"
          f"  python3 -m basalt --config {caminho} mcp     → para os agentes (ver docs/MCP.md)")


def cmd_serve(a):
    cfg = _cfg(a)
    try:
        cfg.exigir_segredo_proxy()          # r16: ex. modo cabecalho sem o segredo do proxy — antes de tudo, numa linha, sem traceback
    except config.ConfigInvalida as e:      # (o web.criar_servidor volta a verificar: é ele que guarda o /mcp e os testes)
        _falha(f"o servidor não arranca: {e}")
    _verificar_trocas(cfg, a)
    try:
        from . import web
    except ImportError as e:
        _falha(f"o servidor web ainda não está neste pacote ({e})")
    web.servir(cfg)


def cmd_utilizador(a):
    from . import auth
    caminho = _caminho_config(a)
    texto = _ler_toml(caminho)            # só para conferir ANTES de pedir a senha; a edição relê o ficheiro dentro do trinco (ronda 13)
    if a.sub == "listar":
        cfg = config.carregar(caminho)
        for u in cfg.utilizadores:
            print(f"{u.email:40} espaço={u.espaco:16} {'admin ' if u.admin else '      '}{'senha ' if u.senha_hash else '      '}{'mcp' if u.mcp_token_hash else ''}")
        if not cfg.utilizadores:
            print("(sem utilizadores — modo aberto)")
        return
    email = _email(a.email)
    if a.sub == "novo":
        if _bloco_de(texto, email):
            _falha(f"{email} já existe (usa `utilizador senha`)")
        cfg = config.carregar(caminho)
        h = auth.hash_senha(_senha(a)) if cfg.auth.modo == "senha" else ""
        esp = _espaco_para(cfg, email, a.espaco)
        bloco = bloco_utilizador(email, esp, a.nome or email.split("@")[0], bool(a.admin), h)
        cortadas = []

        def novo(t):
            if _bloco_de(t, email):           # apareceu entretanto
                _falha(f"{email} já existe (usa `utilizador senha`)")
            _revogar_ligacoes(cfg.dados, email)   # ronda 13b: ligações de uma conta antiga com este e-mail não passam para a nova
            cortadas.append(email)
            return acrescentar_utilizador(t, bloco)
        _editar_toml(caminho, novo, cortadas)
        cfg = config.carregar(caminho)
        e = cfg.espaco_de(cfg.utilizador(email)).criar()
        if cfg.git:
            _git_init(e)
        print(f"✓ {email} criado (espaço «{esp}»)")
        return
    if not _bloco_de(texto, email):
        _falha(f"utilizador desconhecido: {email}")
    if a.sub == "senha":
        h = auth.hash_senha(_senha(a))
        try:
            dados = config.carregar(caminho).dados
        except (config.ConfigInvalida, ValueError, OSError) as e:
            _falha(f"o basalt.toml não carrega ({e}) — não mexi em {caminho}")

        cortadas = []

        def senha(t):
            novo = definir_chave_utilizador(t, email, "senha_hash", h)
            _revogar_ligacoes(dados, email)   # ronda 13b: gravado antes de o toml mudar (o servidor pode reiniciar antes de o reler)
            cortadas.append(email)
            return novo
        _editar_toml(caminho, senha, cortadas)
        print(f"✓ senha de {email} guardada")
    elif a.sub == "mcp-token":
        token, h = auth.gerar_token()
        _editar_toml(caminho, lambda t: definir_chave_utilizador(t, email, "mcp_token_hash", h))
        print(f"token MCP de {email} (mostra-se UMA vez; guarda-o no cliente, nunca numa nota):\n\n  {token}\n")
        print('  claude mcp add --transport http basalt https://O-TEU-BASALT/mcp --header "Authorization: Bearer ' + token + '"')
    elif a.sub == "apagar":
        _apagar_utilizador(caminho, email, a)
    elif a.sub == "exportar":
        _exportar_utilizador(caminho, email, a)


def _apagar_utilizador(caminho: Path, email: str, a):
    """r14: apaga a CONTA (o bloco [[utilizadores]], as sessões abertas e as ligações OAuth) mas não os dados logo — a pasta do
    espaço é MOVIDA para <dados>/apagados/<espaco>-<AAAAMMDD-HHMMSS>/. Recusa-se a tirar o último admin.
    r17: fica uma entrada em <dados>/contas-apagadas.json e `basalt apagados eliminar` apaga a pasta de vez passados 30 dias."""
    cfg = config.carregar(caminho)
    u = cfg.utilizador(email) or _falha(f"utilizador desconhecido: {email}")
    if u.admin and sum(1 for x in cfg.utilizadores if x.admin) <= 1:
        _falha(f"{email} é o único admin — cria outro admin antes de apagar este")
    if not a.sim:
        resposta = input(f"Vai apagar a conta {email} (espaço «{u.espaco}») — a pasta de dados fica em "
                          f"apagados/ e é eliminada de vez daqui a 30 dias; a conta deixa de poder entrar já. "
                          f"Escreve o e-mail outra vez para confirmar: ")
        if resposta.strip().lower() != email:
            _falha("confirmação não coincide — nada foi apagado")
    print(_apagar_conta(caminho, email))


def _apagar_conta(caminho: Path, email: str, conta_id: str | None = None, apagado_em: str | None = None) -> str:
    """o núcleo de `utilizador apagar` — também o de `apagados reaplicar`, que passa o `conta_id` esperado (outro no basalt.toml =
    não mexe) e a data ORIGINAL do apagamento. Sem perguntas; um erro é _falha (SystemExit). Devolve a mensagem.
    r17: a entrada do registo grava-se dentro do trinco do basalt.toml, depois de revogar as ligações e ANTES de o toml mudar e de
    a pasta sair do sítio, já com o destino decidido. Se o toml não chegar a mudar, a entrada é desfeita."""
    from . import apagados, auth
    cfg = config.carregar(caminho)
    u = cfg.utilizador(email) or _falha(f"utilizador desconhecido: {email}")
    if u.admin and sum(1 for x in cfg.utilizadores if x.admin) <= 1:
        _falha(f"{email} é o único admin — cria outro admin antes de apagar este")
    if conta_id is not None and u.conta_id != conta_id:
        _falha("a conta do basalt.toml é outra (outro conta_id) — não mexi")
    caminhos.definir_ancora(cfg.dados)
    esp = cfg.espaco_de(u)
    # [basalt] espaco_partilhado = true: a pasta pode ser de mais contas — mover «a pasta da Bea» levava as notas da Ana com ela
    partilham = sorted(x.email for x in cfg.utilizadores if x.espaco == u.espaco and x.email.lower() != u.email.lower())
    destino = None if partilham or not esp.raiz.exists() else apagados.destino_livre(cfg.dados, esp.nome)

    cortadas, revogadas, registo, nao_saiu = [], [], [], []

    def tirar(t):
        if not _bloco_de(t, email):
            _falha(f"utilizador desconhecido: {email}")
        if conta_id is not None and str(auth.chave_utilizador(t, email, "conta_id") or "").strip() != conta_id:
            _falha("a conta do basalt.toml mudou entretanto (outro conta_id) — não mexi")
        novo = remover_utilizador(t, email)
        # ronda 13b (como `senha`/`novo`): as ligações das IAs ficam revogadas NA BASE antes de o toml mudar — se não der, o toml
        # fica como estava. Antes tirava-se o bloco primeiro: com a base inacessível, a conta saía com as ligações vivas
        revogadas.append(_revogar_ligacoes(cfg.dados, email))
        cortadas.append(email)
        try:
            registo.append(apagados.registar(cfg.dados, email, u.conta_id, u.espaco, destino, apagado_em))
        except (apagados.RegistoInvalido, OSError) as e:
            _falha(f"não consegui gravar o registo das contas apagadas ({e}) — não mexi no basalt.toml; as ligações das IAs de "
                   f"{email} já ficaram cortadas na base (isso fica) — repete o comando")
        return novo

    def mover():
        # com o trinco do toml AINDA tomado (a entrada do registo já diz para onde a pasta vai): o `apagados eliminar` espera pelo
        # mesmo trinco e, quando o apanha, ou a pasta já lá está ou a entrada foi desfeita — antes movia-se depois de o largar e o
        # `eliminar`, a correr nesse instante, via a pasta registada «já não existir», marcava a entrada eliminada e a pasta chegava
        # a seguir, para ficar em apagados/ sem registo para sempre
        if partilham or destino is None:
            return
        alvo = cfg.dados / destino
        try:
            alvo.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(esp.raiz), str(alvo))
        except OSError as e:                  # não pode lançar aqui (o toml já mudou): diz-se depois, fora do trinco
            nao_saiu.append(e)
    try:
        _editar_toml(caminho, tirar, cortadas, depois=mover)
    except SystemExit:
        if registo:                       # a entrada foi gravada mas o basalt.toml não mudou: volta ao que era
            try:
                apagados.desfazer(cfg.dados, registo[0][0]["id"], registo[0][1])
            except (apagados.RegistoInvalido, OSError) as e:
                print(f"aviso: não consegui desfazer a entrada {registo[0][0]['id']} do registo ({e})", file=sys.stderr)
        raise
    n = revogar_sessoes(cfg.dados / "sessoes.json", email)
    r = revogadas[0] if revogadas else 0
    entrada = registo[0][0]
    msg = f"✓ {email} apagado ({n} sessão(ões) fechada(s), {r} ligação(ões) OAuth revogada(s))"
    if partilham:
        msg += f"; a pasta {esp.raiz} ficou onde está — o espaço «{u.espaco}» é partilhado com {', '.join(partilham)}"
    elif destino is not None:
        alvo = cfg.dados / destino
        if nao_saiu:
            e = nao_saiu[0]
            _falha(f"a conta foi apagada, mas a pasta {esp.raiz} não saiu do sítio ({e.strerror or e}) — move-a à mão para {alvo}: "
                   f"é aí que `basalt apagados eliminar` a vai buscar (entrada {entrada['id']})")
        msg += f"; a pasta foi para {alvo}"
    prazo = apagados.carimbo(apagados.instante(entrada["apagado_em"]) + apagados.DIAS * 86400)[:10]
    msg += f"; registo {entrada['id']}: elimina-se de vez a partir de {prazo} (`basalt apagados eliminar`)"
    return msg


def cmd_apagados(a):
    """r17: as contas apagadas (ver basalt/apagados.py). `eliminar` e `reaplicar` acabam com a linha `resumo: …`."""
    from . import apagados
    try:
        caminho = _caminho_config(a)
    except (SystemExit, config.ConfigInvalida) as x:
        if a.sub not in ("eliminar", "reaplicar"):
            if isinstance(x, config.ConfigInvalida):
                _falha(str(x))
            raise
        print(x if isinstance(x, SystemExit) else f"basalt: {x}", file=sys.stderr)   # sem basalt.toml: rc 1 e o resumo na mesma
        caminho = None
    if a.sub == "listar":
        rc = apagados.listar(caminho)
    elif a.sub == "exportar":
        rc = apagados.exportar(caminho)
    elif a.sub == "eliminar":
        rc = apagados.eliminar(caminho, a.dias, a.sim)          # um --dias negativo ou um erro: rc 1, com o resumo na mesma
    else:
        # a mensagem do `utilizador apagar` traz o e-mail: aqui fica só a linha do reaplicar (com o resumo do e-mail)
        rc = apagados.reaplicar(caminho, a.lista, a.sim, lambda email, conta_id, quando: _apagar_conta(caminho, email, conta_id, quando))
    sys.stdout.flush()
    if rc:
        raise SystemExit(rc)


def _exportar_utilizador(caminho: Path, email: str, a):
    """r14: zip das notas + anexos de uma conta (nunca o basalt.toml nem tokens — só `esp.real`, que é onde vivem as notas e
    `anexos/`; a cópia editável e `dados/` — sessões, índice — ficam de fora de propósito).
    r21 (EXPORTAR_COPIA_v1, auditoria externa à r14, ponto 7): a web e o /mcp escrevem na cópia editável e o vigia só a leva
    para a memória uns segundos depois (ou nunca, com o vigia parado): essas notas — novas, ou diferentes da memória — vão também
    para `por-sincronizar/<nome>.md` (a versão da memória fica onde estava). Continua só de leitura: não corre o vigia. O que o
    vigia não sincroniza (conflitos, .bak, ligações simbólicas, index.md…) fica de fora, como antes."""
    if not a.para:
        _falha("falta --para FICHEIRO.zip")
    cfg = config.carregar(caminho)
    u = cfg.utilizador(email) or _falha(f"utilizador desconhecido: {email}")
    esp = cfg.espaco_de(u)
    destino = Path(a.para).expanduser().resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)
    n = por_sinc = 0
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        if esp.real.exists():
            for p in sorted(esp.real.rglob("*")):
                if p.is_file() and not p.is_symlink():
                    z.write(p, p.relative_to(esp.real))
                    n += 1
        for nome, dados in _por_sincronizar(esp):                 # r21 (EXPORTAR_COPIA_v1)
            z.writestr(f"por-sincronizar/{nome}", dados)
            n += 1
            por_sinc += 1
    print(f"✓ {n} ficheiro(s) de {email} em {destino}")
    if por_sinc:
        print(f"  {por_sinc} nota(s) ainda por sincronizar (só na cópia editável) em por-sincronizar/")


def _por_sincronizar(esp):
    """r21 (EXPORTAR_COPIA_v1): (nome, bytes) das notas da cópia editável que o vigia ainda não levou para a memória — novas ou
    diferentes. O mesmo filtro do vigia (`duasvias.nota`, sem ligações simbólicas); uma pasta recusada ou ilegível salta-se."""
    from . import duasvias
    try:
        nomes = sorted(duasvias.notas_de(esp.copia)) if esp.copia.exists() else []
    except OSError as e:
        print(f"basalt: cópia editável ilegível ({e}) — o ZIP leva só a memória", file=sys.stderr)
        return []
    out = []
    for nome in nomes:
        try:
            dados = caminhos.ler(esp.copia / nome)
        except OSError:
            continue
        try:
            igual = (esp.real / nome).is_file() and caminhos.ler(esp.real / nome) == dados
        except OSError:
            igual = False
        if not igual:
            out.append((nome, dados))
    return out


def _dentro_de_cada(cfg, a, fn):
    for esp in _espacos(cfg, a):
        esp.criar()
        with config.dentro(esp):
            fn(esp)


def cmd_indexar(a):
    from . import sig
    cfg = _cfg(a)

    def um(esp):
        print(f"[{esp.nome}] ", end="", flush=True)
        sig.indexar()
    _dentro_de_cada(cfg, a, um)


def cmd_procurar(a):
    from . import sig
    cfg = _cfg(a)
    _dentro_de_cada(cfg, a, lambda esp: sig.procura(" ".join(a.termo), a.n, como_json=a.json))


def cmd_pergunta(a):
    from . import sig
    cfg = _cfg(a)
    _dentro_de_cada(cfg, a, lambda esp: sig.pergunta(" ".join(a.termo), como_json=a.json))


def cmd_sincronizar(a):
    from . import duasvias
    cfg = _cfg(a)
    _dentro_de_cada(cfg, a, lambda esp: duasvias.ciclo())


def cmd_arrumar(a):
    cfg = _cfg(a)
    from . import arrumar, auto_arrumar, categorias, sig
    from . import entrada as entrada_mod
    from . import ia as ia_mod

    def um(esp):
        if a.auto:
            # ronda 9 (achado M6 da ronda 2): o timer nocturno faz o mesmo que o servidor numa volta do vigia — a Entrada (web.processar_entrada) —
            # e mais a arrumação das notas parecidas, o índice e as categorias, pelas MESMAS funções. Com --simular não escreve nada.
            # Ronda 12: a Entrada e os pares têm cada um a sua IA ([ia.entrada] → [ia.arrumar] → [ia]; [ia.arrumar] → [ia]) e uma
            # desligada não leva a outra: com os pares desligados a Entrada continua a ser classificada (antes saltava as duas).
            ent, par = ia_mod.actual("entrada"), ia_mod.actual("arrumar")
            if not ent.chat_activo and not par.chat_activo:
                print(f"[{esp.nome}] a arrumação automática precisa de uma IA ([ia], [ia.entrada] ou [ia.arrumar] fornecedor); só o relatório.",
                      file=sys.stderr)
            else:
                if cfg.entrada.automatica:
                    if ent.chat_activo:
                        _arrumar_entrada(esp, bool(a.simular), entrada_mod)
                    else:
                        print(f"[{esp.nome}] Entrada: sem IA ([{_seccao_ia(ent)}] fornecedor = \"nenhum\"); as ideias ficam para arrumar à mão.",
                              file=sys.stderr)
                if par.chat_activo:
                    auto_arrumar.SIMULAR = bool(a.simular)
                    auto_arrumar._correr()
                else:
                    print(f"[{esp.nome}] notas parecidas: sem IA ([{_seccao_ia(par)}] fornecedor = \"nenhum\"); não decidi nenhum par.",
                          file=sys.stderr)
            if not a.simular:
                sig.indexar()
                categorias.main()
        arrumar.relatorio()
    _dentro_de_cada(cfg, a, um)


def _seccao_ia(x):
    """a secção do basalt.toml de onde vem uma IA ("ia", "ia.arrumar"…), para as mensagens."""
    return getattr(getattr(x, "cfg", None), "seccao", "ia")


def _arrumar_entrada(esp, simular, entrada_mod):
    """a Entrada de uma volta do `arrumar --auto`: as ideias que o vigia do servidor trataria (`web.processar_entrada`). Em simulação só se
    conta o que ficava por tratar — nada vai à IA nem se escreve. Um erro aqui não impede o resto da arrumação."""
    if simular:
        n = sum(1 for r in entrada_mod.pendentes() if not r.get("auto") and not r.get("auto_bloqueado"))
        print(f"[simulação] [{esp.nome}] {n} ideia(s) na Entrada por tratar — não toquei em nada.")
        return
    try:
        from . import web                             # só aqui: a página inteira não é precisa para os outros comandos
        n = web.processar_entrada()
        print(f"[{esp.nome}] Entrada: {n} ideia(s) tratada(s).")
    except Exception as e:
        print(f"[{esp.nome}] Entrada: não consegui tratá-la ({e.__class__.__name__}: {str(e)[:120]}).", file=sys.stderr)


def _um_espaco(cfg, a):
    esp = _espacos(cfg, a)
    if len(esp) != 1:
        _falha("há vários espaços: indica --utilizador EMAIL")
    return esp[0].criar()


ENTRADA_MAX = 4000                          # o mesmo corte da página (/api/guarda)


def cmd_entrada(a):
    """guardar uma ideia na Entrada a partir de outro programa (um bot, um script, um atalho) PELO MESMO CAMINHO da página: o trinco da
    Entrada (sem ele, uma linha acrescentada enquanto a página reescreve o ficheiro perdia-se), o mesmo corte, a mesma recusa de
    passwords em claro. «-» lê o texto do stdin (não fica na lista de processos). Sem texto, lista as ideias por arrumar."""
    from . import sig
    from .segredos import SegredoEmClaro
    cfg = _cfg(a)
    esp = _um_espaco(cfg, a)
    with config.dentro(esp):
        if not a.texto:
            return sig.entrada(a.json)
        t = (sys.stdin.read() if a.texto == ["-"] else " ".join(a.texto)).strip()
        if not t:
            _falha("texto vazio — nada para guardar")
        if len(t) > ENTRADA_MAX:
            print(f"aviso: o texto tinha {len(t)} caracteres; guardei os primeiros {ENTRADA_MAX} (o mesmo limite da página).", file=sys.stderr)
            t = t[:ENTRADA_MAX]
        de = (a.de or "terminal").strip() or "terminal"
        try:
            sig.guardar(t, de)
        except SegredoEmClaro as e:
            print(e, file=sys.stderr)
            sys.exit(2)
        if de not in cfg.entrada.fontes_de_confianca and not _pode_entrar_sozinha(esp, f"desconhecido:{de}"):
            print(f"aviso: «{de}» não é uma fonte de confiança ([entrada] fontes_de_confianca do basalt.toml): a ideia fica na Entrada "
                  "à espera de ser aprovada na página.", file=sys.stderr)


def _pode_entrar_sozinha(esp, autor):
    """a mesma regra da página (web.pode_auto) sem carregar a página: entrada-permissoes.json {"auto": [...]}, lido sem seguir
    ligações (como a página) — uma ligação para as permissões de outra conta não deixa entrar ninguém sozinho."""
    import json
    try:
        return autor in json.loads(caminhos.ler_texto(esp.permissoes_entrada)).get("auto", [])
    except (OSError, ValueError, AttributeError):
        return False


def cmd_importar(a):
    from . import obsidian
    cfg = _cfg(a)
    esp = _um_espaco(cfg, a)
    with config.dentro(esp):
        r = obsidian.importar(a.pasta, esp, simular=a.simular)
    print(("[simulação] " if a.simular else "") + f"{len(r['importadas'])} notas importadas, {len(r['anexos'])} anexos, {len(r['saltadas'])} saltadas")
    from .segredos import mascarar
    for f, porque in r["saltadas"]:
        # ronda 10 (G7 da r3): um nome de ficheiro com uma chave não vai para o ecrã; ronda 13: nem com uma password («senha: …»)
        print(mascarar(f"  · {f}: {porque}", "  · (nome omitido: tinha o que parece uma password ou chave)"))
    if r["importadas"] and not a.simular:
        print("A seguir: `basalt sincronizar` (põe-as na cópia editável) e `basalt indexar`.")


def cmd_exportar(a):
    from . import obsidian
    cfg = _cfg(a)
    esp = _um_espaco(cfg, a)
    n = obsidian.exportar(a.pasta, esp)
    print(f"✓ {n} notas em {Path(a.pasta).expanduser().resolve()} — abre a pasta no Obsidian")


def cmd_mcp(a):
    from . import mcp
    _verificar_trocas(_cfg(a), a)
    argv = []
    if a.config:
        argv += ["--config", a.config]
    if a.utilizador:
        argv += ["--utilizador", a.utilizador]
    mcp.main(argv)


def cmd_estado(a):
    cfg = _cfg(a)
    print(f"basalt {__version__} · config {_caminho_config(a)}\n dados {cfg.dados} · escuta {config.endereco(cfg.escutar, cfg.porta)} · auth {cfg.auth.modo} · git {'sim' if cfg.git else 'não'}")
    print(f" ia {cfg.ia.fornecedor} {cfg.ia.modelo_chat or ''} · embeddings {cfg.ia.embeddings_fornecedor or 'nenhum'} {cfg.ia.embeddings_modelo or ''}".rstrip())
    if cfg.ia.tarefas:                    # ronda 12: [ia.unir]/[ia.arrumar]/[ia.entrada] — qual IA cada tarefa usa e de onde vem
        for t in config.TAREFAS_IA:
            c = cfg.ia.para(t)
            print(f"  ia.{t:8} {c.fornecedor} {c.modelo_chat or ''}".rstrip()
                  + (" (desligada)" if c.fornecedor == "nenhum" else "") + ("" if c.seccao == f"ia.{t}" else f"  (a de [{c.seccao}])"))
    for esp in _espacos(cfg, a):
        n = len(caminhos.notas(esp.real)) if esp.real.exists() else 0
        c = len(caminhos.notas(esp.copia)) if esp.copia.exists() else 0
        print(f" espaço {esp.nome:16} {n:5} notas · cópia {c:5} · {esp.real}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="basalt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", "-c", default=os.environ.get("BASALT_CONFIG") or None, help="basalt.toml")
    ap.add_argument("--versao", "-V", action="version", version=f"basalt {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("iniciar", help="criar basalt.toml, pastas e primeiro utilizador")
    p.add_argument("--dados", help="pasta dos dados (omissão: ./basalt-dados)")
    p.add_argument("--email"); p.add_argument("--nome"); p.add_argument("--espaco"); p.add_argument("--senha")
    p.add_argument("--modo", choices=config.MODOS_AUTH); p.add_argument("--escutar", default="127.0.0.1"); p.add_argument("--porta", default=8121, type=int)
    p.add_argument("--sem-ia", action="store_true", help="fornecedor = nenhum (pesquisa só por palavras)")
    p.add_argument("--forcar", action="store_true")
    p.set_defaults(fn=cmd_iniciar)

    sub.add_parser("serve", help="servidor web + /mcp").set_defaults(fn=cmd_serve)

    p = sub.add_parser("utilizador", help="novo · senha · mcp-token · listar · apagar · exportar")
    p.add_argument("sub", choices=["novo", "senha", "mcp-token", "listar", "apagar", "exportar"]); p.add_argument("email", nargs="?")
    p.add_argument("--nome"); p.add_argument("--espaco"); p.add_argument("--senha"); p.add_argument("--admin", action="store_true")
    p.add_argument("--sim", action="store_true", help="apagar: não pedir para escrever o e-mail outra vez (scripts/testes)")
    p.add_argument("--para", help="exportar: o ficheiro .zip de destino")
    p.set_defaults(fn=cmd_utilizador)

    p = sub.add_parser("apagados", help="as contas apagadas: listar · eliminar (de vez, aos 30 dias) · exportar · reaplicar")
    p.add_argument("sub", choices=["listar", "eliminar", "exportar", "reaplicar"])
    p.add_argument("--dias", type=int, default=30, help="eliminar: só as apagadas há MAIS de N dias (omissão: 30)")
    p.add_argument("--sim", action="store_true", help="eliminar/reaplicar: fazer mesmo (sem isto só mostra o que faria)")
    p.add_argument("--lista", help="reaplicar: o ficheiro com a saída de `basalt apagados exportar`")
    p.set_defaults(fn=cmd_apagados)

    for nome, fn, ajuda in (("indexar", cmd_indexar, "(re)construir o índice"), ("sincronizar", cmd_sincronizar, "memória ↔ cópia"),
                            ("arrumar", cmd_arrumar, "Entrada → notas, ligações, categorias"), ("estado", cmd_estado, "o que está configurado")):
        p = sub.add_parser(nome, help=ajuda); p.add_argument("--utilizador", "-u", default=""); p.set_defaults(fn=fn)
        if nome == "arrumar":
            p.add_argument("--auto", action="store_true", help="arrumação automática com a IA (ligar/arquivar/unir parecidas) + reindexar; para o timer nocturno")
            p.add_argument("--simular", action="store_true", help="com --auto: só dizer o que faria")
    for nome, fn in (("procurar", cmd_procurar), ("pergunta", cmd_pergunta)):
        p = sub.add_parser(nome); p.add_argument("termo", nargs="+"); p.add_argument("-n", type=int, default=8)
        p.add_argument("--json", action="store_true"); p.add_argument("--utilizador", "-u", default=""); p.set_defaults(fn=fn)
    p = sub.add_parser("entrada", help="guardar uma ideia na Entrada (com o trinco da página) · sem texto: listá-las")
    p.add_argument("texto", nargs="*", help='o texto da ideia; «-» lê-o do stdin. Um texto que comece por «-» vai depois de -- '
                                             '(ex.: basalt entrada --de bot -- "-urgente")')
    p.add_argument("--de", default="terminal", help="de onde vem (omissão: terminal); fora de [entrada] fontes_de_confianca fica à espera de aprovação")
    p.add_argument("--json", action="store_true", help="sem texto: a lista em JSON")
    p.add_argument("--utilizador", "-u", default=""); p.set_defaults(fn=cmd_entrada)
    p = sub.add_parser("importar", help="trazer um cofre Obsidian (ou pasta de Markdown) para o espaço; nunca sobrepõe")
    p.add_argument("pasta"); p.add_argument("--simular", action="store_true", help="só dizer o que faria"); p.add_argument("--utilizador", "-u", default=""); p.set_defaults(fn=cmd_importar)
    p = sub.add_parser("exportar", help="copiar a memória para uma pasta (abre-se no Obsidian)")
    p.add_argument("pasta"); p.add_argument("--utilizador", "-u", default=""); p.set_defaults(fn=cmd_exportar)
    p = sub.add_parser("mcp", help="servidor MCP por stdio"); p.add_argument("--utilizador", "-u", default=""); p.set_defaults(fn=cmd_mcp)

    a = ap.parse_args(argv)
    if a.cmd == "utilizador" and a.sub != "listar" and not a.email:
        ap.error("falta o e-mail")
    a.fn(a)


if __name__ == "__main__":
    main()
