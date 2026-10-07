"""Percursos de instalação publicados — Docker, systemd, READMEs, MCP e o pacote Python.

Testes ESTÁTICOS: lêem ficheiros; não executam comandos, não precisam de rede, de Docker nem de systemd. Cada grupo fixa um
defeito que uma auditoria externa encontrou ao seguir os percursos como um utilizador novo:

  ronda 8 (instalação)  Docker sem `basalt.toml` e com o healthcheck numa API protegida · systemd sem `mkdir` e sem `PYTHONPATH` · README sem
       o caminho do toml no `serve` · exemplos de MCP com `--config` depois de `mcp` (o parser recusa: código 2)
  ronda 8 (arrumar --auto)  o timer só imprimia um relatório e a unidade não tinha o endurecimento do servidor
  ronda 8 (recursos do pacote)  `package-data` do pyproject apontava para `../static/*` (a árvore real é `basalt/static/**`)
  ronda 8 (MCP stdio)  `basalt mcp` por stdio não sincroniza nem faz commit

As linhas de comando dos docs são validadas com o parser REAL (`basalt.__main__.main`), com os `cmd_*` trocados por um
gravador — nada corre, só se verifica que os argumentos fazem parse.
"""
import re
import shlex
import tomllib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "README.en.md", "docs/INSTALAR.md", "docs/MCP.md")
# quantos comandos `basalt` (em blocos de código) cada doc tem de ter, no mínimo: se o extractor deixar de ver comandos, os
# testes de parse passariam sem verificar nada
MINIMO_COMANDOS = {"README.md": 6, "README.en.md": 6, "docs/INSTALAR.md": 6, "docs/MCP.md": 4}


def _ler(rel):
    return (RAIZ / rel).read_text(encoding="utf-8")


# ───────────────────────── extracção de comandos dos docs ─────────────────────────
_BLOCO = re.compile(r"^[ \t]*```[ \t]*([\w+-]*)[^\n]*\n(.*?)^[ \t]*```[ \t]*$", re.S | re.M)
_INLINE = re.compile(r"`([^`\n]+)`")
_PY = re.compile(r"(?:^|[\s;&|(])python3 -m basalt(?=\s|$)\s*(.*)$")
_SOLTO = re.compile(r"^(?:\$\s+)?(?:\w+=\S+\s+)*(?:sudo\s+(?:-u\s+\w+\s+)?)?basalt(?=\s)\s+(.*)$")
_DOCKER = re.compile(r"docker compose (?:run --rm|exec) basalt\s+(?!python3 -m basalt)(.*)$")
_ARRAY = re.compile(r'\[\s*"-m",\s*"basalt"((?:\s*,\s*"[^"]*")*)\s*\]')       # args = ["-m", "basalt", …] (Codex, Cursor)
_PONTUACAO = set("();<>|&")


def _tokens(args: str) -> list[str]:
    """os argumentos de UM comando: pára no primeiro operador de shell (&&, |, ;, >, parêntese) e ignora `# comentários`."""
    lex = shlex.shlex(args, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = "#"
    out = []
    for t in lex:
        if set(t) <= _PONTUACAO:
            break
        out.append(re.sub(r"<[^>]*>|…", "x", t))             # <pasta>, O-TEU-… , «…» → valor fictício
    return out


def _de_uma_linha(linha: str):
    """os argumentos que seguem `basalt` numa linha de shell, ou None se a linha não invoca o basalt."""
    linha = linha.strip().lstrip("#").strip() if linha.lstrip().startswith("#") else linha.strip()
    for rx in (_PY, _SOLTO, _DOCKER):
        m = rx.search(linha)
        if m:
            return _tokens(m.group(1))
    return None


def comandos(texto: str, *, inline=False):
    """[(origem, texto, tokens)] dos blocos de código (bash/sh/sem língua) e das matrizes `["-m", "basalt", …]`;
    com `inline=True` também dos `trechos de código` da prosa."""
    out = []
    for m in _BLOCO.finditer(texto):
        if m.group(1).lower() not in ("", "bash", "sh", "shell", "console"):
            continue
        corpo = m.group(2).replace("\\\n", " ")
        for linha in corpo.splitlines():
            tk = _de_uma_linha(linha)
            if tk is not None:
                out.append(("bloco", linha.strip(), tk))
            for a in _ARRAY.finditer(linha):
                itens = re.findall(r'"([^"]*)"', a.group(1))
                out.append(("matriz", a.group(0), [re.sub(r"<[^>]*>|…", "x", i) for i in itens]))
    if inline:
        for m in _INLINE.finditer(_BLOCO.sub("", texto)):
            tk = _de_uma_linha(m.group(1))
            if tk is not None:
                out.append(("inline", m.group(1), tk))
    return out


# ───────────────────────── o parser real, sem executar nada ─────────────────────────
@pytest.fixture
def parse(monkeypatch, capsys):
    """parse(tokens) → Namespace que o `main` real construiria; SystemExit (erro de uso) → AssertionError com a mensagem."""
    from basalt import __main__ as cli
    visto = {}

    def gravar(a):
        visto["a"] = a

    for nome in dir(cli):
        if nome.startswith("cmd_") and callable(getattr(cli, nome)):
            monkeypatch.setattr(cli, nome, gravar)

    def _parse(tokens):
        visto.clear()
        try:
            cli.main(list(tokens))
        except SystemExit as e:
            pytest.fail(f"o parser real recusou `basalt {' '.join(tokens)}` (código {e.code}): {capsys.readouterr().err.strip()[-200:]}")
        return visto["a"]
    return _parse


def test_parse_validado_contra_um_caso_bom_e_um_mau(parse):
    """o teste dos docs só vale se o parse recusar o que o utilizador não consegue correr."""
    a = parse(["--config", "/x/basalt.toml", "mcp", "--utilizador", "ana@exemplo.pt"])
    assert a.config == "/x/basalt.toml" and a.cmd == "mcp" and a.utilizador == "ana@exemplo.pt"
    with pytest.raises(pytest.fail.Exception):
        parse(["mcp", "--config", "/x/basalt.toml"])


@pytest.mark.parametrize("doc", DOCS)
def test_extractor_ve_comandos_em_cada_doc(doc):
    n = len([c for c in comandos(_ler(doc)) if c[0] != "inline"])
    assert n >= MINIMO_COMANDOS[doc], f"{doc}: só {n} comandos extraídos (mínimo {MINIMO_COMANDOS[doc]}) — o extractor deixou de ver"


@pytest.mark.parametrize("doc", DOCS)
def test_comandos_dos_docs_fazem_parse_com_o_parser_real(doc, parse):
    for origem, texto, tokens in comandos(_ler(doc)):
        parse(tokens)                                       # falha com o comando concreto se o parser o recusar


@pytest.mark.parametrize("doc", DOCS)
def test_config_vem_logo_depois_de_basalt_nunca_depois_do_subcomando(doc):
    texto = _ler(doc)
    assert not re.search(r"\bmcp\s+(?:--config|-c)\b", texto), f"{doc}: `mcp --config` (o parser só aceita `basalt --config X mcp`)"
    assert not re.search(r'"mcp",\s*"(?:--config|-c)"', texto), f"{doc}: matriz com `--config` depois de `mcp`"
    for origem, cmd, tokens in comandos(texto, inline=True):
        for opcao in ("--config", "-c"):
            if opcao in tokens:
                assert tokens[0] == opcao, f"{doc} ({origem}): `{cmd}` — {opcao} tem de vir logo a seguir a `basalt`"


# ───────────────────────── ronda 8 (instalação) — o arranque rápido dos READMEs ─────────────────────────
@pytest.mark.parametrize("doc", ("README.md", "README.en.md"))
def test_readme_iniciar_depois_serve_com_o_caminho_do_toml(doc):
    """`iniciar` guarda o toml na pasta dos dados; o `serve` seguinte tem de o receber (--config ou BASALT_CONFIG)."""
    for m in _BLOCO.finditer(_ler(doc)):
        linhas = [l.strip() for l in m.group(2).splitlines()]
        ini = next((i for i, l in enumerate(linhas) if re.search(r"basalt iniciar\b", l)), None)
        if ini is None:
            continue
        srv = next((i for i, l in enumerate(linhas) if i > ini and re.search(r"basalt (?:--config \S+ )?serve\b", l)), None)
        assert srv is not None, f"{doc}: depois do `iniciar` falta um `serve` no mesmo bloco"
        assert any("BASALT_CONFIG" in l or "--config" in l for l in linhas[ini + 1: srv + 1]), \
            f"{doc}: entre o `iniciar` e o `serve` ninguém diz onde está o basalt.toml"
        return
    pytest.fail(f"{doc}: não há bloco de arranque com `iniciar`")


def test_marcador_do_repositorio_nos_tres_docs():
    for doc in ("README.md", "README.en.md", "docs/INSTALAR.md"):
        t = _ler(doc)
        assert "git clone https://github.com/REPO_POR_DEFINIR/basalt" in t, f"{doc}: falta o marcador REPO_POR_DEFINIR no clone"
        assert "github.com/…" not in t, f"{doc}: ficou um URL com reticências"


# ───────────────────────── ronda 8 (instalação) — Docker ─────────────────────────
def _dockerfile_linhas():
    return _ler("docker/Dockerfile").splitlines()


def test_dockerfile_healthcheck_usa_a_rota_publica_e_compila():
    hc = [l for l in _dockerfile_linhas() if l.startswith("HEALTHCHECK")]
    assert len(hc) == 1
    assert "/api/saude" in hc[0] and "/api/estado" not in hc[0], "o healthcheck tem de usar a rota pública /api/saude"
    assert "curl" not in hc[0], "a imagem não tem curl"
    argv = shlex.split(hc[0].split(" CMD ", 1)[1])
    assert argv[:2] == ["python3", "-c"]
    compile(argv[2], "healthcheck", "exec")                  # aspas/ponto-e-vírgula bem formados
    porta = re.search(r"BASALT_PORTA=(\d+)", "\n".join(_dockerfile_linhas())).group(1)
    assert f"http://127.0.0.1:{porta}/api/saude" in argv[2]


def test_docker_nao_anuncia_modo_aberto():
    """a imagem escuta em 0.0.0.0, onde o modo `aberto` é recusado: nem o Dockerfile nem o compose o prometem."""
    for rel in ("docker/Dockerfile", "docker/compose.yml"):
        assert not re.search(r"abert[oa]", _ler(rel), re.I), f"{rel}: fala em modo aberto"
    compose = _ler("docker/compose.yml")
    assert '"127.0.0.1:8121:8121"' in compose, "a porta só pode ser publicada em 127.0.0.1"


def test_compose_explica_o_arranque_do_zero_pela_ordem_certa(parse):
    linhas = _ler("docker/compose.yml").splitlines()
    cabecalho = [l.lstrip("# ").strip() for l in linhas if l.startswith("#")]

    def pos(rx):
        return next(i for i, l in enumerate(cabecalho) if re.search(rx, l))
    chown, iniciar, subir = pos(r"chown 1000:1000 dados"), pos(r"docker compose run --rm basalt iniciar"), pos(r"docker compose up -d")
    assert chown < iniciar < subir, "ordem: dono da pasta → `iniciar` (cria o basalt.toml) → `up`"
    a = parse(_de_uma_linha(cabecalho[iniciar]))
    assert (a.cmd, a.dados, a.modo) == ("iniciar", "/dados", "senha") and a.email


def test_instalar_docker_cria_o_toml_antes_de_subir(parse):
    t = _ler("docs/INSTALAR.md")
    bloco = next(m.group(2) for m in _BLOCO.finditer(t) if "docker compose run --rm basalt iniciar" in m.group(2))
    linhas = [l.strip() for l in bloco.splitlines()]
    idx = lambda s: next(i for i, l in enumerate(linhas) if s in l)
    assert idx("chown 1000:1000 dados") < idx("run --rm basalt iniciar") < idx("docker compose up -d")
    a = parse(_de_uma_linha(linhas[idx("run --rm basalt iniciar")]))
    assert a.dados == "/dados" and a.modo == "senha" and a.email, "o `iniciar` do Docker tem de escrever em /dados, com senha"


# ───────────────────────── ronda 8 (instalação, arrumar --auto) — systemd ─────────────────────────
def _ficheiro_unidade(rel):
    """{chave: [valores]} das linhas `Chave=valor` (sem comentários); `Environment=` guarda-se por variável (`Environment:NOME`)."""
    out = {}
    for l in _ler(rel).splitlines():
        m = re.match(r"([A-Za-z]+)=(.*)$", l.strip())
        if not m:
            continue
        chave, valor = m.group(1), m.group(2).strip()
        if chave == "Environment" and "=" in valor:
            chave, valor = f"Environment:{valor.split('=', 1)[0]}", valor.split("=", 1)[1]
        out.setdefault(chave, []).append(valor)
    return out


ENDURECIMENTO = ("NoNewPrivileges", "ProtectSystem", "ProtectHome", "PrivateTmp", "ReadWritePaths", "UMask")


def test_servico_encontra_o_modulo_copiado_para_opt():
    texto = _ler("sistema/basalt.service")
    assert "Environment=PYTHONPATH=/opt/basalt" in [l.strip() for l in texto.splitlines()]
    u = _ficheiro_unidade("sistema/basalt.service")
    assert u["ExecStart"] == ["/usr/bin/python3 -m basalt serve"]
    # coerência: corre e escreve só nos dados; o toml está lá dentro
    assert u["WorkingDirectory"] == ["/var/lib/basalt"] and u["ReadWritePaths"] == ["/var/lib/basalt"]
    assert u["Environment:BASALT_CONFIG"][0].startswith(u["ReadWritePaths"][0] + "/")


def test_arrumar_chama_o_modo_automatico():
    u = _ficheiro_unidade("sistema/basalt-arrumar.service")
    assert any("arrumar --auto" in v for v in u["ExecStart"]), "o timer tem de ARRUMAR (--auto), não só imprimir o relatório"
    assert u["Type"] == ["oneshot"]


def test_arrumar_tem_o_mesmo_endurecimento_e_ambiente_que_o_servidor():
    srv, arr = _ficheiro_unidade("sistema/basalt.service"), _ficheiro_unidade("sistema/basalt-arrumar.service")
    for chave in ENDURECIMENTO:
        assert chave in srv, f"basalt.service perdeu {chave}= (o teste compara com ele)"
        assert arr.get(chave) == srv[chave], f"basalt-arrumar.service: {chave} = {arr.get(chave)} (o servidor tem {srv[chave]})"
    for chave in ("User", "Group", "WorkingDirectory", "Environment:PYTHONPATH", "Environment:BASALT_CONFIG"):
        assert srv.get(chave) and arr.get(chave) == srv[chave], f"basalt-arrumar.service: {chave} difere do servidor"


def test_instalar_cria_as_pastas_antes_de_copiar_e_inicia_com_pythonpath():
    linhas = _ler("docs/INSTALAR.md").splitlines()
    mk = next(i for i, l in enumerate(linhas) if "mkdir -p /opt/basalt" in l)
    cp = next(i for i, l in enumerate(linhas) if "cp -r basalt" in l)
    assert mk < cp, "o `mkdir -p /opt/basalt` tem de vir ANTES do `cp -r basalt …` (senão o cp falha)"
    assert "/var/lib/basalt" in linhas[mk]
    ini = next(l for l in linhas[cp:] if "basalt iniciar" in l)
    assert "PYTHONPATH=/opt/basalt" in ini, "o `iniciar` corre como o utilizador do serviço, fora da pasta do código"
    assert "PYTHONPATH=/opt/basalt" in _ler("sistema/basalt.service")


# ───────────────────────── ronda 8 (recursos do pacote) — package-data ─────────────────────────
def _glob(padrao: str) -> re.Pattern:
    """a semântica de globs do setuptools: `*` não atravessa pastas; `**/` = zero ou mais pastas."""
    out, i = "", 0
    while i < len(padrao):
        if padrao.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif padrao.startswith("**", i):
            out, i = out + ".*", i + 2
        elif padrao[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif padrao[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(padrao[i]), i + 1
    return re.compile(out + r"\Z")


def test_glob_do_teste_distingue_o_bom_do_mau():
    assert _glob("static/**/*").match("static/vendor/katex/fonts/x.woff2") and _glob("static/**/*").match("static/index.html")
    assert not _glob("static/*").match("static/vendor/mermaid.min.js")          # um só nível
    assert not _glob("../static/*").match("static/index.html")                  # o padrão antigo: aponta para fora do pacote


def test_package_data_cobre_toda_a_arvore_de_static():
    cfg = tomllib.loads(_ler("pyproject.toml"))
    padroes = cfg["tool"]["setuptools"]["package-data"]["basalt"]
    fora = [p for p in padroes if p.startswith("..") or p.startswith("/")]
    assert not fora, f"package-data aponta para fora do pacote: {fora}"
    static = RAIZ / "basalt" / "static"
    ficheiros = sorted(f.relative_to(RAIZ / "basalt").as_posix() for f in static.rglob("*") if f.is_file())
    assert len(ficheiros) > 20 and any(f.count("/") >= 3 for f in ficheiros), "a árvore de static mudou: rever o teste"
    regexes = [_glob(p) for p in padroes]
    sem = [f for f in ficheiros if not any(rx.match(f) for rx in regexes)]
    assert not sem, f"{len(sem)} ficheiros de basalt/static/ ficam fora do pacote, p. ex.: {sem[:4]}"


# ───────────────────────── ronda 8 (MCP stdio) — stdio vs servidor, revogação ─────────────────────────
def test_mcp_md_diz_que_o_stdio_nao_sincroniza_e_que_o_toml_e_relido():
    t = _ler("docs/MCP.md")
    assert re.search(r"stdio sozinho não sincroniza", t)
    assert "serve" in t and "sincronizar" in t, "tem de dizer como se sincroniza: `serve` (vigia) ou `sincronizar`"
    assert re.search(r"relê[^.]*basalt\.toml", t, re.S), "a revogação do token tem de dizer que o servidor relê o basalt.toml"


# ───────────────────────── ronda 8 (instalação) — cada comando solto encontra o basalt.toml ─────────────────────────
DOCS_COM_COMANDOS = DOCS + ("docs/OBSIDIAN.md",)
_NAO_PRECISA = ("iniciar", "--config", "-c", "--version", "--help", "-h")


def _soltos_sem_toml(texto: str) -> list[str]:
    """os comandos `basalt` em blocos de código que não dizem onde está o basalt.toml (nem `--config`, nem `BASALT_CONFIG=`
    na própria linha, nem Docker, que tem o seu) — num doc que também não manda fazer `export BASALT_CONFIG`."""
    if re.search(r"^\s*export BASALT_CONFIG=", texto, re.M) or "`export BASALT_CONFIG=" in texto:
        return []
    return [cmd for origem, cmd, tk in comandos(texto) if origem == "bloco" and tk and tk[0] not in _NAO_PRECISA
            and not re.search(r"BASALT_CONFIG=|docker compose|--config\b", cmd)]


def test_soltos_sem_toml_distingue_o_bom_do_mau():
    """validado contra um caso mau (o `iniciar` grava o toml em <dados>/basalt.toml, que nenhum comando seguinte procura)
    e dois bons (export no bloco ou na prosa; caminho na própria linha)."""
    mau = "```bash\npython3 -m basalt iniciar --dados ~/d --email a@exemplo.pt\npython3 -m basalt indexar\n```\n"
    assert _soltos_sem_toml(mau) == ["python3 -m basalt indexar"]
    assert _soltos_sem_toml(mau.replace("python3 -m basalt indexar", "export BASALT_CONFIG=~/d/basalt.toml\npython3 -m basalt indexar")) == []
    assert _soltos_sem_toml("Supõe-se `export BASALT_CONFIG=~/d/basalt.toml`.\n\n" + mau) == []
    assert _soltos_sem_toml(mau.replace("-m basalt indexar", "-m basalt --config ~/d/basalt.toml indexar")) == []


@pytest.mark.parametrize("doc", DOCS_COM_COMANDOS)
def test_comandos_soltos_dos_docs_encontram_o_toml(doc, parse):
    """ronda 8 (instalação): o `iniciar` grava o toml na pasta dos dados; `python3 -m basalt indexar` (ou serve, importar…)
    sem `--config` nem `BASALT_CONFIG` procura noutros sítios e não o encontra. Antes só o `serve` do README o recebia."""
    texto = _ler(doc)
    assert comandos(texto), f"{doc}: o extractor não viu nenhum comando"
    for origem, cmd, tokens in comandos(texto):
        parse(tokens)
    falta = _soltos_sem_toml(texto)
    assert not falta, f"{doc}: comandos sem caminho para o basalt.toml e sem `export BASALT_CONFIG=` no doc: {falta}"


def test_chaves_comentadas_do_exemplo_sao_as_que_o_servidor_le(tmp_path, monkeypatch):
    """ronda 8 (configuração): quem tira o `#` às linhas do exemplo tem de obter a opção — um nome trocado era ignorado calado."""
    from basalt import config
    for v in [k for k in __import__("os").environ if k.startswith("BASALT_")]:
        monkeypatch.delenv(v)
    texto = _ler("basalt/basalt.toml.exemplo").replace("/var/lib/basalt", str(tmp_path / "dados"))
    for chave in ("anfitrioes", "espaco_partilhado", "proxies_de_confianca"):
        assert re.search(rf"^# {chave} = ", texto, re.M), f"o exemplo deixou de mostrar `{chave}`"
        texto = re.sub(rf"^# ({chave} = )", r"\1", texto, flags=re.M)
    texto = texto.replace("espaco_partilhado = false", "espaco_partilhado = true")
    p = tmp_path / "basalt.toml"
    p.write_text(texto, encoding="utf-8")
    cfg = config.carregar(p)
    assert cfg.anfitrioes == ("basalt.exemplo.pt",) and cfg.espaco_partilhado is True
    assert cfg.auth.proxies_de_confianca == ("203.0.113.5",)
