"""r21 (auditoria externa à r14, pontos 3 e 4) — o tecto `[auth] espaco_max_mb` com as duas cópias de cada nota.

Ponto 3: a web e o /mcp escrevem na cópia editável (`copia/`) e o vigia (`duasvias`) copia a nota para a memória (`memoria/`)
uns segundos depois. A conta via-se só com a cópia: uma nota que cabia uma vez passava o tecto logo a seguir, sem concorrência
nenhuma (e o git da memória ainda cresce). Agora (QUOTA_COPIAS_v1) conta-se o que o vigia AINDA vai copiar: cada nota conta
como se as duas cópias ficassem com a maior das duas versões — criar uma nota de N bytes pede 2N.
Ponto 4: «uma nota pode sempre encolher, mesmo com o espaço cheio» (GOOGLE.md §4) — com o espaço JÁ acima do tecto (tecto
baixado, ou o git da memória a crescer) o encolher dava 413, porque se via `uso + 0 > tecto`. Agora encolher (ou gravar ao mesmo
tamanho) nunca é recusado pelo tecto.
Cada teste falha no 68ad69d (r20) pela razão certa."""
from basalt import config, duasvias
from test_r14_limites import ANA, BEA, com_espaco_max_mb  # noqa: F401 (fixture)

KIB = 1024
DUAS = [{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}]


def test_nota_que_so_cabe_uma_vez_e_recusada_logo_ao_criar(com_espaco_max_mb):
    s = com_espaco_max_mb(1, DUAS)
    r = s.pedido("POST", "/api/nota", {"nome": "n1", "texto": "x" * (700 * KIB)}, email=ANA)
    assert r.codigo == 413, (r.codigo, r.corpo)                    # antes: 201 — e o vigia levava o espaço a 1,4 MB
    assert "1" in r.json["erro"]
    assert not (s.espaco("a").copia / "n1.md").exists()


def test_duas_notas_antes_do_vigia_nao_levam_o_disco_acima_do_tecto(com_espaco_max_mb):
    s = com_espaco_max_mb(1, DUAS)
    esp = s.espaco("a")
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": "a" * (300 * KIB)}, email=ANA).codigo == 201
    r = s.pedido("POST", "/api/nota", {"nome": "n2", "texto": "b" * (300 * KIB)}, email=ANA)
    with config.dentro(esp):                                       # o vigia (no servidor a sério, de 10 em 10 s)
        duasvias.ciclo()
    uso = esp.uso_bytes()
    assert uso <= 1024 * KIB, f"depois do vigia o espaço ficou com {uso} bytes, acima do tecto de 1 MiB (2.ª nota: {r.codigo})"
    assert r.codigo == 413, (r.codigo, r.corpo)
    assert (esp.real / "n1.md").exists()                           # controlo: o vigia correu mesmo


def test_crescer_uma_nota_conta_as_duas_copias(com_espaco_max_mb):
    s = com_espaco_max_mb(1, DUAS)
    esp = s.espaco("a")
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": "a" * (200 * KIB)}, email=ANA).codigo == 201
    with config.dentro(esp):
        duasvias.ciclo()                                           # 400 KiB: as duas cópias iguais
    h = s.get("/api/nota?nome=n1", email=ANA).json["hash"]
    r = s.pedido("PUT", "/api/nota?nome=n1", {"texto": "a" * (600 * KIB), "base_hash": h}, email=ANA)
    assert r.codigo == 413, (r.codigo, r.corpo)                    # antes: 200 — 600+200 cabia, e depois do vigia eram 1,2 MiB
    r = s.pedido("PUT", "/api/nota?nome=n1", {"texto": "a" * (400 * KIB), "base_hash": h}, email=ANA)
    assert r.codigo == 200, (r.codigo, r.corpo)                    # controlo: 2 × 400 KiB cabe
    with config.dentro(esp):
        duasvias.ciclo()
    assert esp.uso_bytes() <= 1024 * KIB


def test_encolher_passa_com_o_espaco_ja_acima_do_tecto(com_espaco_max_mb):
    s = com_espaco_max_mb(1, DUAS)
    esp = s.espaco("a")
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": "pequena\n"}, email=ANA).codigo == 201
    grande = "g" * (700 * KIB)
    for d in (esp.copia, esp.real):                               # o espaço já passou o tecto (tecto baixado, git, …)
        (d / "n1.md").write_text(grande, encoding="utf-8")
    assert esp.uso_bytes() > 1024 * KIB                            # controlo
    h = s.get("/api/nota?nome=n1", email=ANA).json["hash"]
    r = s.pedido("PUT", "/api/nota?nome=n1", {"texto": grande + "mais", "base_hash": h}, email=ANA)
    assert r.codigo == 413, (r.codigo, r.corpo)                    # controlo: crescer continua recusado
    r = s.pedido("PUT", "/api/nota?nome=n1", {"texto": "h" * (700 * KIB), "base_hash": h}, email=ANA)
    assert r.codigo == 200, (r.codigo, r.corpo)                    # o mesmo tamanho passa (antes: 413)
    with config.dentro(esp):
        duasvias.ciclo()                                           # o vigia leva-a para a memória (senão o PUT seguinte é «pendente»)
    h2 = s.get("/api/nota?nome=n1", email=ANA).json["hash"]
    r = s.pedido("PUT", "/api/nota?nome=n1", {"texto": "curta\n", "base_hash": h2}, email=ANA)
    assert r.codigo == 200, (r.codigo, r.corpo)                    # antes: 413 («uso + 0 > tecto»)


def test_encolher_pela_sincronizacao_offline_passa_com_o_espaco_acima_do_tecto(com_espaco_max_mb):
    s = com_espaco_max_mb(1, DUAS)
    esp = s.espaco("a")
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": "pequena\n"}, email=ANA).codigo == 201
    grande = "g" * (700 * KIB)
    for d in (esp.copia, esp.real):
        (d / "n1.md").write_text(grande, encoding="utf-8")
    r = s.pedido("POST", "/api/sincronizar", {"nome": "n1", "base_texto": grande, "texto": "curta\n"}, email=ANA)
    assert r.codigo == 200, (r.codigo, r.corpo)                    # antes: 413


def test_a_conta_vizinha_nao_e_afectada(com_espaco_max_mb):
    s = com_espaco_max_mb(1, DUAS)
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": "a" * (400 * KIB)}, email=ANA).codigo == 201
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": "b" * (400 * KIB)}, email=BEA).codigo == 201
