"""r14 — `basalt utilizador apagar EMAIL` e `utilizador exportar EMAIL --para FICHEIRO.zip`.

`apagar` tira a CONTA (o bloco [[utilizadores]], as sessões, as ligações OAuth) mas NUNCA os dados: a pasta do espaço é
MOVIDA para <dados>/apagados/<espaco>-<AAAAMMDD-HHMMSS>/. Recusa-se a apagar o último admin. `exportar` faz um .zip das
notas + anexos de uma conta, nunca do basalt.toml nem de tokens."""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from basalt import auth, config  # noqa: E402
from conftest import ServidorLocal  # noqa: E402

ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"


def correr(*args, entrada=""):
    r = subprocess.run([sys.executable, "-m", "basalt", *args], cwd=RAIZ, input=entrada, capture_output=True, text=True,
                       env={**os.environ, "BASALT_CONFIG": "", "BASALT_SENHA": ""})
    return r.returncode, r.stdout, r.stderr


@pytest.fixture
def s(tmp_path):
    srv = ServidorLocal(tmp_path, modo="cabecalho",
                        utilizadores=[{"email": ANA, "espaco": "a", "admin": True}, {"email": BEA, "espaco": "b"}])
    yield srv
    srv.parar()


def _eu(s, email):
    return s.get("/api/eu", email=email, dono=False)


def test_apagar_recusa_o_ultimo_admin(s):
    rc, out, err = correr("--config", str(s.toml), "utilizador", "apagar", ANA, "--sim")
    assert rc != 0 and "admin" in err
    assert ANA in s.toml.read_text(encoding="utf-8")
    cfg = config.carregar(s.toml)
    assert cfg.utilizador(ANA) is not None                                   # nada mudou


def test_apagar_remove_bloco_move_pasta_sessoes_e_nao_afecta_outra_conta(s):
    esp_b = s.espaco("b").criar()
    (esp_b.real / "nota.md").write_text("olá\n", encoding="utf-8")
    # uma sessão da Bea (modo senha não está activo aqui, mas o ficheiro de sessões é só JSON — simula uma sessão aberta)
    sessoes = s.cfg.dados / "sessoes.json"
    sessoes.parent.mkdir(parents=True, exist_ok=True)
    sessoes.write_text(json.dumps({"ct1": {"email": BEA, "expira": 9e12, "criada": 0, "cred": ""},
                                   "ct2": {"email": ANA, "expira": 9e12, "criada": 0, "cred": ""}}), encoding="utf-8")
    assert _eu(s, BEA).codigo == 200 and _eu(s, ANA).codigo == 200

    rc, out, err = correr("--config", str(s.toml), "utilizador", "apagar", BEA, "--sim")
    assert rc == 0, err

    texto = s.toml.read_text(encoding="utf-8")
    assert BEA not in texto
    cfg = config.carregar(s.toml)                                           # o toml continua válido
    assert cfg.utilizador(BEA) is None and cfg.utilizador(ANA) is not None  # a Ana não foi tocada

    # a pasta da Bea foi MOVIDA (nunca apagada)
    assert not esp_b.real.exists()
    apagados = list((s.cfg.dados / "apagados").glob("b-*"))
    assert len(apagados) == 1 and (apagados[0] / "memoria" / "nota.md").read_text(encoding="utf-8") == "olá\n"

    # a sessão da Bea saiu do ficheiro; a da Ana ficou
    d = json.loads(sessoes.read_text(encoding="utf-8"))
    assert "ct1" not in d and "ct2" in d

    # o /api/eu da Bea já não entra (cabecalho: a conta já não existe no toml); a Ana continua
    assert _eu(s, BEA).codigo in (401, 403)
    assert _eu(s, ANA).codigo == 200


def test_apagar_sem_sim_pede_confirmacao_por_escrever_o_email(s):
    rc, out, err = correr("--config", str(s.toml), "utilizador", "apagar", BEA, entrada="email-errado@exemplo.pt\n")
    assert rc != 0 and "não coincide" in err
    assert BEA in s.toml.read_text(encoding="utf-8")                        # nada mudou
    rc, out, err = correr("--config", str(s.toml), "utilizador", "apagar", BEA, entrada=f"{BEA}\n")
    assert rc == 0, err
    assert BEA not in s.toml.read_text(encoding="utf-8")


def test_apagar_revoga_a_ligacao_oauth(s, tmp_path):
    from basalt import oauth
    cfg = config.carregar(s.toml)
    u = cfg.utilizador(BEA)
    o = oauth.OAuth(cfg, None)
    o.token_pessoal(u, "agente de teste", o.geracao())                     # ronda 13b: a geração lida antes de autenticar
    assert len(o.ligacoes(u)) == 1

    rc, out, err = correr("--config", str(s.toml), "utilizador", "apagar", BEA, "--sim")
    assert rc == 0, err

    cfg2 = config.carregar(s.toml)
    # a conta já não existe no toml — a prova de que a ligação morreu é a contagem na base, por espaço/credencial antigos
    assert cfg2.utilizador(BEA) is None
    assert o.revogar(u) == 0                                                # já não havia nenhuma por revogar (a CLI já tinha feito)


def test_exportar_tem_notas_e_anexos_mas_nao_o_toml_nem_tokens(s, tmp_path):
    esp_a = s.espaco("a").criar()
    (esp_a.real / "nota.md").write_text("segredo: não\numa nota normal\n", encoding="utf-8")
    (esp_a.anexos).mkdir(parents=True, exist_ok=True)
    (esp_a.anexos / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    destino = tmp_path / "ana.zip"

    rc, out, err = correr("--config", str(s.toml), "utilizador", "exportar", ANA, "--para", str(destino))
    assert rc == 0, err
    assert destino.exists()
    with zipfile.ZipFile(destino) as z:
        nomes = z.namelist()
        assert "nota.md" in nomes and "anexos/foto.png" in nomes
        assert not any(n.endswith("basalt.toml") or "token" in n.lower() or "sessoes" in n.lower() for n in nomes)
        assert z.read("anexos/foto.png") == b"\x89PNG\r\n\x1a\n"


def test_exportar_utilizador_desconhecido_falha(s, tmp_path):
    rc, out, err = correr("--config", str(s.toml), "utilizador", "exportar", "fantasma@exemplo.pt", "--para", str(tmp_path / "x.zip"))
    assert rc != 0 and "desconhecido" in err
    assert not (tmp_path / "x.zip").exists()


def test_apagar_nao_move_a_pasta_quando_outra_conta_partilha_o_espaco(tmp_path):
    """revisão Fable (20/10): com [basalt] espaco_partilhado = true, «a pasta da Bea» é também a da Ana — mover levava as notas
    da Ana para apagados/. Caso bom: o espaço só dela (Cai, «c») continua a ser movido."""
    from conftest import toml_de
    CAI = "cai@exemplo.pt"
    dados, toml = tmp_path / "dados", tmp_path / "basalt.toml"
    t = toml_de(0, dados, "cabecalho", None, [{"email": ANA, "espaco": "a", "admin": True}, {"email": BEA, "espaco": "a"},
                                               {"email": CAI, "espaco": "c"}])
    toml.write_text(t.replace("git = false\n", "git = false\nespaco_partilhado = true\n", 1), encoding="utf-8")
    cfg = config.carregar(toml)
    esp_a, esp_c = cfg.espaco("a").criar(), cfg.espaco("c").criar()
    (esp_a.real / "nota.md").write_text("da ana e da bea\n", encoding="utf-8")
    (esp_c.real / "nota.md").write_text("só do cai\n", encoding="utf-8")

    rc, out, err = correr("--config", str(toml), "utilizador", "apagar", BEA, "--sim")
    assert rc == 0, err
    assert (esp_a.real / "nota.md").read_text(encoding="utf-8") == "da ana e da bea\n"    # ficou onde estava
    assert not list((dados / "apagados").glob("a-*")) if (dados / "apagados").exists() else True
    assert "partilhado" in out and ANA in out
    cfg2 = config.carregar(toml)
    assert cfg2.utilizador(BEA) is None and cfg2.utilizador(ANA).espaco == "a"

    rc, out, err = correr("--config", str(toml), "utilizador", "apagar", CAI, "--sim")
    assert rc == 0, err
    assert not esp_c.real.exists() and len(list((dados / "apagados").glob("c-*"))) == 1


def test_apagar_revoga_na_base_antes_de_mudar_o_toml_e_se_nao_der_nao_muda_nada(s, tmp_path):
    """r14 sobre a r13b: como `utilizador senha`/`novo`, o `apagar` revoga as ligações das IAs NA BASE dentro do trinco do
    basalt.toml e ANTES de ele mudar. Antes tirava o bloco primeiro e só depois revogava: com a base inacessível, a conta saía
    do toml com as ligações ainda vivas na base (um toml antigo reposto, ou o servidor parado antes de o reler, e voltavam a
    valer). Aqui a base é uma ligação simbólica (recusada pela revogação da r13b): nada pode mudar."""
    from basalt import oauth
    cfg = config.carregar(s.toml)
    o = oauth.OAuth(cfg, None)
    o.token_pessoal(cfg.utilizador(BEA), "agente de teste", o.geracao())
    base = cfg.dados / "oauth.sqlite"
    assert base.is_file()
    verdadeira = tmp_path / "oauth-verdadeira.sqlite"
    os.replace(base, verdadeira)
    os.symlink(verdadeira, base)
    esp_b = s.espaco("b").criar()
    (esp_b.real / "nota.md").write_text("olá\n", encoding="utf-8")
    antes = s.toml.read_text(encoding="utf-8")

    rc, out, err = correr("--config", str(s.toml), "utilizador", "apagar", BEA, "--sim")
    assert s.toml.read_text(encoding="utf-8") == antes, "a conta saiu do toml sem as ligações das IAs revogadas"
    assert rc != 0 and "revogar" in err and "não mexi no basalt.toml" in err, (rc, out, err)
    assert (esp_b.real / "nota.md").exists(), "a pasta foi movida sem a conta ter sido apagada"

    os.remove(base)                                                         # a base volta a ser um ficheiro: agora apaga
    os.replace(verdadeira, base)
    rc, out, err = correr("--config", str(s.toml), "utilizador", "apagar", BEA, "--sim")
    assert rc == 0, err
    assert "1 ligação(ões) OAuth revogada(s)" in out, out
    assert config.carregar(s.toml).utilizador(BEA) is None
