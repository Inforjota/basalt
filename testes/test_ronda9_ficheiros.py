"""Ronda 9 (anexos sem ligações simbólicas): nada do que o Basalt abre, lista, importa ou exporta atravessa uma ligação simbólica —
em nenhum componente do caminho, nem a meio (entre verificar e abrir). A importação Obsidian passa os anexos pelo mesmo caminho da
página (detector de segredos, nunca sobrepor) e valida o texto FINAL das notas. Os carimbos da Entrada nunca se repetem.

Os testes de comportamento falham POR ASSERÇÃO na versão anterior e passam com esta; os que usam directamente `caminhos.abrir`
& co só podem dar erro na anterior (não existiam). As trocas «a meio» são feitas na fronteira das chamadas ao sistema (a
primeira abertura do ficheiro, a primeira cópia…) e valem para as duas versões: o que muda é o que cada uma faz com a troca."""
import errno
import os
import shutil
import stat
import types
from pathlib import Path

import pytest

from basalt import anexos, caminhos, config, entrada, obsidian
from basalt.config import Espaco

PASSWORD = "senha do router: " + "Tr0ub4dor" + "&3"
CHAVE = "bs" + "lt_" + "a" * 32                      # parece uma chave do Basalt (montada aqui para o ficheiro não a conter)
FORA = b"conteudo-de-fora-do-espaco"


@pytest.fixture
def esp(tmp_path):
    return Espaco.de("ana", tmp_path / "dados").criar()


@pytest.fixture
def fora(tmp_path):
    """uma pasta fora de qualquer espaço, com um ficheiro e uma nota que nunca devem aparecer dentro."""
    d = tmp_path / "fora"
    d.mkdir()
    (d / "segredo.txt").write_bytes(FORA)
    (d / "foto.png").write_bytes(FORA)
    (d / "ficha.md").write_bytes(b"# Ficha\n\n" + FORA + b"\n")
    return d


def _interpor(monkeypatch, accao, *alvos):
    """faz `accao()` uma só vez, imediatamente antes de uma chamada que `quando` reconheça — o «outro processo» que age entre
    duas operações de quem está a ser testado. `alvos`: (objecto, atributo, quando(*a, **k), saltar) — `saltar` ignora as
    primeiras N chamadas que casam. Devolve o estado: `feito` diz se a troca chegou a acontecer (o teste confirma-o)."""
    estado = {"feito": False, "dentro": False}
    for obj, nome, quando, saltar in alvos:
        original = getattr(obj, nome)
        vistas = [0]

        def embrulho(*a, _o=original, _q=quando, _s=saltar, _v=vistas, **k):
            if not estado["feito"] and not estado["dentro"] and _q(*a, **k):
                _v[0] += 1
                if _v[0] > _s:
                    estado["dentro"] = True
                    try:
                        accao()
                    finally:
                        estado["dentro"] = False
                        estado["feito"] = True
            return _o(*a, **k)
        monkeypatch.setattr(obj, nome, embrulho)
    return estado


def _abre(nome):
    """os.open(nome, …, dir_fd=…): a abertura relativa a uma pasta de um componente com este nome (o que a versão nova faz)."""
    return lambda path, *a, **k: isinstance(path, str) and path == nome and k.get("dir_fd") is not None


def _nome(attr, nome):
    """um método de Path (ou uma função que recebe um caminho ou uma DirEntry) chamado com um ficheiro deste nome;
    um descritor (inteiro) nunca casa — é o que a versão nova passa."""
    def quando(p=None, *a, **k):
        try:
            return getattr(p, "name", None) == nome or Path(os.fspath(p)).name == nome
        except TypeError:
            return False
    return quando


def _ficheiro_para_ligacao(caminho: Path, alvo: Path):
    caminho.unlink()
    caminho.symlink_to(alvo)


def _pasta_para_ligacao(pasta: Path, alvo: Path):
    pasta.rename(pasta.with_name(pasta.name + "_velha"))
    pasta.symlink_to(alvo, target_is_directory=True)


def _tudo(destino: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(destino.rglob("*")) if p.is_file() and not p.is_symlink())


# ───────────────────────── caminhos.abrir & co ─────────────────────────
class TestAbrir:
    @pytest.fixture
    def arvore(self, tmp_path, fora):
        r = tmp_path / "raiz"
        (r / "a").mkdir(parents=True)
        (r / "a" / "x.txt").write_bytes(b"dentro")
        os.symlink(fora, r / "lnk")                            # pasta que aponta para fora
        os.symlink(fora / "segredo.txt", r / "a" / "f.txt")     # ficheiro que aponta para fora
        os.symlink("x.txt", r / "a" / "i.txt")                 # ficheiro que aponta para dentro
        os.mkfifo(r / "a" / "fila")
        return r

    def test_le_um_ficheiro_normal(self, arvore):
        assert caminhos.ler_bytes(arvore, "a/x.txt") == b"dentro"
        assert caminhos.ler_bytes(arvore, ["a", "x.txt"]) == b"dentro"
        fd = caminhos.abrir(arvore, "a/x.txt")
        try:
            assert stat.S_ISREG(os.fstat(fd).st_mode)
        finally:
            os.close(fd)

    @pytest.mark.parametrize("rel", ["lnk/segredo.txt", "a/f.txt", "a/i.txt", "lnk"])
    def test_recusa_ligacoes_em_qualquer_componente(self, arvore, rel):
        with pytest.raises(caminhos.Recusado):
            caminhos.ler_bytes(arvore, rel)

    @pytest.mark.parametrize("rel", ["../fora/segredo.txt", "a/../a/x.txt", "/etc/hostname", "", "a//x.txt", "a/./x.txt", "a/x.txt/", "a\0b"])
    def test_recusa_caminhos_invalidos(self, arvore, rel):
        with pytest.raises(caminhos.Recusado):
            caminhos.abrir(arvore, rel)

    def test_recusa_o_que_nao_e_ficheiro_normal_sem_bloquear(self, arvore):
        for rel in ("a/fila", "a"):                             # uma fila só abre sem bloquear com O_NONBLOCK
            with pytest.raises(caminhos.Recusado):
                caminhos.abrir(arvore, rel)

    def test_a_raiz_e_de_confianca(self, arvore, tmp_path):
        atalho = tmp_path / "atalho-da-raiz"                    # a raiz pode ser ela própria um caminho com ligações
        atalho.symlink_to(arvore, target_is_directory=True)
        assert caminhos.ler_bytes(atalho, "a/x.txt") == b"dentro"

    def test_limite_de_tamanho(self, arvore):
        assert caminhos.ler_bytes(arvore, "a/x.txt", limite=6) == b"dentro"
        with pytest.raises(caminhos.Recusado) as e:
            caminhos.ler_bytes(arvore, "a/x.txt", limite=5)
        assert e.value.errno == errno.EFBIG

    def test_escrever_cria_pastas_mas_nao_atravessa_ligacoes(self, arvore, fora):
        fd = caminhos.abrir(arvore, "nova/sub/ficheiro.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.write(fd, b"ola")
        os.close(fd)
        assert (arvore / "nova" / "sub" / "ficheiro.txt").read_bytes() == b"ola"
        with pytest.raises(caminhos.Recusado):
            caminhos.abrir(arvore, "lnk/novo.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        assert not (fora / "novo.txt").exists()
        with pytest.raises(FileExistsError):
            caminhos.abrir(arvore, "a/x.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL)

    def test_listar_dir_nao_inclui_ligacoes_como_ficheiros(self, arvore):
        tipos = {n: t for n, t, _ in caminhos.listar_dir(arvore, "a")}
        assert tipos == {"f.txt": "l", "fila": "o", "i.txt": "l", "x.txt": "f"}
        assert caminhos.listar_dir(arvore, "nao-existe") == []
        with pytest.raises(caminhos.Recusado):
            caminhos.listar_dir(arvore, "lnk")

    def test_percorrer_nao_desce_por_ligacoes(self, arvore):
        vistos = {rel: tipo for rel, tipo, _ in caminhos.percorrer(arvore)}
        assert vistos["lnk"] == "l" and "lnk/segredo.txt" not in vistos
        assert vistos["a/x.txt"] == "f" and vistos["a"] == "d"


# ───────────────────────── anexos: ligações nos ancestrais e troca a meio ─────────────────────────
class TestAnexosSemLigacoes:
    def test_uma_pasta_anexos_que_e_ligacao_nao_se_serve_nem_se_lista_nem_se_escreve(self, esp, fora):
        # ronda 9 (anexos sem ligações simbólicas): a verificação só olhava para o último componente
        (esp.real / "anexos").symlink_to(fora, target_is_directory=True)
        estado, _, corpo = anexos.ler(esp, "foto.png")
        assert estado == 404 and FORA not in corpo, "serviu um anexo através de uma pasta que é ligação simbólica"
        assert anexos.listar(esp) == []
        antes = sorted(p.name for p in fora.iterdir())
        with pytest.raises(ValueError, match="ligação simbólica"):
            anexos.guardar(esp, "novo.png", b"png")
        assert sorted(p.name for p in fora.iterdir()) == antes, "escreveu fora do espaço"

    def test_um_atalho_para_dentro_tambem_nao_se_lista_nem_se_serve(self, esp):
        anexos.guardar(esp, "foto.png", b"png")
        (esp.anexos / "atalho.png").symlink_to("foto.png")
        assert [a["nome"] for a in anexos.listar(esp)] == ["foto.png"]
        assert anexos.ler(esp, "atalho.png")[0] == 404
        assert anexos.ler(esp, "foto.png")[0] == 200

    def test_pasta_dos_anexos_noutro_sitio_e_a_propria_raiz(self, tmp_path, fora):
        esp = types.SimpleNamespace(real=tmp_path / "memoria", anexos=tmp_path / "outro-sitio")
        esp.real.mkdir()
        assert anexos.guardar(esp, "a.png", b"um") == "a.png"
        assert anexos.guardar(esp, "a.png", b"dois") == "a_2.png"
        (esp.anexos / "i.png").symlink_to("a.png")
        (esp.anexos / "f.png").symlink_to(fora / "foto.png")
        assert [a["nome"] for a in anexos.listar(esp)] == ["a.png", "a_2.png"]
        assert anexos.ler(esp, "a_2.png")[2] == b"dois" and anexos.ler(esp, "f.png")[0] == 404

    def test_toctou_o_anexo_trocado_por_uma_ligacao_depois_de_verificado_nao_se_serve(self, esp, fora, monkeypatch):
        anexos.guardar(esp, "foto.png", b"publico")
        alvo = esp.anexos / "foto.png"
        estado = _interpor(monkeypatch, lambda: _ficheiro_para_ligacao(alvo, fora / "segredo.txt"),
                           (Path, "read_bytes", _nome("read_bytes", "foto.png"), 0),     # versão anterior: ler o caminho outra vez
                           (os, "open", _abre("foto.png"), 0))                              # esta: abrir a partir da pasta
        estado_http, _, corpo = anexos.ler(esp, "foto.png")
        assert estado["feito"], "a troca não chegou a acontecer — o teste não provou nada"
        assert estado_http == 404 and FORA not in corpo, "serviu o ficheiro para onde a ligação (trocada a meio) aponta"

    def test_guardar_grava_por_temporario_e_ligacao_sem_deixar_restos(self, esp, monkeypatch):
        chamadas = []
        original = os.link

        def falha(*a, **k):
            chamadas.append(a)
            raise OSError(errno.ENOSPC, "sem espaço")
        monkeypatch.setattr(os, "link", falha)
        try:
            anexos.guardar(esp, "foto.png", b"png")
            falhou = False
        except OSError:
            falhou = True
        assert chamadas, "o anexo não foi publicado por link (nome final só aparece completo)"
        assert falhou, "a falha da publicação foi engolida"
        assert [p.name for p in esp.anexos.iterdir()] == [], "ficou um temporário ou um anexo a meio"
        monkeypatch.setattr(os, "link", original)
        assert anexos.guardar(esp, "foto.png", b"png") == "foto.png"
        assert [p.name for p in esp.anexos.iterdir()] == ["foto.png"]

    def test_sem_ligacoes_duras_grava_directamente_e_continua_a_nao_sobrepor(self, esp, monkeypatch):
        chamadas = []

        def sem_link(*a, **k):
            chamadas.append(a)
            raise PermissionError(errno.EPERM, "este volume não tem ligações duras")
        monkeypatch.setattr(os, "link", sem_link)
        assert anexos.guardar(esp, "foto.png", b"um") == "foto.png"
        assert anexos.guardar(esp, "foto.png", b"um") == "foto.png"
        assert anexos.guardar(esp, "foto.png", b"dois") == "foto_2.png"
        assert chamadas and sorted(p.name for p in esp.anexos.iterdir()) == ["foto.png", "foto_2.png"]
        assert (esp.anexos / "foto.png").read_bytes() == b"um"


# ───────────────────────── importar: segredos, ordem, simulação ─────────────────────────
def _cofre(tmp_path, nome="Cofre"):
    c = tmp_path / nome
    c.mkdir()
    return c


class TestImportarSegredosEOrdem:
    def test_um_anexo_com_password_nao_entra_e_o_embed_fica_como_esta(self, tmp_path, esp):
        c = _cofre(tmp_path)
        (c / "senhas.txt").write_text(f"{PASSWORD}\n", encoding="utf-8")
        (c / "ok.txt").write_text("nada de especial\n", encoding="utf-8")
        (c / "Nota.md").write_text("# Nota\n\nVer ![[senhas.txt]] e ![[ok.txt]].\n", encoding="utf-8")
        r = obsidian.importar(c, esp)
        assert not (esp.anexos / "senhas.txt").exists(), "um anexo com uma password passou pela importação"
        assert "senhas.txt" not in r["anexos"] and r["anexos"] == ["ok.txt"]
        assert any(f == "senhas.txt" and "password" in porque for f, porque in r["saltadas"]), r["saltadas"]
        assert "![[senhas.txt]]" in (esp.real / "nota.md").read_text(encoding="utf-8")
        assert not any("Tr0ub4dor" in p.read_text(encoding="utf-8", errors="replace") for p in esp.real.rglob("*") if p.is_file())

    def test_uma_nota_com_nome_de_chave_nao_entra_porque_o_cabecalho_levava_o_nome(self, tmp_path, esp):
        c = _cofre(tmp_path)
        (c / f"{CHAVE}.md").write_text("# Uma nota qualquer\n\nsem nada de especial no corpo.\n", encoding="utf-8")
        (c / "Normal.md").write_text("# Normal\n\ncorpo.\n", encoding="utf-8")
        r = obsidian.importar(c, esp)
        assert r["importadas"] == ["normal"]
        assert not list(esp.real.glob("bslt*")), "a chave ficou no nome da nota e no cabeçalho"
        assert not any(CHAVE in p.read_text(encoding="utf-8") for p in esp.real.glob("*.md"))
        assert any(f.startswith("bslt_") and "chave" in porque for f, porque in r["saltadas"]), r["saltadas"]

    def test_valida_se_o_texto_final_depois_de_reescrever(self, tmp_path, esp, monkeypatch):
        c = _cofre(tmp_path)
        (c / "Nota.md").write_text("# Nota\n\ncorpo simples.\n", encoding="utf-8")
        original = obsidian._com_nome
        monkeypatch.setattr(obsidian, "_com_nome", lambda nome, texto: original(nome, texto) + f"\n{PASSWORD}\n")   # a transformação final introduz o segredo
        r = obsidian.importar(c, esp)
        assert r["importadas"] == [] and not list(esp.real.glob("*.md")), "gravou o texto final com uma password"
        assert any(f == "Nota.md" and "password" in porque for f, porque in r["saltadas"])

    def test_os_anexos_gravam_se_antes_das_notas(self, tmp_path, esp, monkeypatch):
        c = _cofre(tmp_path)
        (c / "foto.png").write_bytes(b"png")
        (c / "Nota.md").write_text("# Nota\n\n![[foto.png]]\n", encoding="utf-8")
        from basalt import cas
        original = cas.escrever
        ao_gravar = []

        def espia(alvo, *a, **k):
            ao_gravar.append((Path(alvo).name, sorted(p.name for p in esp.anexos.glob("*")) if esp.anexos.exists() else []))
            return original(alvo, *a, **k)
        monkeypatch.setattr(cas, "escrever", espia)
        obsidian.importar(c, esp)
        assert ao_gravar == [("nota.md", ["foto.png"])], f"a nota gravou-se antes do anexo: {ao_gravar}"

    def test_simular_nao_escreve_nada_e_diz_o_mesmo_que_a_gravacao(self, tmp_path):
        c = _cofre(tmp_path)
        (c / "a").mkdir(); (c / "b").mkdir()
        (c / "a" / "foto.png").write_bytes(b"AAA")
        (c / "b" / "foto.png").write_bytes(b"BBB")
        (c / "senhas.txt").write_text(PASSWORD, encoding="utf-8")
        (c / "a" / "Nota A.md").write_text("![[foto.png]] ![[senhas.txt]]\n", encoding="utf-8")
        (c / "b" / "Nota B.md").write_text("![[foto.png]]\n", encoding="utf-8")
        prova = Espaco.de("prova", tmp_path / "dados-prova").criar()
        real = Espaco.de("real", tmp_path / "dados-real").criar()
        (prova.real / "ja-existia.md").write_text("---\nname: ja-existia\n---\nigual\n", encoding="utf-8")
        antes = sorted(str(p.relative_to(prova.real)) for p in prova.real.rglob("*"))
        sim = obsidian.importar(c, prova, simular=True)
        assert sorted(str(p.relative_to(prova.real)) for p in prova.real.rglob("*")) == antes, "a simulação escreveu"
        assert not prova.anexos.exists()
        efectivo = obsidian.importar(c, real)
        assert sim["anexos"] == efectivo["anexos"] == ["foto.png", "foto_2.png"], (sim["anexos"], efectivo["anexos"])
        assert sim["importadas"] == efectivo["importadas"]
        assert sorted(sim["saltadas"]) == sorted(efectivo["saltadas"])
        assert any(f == "senhas.txt" and "password" in porque for f, porque in sim["saltadas"]), "a simulação não avisou do segredo"


# ───────────────────────── importar: o anexo criado entretanto não se sobrepõe ─────────────────────────
class TestAnexoConcorrente:
    def test_o_anexo_que_apareceu_entretanto_fica_intacto(self, tmp_path, esp, monkeypatch):
        c = _cofre(tmp_path)
        (c / "foto.png").write_bytes(b"do cofre")
        (c / "Nota.md").write_text("# Nota\n\n![[foto.png]]\n", encoding="utf-8")
        destino = esp.anexos / "foto.png"

        def concorrente():
            esp.anexos.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(b"do concorrente")            # outro processo gravou o mesmo nome entre o «existe?» e a gravação
        def para_foto(destino_):
            return lambda *a, **k: Path(os.fspath(a[1] if len(a) > 1 else k.get("dst"))).name == "foto.png"
        estado = _interpor(monkeypatch, concorrente,
                           (shutil, "copy2", para_foto(None), 0),      # versão anterior: copy2 para o destino
                           (os, "link", para_foto(None), 0))           # esta: link para o nome final
        r = obsidian.importar(c, esp)
        assert estado["feito"], "o concorrente não chegou a escrever — o teste não provou nada"
        assert destino.read_bytes() == b"do concorrente", "a importação sobrepôs o anexo gravado entretanto"
        assert (esp.anexos / "foto_2.png").read_bytes() == b"do cofre" and r["anexos"] == ["foto_2.png"]
        assert "![[foto_2.png]]" in (esp.real / "nota.md").read_text(encoding="utf-8")


# ───────────────────────── importar: anexos com o mesmo nome em pastas diferentes ─────────────────────────
class TestAnexosPorCaminho:
    def _duas_pastas(self, tmp_path):
        c = _cofre(tmp_path)
        for p in ("a", "b"):
            (c / p).mkdir()
        (c / "a" / "foto.png").write_bytes(b"AAA")
        (c / "b" / "foto.png").write_bytes(b"BBB")
        return c

    def test_um_nome_sozinho_resolve_para_o_da_pasta_da_nota_e_um_caminho_para_esse_ficheiro(self, tmp_path, esp):
        c = self._duas_pastas(tmp_path)
        (c / "a" / "Nota A.md").write_text("A: ![[foto.png]]\n", encoding="utf-8")
        (c / "b" / "Nota B.md").write_text("B: ![[foto.png]]\n", encoding="utf-8")
        (c / "Raiz.md").write_text("explícitos: ![[a/foto.png]] e ![[b/foto.png|a outra]]\n", encoding="utf-8")
        r = obsidian.importar(c, esp)
        assert (esp.anexos / "foto.png").read_bytes() == b"AAA" and (esp.anexos / "foto_2.png").read_bytes() == b"BBB"
        assert r["anexos"] == ["foto.png", "foto_2.png"]
        ler = lambda n: (esp.real / f"{n}.md").read_text(encoding="utf-8")
        assert "A: ![[foto.png]]" in ler("nota_a") and "B: ![[foto_2.png]]" in ler("nota_b")
        assert "![[foto.png]] e ![[foto_2.png|a outra]]" in ler("raiz")
        assert not any("diferente" in porque for _, porque in r["saltadas"])

    def test_nome_ambiguo_fora_das_pastas_dos_anexos_salta_e_deixa_o_embed(self, tmp_path, esp):
        c = self._duas_pastas(tmp_path)
        (c / "Raiz.md").write_text("sem saber qual: ![[foto.png]]\n", encoding="utf-8")
        r = obsidian.importar(c, esp)
        assert any(f == "foto.png" and "ambíguo" in porque for f, porque in r["saltadas"]), r["saltadas"]
        assert r["anexos"] == [] and not list(esp.anexos.glob("*")) if esp.anexos.exists() else True
        assert "![[foto.png]]" in (esp.real / "raiz.md").read_text(encoding="utf-8")

    def test_nome_que_a_pagina_nao_aceita_ganha_um_nome_seguro(self, tmp_path, esp):
        c = _cofre(tmp_path)
        (c / "Foto (1).PNG").write_bytes(b"png")
        (c / "Nota.md").write_text("![[Foto (1).PNG|ao pé do mar]]\n", encoding="utf-8")
        r = obsidian.importar(c, esp)
        assert r["anexos"] == ["foto_1.png"] and (esp.anexos / "foto_1.png").read_bytes() == b"png"
        assert "![[foto_1.png|ao pé do mar]]" in (esp.real / "nota.md").read_text(encoding="utf-8")

    def test_uma_nota_que_refere_a_nota_de_outra_pasta_nao_e_um_anexo_em_falta(self, tmp_path, esp):
        c = _cofre(tmp_path)
        (c / "Um.md").write_text("inclui ![[Dois]]\n", encoding="utf-8")
        (c / "Dois.md").write_text("dois\n", encoding="utf-8")
        r = obsidian.importar(c, esp)
        assert not any("anexo" in porque for _, porque in r["saltadas"]), r["saltadas"]


# ───────────────────────── importar: pastas que são ligações simbólicas ─────────────────────────
class TestPastasSimbolicasNoCofre:
    def test_uma_pasta_simbolica_do_cofre_nao_se_importa_e_avisa(self, tmp_path, esp, fora):
        c = _cofre(tmp_path)
        (c / "Nota.md").write_text("# Nota\n\n![[foto.png]]\n", encoding="utf-8")
        (c / "ligada").symlink_to(fora, target_is_directory=True)
        r = obsidian.importar(c, esp)
        assert r["importadas"] == ["nota"] and not (esp.anexos / "foto.png").exists()
        assert not any(FORA in p.read_bytes() for p in esp.real.rglob("*") if p.is_file())
        assert any(f == "ligada" and "simbólica" in porque for f, porque in r["saltadas"]), r["saltadas"]
        assert any(f == "foto.png" and "não encontrado" in porque for f, porque in r["saltadas"])

    def test_uma_pasta_trocada_por_ligacao_depois_de_listada_nao_se_le(self, tmp_path, esp, fora, monkeypatch):
        c = _cofre(tmp_path)
        (c / "sub").mkdir()
        (c / "sub" / "ficha.md").write_text("# Ficha\n\ncorpo do cofre.\n", encoding="utf-8")
        estado = _interpor(monkeypatch, lambda: _pasta_para_ligacao(c / "sub", fora),
                           (Path, "read_text", _nome("read_text", "ficha.md"), 0),   # versão anterior: ler o caminho (já listado) outra vez
                           (os, "open", _abre("sub"), 1))                              # esta: a 2.ª abertura de «sub» é a da leitura (a 1.ª, a da listagem)
        r = obsidian.importar(c, esp)
        assert estado["feito"], "a troca não chegou a acontecer — o teste não provou nada"
        assert not any(FORA in p.read_bytes() for p in esp.real.rglob("*") if p.is_file()), "importou o conteúdo para onde a ligação (trocada a meio) aponta"
        assert r["importadas"] == [] and any(f == "sub/ficha.md" and "simbólica" in porque for f, porque in r["saltadas"]), r["saltadas"]


# ───────────────────────── exportar: não segue ligações (nem as trocadas a meio) ─────────────────────────
class TestExportarSemLigacoes:
    @pytest.fixture
    def memoria_com_ligacoes(self, esp, fora):
        (esp.real / "dentro.md").write_text("---\nname: dentro\n---\nd\n", encoding="utf-8")
        (esp.real / "sub").mkdir()
        (esp.real / "sub" / "fundo.md").write_text("---\nname: fundo\n---\nf\n", encoding="utf-8")
        anexos.guardar(esp, "foto.png", b"png")
        (esp.real / "fuga.md").symlink_to(fora / "ficha.md")
        (esp.real / "pasta-fuga").symlink_to(fora, target_is_directory=True)
        (esp.anexos / "atalho.png").symlink_to(fora / "foto.png")
        (esp.real / ".git").mkdir()
        (esp.real / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        (esp.real / ".nota.md.tmp").write_text("temporario\n", encoding="utf-8")
        return esp

    def test_exporta_o_que_e_normal_e_nada_do_que_aponta_para_fora(self, memoria_com_ligacoes, tmp_path):
        # guarda de regressão: as ligações ESTÁTICAS já eram saltadas; o que muda são as trocadas a meio (testes seguintes)
        destino = tmp_path / "export"
        assert obsidian.exportar(destino, memoria_com_ligacoes) == 1                 # dentro.md (as notas da raiz)
        assert (destino / "dentro.md").is_file() and (destino / "sub" / "fundo.md").is_file()
        assert (destino / "anexos" / "foto.png").read_bytes() == b"png"
        assert not (destino / ".git").exists() and not (destino / ".nota.md.tmp").exists()
        for fuga in ("fuga.md", "pasta-fuga", "anexos/atalho.png"):
            assert not (destino / fuga).exists() and not (destino / fuga).is_symlink(), fuga
        assert FORA not in _tudo(destino)

    def test_um_ficheiro_trocado_por_ligacao_depois_de_listado_nao_se_copia(self, esp, fora, tmp_path, monkeypatch):
        for n in ("a", "b", "c"):
            (esp.real / f"{n}.md").write_text(f"---\nname: {n}\n---\n{n}\n", encoding="utf-8")
        estado = _interpor(monkeypatch, lambda: _ficheiro_para_ligacao(esp.real / "b.md", fora / "ficha.md"),
                           (shutil, "copyfile", _nome("copyfile", "b.md"), 0),  # versão anterior: copytree copia pelo caminho já listado (copy2 → copyfile)
                           (os, "open", _abre("b.md"), 0))                      # esta: abertura relativa à pasta
        destino = tmp_path / "export"
        obsidian.exportar(destino, esp)
        assert estado["feito"], "a troca não chegou a acontecer — o teste não provou nada"
        assert FORA not in _tudo(destino), "exportou o conteúdo para onde a ligação (trocada a meio) aponta"
        assert (destino / "a.md").is_file() and (destino / "c.md").is_file()

    def test_uma_pasta_trocada_por_ligacao_depois_de_listada_nao_se_copia(self, esp, fora, tmp_path, monkeypatch):
        (esp.real / "sub").mkdir()
        (esp.real / "sub" / "fundo.md").write_text("---\nname: fundo\n---\nf\n", encoding="utf-8")
        (esp.real / "fora-da-pasta.md").write_text("---\nname: x\n---\nx\n", encoding="utf-8")
        estado = _interpor(monkeypatch, lambda: _pasta_para_ligacao(esp.real / "sub", fora),
                           (os, "scandir", _nome("scandir", "sub"), 0),                           # versão anterior: copytree lista «sub» pelo caminho para a copiar
                           (os, "open", _abre("sub"), 0))                                     # esta: abrir «sub» a partir da raiz
        destino = tmp_path / "export"
        obsidian.exportar(destino, esp)
        assert estado["feito"], "a troca não chegou a acontecer — o teste não provou nada"
        assert FORA not in _tudo(destino), "copiou a pasta para onde a ligação (trocada a meio) aponta"
        assert (destino / "fora-da-pasta.md").is_file()


# ───────────────────────── Entrada: um carimbo nunca se emite duas vezes ─────────────────────────
class TestCarimbosDaEntrada:
    SEG = "2026-10-08T10:00:00"

    @pytest.fixture(autouse=True)
    def _relogio(self, memoria, monkeypatch):
        self.relogio = [self.SEG]
        monkeypatch.setattr(entrada, "_agora", lambda: self.relogio[0])

    def test_criar_apagar_criar_no_mesmo_segundo_nao_repete_o_carimbo(self, memoria):
        a = entrada.acrescentar("um", "web")
        b = entrada.acrescentar("dois", "web")
        assert (a["t"], b["t"]) == (self.SEG, self.SEG + "~2")
        assert entrada.mudar(b["t"], lambda r: None) is not None               # apagada a segunda
        c = entrada.acrescentar("três", "web")
        assert c["t"] not in (a["t"], b["t"]), "o carimbo de uma ideia apagada foi emitido outra vez"
        assert c["t"] == self.SEG + "~3"

    def test_apagadas_todas_o_seguinte_continua_a_ser_maior(self, memoria):
        ts = [entrada.acrescentar(f"ideia {n}", "web")["t"] for n in range(3)]
        for t in ts:
            entrada.mudar(t, lambda r: None)
        assert entrada.ler() == []
        assert entrada.acrescentar("nova", "web")["t"] == self.SEG + "~4"

    def test_o_ultimo_carimbo_fica_gravado_ao_lado_da_entrada_e_sobrevive_a_apagar_o_ficheiro(self, memoria):
        t = entrada.acrescentar("um", "web")["t"]
        ultimo = entrada.caminho().with_name(".entrada.ultimo")
        assert ultimo.is_file(), "o último carimbo não ficou gravado ao lado da Entrada"
        assert ultimo.read_text(encoding="utf-8").strip() == t, "o último carimbo não ficou gravado"
        entrada.caminho().unlink()                                               # a Entrada desapareceu (apagada à mão, restaurada…)
        assert entrada.acrescentar("dois", "web")["t"] == self.SEG + "~2"

    @staticmethod
    def _chave(t):
        base, _, n = t.partition("~")
        return (base, int(n or 1))

    def test_um_relogio_que_anda_para_tras_nao_repete_nem_diminui(self, memoria):
        a = entrada.acrescentar("um", "web")
        self.relogio[0] = "2026-10-08T09:59:00"
        b = entrada.acrescentar("dois", "web")
        assert self._chave(b["t"]) > self._chave(a["t"]), (a["t"], b["t"])
        self.relogio[0] = "2026-10-08T10:00:05"
        assert self._chave(entrada.acrescentar("três", "web")["t"]) > self._chave(b["t"])

    def test_a_ordem_e_numerica_e_nao_textual(self, memoria):
        ts = [entrada.acrescentar(f"ideia {n}", "web")["t"] for n in range(11)]    # …, ~9, ~10, ~11
        assert ts[-1] == self.SEG + "~11"
        for t in ts[-2:]:                                                           # apagadas a ~10 e a ~11
            entrada.mudar(t, lambda r: None)
        novo = entrada.acrescentar("seguinte", "web")["t"]
        assert novo == self.SEG + "~12", novo                                       # não «o primeiro livre» (~10) nem ~9 + 1
        assert len(set(ts + [novo])) == 12

    def test_a_escrita_do_ultimo_e_atomica_e_nao_deixa_temporarios(self, memoria):
        entrada.acrescentar("um", "web")
        entrada.acrescentar("dois", "web")
        pasta = entrada.caminho().parent
        assert (pasta / ".entrada.ultimo").is_file()
        assert not [p for p in pasta.iterdir() if p.name.endswith(".tmp")]
