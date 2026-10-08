"""Ronda 9 (achado G2 da ronda 2) — o servidor exige o dono da cópia em TODOS os pedidos da página.

Na ronda 8 o servidor só recusava um `X-Basalt-Dono` de outra conta; um pedido SEM o cabeçalho passava (era o caso das páginas
antigas). Mas a página nova também os fazia: o envio de anexos não o punha, e um pedido feito antes de a página saber de quem é a
cópia (ou depois de uma verificação falhada) ia em nome de quem estivesse no browser. Agora:
  · /api/… e /anexos/… sem dono → 428 (`semDono`, `X-Basalt-Sem-Dono: 1`) e NADA se lê nem se escreve; só /api/eu fica de fora
    (é por ele que a página sabe o dono);
  · as imagens dos anexos (<img>, sem cabeçalhos) levam o dono em `?d=`;
  · uma resposta servida com o dono certo leva-o de volta (`X-Basalt-Dono`), e o service worker só guarda o que traz o eco do
    mesmo dono (uma cache por dono — ver test_sw.py);
  · os agentes (Authorization: Bearer) não têm cópia no browser: não precisam de dono.
Cada teste falha no código anterior a esta ronda pela razão certa, menos os do /api/eu e do Bearer (guardas contra
regressões: já passavam)."""
import pytest

from basalt import auth
from conftest import FM, sha

ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"
PNG = b"\x89PNG\r\n\x1a\n" + b"bytes de uma foto"


@pytest.fixture
def s(servidor_local):
    return servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}])


def _dono(s, email):
    r = s.get("/api/eu", email=email, dono=False)
    assert r.codigo == 200 and r.json.get("dono")
    return r.json["dono"]


def _nota(s, espaco, nome, texto="olá\n"):
    t = FM.format(nome=nome) + "\n" + texto
    (s.espaco(espaco).criar().copia / f"{nome}.md").write_text(t, encoding="utf-8")
    return t


def _sem_dono(r, o_que):
    assert r.codigo == 428, f"{o_que} sem o dono da cópia foi aceite ({r.codigo})"
    assert r.cabecalhos.get("X-Basalt-Sem-Dono") == "1" and r.json.get("semDono") is True
    assert "X-Basalt-Dono" not in r.cabecalhos


def test_leitura_sem_dono_recusa_428(s):
    _nota(s, "a", "n1", "segredo da ana\n")
    for caminho in ("/api/notas", s.nota_url("n1"), "/api/pacote"):
        r = s.get(caminho, email=ANA, dono=False)
        _sem_dono(r, f"GET {caminho}")
        assert b"segredo da ana" not in r.corpo
    # controlo: com o dono certo serve
    assert s.get(s.nota_url("n1"), email=ANA).codigo == 200


def test_api_eu_nao_precisa_de_dono(s):
    r = s.get("/api/eu", email=ANA, dono=False)
    assert r.codigo == 200 and r.json.get("dono")
    assert "X-Basalt-Dono" not in r.cabecalhos             # sem dono no pedido não há eco


def test_escrita_sem_dono_nao_grava(s):
    t = _nota(s, "a", "n1")
    p = s.espaco("a").criar().copia / "n1.md"
    r = s.pedido("PUT", s.nota_url("n1"), {"texto": t + "sem dono\n", "base_hash": sha(t)}, email=ANA, dono=False)
    _sem_dono(r, "PUT")
    r = s.pedido("POST", "/api/sincronizar", {"nome": "n1", "base_texto": t, "texto": t + "sem dono\n"}, email=ANA, dono=False)
    _sem_dono(r, "POST /api/sincronizar")
    assert p.read_text(encoding="utf-8") == t, "uma escrita sem dono foi gravada"
    # controlo: com o dono certo grava
    r = s.pedido("PUT", s.nota_url("n1"), {"texto": t + "com dono\n", "base_hash": sha(t)}, email=ANA)
    assert r.codigo == 200 and p.read_text(encoding="utf-8").endswith("com dono\n")


def test_envio_de_anexo_sem_dono_nao_grava(s):
    esp = s.espaco("a").criar()
    r = s.pedido("POST", "/api/anexos?nome=foto.png", bruto=PNG, email=ANA, cabecalhos={"Content-Type": "image/png"}, dono=False)
    _sem_dono(r, "POST /api/anexos")
    assert not (esp.anexos / "foto.png").exists(), "o anexo enviado sem dono foi gravado"
    # o dono de OUTRA conta também não (ronda 8, continua)
    r = s.pedido("POST", "/api/anexos?nome=foto.png", bruto=PNG, email=ANA, cabecalhos={"Content-Type": "image/png"},
                 dono=_dono(s, BEA))
    assert r.codigo == 403 and not (esp.anexos / "foto.png").exists()
    # controlo
    r = s.pedido("POST", "/api/anexos?nome=foto.png", bruto=PNG, email=ANA, cabecalhos={"Content-Type": "image/png"})
    assert r.codigo == 201 and (esp.anexos / "foto.png").read_bytes() == PNG


def test_imagem_de_anexo_leva_o_dono_no_endereco(s):
    r = s.pedido("POST", "/api/anexos?nome=foto.png", bruto=PNG, email=ANA, cabecalhos={"Content-Type": "image/png"})
    assert r.codigo == 201, r.corpo
    da, db = _dono(s, ANA), _dono(s, BEA)
    r = s.get("/anexos/foto.png?d=" + db, email=ANA, dono=False)              # uma página de B a mostrar a imagem com a sessão de A
    assert r.codigo == 403 and r.corpo != PNG, f"a imagem de uma conta foi servida à página de outra ({r.codigo})"
    assert r.cabecalhos.get("X-Basalt-Outra-Conta") == "1"
    r = s.get("/anexos/foto.png?d=" + da, email=ANA, dono=False)              # a <img> da página: sem cabeçalhos, dono no endereço
    assert r.codigo == 200 and r.corpo == PNG and r.cabecalhos.get("X-Basalt-Dono") == da
    _sem_dono(s.get("/anexos/foto.png", email=ANA, dono=False), "GET /anexos/ sem ?d=")
    assert s.get("/anexos/foto.png", email=ANA).codigo == 200                   # pelo cabeçalho também serve


def test_resposta_com_o_dono_certo_traz_o_eco_e_as_outras_nao(s):
    da, db = _dono(s, ANA), _dono(s, BEA)
    r = s.get("/api/notas", email=ANA, dono=da)
    assert r.codigo == 200 and r.cabecalhos.get("X-Basalt-Dono") == da, "sem eco o service worker não sabe de quem é a resposta"
    r = s.get("/api/notas", email=ANA, dono=db)
    assert r.codigo == 403 and "X-Basalt-Dono" not in r.cabecalhos
    # o eco é por pedido: a resposta seguinte (sem dono) não herda o da anterior
    _sem_dono(s.get("/api/notas", email=ANA, dono=False), "GET /api/notas depois de um pedido com dono")


def test_agente_com_token_nao_precisa_de_dono(servidor_local):
    """guarda contra regressões (já passava antes): os agentes entram por Bearer e não têm cópia no browser."""
    token, h = auth.gerar_token()
    s = servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a", "mcp_token_hash": h}])
    _nota(s, "a", "n1")
    r = s.get("/api/notas", email=None, cabecalhos={"Authorization": "Bearer " + token}, dono=False)
    assert r.codigo == 200 and b'"n1"' in r.corpo, r.corpo[:300]
    # e um token errado não cai para o cabeçalho do proxy
    r = s.get("/api/notas", email=ANA, cabecalhos={"Authorization": "Bearer bslt_errado"}, dono=False)
    assert r.codigo in (401, 403)
