"""python3 -m basalt — a linha de comandos do Basalt.

  iniciar      cria o basalt.toml, as pastas e o primeiro utilizador
  serve        o servidor web (+ /mcp)
  utilizador   novo · senha · mcp-token · listar  (edita o basalt.toml no sítio: só a chave pedida, o resto fica igual)
  indexar      (re)constrói o índice semântico do(s) espaço(s)
  procurar     pesquisa por significado (ou por palavras)
  pergunta     resposta com fontes (precisa de IA)
  sincronizar  memória ↔ cópia editável, nos dois sentidos (CAS)
  arrumar      Entrada → notas, ligações, categorias
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
import subprocess
import sys
from pathlib import Path

from . import __version__, caminhos, config
from .config import Config, Utilizador

EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _falha(msg):
    raise SystemExit(f"basalt: {msg}")


# ───────────────────────────── editar o basalt.toml sem o reescrever todo ─────────────────────────────
_BLOCO_RX = re.compile(r"(?ms)^\[\[utilizadores\]\][ \t]*\n(.*?)(?=^\[|\Z)")


def _bloco_de(texto: str, email: str):
    """(início, fim) do bloco [[utilizadores]] com esse e-mail, ou None."""
    for m in _BLOCO_RX.finditer(texto):
        e = re.search(r'(?m)^\s*email\s*=\s*"([^"]*)"', m.group(1))
        if e and e.group(1).strip().lower() == email.lower():
            return m.start(), m.end()
    return None


def _toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def definir_chave_utilizador(texto: str, email: str, chave: str, valor) -> str:
    """põe `chave = valor` no bloco desse utilizador: substitui a linha (mesmo comentada) ou acrescenta-a a seguir ao e-mail."""
    pos = _bloco_de(texto, email)
    if not pos:
        raise KeyError(email)
    a, b = pos
    bloco = texto[a:b]
    linha = f"{chave} = {_toml_str(valor) if isinstance(valor, str) else str(valor).lower()}"
    rx = re.compile(rf"(?m)^[ \t]*#?[ \t]*{re.escape(chave)}[ \t]*=[^\n]*$")
    if rx.search(bloco):
        bloco = rx.sub(linha, bloco, count=1)
    else:
        bloco = re.sub(r'(?m)^(\s*email\s*=\s*"[^"]*"[^\n]*)$', lambda m: m.group(1) + "\n" + linha, bloco, count=1)
    return texto[:a] + bloco + texto[b:]


def bloco_utilizador(email: str, espaco: str, nome: str, admin: bool, senha_hash: str = "") -> str:
    out = ["[[utilizadores]]", f"email = {_toml_str(email)}", f"espaco = {_toml_str(espaco)}", f"nome = {_toml_str(nome)}"]
    if senha_hash:
        out.append(f"senha_hash = {_toml_str(senha_hash)}")
    out.append(f"admin = {'true' if admin else 'false'}")
    return "\n".join(out) + "\n"


def acrescentar_utilizador(texto: str, bloco: str) -> str:
    """o novo bloco fica a seguir ao último [[utilizadores]] (ou antes de [ia], ou no fim)."""
    blocos = list(_BLOCO_RX.finditer(texto))
    if blocos:
        fim = blocos[-1].end()
        return texto[:fim].rstrip("\n") + "\n\n" + bloco + "\n" + texto[fim:].lstrip("\n")
    m = re.search(r"(?m)^\[ia\]", texto)
    if m:
        return texto[:m.start()] + bloco + "\n" + texto[m.start():]
    return texto.rstrip("\n") + "\n\n" + bloco


def _ler_toml(caminho: Path) -> str:
    try:
        return caminho.read_text(encoding="utf-8")
    except OSError as e:
        _falha(f"não consigo ler {caminho}: {e.strerror}")


def _escrever_toml(caminho: Path, texto: str):
    tmp = caminho.with_suffix(".toml.tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.chmod(tmp, 0o600)
    try:
        config.carregar(tmp)          # tem de carregar ANTES de substituir o verdadeiro; senão ficava um toml inválido no sítio
    except (config.ConfigInvalida, ValueError, OSError) as e:   # ValueError = toml mal formado
        tmp.unlink(missing_ok=True)
        _falha(f"o basalt.toml ficaria inválido ({e}) — não mexi em {caminho}")
    os.replace(tmp, caminho)


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
    while n in donos:
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
    print(f"✓ {caminho}\n✓ dados em {dados}" + (f"\n✓ utilizador {email} ({modo})" if email else f"\n· modo {modo}, sem utilizadores (só localhost)"))
    print(f"\nA seguir:\n  python3 -m basalt --config {caminho} serve   → http://{config.endereco(a.escutar, a.porta)}\n"
          f"  python3 -m basalt --config {caminho} mcp     → para os agentes (ver docs/MCP.md)")


def cmd_serve(a):
    cfg = _cfg(a)
    try:
        from . import web
    except ImportError as e:
        _falha(f"o servidor web ainda não está neste pacote ({e})")
    web.servir(cfg)


def cmd_utilizador(a):
    from . import auth
    caminho = _caminho_config(a)
    texto = _ler_toml(caminho)
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
        texto = acrescentar_utilizador(texto, bloco_utilizador(email, esp, a.nome or email.split("@")[0], bool(a.admin), h))
        _escrever_toml(caminho, texto)
        cfg = config.carregar(caminho)
        e = cfg.espaco_de(cfg.utilizador(email)).criar()
        if cfg.git:
            _git_init(e)
        print(f"✓ {email} criado (espaço «{esp}»)")
        return
    if not _bloco_de(texto, email):
        _falha(f"utilizador desconhecido: {email}")
    if a.sub == "senha":
        _escrever_toml(caminho, definir_chave_utilizador(texto, email, "senha_hash", auth.hash_senha(_senha(a))))
        print(f"✓ senha de {email} guardada")
    elif a.sub == "mcp-token":
        token, h = auth.gerar_token()
        _escrever_toml(caminho, definir_chave_utilizador(texto, email, "mcp_token_hash", h))
        print(f"token MCP de {email} (mostra-se UMA vez; guarda-o no cliente, nunca numa nota):\n\n  {token}\n")
        print('  claude mcp add --transport http basalt https://O-TEU-BASALT/mcp --header "Authorization: Bearer ' + token + '"')


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
            if not ia_mod.actual().chat_activo:
                print(f"[{esp.nome}] a arrumação automática precisa de uma IA ([ia] fornecedor); só o relatório.", file=sys.stderr)
            else:
                if cfg.entrada.automatica:
                    _arrumar_entrada(esp, bool(a.simular), entrada_mod)
                auto_arrumar.SIMULAR = bool(a.simular)
                auto_arrumar._correr()
            if not a.simular:
                sig.indexar()
                categorias.main()
        arrumar.relatorio()
    _dentro_de_cada(cfg, a, um)


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


def cmd_importar(a):
    from . import obsidian
    cfg = _cfg(a)
    esp = _um_espaco(cfg, a)
    with config.dentro(esp):
        r = obsidian.importar(a.pasta, esp, simular=a.simular)
    print(("[simulação] " if a.simular else "") + f"{len(r['importadas'])} notas importadas, {len(r['anexos'])} anexos, {len(r['saltadas'])} saltadas")
    from .segredos import SEGREDO_RX
    for f, porque in r["saltadas"]:
        print(SEGREDO_RX.sub("•••", f"  · {f}: {porque}"))      # ronda 10 (G7 da r3): um nome de ficheiro com uma chave não vai para o ecrã
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

    p = sub.add_parser("utilizador", help="novo · senha · mcp-token · listar")
    p.add_argument("sub", choices=["novo", "senha", "mcp-token", "listar"]); p.add_argument("email", nargs="?")
    p.add_argument("--nome"); p.add_argument("--espaco"); p.add_argument("--senha"); p.add_argument("--admin", action="store_true")
    p.set_defaults(fn=cmd_utilizador)

    for nome, fn, ajuda in (("indexar", cmd_indexar, "(re)construir o índice"), ("sincronizar", cmd_sincronizar, "memória ↔ cópia"),
                            ("arrumar", cmd_arrumar, "Entrada → notas, ligações, categorias"), ("estado", cmd_estado, "o que está configurado")):
        p = sub.add_parser(nome, help=ajuda); p.add_argument("--utilizador", "-u", default=""); p.set_defaults(fn=fn)
        if nome == "arrumar":
            p.add_argument("--auto", action="store_true", help="arrumação automática com a IA (ligar/arquivar/unir parecidas) + reindexar; para o timer nocturno")
            p.add_argument("--simular", action="store_true", help="com --auto: só dizer o que faria")
    for nome, fn in (("procurar", cmd_procurar), ("pergunta", cmd_pergunta)):
        p = sub.add_parser(nome); p.add_argument("termo", nargs="+"); p.add_argument("-n", type=int, default=8)
        p.add_argument("--json", action="store_true"); p.add_argument("--utilizador", "-u", default=""); p.set_defaults(fn=fn)
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
