"""Ronda 10 — achados da 3.ª revisão externa do pacote (r3, sobre cbf5489).

Cada teste falha no código de cbf5489 pela razão certa (menos os controlos, que passam nos dois); a prova está em CHANGELOG.md,
secção «Segurança, ronda 10»."""
import os
import threading
from pathlib import Path

import pytest

from basalt import auth, config
from conftest import toml_de

ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"
SENHA = "uma senha comprida"


def _toml(tmp_path, modo, utilizadores):
    p = tmp_path / "basalt.toml"
    p.write_text(toml_de(0, tmp_path / "dados", modo, None, utilizadores), encoding="utf-8")
    return p


def _rescrever(p: Path, texto: str):
    antes = p.stat().st_mtime_ns
    p.write_text(texto, encoding="utf-8")
    os.utime(p, ns=(antes + 2_000_000, antes + 2_000_000))


def _bearer(aut, tok):
    return aut.utilizador_do_pedido({"Authorization": "Bearer " + tok}, "127.0.0.1")


# ───────────────────────── G3: a marca nova só se vê depois de os utilizadores novos estarem no lugar ─────────────────────────
class _Lento:
    """embrulha a Config nova: ler `.utilizadores` (o momento em que a releitura os põe no lugar) pára até ser solto."""
    def __init__(self, novo, a_trocar, soltar):
        self._novo, self._a_trocar, self._soltar = novo, a_trocar, soltar

    @property
    def utilizadores(self):
        self._a_trocar.set()
        self._soltar.wait(10)
        return self._novo.utilizadores

    def __getattr__(self, k):
        return getattr(self._novo, k)


@pytest.mark.parametrize("via", ["bearer", "proxy"])
def test_g3_pedido_durante_a_troca_dos_utilizadores_nao_entra_com_a_lista_antiga(tmp_path, monkeypatch, via):
    tok_a, h_a = auth.gerar_token()
    tok_b, h_b = auth.gerar_token()
    p = _toml(tmp_path, "cabecalho", [{"email": ANA, "espaco": "a", "mcp_token_hash": h_a},
                                      {"email": BEA, "espaco": "b", "mcp_token_hash": h_b}])
    aut = auth.Autenticador(config.carregar(p), tmp_path / "sessoes.json")
    pedir = (lambda: _bearer(aut, tok_a)) if via == "bearer" else (lambda: aut.utilizador_do_pedido({"X-Basalt-Email": ANA}))
    assert pedir().email == ANA
    a_trocar, soltar = threading.Event(), threading.Event()
    carregar = config.carregar
    monkeypatch.setattr(auth.config, "carregar", lambda *a, **k: _Lento(carregar(*a, **k), a_trocar, soltar))
    _rescrever(p, toml_de(0, tmp_path / "dados", "cabecalho", None, [{"email": BEA, "espaco": "b", "mcp_token_hash": h_b}]))  # ana sai
    primeiro = threading.Thread(target=lambda: _bearer(aut, tok_b), daemon=True)
    primeiro.start()
    assert a_trocar.wait(5)
    resultado = []
    segundo = threading.Thread(target=lambda: resultado.append(pedir()), daemon=True)
    segundo.start()
    segundo.join(0.5)
    entrou = bool(resultado and resultado[0] is not None)
    soltar.set()
    primeiro.join(5)
    segundo.join(5)
    assert not entrou, "a meio da troca dos utilizadores, a conta já apagada entrou com a lista antiga"
    assert resultado == [None]
    assert _bearer(aut, tok_b).email == BEA                                    # controlo: quem ficou continua a entrar


# ───────────────────────── M1: as sessões já expiradas no arranque saem também do ficheiro ─────────────────────────
def test_m1_sessao_expirada_no_arranque_sai_do_ficheiro(tmp_path):
    import json
    import time
    p = _toml(tmp_path, "senha", [{"email": ANA, "espaco": "a", "senha_hash": auth.hash_senha(SENHA)}])
    sessoes = tmp_path / "sessoes.json"
    aut = auth.Autenticador(config.carregar(p), sessoes)
    ok, tok = aut.entrar(ANA, SENHA, "127.0.0.1")
    assert ok
    d = json.loads(sessoes.read_text(encoding="utf-8"))
    viva = dict(next(iter(d.values())))
    d = {"morta": dict(viva, expira=time.time() - 60), "viva": viva}
    sessoes.write_text(json.dumps(d), encoding="utf-8")
    auth.Autenticador(config.carregar(p), sessoes)                            # arranque
    ficou = json.loads(sessoes.read_text(encoding="utf-8"))
    assert "morta" not in ficou, "a sessão já expirada ao arrancar ficou no ficheiro (hash do cookie + e-mail)"
    assert "viva" in ficou                                                    # controlo: a viva fica


# ───────────────────────── G4: nada abaixo da raiz de dados segue ligações simbólicas (nem os ancestrais das notas) ─────────────────────────
FM_B = "---\nname: privada da bea\ndescription: x\nmetadata:\n  type: project\n---\nPRIVADO_B\n"


def _dois_espacos(tmp_path):
    from basalt.config import Espaco
    dados = tmp_path / "dados"
    a, b = Espaco.de("ana", dados).criar(), Espaco.de("bea", dados).criar()
    (b.copia / "privada_b.md").write_text(FM_B, encoding="utf-8")
    (b.real / "privada_b.md").write_text(FM_B, encoding="utf-8")
    (b.anexos).mkdir(parents=True, exist_ok=True)
    (b.anexos / "foto.png").write_bytes(b"PRIVADO_B_PNG")
    return dados, a, b


def _por_ligacao(caminho: Path, alvo: Path, tmp_path):
    """troca a pasta `caminho` por uma ligação simbólica para `alvo` (a original vai para o lado, sem apagar nada)."""
    caminho.rename(tmp_path / ("posta-de-lado-" + caminho.name))
    caminho.symlink_to(alvo, target_is_directory=True)


def _ou_nada(f, *a):
    try:
        return f(*a)
    except OSError:
        return None


@pytest.mark.parametrize("onde", ["copia", "espaco"])
def test_g4_nota_de_outra_conta_por_ancestral_simbolico_nao_se_le_nem_se_grava(tmp_path, onde):
    from basalt import web
    dados, a, b = _dois_espacos(tmp_path)
    if onde == "copia":
        _por_ligacao(a.copia, b.copia, tmp_path)                  # A/copia → B/copia
    else:
        _por_ligacao(dados / "ana", dados / "bea", tmp_path)      # A → B (a pasta do espaço inteira)
    with config.dentro(a):
        assert _ou_nada(web.localizar, "privada_b") is None, "a nota da outra conta foi encontrada por um ancestral simbólico"
        assert "privada_b" not in [o["nome"] for o in (_ou_nada(web.todas_notas) or [])]
        assert "PRIVADO_B" not in str(_ou_nada(web.pacote) or "")
        assert _ou_nada(caminhos_ler, a.copia / "privada_b.md") is None
        r = _ou_nada(web.gravar, "privada_b", FM_B.replace("PRIVADO_B", "ESCRITO_POR_A"), _sha(FM_B))
        assert not r or r[0] != 200
    assert "ESCRITO_POR_A" not in (b.copia / "privada_b.md").read_text(encoding="utf-8"), "a conta A escreveu na nota da conta B"


def caminhos_ler(p):
    """a leitura verificada (ronda 10); no código anterior, a leitura por caminho que lá se usava."""
    from basalt import caminhos
    return caminhos.ler(p) if hasattr(caminhos, "ler") else Path(p).read_bytes()


def _sha(t):
    import hashlib
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


def test_g4_memoria_por_ligacao_nao_serve_os_anexos_da_outra_conta(tmp_path):
    from basalt import anexos
    dados, a, b = _dois_espacos(tmp_path)
    _por_ligacao(a.real, b.real, tmp_path)                        # A/memoria → B/memoria (a raiz dos anexos)
    estado, _, corpo = anexos.ler(a, "foto.png")
    assert estado != 200 and b"PRIVADO_B" not in (corpo or b""), "serviu o anexo da outra conta pela memória simbólica"
    assert [x["nome"] for x in (_ou_nada(anexos.listar, a) or [])] == []


def test_g4_controlo_raiz_de_dados_simbolica_e_de_confianca(tmp_path):
    """a raiz `dados` do toml é do administrador: pode ser ela própria uma ligação (ex.: /var/lib/basalt → disco grande)."""
    from basalt import web
    from basalt.config import Espaco
    real = tmp_path / "disco-grande"
    real.mkdir()
    (tmp_path / "dados").symlink_to(real, target_is_directory=True)
    a = Espaco.de("ana", tmp_path / "dados").criar()
    (a.copia / "minha.md").write_text(FM_B.replace("PRIVADO_B", "MEU"), encoding="utf-8")
    with config.dentro(a):
        assert web.localizar("minha") is not None
        assert "MEU" in caminhos_ler(web.localizar("minha")[0]).decode()
