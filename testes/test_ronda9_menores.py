"""Ronda 9 (achados menores M2, M4, M6 e M7 da ronda 2) — o que ainda ficava por fechar depois dos achados graves.

  · M2 — «juntar à nota» uma ideia da Entrada: o trinco da Entrada só cobria o arquivar, não a operação inteira; duas juntas da mesma ideia
    a notas diferentes davam dois 200 e duas notas alteradas. Agora confirmar pendente + escrever a nota + arquivar correm sob o mesmo trinco
    (uma passa, a outra recebe 409), e se o arquivar falhar a nota volta ao que era.
  · M4 — o que se guardava para sempre sem necessidade: sessões mortas em sessoes.json (só saíam se alguém voltasse com o cookie) e tentativas
    por IP de quem nunca voltou. Agora saem ao carregar e, durante o uso, no máximo de hora a hora; um disco que não grava não derruba o pedido.
    (As salvaguardas do CAS já se podavam aos 60 dias: o teste é o controlo que fixa o prazo anunciado em docs/PRIVACIDADE.md.)
  · M6 — `arrumar --auto` (o que o timer nocturno chama) não tratava a Entrada, só o resto: passa a fazer o mesmo que uma volta do vigia do servidor.
  · M7 — «basalt.exemplo.pt.» (com ponto final) é a mesma máquina que «basalt.exemplo.pt»; e `escutar = "::1"` tem de escutar mesmo em IPv6.
Cada teste de comportamento falha no código anterior a esta ronda (f52b141) por asserção e pela razão certa (menos os controlos, marcados)."""
import json
import os
import subprocess
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

import pytest

from basalt import auth, cas, config, mcp, web
from basalt.config import AuthConfig, Config, Utilizador
from conftest import FM, ServidorLocal, toml_de
from ollama_falso import OllamaFalso

RAIZ = Path(__file__).resolve().parent.parent
ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"
SENHA = "uma senha comprida"
HASH = auth.hash_senha(SENHA)          # 40 ms: calculado uma vez para o ficheiro todo
T = "2026-10-08T10:00:00"


# ───────────────────────── M2: juntar à nota sob o trinco da Entrada ─────────────────────────
def _ideia(memoria, texto="comprar velas para os barcos"):
    memoria.nota("nota_a", FM.format(nome="nota_a") + "\nA\n")
    memoria.nota("nota_b", FM.format(nome="nota_b") + "\nB\n")
    inbox = memoria.esp.inbox
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(json.dumps({"t": T, "texto": texto, "de": "web"}) + "\n", encoding="utf-8")
    return inbox


def _registo(inbox):
    return json.loads(inbox.read_text(encoding="utf-8").splitlines()[0])


def test_duas_juntas_da_mesma_ideia_a_notas_diferentes_so_uma_passa(memoria, monkeypatch):
    inbox = _ideia(memoria)
    original = web.gravar
    chegaram, juntos = [], threading.Event()

    def gravar_a_dois(nome, texto, base_hash):
        """força a sobreposição: nenhuma das duas escreve até as duas estarem a escrever (sem trinco que as separe, chegam as duas)."""
        chegaram.append(nome)
        if len(chegaram) == 2:
            juntos.set()
        juntos.wait(1.0)
        return original(nome, texto, base_hash)
    monkeypatch.setattr(web, "gravar", gravar_a_dois)
    codigos = {}

    def juntar(nome):
        codigos[nome] = web.entrada_juntar(T, nome)[0]
    fios = [threading.Thread(target=juntar, args=(n,)) for n in ("nota_a", "nota_b")]
    for f in fios:
        f.start()
    for f in fios:
        f.join(15)
    assert sorted(codigos.values()) == [200, 409], f"as duas juntas passaram: {codigos}"
    mudadas = [n for n in ("nota_a", "nota_b") if "comprar velas" in memoria.ler("copia", n)]
    assert len(mudadas) == 1 and codigos[mudadas[0]] == 200, f"notas alteradas: {mudadas}, códigos: {codigos}"
    reg = _registo(inbox)
    assert reg["arquivado"] and reg["juntado_em"] == mudadas[0], reg


def test_ideia_que_ja_nao_esta_pendente_da_409_e_nao_toca_em_nenhuma_nota(memoria):
    inbox = _ideia(memoria)
    assert web.entrada_juntar(T, "nota_a")[0] == 200
    b_antes = memoria.ler("copia", "nota_b")
    codigo, r = web.entrada_juntar(T, "nota_b")
    assert codigo == 409 and "já não está" in r["erro"], (codigo, r)
    assert memoria.ler("copia", "nota_b") == b_antes and _registo(inbox)["juntado_em"] == "nota_a"


@pytest.mark.parametrize("falha", ["devolve_none", "levanta_oserror"])
def test_se_o_arquivar_falha_a_nota_destino_volta_ao_que_era(memoria, monkeypatch, falha):
    _ideia(memoria)
    antes = memoria.ler("copia", "nota_a")

    def mudar_que_falha(*a, **k):
        if falha == "levanta_oserror":
            raise OSError(28, "No space left on device")
        return None                                     # None = a ideia já não estava pendente
    monkeypatch.setattr(web.entrada_mod, "mudar", mudar_que_falha)
    try:
        codigo, r = web.entrada_juntar(T, "nota_a")
    except Exception as e:                              # o código anterior deixava rebentar (ou ignorava o None): é a falha que se quer ver
        pytest.fail(f"entrada_juntar levantou {e!r}")
    assert codigo == 500 and "ficou como estava" in r["erro"], (codigo, r)
    assert memoria.ler("copia", "nota_a") == antes, "a nota ficou com a linha juntada apesar de a ideia não ter sido arquivada"


# ───────────────────────── M4: sessões mortas e tentativas de IPs que não voltam ─────────────────────────
def _aut(tmp_path):
    c = Config(dados=tmp_path / "dados")
    c.auth = AuthConfig(modo="senha")
    c.utilizadores = [Utilizador(email=ANA, nome="Ana", senha_hash=HASH, admin=True), Utilizador(email=BEA, nome="Bea", senha_hash=HASH)]
    return auth.Autenticador(c, tmp_path / "sessoes.json")


def _no_ficheiro(tmp_path):
    return json.loads((tmp_path / "sessoes.json").read_text(encoding="utf-8"))


def test_ao_carregar_saem_do_ficheiro_as_sessoes_que_ja_nao_valem(tmp_path):
    agora, cred = time.time(), auth.impressao_credencial(HASH)
    (tmp_path / "sessoes.json").write_text(json.dumps({
        "viva": {"email": ANA, "expira": agora + 3600, "criada": agora, "cred": cred},
        "expirada": {"email": ANA, "expira": agora - 1, "criada": agora - 7200, "cred": cred},
        "sem_utilizador": {"email": "fantasma@exemplo.pt", "expira": agora + 3600, "criada": agora, "cred": cred},
        "senha_mudada": {"email": ANA, "expira": agora + 3600, "criada": agora, "cred": auth.impressao_credencial(auth.hash_senha("outra senha comprida"))}}))
    _aut(tmp_path)                                      # só carregar: nenhum cookie é usado
    assert set(_no_ficheiro(tmp_path)) == {"viva"}, "as sessões mortas continuam em sessoes.json"


@pytest.mark.parametrize("como", ["outro_entra", "cookie_desconhecido"])
def test_durante_o_uso_a_sessao_expirada_sai_do_ficheiro_sem_usar_o_cookie_dela(tmp_path, monkeypatch, como):
    relogio = [time.time()]
    monkeypatch.setattr(auth, "time", types.SimpleNamespace(time=lambda: relogio[0]))
    a = _aut(tmp_path)
    ok, tok_a = a.entrar(ANA, SENHA, "198.51.100.1")
    assert ok and auth.hash_token(tok_a) in _no_ficheiro(tmp_path)
    relogio[0] += (a.cfg.auth.sessao_horas + 2) * 3600           # a sessão da ana expirou há 2 h (e passou mais de 1 h desde a última poda)
    if como == "outro_entra":
        ok, tok_b = a.entrar(BEA, SENHA, "198.51.100.2")
        assert ok and set(_no_ficheiro(tmp_path)) == {auth.hash_token(tok_b)}
    else:
        assert a.utilizador_do_pedido({"Cookie": f"{auth.COOKIE}=nunca-existiu"}) is None
        assert _no_ficheiro(tmp_path) == {}, "a sessão expirada continua em sessoes.json"


def test_um_disco_que_nao_grava_nao_derruba_o_pedido(tmp_path, monkeypatch, capsys):
    relogio = [time.time()]
    monkeypatch.setattr(auth, "time", types.SimpleNamespace(time=lambda: relogio[0]))
    a = _aut(tmp_path)
    ok, tok = a.entrar(ANA, SENHA, "198.51.100.1")
    assert ok
    relogio[0] += (a.cfg.auth.sessao_horas + 2) * 3600

    def sem_espaco():
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(a, "_guardar", sem_espaco)
    try:
        quem = a.utilizador_do_pedido({"Cookie": f"{auth.COOKIE}={tok}"})
    except OSError as e:
        pytest.fail(f"o pedido levantou {e!r} por o disco não deixar gravar")
    assert quem is None
    assert "não consegui gravar" in capsys.readouterr().err


def test_tentativas_de_ips_que_nao_voltam_esquecem_se(tmp_path):
    a = _aut(tmp_path)
    janela, agora = a.cfg.auth.janela_minutos * 60, time.time()
    a._falhas["198.51.100.7"] = [agora - janela - 5]             # há mais que a janela, de um IP que nunca voltou
    a._falhas["198.51.100.8"] = [agora - 5]                      # dentro da janela
    assert a.entrar(ANA, "errada errada", "198.51.100.9") == (False, "E-mail ou senha errados.")
    assert "198.51.100.7" not in a._falhas, "a tentativa antiga de outro IP ficou na memória"
    assert len(a._falhas["198.51.100.8"]) == 1 and len(a._falhas["198.51.100.9"]) == 1     # as de dentro da janela contam


def test_controlo_salvaguarda_de_61_dias_apagada_e_de_59_mantida(memoria):
    """controlo (o código já fazia isto): ao guardar uma salvaguarda nova do CAS saem as do mesmo espaço com mais de 60 dias — o prazo de docs/PRIVACIDADE.md."""
    assert cas.SALVOS_DIAS == 60
    pasta = memoria.esp.cas_salvos
    pasta.mkdir(parents=True, exist_ok=True)
    v61, v59 = pasta / "n.20260101-000000.aaaaaaaaaa.md", pasta / "n.20260101-000001.bbbbbbbbbb.md"
    v61.write_bytes(b"61 dias"); v59.write_bytes(b"59 dias")
    agora = time.time()
    os.utime(v61, (agora - 61 * 86400,) * 2)
    os.utime(v59, (agora - 59 * 86400,) * 2)
    alvo = memoria.real / "n.md"
    alvo.write_bytes(b"BASE")
    saiu = memoria.real / ".n.md.teste.tmp"
    saiu.write_bytes(b"NOVO")
    cas._guardar_a_parte(cas.Troca(alvo, "e", "n", saiu))
    assert not v61.exists() and v59.exists()


# ───────────────────────── M6: `arrumar --auto` faz o que o vigia do servidor ─────────────────────────
LIXO = json.dumps({"acao": "lixo", "nota": "", "confianca": 0.95, "motivo": "só uma saudação"})


def _ficheiros(pasta: Path):
    return {str(p.relative_to(pasta)): p.read_bytes() for p in sorted(pasta.rglob("*")) if p.is_file()}


def _com_uma_ideia(tmp_path, ollama):
    """um espaço «t» com a IA falsa e uma ideia pendente («ola»: curta e sem palavras raras, que a IA falsa manda para o lixo)."""
    toml = tmp_path / "basalt.toml"
    toml.write_text(toml_de(0, tmp_path / "dados", "aberto", ollama, [{"email": ANA, "espaco": "t", "admin": True}]), encoding="utf-8")
    esp = config.carregar(toml).espaco("t").criar()
    esp.inbox.write_text(json.dumps({"t": T, "texto": "ola", "de": "web"}) + "\n", encoding="utf-8")
    return toml, esp


def _arrumar_auto(toml, tmp_path, *extra):
    """o que o timer nocturno chama, NOUTRO processo e sem nenhuma variável BASALT_* do ambiente (só a --config)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BASALT_") or k in ("BASALT_SIG_ISOLADO", "BASALT_CAS_GRACA")}
    env["HOME"] = str(tmp_path)
    r = subprocess.run([sys.executable, "-m", "basalt", "--config", str(toml), "arrumar", "--auto", *extra], cwd=RAIZ, env=env,
                       capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


def test_arrumar_auto_trata_a_entrada_como_o_vigia_do_servidor(tmp_path):
    ollama = OllamaFalso(); ollama.resposta_chat = LIXO
    try:
        toml, esp = _com_uma_ideia(tmp_path, ollama)
        rc, out, err = _arrumar_auto(toml, tmp_path)
        assert rc == 0, out + err
        reg = json.loads(esp.inbox.read_text(encoding="utf-8").splitlines()[0])
        assert reg.get("arquivado") and reg["auto"]["acao"] == "lixo", f"a ideia continua por tratar: {reg}\n{out}{err}"
        assert len(ollama.pedidos_chat) == 1
    finally:
        ollama.parar()


def test_arrumar_auto_simular_nao_escreve_nada_nem_chama_a_ia(tmp_path):
    """controlo (o --simular já não escrevia): com a Entrada a ser tratada, a simulação continua sem escrever e sem ir à IA."""
    ollama = OllamaFalso(); ollama.resposta_chat = LIXO
    try:
        toml, esp = _com_uma_ideia(tmp_path, ollama)
        antes = _ficheiros(tmp_path / "dados")
        rc, out, err = _arrumar_auto(toml, tmp_path, "--simular")
        assert rc == 0, out + err
        assert _ficheiros(tmp_path / "dados") == antes and ollama.pedidos_chat == []
        assert not json.loads(esp.inbox.read_text(encoding="utf-8").splitlines()[0]).get("arquivado")
    finally:
        ollama.parar()


# ───────────────────────── M7: nomes com ponto final e escutar em IPv6 ─────────────────────────
def _toml_anf(tmp_path, anfitrioes):
    t = toml_de(0, tmp_path / "dados", "cabecalho", None, [{"email": ANA, "espaco": "t", "admin": True}])
    p = tmp_path / "basalt.toml"
    p.write_text(t.replace("[basalt]\n", "[basalt]\nanfitrioes = " + json.dumps(anfitrioes) + "\n", 1), encoding="utf-8")
    return p


class _Local(ServidorLocal):
    def __init__(self, toml: Path, host="127.0.0.1"):
        self.toml, self.dados = toml, toml.parent / "dados"
        self.cfg = config.carregar(toml)
        self.srv = web.criar_servidor(self.cfg, host=host, porta=0)
        self.porta = self.srv.server_address[1]
        self.fio = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.fio.start()


@pytest.mark.parametrize("lista, host", [
    (["basalt.exemplo.pt"], "BASALT.EXEMPLO.PT.:8121"),       # o browser manda o nome com ponto, a lista não o tem
    (["basalt.exemplo.pt."], "basalt.exemplo.pt:8121"),       # a lista tem o ponto, o Host não
    (["Basalt.Exemplo.PT."], "basalt.exemplo.pt.:8121"),      # os dois
])
def test_o_ponto_final_e_as_maiusculas_nao_mudam_o_anfitriao(tmp_path, lista, host):
    try:
        s = _Local(_toml_anf(tmp_path, lista))
    except config.ConfigInvalida as e:
        pytest.fail(f"a lista {lista} foi recusada: {e}")
    try:
        r = s.get("/api/saude", email=None, cabecalhos={"Host": host})
        assert r.codigo == 200, (host, r.codigo, r.corpo[:120])
        assert s.get("/api/saude", email=None, cabecalhos={"Host": "outro.exemplo.pt.:8121"}).codigo == 421      # outro nome continua recusado
        assert s.get("/api/saude", email=None, cabecalhos={"Host": "basalt.exemplo.pt..:8121"}).codigo == 421    # só UM ponto se tira
    finally:
        s.parar()


def test_o_mcp_compara_a_origem_com_a_mesma_forma_do_nome(tmp_path):
    cfg = config.carregar(_toml_anf(tmp_path, ["Basalt.Exemplo.PT"]))
    nossos = mcp.anfitrioes_mcp(cfg, "basalt.exemplo.pt")
    assert mcp.origem_ok("https://basalt.exemplo.pt", nossos)
    assert mcp.origem_ok("https://BASALT.exemplo.pt.", nossos), "a origem com ponto final foi recusada"
    assert not mcp.origem_ok("https://outro.exemplo.pt.", nossos)


def test_escutar_em_ipv6_responde_em_ipv6(tmp_path):
    import socket
    try:
        with socket.socket(socket.AF_INET6) as sk:
            sk.bind(("::1", 0))
    except OSError:
        pytest.skip("esta máquina não tem IPv6 em ::1")
    t = toml_de(0, tmp_path / "dados", "aberto", None, [{"email": ANA, "espaco": "t", "admin": True}]).replace('escutar = "127.0.0.1"', 'escutar = "::1"')
    p = tmp_path / "basalt.toml"
    p.write_text(t, encoding="utf-8")
    cfg = config.carregar(p)
    assert cfg.escutar == "::1"
    try:
        srv = web.criar_servidor(cfg, porta=0)
    except OSError as e:
        pytest.fail(f"o servidor não escuta em IPv6: {e}")
    fio = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    fio.start()
    try:
        with urllib.request.urlopen(f"http://[::1]:{srv.server_address[1]}/api/saude", timeout=10) as r:
            assert r.status == 200 and json.loads(r.read())["ok"] is True
    finally:
        srv.shutdown(); srv.server_close()
