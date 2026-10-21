"""r17 — as contas apagadas: o registo <dados>/contas-apagadas.json, `basalt apagados eliminar` (de vez, aos 30 dias) e, depois
de repor uma cópia de segurança, `basalt apagados reaplicar`.

A política de privacidade promete três coisas: (1) ao pedir o apagamento, a conta desliga-se logo e as sessões terminam; (2) os
dados eliminam-se de vez em 30 dias; (3) uma cópia antiga reposta não traz a conta de volta. Uma conta NOVA criada mais tarde com
o mesmo e-mail tem outro `conta_id` e nunca é tocada. Cada regra tem o caso bom e o mau; `eliminar` e `reaplicar` acabam sempre
com uma linha `resumo: …` fixa (a última do stdout, sem e-mails), para quem os vigia não depender do texto."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from basalt import __main__ as cli, apagados, config, oauth  # noqa: E402
from conftest import toml_de  # noqa: E402
from test_login_google import NOVO, _eu, g  # noqa: E402,F401 (g: fixture do login pela Google com criar_contas)

ANA, BEA, CAI, DIA = "ana@exemplo.pt", "bea@exemplo.pt", "cai@exemplo.pt", "dia@exemplo.pt"
UM_DIA = 86400
FMT = "%Y-%m-%dT%H:%M:%SZ"


class Inst:
    """um basalt.toml + dados numa pasta temporária; a linha de comandos corre no próprio processo (para o relógio de
    `apagados._agora` se poder trocar). Cada conta tem uma nota no seu espaço."""

    def __init__(self, tmp_path, capsys, utilizadores=None, extra_basalt=""):
        self.capsys = capsys
        self.dados, self.toml = tmp_path / "dados", tmp_path / "basalt.toml"
        if utilizadores is None:
            utilizadores = [{"email": ANA, "conta_id": "c-ana", "espaco": "a", "admin": True},
                            {"email": BEA, "conta_id": "c-bea", "espaco": "b"},
                            {"email": CAI, "conta_id": "c-cai", "espaco": "c"},
                            {"email": DIA, "conta_id": "c-dia", "espaco": "d"}]
        t = toml_de(0, self.dados, "cabecalho", None, utilizadores)
        if extra_basalt:
            t = t.replace("git = false\n", "git = false\n" + extra_basalt, 1)
        self.toml.write_text(t, encoding="utf-8")
        cfg = self.cfg()
        for u in cfg.utilizadores:
            esp = cfg.espaco_de(u).criar()
            (esp.real / "nota.md").write_text(f"nota do espaço {u.espaco}\n", encoding="utf-8")

    def cfg(self):
        return config.carregar(self.toml)

    def cli(self, *args):
        self.capsys.readouterr()
        rc = 0
        try:
            cli.main(["--config", str(self.toml), *args])
        except SystemExit as x:
            rc = 0 if x.code is None else (x.code if isinstance(x.code, int) else 1)
            if isinstance(x.code, str):
                print(x.code, file=sys.stderr)
        out, err = self.capsys.readouterr()
        return rc, out, err

    def apagar(self, email, monkeypatch=None, quando=None):
        if quando is not None:
            monkeypatch.setattr(apagados, "_agora", lambda: quando)
        rc, out, err = self.cli("utilizador", "apagar", email, "--sim")
        assert rc == 0, err
        return out

    def registo(self):
        return apagados.ler(self.dados)

    def entrada(self, email):
        sha = apagados.email_sha256(email)
        return next(e for e in self.registo() if e["email_sha256"] == sha)

    def mudar(self, email, **campos):
        lista = self.registo()
        for e in lista:
            if e["email_sha256"] == apagados.email_sha256(email):
                e.update(campos)
        apagados.gravar(self.dados, lista)

    def familias(self, email, so_vivas=False):
        con = sqlite3.connect(self.dados / "oauth.sqlite")
        try:
            q = "SELECT id FROM familias WHERE email=?" + (" AND revogada IS NULL" if so_vivas else "")
            return [r[0] for r in con.execute(q, (email,))]
        finally:
            con.close()

    def contar(self, sql, *args):
        con = sqlite3.connect(self.dados / "oauth.sqlite")
        try:
            return con.execute(sql, args).fetchone()[0]
        finally:
            con.close()


def aos(monkeypatch, t):
    monkeypatch.setattr(apagados, "_agora", lambda: t)


def ultima(out):
    linhas = out.splitlines()
    assert linhas, "o comando não escreveu nada (falta a linha do resumo)"
    return linhas[-1]


def token(inst, email):
    cfg = inst.cfg()
    o = oauth.OAuth(cfg, None)
    t, fam = o.token_pessoal(cfg.utilizador(email), "agente de teste", o.geracao())
    assert t, fam
    return fam


def pedido(inst, email, ident):
    con = sqlite3.connect(inst.dados / "oauth.sqlite")
    try:
        with con:
            con.execute("INSERT INTO pedidos (id, client_id, nome, redirect_uri, explicito, state, challenge, ip, email, criado, expira) "
                        "VALUES (?, 'cliente', 'Cliente', 'https://cliente.exemplo/volta', 1, NULL, 'x', '127.0.0.1', ?, ?, ?)",
                        (ident, email, time.time(), time.time() + 3600))
    finally:
        con.close()


@pytest.fixture
def i(tmp_path, capsys):
    return Inst(tmp_path, capsys)


# ───────────────────────────── 1. o apagamento grava a entrada (antes de mover) ─────────────────────────────
def test_apagar_grava_a_entrada_antes_de_mover_a_pasta_em_utc(i, monkeypatch):
    vistos = []
    mover = shutil.move

    def espiao(origem, destino):
        vistos.append((apagados.ler(i.dados), Path(destino)))
        assert Path(origem).exists() and not Path(destino).exists()
        return mover(origem, destino)

    T = 1_800_000_000.0
    monkeypatch.setattr(cli.shutil, "move", espiao)
    monkeypatch.setattr(apagados, "_agora", lambda: T)
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")          # UTC+14: uma hora local nunca passaria por UTC
    time.tzset()
    try:
        rc, out, err = i.cli("utilizador", "apagar", BEA, "--sim")
    finally:
        monkeypatch.undo()
        time.tzset()
    assert rc == 0, err
    assert len(vistos) == 1, "a pasta não foi movida (ou foi movida duas vezes)"
    lista, destino = vistos[0]
    assert len(lista) == 1, "quando a pasta saiu do sítio, a entrada ainda não estava no registo"
    e = lista[0]
    assert e["email"] == BEA and e["conta_id"] == "c-bea" and e["espaco"] == "b" and e["eliminado_em"] is None
    assert e["email_sha256"] == hashlib.sha256(b"basalt-conta-apagada:bea@exemplo.pt").hexdigest()
    assert e["apagado_em"] == time.strftime(FMT, time.gmtime(T)) == "2027-01-15T08:00:00Z"
    assert e["pasta"] == str(destino.relative_to(i.dados)) and e["pasta"].startswith("apagados/b-")
    assert (i.dados / e["pasta"] / "memoria" / "nota.md").read_text(encoding="utf-8") == "nota do espaço b\n"
    assert f"registo {e['id']}" in out and "2027-02-14" in out           # o prazo: 30 dias depois, em UTC
    assert i.cfg().utilizador(BEA) is None
    assert apagados.email_sha256("  Bea@Exemplo.PT ") == e["email_sha256"]
    assert stat.S_IMODE(os.stat(i.dados / "contas-apagadas.json").st_mode) == 0o600


def test_apagar_sem_poder_gravar_o_registo_nao_muda_o_toml(i):
    antes = i.toml.read_text(encoding="utf-8")
    (i.dados / "contas-apagadas.json").write_text("isto não é JSON", encoding="utf-8")
    rc, out, err = i.cli("utilizador", "apagar", BEA, "--sim")
    assert rc != 0 and "registo das contas apagadas" in err, (rc, out, err)
    assert i.toml.read_text(encoding="utf-8") == antes and (i.dados / "b" / "memoria" / "nota.md").exists()


def test_registo_que_e_uma_ligacao_simbolica_e_recusado(i, tmp_path):
    verdadeiro = tmp_path / "noutro-sitio.json"
    verdadeiro.write_text("[]", encoding="utf-8")
    os.symlink(verdadeiro, i.dados / "contas-apagadas.json")
    antes = i.toml.read_text(encoding="utf-8")
    rc, out, err = i.cli("utilizador", "apagar", BEA, "--sim")
    assert rc != 0 and i.toml.read_text(encoding="utf-8") == antes, (rc, out, err)
    assert verdadeiro.read_text(encoding="utf-8") == "[]"
    with pytest.raises(OSError):
        apagados.gravar(i.dados, [])
    assert verdadeiro.read_text(encoding="utf-8") == "[]" and (i.dados / "contas-apagadas.json").is_symlink()


def test_se_a_pasta_nao_sai_do_sitio_a_entrada_fica_e_diz_para_onde_a_mover(i, monkeypatch):
    def falha(origem, destino):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(cli.shutil, "move", falha)
    rc, out, err = i.cli("utilizador", "apagar", BEA, "--sim")
    e = i.entrada(BEA)
    assert rc != 0 and "move-a à mão" in err and e["pasta"] in err and e["id"] in err, err
    assert i.cfg().utilizador(BEA) is None and e["eliminado_em"] is None


# ───────────────────────────── 2. eliminar: só com --sim, só depois de N dias ─────────────────────────────
def test_eliminar_sem_sim_nao_toca_em_nada(i, monkeypatch):
    token(i, BEA)
    i.apagar(BEA)
    reg = (i.dados / "contas-apagadas.json").read_bytes()
    pasta = i.dados / i.entrada(BEA)["pasta"]
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar")
    assert rc == 0, err
    assert "seria eliminada" in out and ultima(out) == "resumo: a_eliminar=1"
    assert (i.dados / "contas-apagadas.json").read_bytes() == reg
    assert (pasta / "memoria" / "nota.md").exists() and len(i.familias(BEA)) == 1


def test_eliminar_so_leva_as_com_mais_de_n_dias(i, monkeypatch):
    agora = float(int(time.time()))
    i.apagar(BEA, monkeypatch, agora - 40 * UM_DIA)
    i.apagar(CAI, monkeypatch, agora - 30 * UM_DIA)                # exactamente 30 dias: ainda não («mais de»)
    i.apagar(DIA, monkeypatch, agora - 10 * UM_DIA)
    pastas = {e: i.dados / i.entrada(e)["pasta"] for e in (BEA, CAI, DIA)}
    aos(monkeypatch, agora)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0, err
    assert ultima(out) == "resumo: eliminadas=1 falhas=0 voltaram=0"
    assert not pastas[BEA].exists() and i.entrada(BEA)["eliminado_em"] == time.strftime(FMT, time.gmtime(agora))
    assert pastas[CAI].exists() and pastas[DIA].exists()
    assert i.entrada(CAI)["eliminado_em"] is None and i.entrada(DIA)["eliminado_em"] is None

    rc, out, err = i.cli("apagados", "eliminar", "--dias", "5", "--sim")
    assert rc == 0 and ultima(out) == "resumo: eliminadas=2 falhas=0 voltaram=0", (out, err)
    assert not pastas[CAI].exists() and not pastas[DIA].exists()
    assert (i.dados / "a" / "memoria" / "nota.md").exists()                       # a conta viva não foi tocada


def test_dias_negativos_sao_recusados_com_o_resumo(i):
    rc, out, err = i.cli("apagados", "eliminar", "--dias", "-1", "--sim")
    assert rc == 1 and "negativo" in err and ultima(out) == "resumo: eliminadas=0 falhas=1 voltaram=0"


def test_nao_toca_em_pastas_sem_registo(i, monkeypatch):
    solta = i.dados / "apagados" / "x-20250101-000000" / "memoria"
    solta.mkdir(parents=True)
    (solta / "nota.md").write_text("apagada antes da r17\n", encoding="utf-8")
    i.apagar(BEA)
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0 and ultima(out) == "resumo: eliminadas=1 falhas=0 voltaram=0", (out, err)
    assert (solta / "nota.md").read_text(encoding="utf-8") == "apagada antes da r17\n"
    assert "1 pasta(s) em apagados/ sem registo" in out
    rc, out, err = i.cli("apagados", "listar")
    assert rc == 0 and "apagados/x-20250101-000000: sem registo" in out


@pytest.mark.parametrize("caso", ["ligacao", "apagados_ligacao", "fora", "sobe", "nome"])
def test_recusa_ligacoes_simbolicas_e_pastas_fora_de_apagados(i, monkeypatch, tmp_path, caso):
    i.apagar(BEA)
    e = i.entrada(BEA)
    if caso == "ligacao":                    # a pasta da entrada passou a ser uma ligação para fora dos dados
        alvo = tmp_path / "fora" / "b-real"
        alvo.parent.mkdir()
        os.replace(i.dados / e["pasta"], alvo)
        os.symlink(alvo, i.dados / e["pasta"])
        protegida = alvo / "memoria" / "nota.md"
    elif caso == "apagados_ligacao":         # a própria pasta apagados/ é uma ligação
        alvo = tmp_path / "fora-apagados"
        os.replace(i.dados / "apagados", alvo)
        os.symlink(alvo, i.dados / "apagados")
        protegida = alvo / e["pasta"].split("/")[1] / "memoria" / "nota.md"
    elif caso in ("fora", "sobe"):           # a entrada aponta para uma pasta fora de apagados/ (com um nome que «bate»)
        outra = i.dados / "outra" / "b-20250101-000000"
        outra.mkdir(parents=True)
        protegida = outra / "nota.md"
        protegida.write_text("não é para apagar\n", encoding="utf-8")
        i.mudar(BEA, pasta="outra/b-20250101-000000" if caso == "fora" else "apagados/../outra/b-20250101-000000")
    else:                                    # o nome da pasta não bate com o espaço da entrada
        outra = i.dados / "apagados" / "a-20250101-000000"
        outra.mkdir(parents=True)
        protegida = outra / "nota.md"
        protegida.write_text("não é para apagar\n", encoding="utf-8")
        i.mudar(BEA, pasta="apagados/a-20250101-000000")
    assert protegida.exists()
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 1, (out, err)
    assert "recusado" in out and ultima(out) == "resumo: eliminadas=0 falhas=1 voltaram=0", out
    assert protegida.exists(), "apagou uma pasta que não devia"
    e2 = i.entrada(BEA)
    assert e2["eliminado_em"] is None and e2["email"] == BEA                 # fica por eliminar (e volta a tentar-se)


# ───────────────────────────── 3. a base OAuth e as sessões ─────────────────────────────
def test_poda_oauth_so_leva_o_que_e_dessa_conta(i, monkeypatch):
    fam_bea, fam_cai = token(i, BEA), token(i, CAI)
    pedido(i, BEA, "p-bea")
    pedido(i, CAI, "p-cai")
    i.apagar(BEA)
    assert i.familias(BEA) == [fam_bea] and i.familias(BEA, so_vivas=True) == []   # revogada no apagamento, ainda lá está
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0 and ultima(out) == "resumo: eliminadas=1 falhas=0 voltaram=0", (out, err)
    assert "1 ligação(ões) OAuth" in out
    assert i.familias(BEA) == [] and i.contar("SELECT COUNT(*) FROM tokens WHERE familia=?", fam_bea) == 0
    assert i.contar("SELECT COUNT(*) FROM pedidos WHERE email=?", BEA) == 0
    assert i.familias(CAI, so_vivas=True) == [fam_cai]
    assert i.contar("SELECT COUNT(*) FROM tokens WHERE familia=? AND revogado IS NULL", fam_cai) == 1
    assert i.contar("SELECT COUNT(*) FROM pedidos WHERE email=?", CAI) == 1


def test_eliminar_tira_as_sessoes_que_uma_copia_trouxe_de_volta(i, monkeypatch):
    i.apagar(BEA)
    sessoes = i.dados / "sessoes.json"
    sessoes.write_text(json.dumps({"s1": {"email": BEA, "expira": 9e12, "criada": 0, "cred": ""},
                                   "s2": {"email": CAI, "expira": 9e12, "criada": 0, "cred": ""}}), encoding="utf-8")
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0, err
    assert set(json.loads(sessoes.read_text(encoding="utf-8"))) == {"s2"}


def test_a_entrada_eliminada_fica_anonimizada(i, monkeypatch):
    i.apagar(BEA)
    antes = i.entrada(BEA)
    T = time.time() + 31 * UM_DIA
    aos(monkeypatch, T)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0, err
    e = i.registo()[0]
    assert e["email"] is None and e["espaco"] is None and e["pasta"] is None
    assert e["eliminado_em"] == time.strftime(FMT, time.gmtime(T))
    assert (e["id"], e["email_sha256"], e["conta_id"], e["apagado_em"]) == (antes["id"], antes["email_sha256"], "c-bea",
                                                                             antes["apagado_em"])
    assert "@" not in (i.dados / "contas-apagadas.json").read_text(encoding="utf-8")
    rc, out, err = i.cli("apagados", "listar")
    assert rc == 0 and "eliminada a" in out and BEA not in out


# ───────────────────────────── 4. cópias de segurança: exportar e reaplicar ─────────────────────────────
def test_reposicao_de_uma_copia_antiga(i, tmp_path, monkeypatch):
    token(i, BEA)
    copia_dados, copia_toml = tmp_path / "copia-dados", i.toml.read_text(encoding="utf-8")
    shutil.copytree(i.dados, copia_dados, symlinks=True)                         # a cópia de segurança: de ANTES do apagamento
    i.apagar(BEA)
    original = i.entrada(BEA)
    rc, out, err = i.cli("apagados", "exportar")
    assert rc == 0 and BEA not in out, (out, err)
    assert [json.loads(ln) for ln in out.splitlines()] == [{"email_sha256": original["email_sha256"], "conta_id": "c-bea",
                                                            "apagado_em": original["apagado_em"]}]
    lista = tmp_path / "fora-das-copias.jsonl"                                    # guardada FORA das cópias de segurança
    lista.write_text(out, encoding="utf-8")
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    assert ultima(i.cli("apagados", "eliminar", "--sim")[1]) == "resumo: eliminadas=1 falhas=0 voltaram=0"

    # repor a cópia: a Bea volta (conta, pasta e ligações das IAs, sem entrada no registo)
    shutil.rmtree(i.dados)
    shutil.copytree(copia_dados, i.dados, symlinks=True)
    i.toml.write_text(copia_toml, encoding="utf-8")
    assert i.cfg().utilizador(BEA) is not None and i.familias(BEA, so_vivas=True)
    assert not (i.dados / "contas-apagadas.json").exists()

    rc, out, err = i.cli("apagados", "reaplicar", "--lista", str(lista))
    assert rc == 0 and ultima(out) == "resumo: reapagaria=1 recriadas=0 acrescentaria=0 protegidas=0", (out, err)
    assert i.cfg().utilizador(BEA) is not None, "sem --sim não pode mexer"

    rc, out, err = i.cli("apagados", "reaplicar", "--lista", str(lista), "--sim")
    assert rc == 0 and ultima(out) == "resumo: reapagadas=1 recriadas=0 acrescentadas=0 protegidas=0", (out, err)
    assert BEA not in out and BEA not in err
    assert i.cfg().utilizador(BEA) is None and not (i.dados / "b").exists()
    e = i.entrada(BEA)
    assert e["apagado_em"] == original["apagado_em"] and e["conta_id"] == "c-bea" and e["eliminado_em"] is None
    assert i.familias(BEA, so_vivas=True) == []                                  # as ligações cortaram-se logo

    rc, out, err = i.cli("apagados", "eliminar", "--sim")                        # a data é a original: já passou dos 30 dias
    assert rc == 0 and ultima(out) == "resumo: eliminadas=1 falhas=0 voltaram=0", (out, err)
    assert not list((i.dados / "apagados").iterdir()) and i.familias(BEA) == []

    rc, out, err = i.cli("apagados", "reaplicar", "--lista", str(lista), "--sim")  # idempotente
    assert rc == 0 and ultima(out) == "resumo: reapagadas=0 recriadas=0 acrescentadas=0 protegidas=0", (out, err)
    assert len(i.registo()) == 1


def test_a_conta_recriada_com_o_mesmo_email_nao_e_tocada(i, tmp_path, monkeypatch):
    token(i, BEA)
    i.apagar(BEA)
    antiga = i.entrada(BEA)
    lista = tmp_path / "lista.jsonl"
    lista.write_text(i.cli("apagados", "exportar")[1], encoding="utf-8")
    rc, out, err = i.cli("utilizador", "novo", BEA, "--nome", "Bea")           # a mesma pessoa (ou outra) volta: conta NOVA
    assert rc == 0, err
    cfg = i.cfg()
    nova = cfg.utilizador(BEA)
    assert re.fullmatch(r"[0-9a-f]{16}", nova.conta_id) and nova.conta_id != "c-bea"
    esp = cfg.espaco_de(nova).criar()
    (esp.real / "nova.md").write_text("da conta nova\n", encoding="utf-8")
    fam_nova = token(i, BEA)
    con = sqlite3.connect(i.dados / "oauth.sqlite")
    with con:                                # criada um minuto depois do apagamento (o teste corre todo no mesmo segundo)
        con.execute("UPDATE familias SET criado=? WHERE id=?", (apagados.instante(antiga["apagado_em"]) + 60, fam_nova))
    con.close()
    (i.dados / "sessoes.json").write_text(json.dumps({"s-nova": {"email": BEA, "expira": 9e12, "criada": 0, "cred": ""}}),
                                          encoding="utf-8")
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0 and ultima(out) == "resumo: eliminadas=1 falhas=0 voltaram=0", (out, err)
    assert "conta nova com o mesmo e-mail" in out
    assert not (i.dados / antiga["pasta"]).exists()                               # a pasta da conta antiga foi-se
    assert (esp.real / "nova.md").read_text(encoding="utf-8") == "da conta nova\n"
    assert i.cfg().utilizador(BEA).conta_id == nova.conta_id
    assert i.familias(BEA) == [fam_nova] and i.familias(BEA, so_vivas=True) == [fam_nova]
    assert "s-nova" in json.loads((i.dados / "sessoes.json").read_text(encoding="utf-8"))

    rc, out, err = i.cli("apagados", "reaplicar", "--lista", str(lista), "--sim")
    assert rc == 0 and ultima(out) == "resumo: reapagadas=0 recriadas=1 acrescentadas=0 protegidas=0", (out, err)
    assert "recriada depois do apagamento" in out and i.cfg().utilizador(BEA).conta_id == nova.conta_id


def test_conta_que_voltou_com_o_mesmo_conta_id_nao_se_elimina(i, monkeypatch):
    toml_antes = i.toml.read_text(encoding="utf-8")
    i.apagar(BEA)
    pasta = i.dados / i.entrada(BEA)["pasta"]
    i.toml.write_text(toml_antes, encoding="utf-8")                              # um basalt.toml antigo reposto sozinho
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 1 and ultima(out) == "resumo: eliminadas=0 falhas=0 voltaram=1", (out, err)
    assert "apagados reaplicar" in out and pasta.exists() and i.entrada(BEA)["email"] == BEA


def test_espaco_partilhado_nao_tem_pasta_e_a_pasta_fica(tmp_path, capsys, monkeypatch):
    i = Inst(tmp_path, capsys, [{"email": ANA, "conta_id": "c-ana", "espaco": "a", "admin": True},
                                {"email": BEA, "conta_id": "c-bea", "espaco": "a"}], extra_basalt="espaco_partilhado = true\n")
    i.apagar(BEA)
    e = i.entrada(BEA)
    assert e["pasta"] is None and e["espaco"] == "a"
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0 and ultima(out) == "resumo: eliminadas=1 falhas=0 voltaram=0", (out, err)
    assert "sem pasta" in out and (i.dados / "a" / "memoria" / "nota.md").exists()
    assert i.cfg().utilizador(ANA) is not None


# ───────────────────────────── 5. conta_id ─────────────────────────────
def test_utilizador_novo_da_um_conta_id_a_cada_conta(i):
    for email in ("eva@exemplo.pt", "fil@exemplo.pt"):
        rc, out, err = i.cli("utilizador", "novo", email)
        assert rc == 0, err
    cfg = i.cfg()
    ids = [cfg.utilizador(e).conta_id for e in ("eva@exemplo.pt", "fil@exemplo.pt")]
    assert all(re.fullmatch(r"[0-9a-f]{16}", x) for x in ids) and ids[0] != ids[1]


def test_iniciar_da_um_conta_id(tmp_path):
    d = tmp_path / "dados"
    r = subprocess.run([sys.executable, "-m", "basalt", "iniciar", "--dados", str(d), "--email", "ana@exemplo.pt",
                        "--senha", "segredo-forte"], cwd=RAIZ, capture_output=True, text=True,
                       env={**os.environ, "BASALT_CONFIG": "", "BASALT_SENHA": ""})
    assert r.returncode == 0, r.stderr
    assert re.fullmatch(r"[0-9a-f]{16}", config.carregar(d / "basalt.toml").utilizadores[0].conta_id)


def test_a_conta_criada_no_primeiro_login_tem_conta_id(g):
    s = g()
    assert _eu(s, s.como(NOVO, sub="1234567890"))[0] == 200
    assert re.fullmatch(r"[0-9a-f]{16}", config.carregar(s.toml).utilizador(NOVO).conta_id)


def test_toml_antigo_sem_conta_id_carrega_e_o_email_chega_para_reconhecer(tmp_path, capsys, monkeypatch):
    """a limitação documentada: uma conta criada antes da r17 não tem conta_id, por isso a mesma pessoa e outra pessoa com o
    mesmo e-mail não se distinguem — o mesmo e-mail chega."""
    i = Inst(tmp_path, capsys, [{"email": ANA, "espaco": "a", "admin": True}, {"email": BEA, "espaco": "b"}])
    toml_antes = i.toml.read_text(encoding="utf-8")
    assert "conta_id =" not in toml_antes and [u.conta_id for u in i.cfg().utilizadores] == ["", ""]
    i.apagar(BEA)
    assert i.entrada(BEA)["conta_id"] == ""
    lista = tmp_path / "lista.jsonl"
    lista.write_text(i.cli("apagados", "exportar")[1], encoding="utf-8")
    i.toml.write_text(toml_antes, encoding="utf-8")
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 1 and ultima(out) == "resumo: eliminadas=0 falhas=0 voltaram=1" and "sem conta_id" in out, (out, err)
    rc, out, err = i.cli("apagados", "reaplicar", "--lista", str(lista), "--sim")
    assert rc == 0 and ultima(out) == "resumo: reapagadas=1 recriadas=0 acrescentadas=0 protegidas=0", (out, err)
    assert i.cfg().utilizador(BEA) is None


# ───────────────────────────── 6. falhas, e-mails e a linha do resumo ─────────────────────────────
def test_uma_falha_numa_entrada_nao_impede_as_outras(i, monkeypatch, tmp_path):
    for e in (BEA, CAI, DIA):
        i.apagar(e)
    pasta_bea = i.dados / i.entrada(BEA)["pasta"]                 # a da Bea passa a ser uma ligação: recusada
    alvo = tmp_path / "b-real"
    os.replace(pasta_bea, alvo)
    os.symlink(alvo, pasta_bea)
    orig = oauth.eliminar_na_base

    def ocupada(dados, email, ate=None):                          # a base fica ocupada para o Cai (com o e-mail na mensagem)
        if email == CAI:
            raise oauth.Ocupado(f"a base está ocupada a tratar de {CAI}")
        return orig(dados, email, ate)
    monkeypatch.setattr(oauth, "eliminar_na_base", ocupada)
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 1, (out, err)
    assert ultima(out) == "resumo: eliminadas=1 falhas=2 voltaram=0", out
    assert i.entrada(DIA)["eliminado_em"] and i.entrada(BEA)["eliminado_em"] is None and i.entrada(CAI)["eliminado_em"] is None
    assert "@" not in out + err and "‹e-mail›" in out
    monkeypatch.setattr(oauth, "eliminar_na_base", orig)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")         # na vez seguinte, a do Cai já passa; a da Bea continua recusada
    assert rc == 1 and ultima(out) == "resumo: eliminadas=1 falhas=1 voltaram=0", (out, err)
    assert (alvo / "memoria" / "nota.md").exists()


def test_eliminar_nunca_escreve_emails(i, monkeypatch):
    toml_antes = i.toml.read_text(encoding="utf-8")
    for e in (BEA, CAI):
        i.apagar(e)
    i.toml.write_text(toml_antes.replace(f'email = "{CAI}"', 'email = "outro@exemplo.pt"'), encoding="utf-8")  # o Cai: sem conta
    toml_bea_voltou = i.toml.read_text(encoding="utf-8")
    assert BEA in toml_bea_voltou                                  # a Bea voltou com o toml antigo
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    saidas = [i.cli("apagados", "eliminar"), i.cli("apagados", "eliminar", "--sim"), i.cli("apagados", "eliminar", "--sim")]
    for rc, out, err in saidas:
        assert BEA not in out + err and CAI not in out + err, (out, err)
    assert ultima(saidas[0][1]) == "resumo: a_eliminar=2"
    assert ultima(saidas[1][1]) == "resumo: eliminadas=1 falhas=0 voltaram=1"


def test_resumo_eliminar_sem_sim(i, monkeypatch):
    rc, out, err = i.cli("apagados", "eliminar")
    assert rc == 0 and out.splitlines() == ["nada por eliminar com mais de 30 dias", "resumo: a_eliminar=0"], out
    agora = float(int(time.time()))
    i.apagar(BEA, monkeypatch, agora - 50 * UM_DIA)
    i.apagar(CAI, monkeypatch, agora - 31 * UM_DIA)
    i.apagar(DIA, monkeypatch, agora - 2 * UM_DIA)
    aos(monkeypatch, agora)
    rc, out, err = i.cli("apagados", "eliminar")
    assert rc == 0 and ultima(out) == "resumo: a_eliminar=2", out
    assert re.fullmatch(r"resumo: a_eliminar=\d+", ultima(out))


def test_resumo_eliminar_com_sim(i, monkeypatch):
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0 and ultima(out) == "resumo: eliminadas=0 falhas=0 voltaram=0", out
    agora = float(int(time.time()))
    for e, dias in ((BEA, 40), (CAI, 35), (DIA, 3)):
        i.apagar(e, monkeypatch, agora - dias * UM_DIA)
    aos(monkeypatch, agora)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0 and ultima(out) == "resumo: eliminadas=2 falhas=0 voltaram=0", out
    assert re.fullmatch(r"resumo: eliminadas=\d+ falhas=\d+ voltaram=\d+", ultima(out))


def _cenario_reaplicar(i, tmp_path):
    """Bea e Cai voltaram com a cópia (mesmo conta_id); a Dia foi recriada (outro conta_id); três contas que já não existem e
    faltam no registo; e a Ana, o único admin."""
    linhas = [(BEA, "c-bea"), (CAI, "c-cai"), (DIA, "c-dia-antiga"), ("eva@exemplo.pt", "c-eva"), ("fil@exemplo.pt", "c-fil"),
              ("gil@exemplo.pt", "c-gil"), (ANA, "c-ana")]
    lista = tmp_path / "lista.jsonl"
    lista.write_text("".join(json.dumps({"email_sha256": apagados.email_sha256(e), "conta_id": c,
                                         "apagado_em": f"2026-0{n + 1}-01T00:00:00Z"}) + "\n" for n, (e, c) in enumerate(linhas)),
                     encoding="utf-8")
    return lista


def test_resumo_reaplicar_sem_sim(i, tmp_path):
    lista = _cenario_reaplicar(i, tmp_path)
    toml = i.toml.read_text(encoding="utf-8")
    rc, out, err = i.cli("apagados", "reaplicar", "--lista", str(lista))
    assert rc == 0, err
    assert ultima(out) == "resumo: reapagaria=2 recriadas=1 acrescentaria=3 protegidas=1", out
    assert re.fullmatch(r"resumo: reapagaria=\d+ recriadas=\d+ acrescentaria=\d+ protegidas=\d+", ultima(out))
    assert i.toml.read_text(encoding="utf-8") == toml and not (i.dados / "contas-apagadas.json").exists()
    assert "@" not in out + err


def test_resumo_reaplicar_com_sim(i, tmp_path):
    lista = _cenario_reaplicar(i, tmp_path)
    rc, out, err = i.cli("apagados", "reaplicar", "--lista", str(lista), "--sim")
    assert rc == 1, (out, err)                                       # o único admin ficou: é preciso tratar dele à mão
    assert ultima(out) == "resumo: reapagadas=2 recriadas=1 acrescentadas=3 protegidas=1", out
    assert re.fullmatch(r"resumo: reapagadas=\d+ recriadas=\d+ acrescentadas=\d+ protegidas=\d+", ultima(out))
    assert "@" not in out + err
    cfg = i.cfg()
    assert cfg.utilizador(BEA) is None and cfg.utilizador(CAI) is None
    assert cfg.utilizador(DIA).conta_id == "c-dia" and cfg.utilizador(ANA) is not None
    reg = i.registo()
    novas = [e for e in reg if e["eliminado_em"]]
    assert len(novas) == 3 and all(e["email"] is None and e["espaco"] is None and e["pasta"] is None for e in novas)
    assert {e["apagado_em"] for e in reg if e["email"] in (BEA, CAI)} == {"2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"}
    rc, out, err = i.cli("apagados", "reaplicar", "--lista", str(lista), "--sim")      # outra vez: nada de novo
    assert rc == 1 and ultima(out) == "resumo: reapagadas=0 recriadas=1 acrescentadas=0 protegidas=1", out
    assert len(i.registo()) == len(reg)


def test_o_resumo_sai_mesmo_quando_nada_se_consegue_ler(i, tmp_path):
    reg = i.dados / "contas-apagadas.json"
    reg.write_text("[{\"estragado\": 1}]", encoding="utf-8")
    for args, esperado in ((["--sim"], "resumo: eliminadas=0 falhas=1 voltaram=0"), ([], "resumo: a_eliminar=0")):
        rc, out, err = i.cli("apagados", "eliminar", *args)
        assert rc == 1 and ultima(out) == esperado and "estragada" in err, (args, out, err)
    assert reg.read_text(encoding="utf-8") == "[{\"estragado\": 1}]"                 # nunca se regrava por cima
    reg.write_text("[]", encoding="utf-8")
    ma = tmp_path / "ma.jsonl"
    ma.write_text(json.dumps({"email_sha256": apagados.email_sha256(BEA), "conta_id": "c-bea", "apagado_em": "2026-01-01T00:00:00Z"})
                  + "\nisto não é JSON\n", encoding="utf-8")
    toml = i.toml.read_text(encoding="utf-8")
    rc, out, err = i.cli("apagados", "reaplicar", "--lista", str(ma), "--sim")
    assert rc == 1 and ultima(out) == "resumo: reapagadas=0 recriadas=0 acrescentadas=0 protegidas=0", (out, err)
    assert i.toml.read_text(encoding="utf-8") == toml, "uma linha má na lista: não se mexe em nada (nem nas linhas boas)"
    rc, out, err = i.cli("apagados", "reaplicar")
    assert rc == 1 and "--lista" in err and ultima(out) == "resumo: reapagaria=0 recriadas=0 acrescentaria=0 protegidas=0"
    i.toml = tmp_path / "nao-existe.toml"
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 1 and ultima(out) == "resumo: eliminadas=0 falhas=1 voltaram=0", (out, err)


# ───────────────────────────── 7. o timer e a documentação ─────────────────────────────
def _unidade(rel):
    out = {}
    for ln in (RAIZ / rel).read_text(encoding="utf-8").splitlines():
        m = re.match(r"([A-Za-z]+)=(.*)$", ln.strip())
        if m:
            out.setdefault(m.group(1), []).append(m.group(2).strip())
    return out


def test_o_servico_elimina_com_sim_sem_rede_e_com_o_endurecimento_do_servidor():
    srv, apg = _unidade("sistema/basalt.service"), _unidade("sistema/basalt-apagados.service")
    assert apg["Type"] == ["oneshot"] and "RemainAfterExit" not in apg, "com RemainAfterExit o timer só corria uma vez"
    assert apg["ExecStart"] == ["/usr/bin/python3 -m basalt apagados eliminar --dias 30 --sim"]
    for chave in ("User", "Group", "WorkingDirectory", "Environment", "NoNewPrivileges", "ProtectSystem", "ProtectHome",
                  "PrivateTmp", "ReadWritePaths", "UMask"):
        assert srv.get(chave) and all(v in apg.get(chave, []) for v in srv[chave] if not v.startswith("BASALT_SEGREDO")), chave
    assert apg["PrivateNetwork"] == ["yes"] and apg["RestrictAddressFamilies"] == ["AF_UNIX"]
    t = _unidade("sistema/basalt-apagados.timer")
    assert t["Persistent"] == ["true"] and t["OnCalendar"] and t["RandomizedDelaySec"]


@pytest.mark.parametrize("doc", ["docs/INSTALAR.md", "docs/SEGURANCA.md"])
def test_a_linha_do_resumo_esta_documentada(doc):
    texto = (RAIZ / doc).read_text(encoding="utf-8")
    for formato in ("resumo: a_eliminar=N", "resumo: eliminadas=N falhas=N voltaram=N",
                    "resumo: reapagaria=N recriadas=N acrescentaria=N protegidas=N",
                    "resumo: reapagadas=N recriadas=N acrescentadas=N protegidas=N"):
        assert formato in texto, f"{doc} não documenta «{formato}»"
