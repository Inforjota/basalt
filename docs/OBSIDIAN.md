# Basalt e Obsidian

A memória do Basalt **já é um cofre Obsidian**: uma pasta de ficheiros `.md` com `[[ligações]]`, histórico git e sem formato
próprio. Não há «sincronizar com o Obsidian» — há uma pasta que os dois abrem.

## Usar o Obsidian em cima do Basalt
Abre no Obsidian a pasta **`copia/`** do teu espaço (o caminho exacto aparece em `python3 -m basalt estado`). O Basalt
mantém `copia/` e `memoria/` iguais nas duas direcções: o que editas no Obsidian entra na memória, o que os agentes escrevem
aparece no Obsidian. Se os dois mudarem a mesma nota no mesmo instante, nada se perde: fica uma nota de conflito com as duas
versões, e a interface mostra-a para resolveres.

## Trazer um cofre que já tens
```bash
python3 -m basalt importar ~/O-meu-cofre --simular    # 1.º só ver: quantas notas, quais ficam de fora e porquê
python3 -m basalt importar ~/O-meu-cofre              # 2.º importar
python3 -m basalt sincronizar && python3 -m basalt indexar
```
O que a importação faz, e só isso:
- **Nomes.** Cada nota passa a ter um nome que o Basalt aceita: minúsculas, sem acentos, `_` em vez de espaços
  (`Barcos do Pico.md` → `barcos_do_pico.md`). As subpastas achatam-se (o Obsidian liga pelo nome, não pela pasta); se dois
  nomes coincidirem, o segundo leva `_2`.
- **Ligações.** `[[Barcos do Pico]]` passa a `[[barcos_do_pico|Barcos do Pico]]`: a ligação funciona e o texto que vês é o de
  sempre. Os aliases que já tinhas (`[[X|assim]]`) mantêm-se; as âncoras (`[[X#Secção]]`) caem; uma ligação a uma nota que não
  vem no cofre fica como está e aparece na interface como «por criar».
- **Cabeçalho.** As notas sem `---` recebem `name`, `description` (a primeira linha) e `type`; nas que já o têm só se acrescenta
  o `name` se faltar.
- **Anexos.** As imagens e ficheiros referenciados com `![[foto.png]]` copiam-se para a pasta `anexos/` do espaço. Um anexo
  referenciado mas que não existe no cofre fica na lista de «saltadas».
- **Nunca sobrepõe.** Uma nota cujo nome já exista no espaço fica de fora e é listada; corres outra vez depois de decidires.
  `.obsidian/`, `.trash/` e tudo o que começa por `.` não entram.
- Com git activo, a importação fica numa só fotografia (`basalt: importação de N notas`), por isso recua-se com `git revert`.

## Levar tudo para fora
```bash
python3 -m basalt exportar ~/cofre-exportado
```
Copia notas e anexos (sem o `.git`) para uma pasta vazia. Abre-a no Obsidian e está feito — não há formato a converter.
