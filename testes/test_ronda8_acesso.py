"""Ronda 8 — um espaço por utilizador, o basalt.toml relê-se em execução (sessões honestas), Host/Origin contra DNS rebinding,
`--config` vale para tudo, X-Forwarded-For só de proxies de confiança, o MCP não esvazia notas, `arrumar --auto`, importar
(anexos em subpastas, duplicados, tamanho), `/api/saude` e o toml validado ANTES de ser substituído. Cada teste falha no código anterior (git archive 5347bc6) pela razão certa."""
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from basalt import __main__ as cli
from basalt import auth, config, mcp, obsidian, web
from basalt import ia as ia_mod
from conftest import FM, ServidorLocal, sha, toml_de

RAIZ = Path(__file__).resolve().parent.parent
ANA, BEA, CEA = "ana@exemplo.pt", "bea@exemplo.pt", "cea@exemplo.pt"


def _toml(tmp_path, modo="cabecalho", utilizadores=None, basalt="", auth_=""):
    """um basalt.toml do toml_de com chaves extra DENTRO de [basalt] / [auth] (o `extra` do toml_de vai para o fim)."""
    t = toml_de(0, tmp_path / "dados", modo, None, utilizadores)
    t = t.replace("[basalt]\n", "[basalt]\n" + basalt, 1).replace("[auth]\n", "[auth]\n" + auth_, 1)
    p = tmp_path / "basalt.toml"
    p.write_text(t, encoding="utf-8")
    return p


def _rescrever(p: Path, texto: str):
    """escreve e garante que a marca (mtime) muda mesmo num sistema de ficheiros grosseiro."""
    antes = p.stat().st_mtime_ns
    p.write_text(texto, encoding="utf-8")
    os.utime(p, ns=(antes + 2_000_000, antes + 2_000_000))


def _nota(s, nome):
    """uma nota na cópia do espaço «t» e o hash que o PUT tem de trazer."""
    esp = s.espaco("t").criar()
    texto = FM.format(nome=nome) + "\nolá\n"
    (esp.copia / f"{nome}.md").write_text(texto, encoding="utf-8")
    return {"texto": texto + "mais\n", "base_hash": sha(texto)}


class _Servidor(ServidorLocal):
    """ServidorLocal a partir de um basalt.toml já escrito."""

    def __init__(self, toml: Path):
        self.toml, self.dados = toml, toml.parent / "dados"
        self.cfg = config.carregar(toml)
        self.srv = web.criar_servidor(self.cfg, host="127.0.0.1", porta=0)
        self.porta = self.srv.server_address[1]
        self.fio = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.fio.start()


@pytest.fixture
def servidor(tmp_path):
    feitos = []

    def fazer(**k):
        s = _Servidor(_toml(tmp_path, **k))
        feitos.append(s)
        return s
    yield fazer
    for s in feitos:
        s.parar()


def correr(*args):
    r = subprocess.run([sys.executable, "-m", "basalt", *args], cwd=RAIZ, capture_output=True, text=True,
                       env={**os.environ, "BASALT_CONFIG": "", "BASALT_SENHA": ""})
    return r.returncode, r.stdout, r.stderr


# ───────────────────────── ronda 8 (um espaço por utilizador) ─────────────────────────
def test_config_recusa_dois_utilizadores_no_mesmo_espaco(tmp_path):
    dois = [{"email": ANA, "espaco": "t"}, {"email": BEA, "espaco": "t"}]
    with pytest.raises(config.ConfigInvalida, match="MESMO espaço"):
        config.carregar(_toml(tmp_path, utilizadores=dois))
    cfg = config.carregar(_toml(tmp_path, utilizadores=dois, basalt="espaco_partilhado = true\n"))   # só de propósito
    assert cfg.espaco_partilhado and [u.espaco for u in cfg.utilizadores] == ["t", "t"]


def test_variaveis_de_pasta_so_com_um_espaco(tmp_path):
    toml = _toml(tmp_path, utilizadores=[{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}])
    with pytest.raises(config.ConfigInvalida, match="BASALT_REAL"):           # fixava a MESMA pasta para os dois
        config.carregar(toml, ambiente={"BASALT_REAL": str(tmp_path / "x")})
    config.carregar(toml, ambiente={})
    um = _toml(tmp_path, utilizadores=[{"email": ANA, "espaco": "a"}])
    config.carregar(um, ambiente={"BASALT_REAL": str(tmp_path / "x")})        # um só espaço: pode


def test_cli_utilizador_novo_nunca_cai_no_espaco_de_outro(tmp_path):
    d = tmp_path / "dados"
    rc, out, err = correr("iniciar", "--dados", str(d), "--email", "ana@a.pt", "--senha", "segredo-forte-1")
    assert rc == 0, err
    assert "python3 -m basalt --config" in out and "BASALT_CONFIG=" not in out   # a dica na ordem que o parser aceita
    toml = d / "basalt.toml"
    rc, out, err = correr("--config", str(toml), "utilizador", "novo", "ana@b.pt", "--senha", "segredo-forte-2")
    assert rc == 0, err
    cfg = config.carregar(toml)
    assert cfg.utilizador("ana@a.pt").espaco == "ana" and cfg.utilizador("ana@b.pt").espaco == "ana_2"
    rc, out, err = correr("--config", str(toml), "utilizador", "novo", CEA, "--espaco", "ana", "--senha", "segredo-forte-3")
    assert rc != 0 and "já é de ana@a.pt" in err
    assert [u.email for u in config.carregar(toml).utilizadores] == ["ana@a.pt", "ana@b.pt"]


# ───────────────────────── ronda 8 (configuração relida em execução): o toml relê-se; sessões honestas ─────────────────────────
def test_toml_relido_sem_reiniciar(servidor):
    s = servidor(utilizadores=[{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}])
    assert s.get("/", email=BEA).codigo == 200
    so_ana = toml_de(0, s.dados, "cabecalho", None, [{"email": ANA, "espaco": "a"}])
    _rescrever(s.toml, so_ana)
    assert s.get("/", email=BEA).codigo == 403                                # revogada no pedido seguinte
    assert s.get("/", email=ANA).codigo == 200
    _rescrever(s.toml, toml_de(0, s.dados, "cabecalho", None, [{"email": ANA, "espaco": "a"}, {"email": CEA, "espaco": "c"}]))
    assert s.get("/", email=CEA).codigo == 200                                # acrescentada sem reiniciar
    com_cea = s.toml.read_text(encoding="utf-8")
    _rescrever(s.toml, "isto não é um toml [[[")                              # inválido: ronda 9 (achado G4 da ronda 2) — ninguém entra
    assert s.get("/", email=CEA).codigo in (401, 403) and s.get("/", email=ANA).codigo in (401, 403)
    _rescrever(s.toml, com_cea)                                               # corrigido: volta sem reiniciar
    assert s.get("/", email=CEA).codigo == 200 and s.get("/", email=BEA).codigo == 403


def test_sessao_so_conta_quando_ficou_gravada(tmp_path):
    senha = "uma senha comprida"
    cfg = config.carregar(_toml(tmp_path, "senha", [{"email": ANA, "espaco": "a", "senha_hash": auth.hash_senha(senha)}]))
    pasta = tmp_path / "sessoes"
    pasta.mkdir()
    aut = auth.Autenticador(cfg, pasta / "sessoes.json")
    os.chmod(pasta, 0o500)
    try:
        ok, motivo = aut.entrar(ANA, senha, "127.0.0.1")
        assert not ok and motivo.startswith("Não consegui guardar a sessão") and not aut._sessoes
    finally:
        os.chmod(pasta, 0o700)
    ok, token = aut.entrar(ANA, senha, "127.0.0.1")
    assert ok
    os.chmod(pasta, 0o500)
    try:
        ok, motivo = aut.sair(token)
        assert ok is False and "não consegui gravar o ficheiro de sessões" in motivo
    finally:
        os.chmod(pasta, 0o700)
    assert aut.sair("token-que-nunca-existiu") == (True, "")



def test_mudar_a_senha_no_toml_termina_as_sessoes_dessa_conta(tmp_path):
    """ronda 8 (configuração relida em execução): mudar a senha no basalt.toml termina as sessões abertas DESSA conta (uma sessão
    roubada não sobrevive à troca de senha); as das outras contas ficam (controlo)."""
    senha = "uma senha comprida"
    def contas(h_ana):
        return [{"email": ANA, "espaco": "a", "senha_hash": h_ana}, {"email": BEA, "espaco": "b", "senha_hash": h_bea}]
    h_ana, h_bea = auth.hash_senha(senha), auth.hash_senha(senha)
    p = _toml(tmp_path, "senha", contas(h_ana))
    aut = auth.Autenticador(config.carregar(p), tmp_path / "sessoes.json")
    ok_a, tok_a = aut.entrar(ANA, senha, "127.0.0.1")
    ok_b, tok_b = aut.entrar(BEA, senha, "127.0.0.1")
    assert ok_a and ok_b
    quem = lambda tok: aut.utilizador_do_pedido({"Cookie": f"{auth.COOKIE}={tok}"}, "127.0.0.1")
    assert quem(tok_a).email == ANA and quem(tok_b).email == BEA
    _rescrever(p, p.read_text(encoding="utf-8").replace(h_ana, auth.hash_senha("outra senha comprida")))
    assert aut.reler_se_mudou()
    assert quem(tok_a) is None, "a sessão de quem mudou de senha continuou viva"
    assert quem(tok_b).email == BEA                                           # controlo: a outra conta não sai
    assert aut.entrar(ANA, "outra senha comprida", "127.0.0.1")[0]


# ───────────────────────── ronda 8 (Host/Origin): contra DNS rebinding ─────────────────────────
def test_modo_aberto_so_responde_por_nomes_nossos(servidor):
    s = servidor(modo="aberto")
    p = s.porta
    assert s.get("/", email=None, cabecalhos={"Host": f"127.0.0.1:{p}"}).codigo == 200
    assert s.get("/", email=None, cabecalhos={"Host": f"localhost:{p}"}).codigo == 200
    r = s.get("/", email=None, cabecalhos={"Host": "notas.mau.exemplo.com"})   # DNS rebinding: o nome do atacante → 127.0.0.1
    assert r.codigo == 421 and "anfitrioes" in r.json["erro"]
    corpo = _nota(s, "x")
    r = s.pedido("PUT", s.nota_url("x"), corpo, email=None,
                 cabecalhos={"Host": f"127.0.0.1:{p}", "Origin": "https://mau.exemplo.com"})
    assert r.codigo == 403 and "outro sítio" in r.json["erro"]
    r = s.pedido("PUT", s.nota_url("x"), corpo, email=None,
                 cabecalhos={"Host": f"127.0.0.1:{p}", "Sec-Fetch-Site": "cross-site"})
    assert r.codigo == 403 and "outro sítio" in r.json["erro"]
    r = s.pedido("PUT", s.nota_url("x"), corpo, email=None,
                 cabecalhos={"Host": f"127.0.0.1:{p}", "Origin": f"http://127.0.0.1:{p}", "Sec-Fetch-Site": "same-origin"})
    assert r.codigo == 200, r.corpo


def test_atras_de_proxy_sem_lista_nao_se_confere_o_host(servidor):
    s = servidor(modo="cabecalho")          # o nginx pode pôr Host = 127.0.0.1:8121 ou o nome público: sem lista, não se adivinha
    assert s.get("/", cabecalhos={"Host": "notas.exemplo.pt"}).codigo == 200


def test_com_anfitrioes_confere_em_qualquer_modo(servidor):
    s = servidor(modo="cabecalho", basalt='anfitrioes = ["Notas.Exemplo.PT"]\n')
    assert s.get("/", cabecalhos={"Host": "notas.exemplo.pt"}).codigo == 200
    assert s.get("/", cabecalhos={"Host": f"127.0.0.1:{s.porta}"}).codigo == 200
    assert s.get("/", cabecalhos={"Host": "outro.exemplo.pt"}).codigo == 421
    bom = {"Host": "notas.exemplo.pt", "Origin": "https://notas.exemplo.pt"}
    mau = {"Host": "notas.exemplo.pt", "Origin": "https://outro.exemplo.pt"}
    assert s.pedido("PUT", s.nota_url("x"), _nota(s, "x"), cabecalhos=mau).codigo == 403
    assert s.pedido("PUT", s.nota_url("x"), _nota(s, "x"), cabecalhos=bom).codigo == 200
    with pytest.raises(config.ConfigInvalida, match="anfitrioes"):
        config.carregar(_toml(s.toml.parent, basalt='anfitrioes = ["http://x/y"]\n'))


def test_mcp_confere_o_origin_contra_a_lista_nao_contra_o_host(tmp_path):
    cfg = config.carregar(_toml(tmp_path, "aberto"))
    nossos = mcp.anfitrioes_mcp(cfg, "notas.mau.exemplo.com")            # o Host vem do mesmo atacante: não conta
    assert "notas.mau.exemplo.com" not in nossos and {"localhost", "127.0.0.1"} <= set(nossos)
    assert not mcp.origem_ok("http://notas.mau.exemplo.com", nossos)
    assert mcp.origem_ok("http://localhost:8121", nossos)
    cfg2 = config.carregar(_toml(tmp_path, "cabecalho"))                  # sem nome público conhecido: a mesma origem que o Host
    assert mcp.origem_ok("https://notas.exemplo.pt", mcp.anfitrioes_mcp(cfg2, "notas.exemplo.pt"))
    cfg3 = config.carregar(_toml(tmp_path, "cabecalho", basalt='anfitrioes = ["notas.exemplo.pt"]\n'))
    assert not mcp.origem_ok("https://outro.exemplo.pt", mcp.anfitrioes_mcp(cfg3, "outro.exemplo.pt"))


# ───────────────────────── ronda 8 (--config para tudo) ─────────────────────────
def test_a_config_pedida_sobrepoe_a_do_ambiente(tmp_path, monkeypatch):
    toml = _toml(tmp_path)
    monkeypatch.setenv("BASALT_CONFIG", str(tmp_path / "outro" / "basalt.toml"))
    cfg = config.carregar(toml)
    config.exportar_ambiente(cfg)
    assert os.environ["BASALT_CONFIG"] == str(toml.resolve())                # os filhos (sig, IA, fontes_extra) lêem ESTA
    assert config.activar(cfg) is cfg and ia_mod.actual().cfg is cfg.ia


# ───────────────────────── ronda 8 (X-Forwarded-For) ─────────────────────────
def _h(peer, xff, proxies=()):
    h = web.H.__new__(web.H)
    h.client_address = (peer, 4321)
    h.headers = {"X-Forwarded-For": xff} if xff else {}
    h.cfg = config.Config(dados=Path("/nada"), auth=config.AuthConfig(modo="aberto", proxies_de_confianca=proxies))
    return h


# IPs de documentação (RFC 5737, 198.51.100.x, 203.0.113.x): o pacote não pode ter nenhum IP privado (test_sem_rasto);
# o comportamento é o mesmo, proxy_de_confianca não distingue redes privadas de públicas
def test_xff_le_se_da_direita():
    assert web.H._ip(_h("127.0.0.1", "1.2.3.4, 203.0.113.7")) == "203.0.113.7"    # o 1.º podia ser inventado pelo visitante
    assert web.H._ip(_h("127.0.0.1", "1.2.3.4, 127.0.0.1")) == "1.2.3.4"
    assert web.H._ip(_h("127.0.0.1", "")) == "127.0.0.1"
    assert web.H._ip(_h("127.0.0.1", "lixo")) == "127.0.0.1"


def test_so_proxies_de_confianca(tmp_path):
    assert web.H._ip(_h("198.51.100.2", "1.2.3.4")) == "198.51.100.2"                               # Docker sem lista: ignora-se
    assert web.H._ip(_h("198.51.100.2", "1.2.3.4", ("198.51.100.0/24",))) == "1.2.3.4"
    assert web.H._ip(_h("198.51.100.2", "9.9.9.9, 1.2.3.4, 198.51.100.3", ("198.51.100.0/24",))) == "1.2.3.4"
    with pytest.raises(config.ConfigInvalida, match="proxies_de_confianca"):
        config.carregar(_toml(tmp_path, auth_='proxies_de_confianca = ["lixo"]\n'))
    assert config.carregar(_toml(tmp_path, auth_='proxies_de_confianca = ["203.0.113.5", "198.51.100.0/24"]\n')).auth.proxies_de_confianca \
        == ("203.0.113.5", "198.51.100.0/24")


# ───────────────────────── ronda 8 (o MCP não esvazia notas) ─────────────────────────
def test_mcp_texto_so_com_espacos_nao_esvazia(tmp_path):
    cfg = config.carregar(_toml(tmp_path, "aberto"))
    u = cfg.utilizadores[0]

    class Web:
        chamadas = []

        def gravar(self, nome, texto, base):
            self.chamadas.append((nome, texto))
            return 200, {"hash": "x"}
    w = Web()
    srv = mcp.ServidorMCP(cfg, web=w)
    r, erro = srv._t_escrever_nota({"nome": "n", "texto": "   \n\t ", "base_hash": "abc"}, u)
    assert erro and "vazio" in r["erro"] and not w.chamadas
    r, erro = srv._t_acrescentar_nota({"nome": "n", "texto": " "}, u)
    assert erro and "vazio" in r["erro"] and not w.chamadas


# ───────────────────────── ronda 8 (arrumar --auto) ─────────────────────────
def test_arrumar_auto_existe_e_sem_ia_avisa(tmp_path):
    d = tmp_path / "dados"
    rc, out, err = correr("iniciar", "--dados", str(d), "--email", ANA, "--senha", "segredo-forte-1", "--sem-ia")
    assert rc == 0, err
    rc, out, err = correr("--config", str(d / "basalt.toml"), "arrumar", "--auto", "--simular")   # o que o timer chama
    assert rc == 0, out + err
    assert "precisa de uma IA" in err


# ───────────────────────── ronda 8 (importar anexos) ─────────────────────────
def test_importar_anexos_em_subpastas_duplicados_e_tamanho(tmp_path):
    cfg = config.carregar(_toml(tmp_path))
    esp = cfg.espaco("t").criar()
    cofre = tmp_path / "cofre"
    (cofre / "imagens").mkdir(parents=True)
    (cofre / "imagens" / "foto.png").write_bytes(b"\x89PNG nova")
    (cofre / "igual.png").write_bytes(b"igual")
    (cofre / "dup.png").write_bytes(b"conteudo novo")
    with open(cofre / "grande.png", "wb") as f:
        f.truncate(26 * 1024 * 1024)                                       # esparso: 26 MiB sem escrever 26 MiB
    (cofre / "nota.md").write_text("Ver ![[imagens/foto.png]], ![[grande.png]], ![[igual.png]] e ![[dup.png|a foto]].\n", encoding="utf-8")
    esp.anexos.mkdir(parents=True, exist_ok=True)
    (esp.anexos / "igual.png").write_bytes(b"igual")
    (esp.anexos / "dup.png").write_bytes(b"conteudo velho")
    r = obsidian.importar(cofre, esp)
    # ronda 9 (achado M3 da ronda 2): o anexo com o mesmo nome e outro conteúdo entra com outro nome, e a nota aponta para ele
    assert sorted(r["anexos"]) == ["dup_2.png", "foto.png", "igual.png"]
    assert (esp.anexos / "foto.png").read_bytes() == b"\x89PNG nova"
    assert (esp.anexos / "dup.png").read_bytes() == b"conteudo velho" and (esp.anexos / "dup_2.png").read_bytes() == b"conteudo novo"
    assert not (esp.anexos / "grande.png").exists()
    texto = (esp.real / "nota.md").read_text(encoding="utf-8")
    assert "![[foto.png]]" in texto and "imagens/" not in texto and "![[dup_2.png|a foto]]" in texto
    motivos = dict(r["saltadas"])
    assert "grande" in motivos["grande.png"] and "dup.png" not in motivos


# ───────────────────────── /api/saude e o toml validado antes de substituir ─────────────────────────
def test_saude_responde_sem_sessao(servidor):
    s = servidor(modo="senha", utilizadores=[{"email": ANA, "espaco": "a", "senha_hash": "x"}])
    r = s.get("/api/saude", email=None)
    assert r.codigo == 200 and r.json == {"ok": True}
    assert s.get(s.nota_url("x"), email=None).codigo != 200                 # o resto continua a pedir sessão


def test_escrever_toml_valida_antes_de_substituir(tmp_path):
    toml = _toml(tmp_path)
    antes = toml.read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="ficaria inválido"):
        cli._escrever_toml(toml, antes.replace('modo = "cabecalho"', 'modo = "lixo"'))
    assert toml.read_text(encoding="utf-8") == antes and not toml.with_suffix(".toml.tmp").exists()
