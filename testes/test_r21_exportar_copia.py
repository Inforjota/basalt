"""r21 (auditoria externa à r14, ponto 7) — `basalt utilizador exportar` só lia a memória (`memoria/`): o que a pessoa escreveu
pela web ou pelo /mcp nos últimos segundos (ainda só na cópia editável, à espera do vigia) — ou tudo, com o vigia parado — ficava
fora do ZIP sem aviso. Agora (EXPORTAR_COPIA_v1) as notas da cópia que ainda não chegaram à memória (novas ou diferentes) entram
em `por-sincronizar/<nome>.md`, sem mexer em nada (o exportar continua só de leitura: não corre o vigia). O resto do ZIP fica
igual. Os testes falham no 68ad69d (r20)."""
import zipfile

from test_utilizador_apagar import ANA, correr, s  # noqa: F401 (fixture)


def _zip(s, tmp_path):
    destino = tmp_path / "ana.zip"
    rc, out, err = correr("--config", str(s.toml), "utilizador", "exportar", ANA, "--para", str(destino))
    assert rc == 0, err
    with zipfile.ZipFile(destino) as z:
        return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}, out


def test_nota_escrita_pela_web_antes_do_vigia_vai_no_zip(s, tmp_path):
    assert s.pedido("POST", "/api/nota", {"nome": "nova", "texto": "escrita agora pela web\n"}, email=ANA).codigo == 201
    esp = s.espaco("a")
    assert not (esp.real / "nova.md").exists()                         # controlo: o vigia ainda não a copiou
    z, out = _zip(s, tmp_path)
    assert z.get("por-sincronizar/nova.md") == "escrita agora pela web\n", sorted(z)   # antes: não estava no ZIP
    assert "por sincronizar" in out or "por-sincronizar" in out, out


def test_edicao_ainda_nao_copiada_vai_no_zip_e_a_versao_da_memoria_fica_onde_estava(s, tmp_path):
    esp = s.espaco("a").criar()
    for d in (esp.real, esp.copia):
        (d / "n1.md").write_text("versão antiga\n", encoding="utf-8")
        (d / "igual.md").write_text("igual nas duas\n", encoding="utf-8")
    (esp.copia / "n1.md").write_text("versão nova, ainda só na cópia\n", encoding="utf-8")
    z, _ = _zip(s, tmp_path)
    assert z.get("n1.md") == "versão antiga\n" and z.get("igual.md") == "igual nas duas\n"
    assert z.get("por-sincronizar/n1.md") == "versão nova, ainda só na cópia\n", sorted(z)    # antes: perdia-se
    assert "por-sincronizar/igual.md" not in z                         # o que já está igual não se repete


def test_sem_nada_por_sincronizar_o_zip_fica_como_antes(s, tmp_path):
    esp = s.espaco("a").criar()
    for d in (esp.real, esp.copia):
        (d / "n1.md").write_text("igual\n", encoding="utf-8")
    (esp.copia / "x.CONFLITO-2026.md").write_text("conflito\n", encoding="utf-8")   # o que o vigia não sincroniza fica de fora
    (esp.copia / "n1.md.bak").write_text("bak\n", encoding="utf-8")
    z, _ = _zip(s, tmp_path)
    assert not [n for n in z if n.startswith("por-sincronizar/")], sorted(z)
    assert z.get("n1.md") == "igual\n"


def test_o_exportar_nao_mexe_na_memoria_nem_na_copia(s, tmp_path):
    assert s.pedido("POST", "/api/nota", {"nome": "nova", "texto": "só na cópia\n"}, email=ANA).codigo == 201
    esp = s.espaco("a")
    antes = {p: p.read_bytes() for d in (esp.real, esp.copia) if d.exists() for p in d.rglob("*") if p.is_file()}
    _zip(s, tmp_path)
    depois = {p: p.read_bytes() for d in (esp.real, esp.copia) if d.exists() for p in d.rglob("*") if p.is_file()}
    assert antes == depois                                             # controlo: continua só de leitura (não corre o vigia)
