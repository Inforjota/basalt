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
