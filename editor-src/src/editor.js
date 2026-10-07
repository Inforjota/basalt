// Basalt — editor markdown (CodeMirror 6), empacotado como window.BasaltEditor
// Fonte: editor-src/src/editor.js — construir com ./construir.sh
// PT-PT em todos os textos visíveis. Sem CDNs, sem eval (compatível com CSP script-src 'self').

import { EditorState, Compartment, Prec } from "@codemirror/state";
import {
  EditorView, Decoration, WidgetType, ViewPlugin, keymap, drawSelection, dropCursor
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import {
  syntaxTree, indentUnit, Language, LanguageSupport,
  defineLanguageFacet, languageDataProp, indentNodeProp
} from "@codemirror/language";
import { parser as mdParser, GFM } from "@lezer/markdown";
import { search, searchKeymap } from "@codemirror/search";
import { autocompletion, completionKeymap } from "@codemirror/autocomplete";

// Nota: NÃO se usa @codemirror/lang-markdown de propósito — essa embalagem importa
// incondicionalmente @codemirror/lang-html (que por sua vez arrasta lang-css,
// lang-javascript e @codemirror/lint só para completar tags/HTML embutido, algo
// que este editor não precisa). Construir a língua directamente a partir de
// @lezer/markdown poupa ~350 KB no bundle final. O comando de Enter que continua
// listas/citações é uma reimplementação leve própria (ver continuarListaOuCitacao),
// inspirada no insertNewlineContinueMarkup do CodeMirror.

/* ------------------------------------------------------------------------
 * 1. Extensões da gramática markdown: ==realce==, [[wikilink]], #etiqueta
 * ---------------------------------------------------------------------- */

let Punctuation;
try {
  Punctuation = new RegExp("[\\p{S}|\\p{P}]", "u");
} catch (_e) {
  Punctuation = /[!"#$%&'()*+,\-.\/:;<=>?@[\\\]^_`{|}~]/;
}

const HighlightDelim = { resolve: "Highlight", mark: "HighlightMark" };

const Highlight = {
  defineNodes: ["Highlight", "HighlightMark"],
  parseInline: [{
    name: "Highlight",
    parse(cx, next, pos) {
      if (next !== 61 /* = */ || cx.char(pos + 1) !== 61 || cx.char(pos + 2) === 61) return -1;
      const before = cx.slice(pos - 1, pos), after = cx.slice(pos + 2, pos + 3);
      const sBefore = /\s|^$/.test(before), sAfter = /\s|^$/.test(after);
      const pBefore = Punctuation.test(before), pAfter = Punctuation.test(after);
      return cx.addDelimiter(
        HighlightDelim, pos, pos + 2,
        !sAfter && (!pAfter || sBefore || pBefore),
        !sBefore && (!pBefore || sAfter || pAfter)
      );
    },
    after: "Emphasis"
  }]
};

// [[nota]] ou [[nota|texto]] — nó plano; a decoração recalcula a divisão a partir do texto.
const WikiLink = {
  defineNodes: ["WikiLink"],
  parseInline: [{
    name: "WikiLink",
    parse(cx, next, pos) {
      if (next !== 91 /* [ */ || cx.char(pos + 1) !== 91) return -1;
      const text = cx.text, offset = cx.offset;
      const start = pos - offset;
      const close = text.indexOf("]]", start + 2);
      if (close < 0) return -1;
      const inner = text.slice(start + 2, close);
      if (!inner || /[\n[\]]/.test(inner)) return -1;
      const end = offset + close + 2;
      return cx.addElement(cx.elt("WikiLink", pos, end));
    },
    before: "Link"
  }]
};

// #etiqueta — só depois de espaço/início de linha, nunca dentro de heading (já consumido no bloco).
const Tag = {
  defineNodes: ["Tag", "TagMark"],
  parseInline: [{
    name: "Tag",
    parse(cx, next, pos) {
      if (next !== 35 /* # */) return -1;
      const before = cx.char(pos - 1);
      if (!(before === -1 || before === 32 || before === 10 || before === 9)) return -1;
      const text = cx.text, offset = cx.offset;
      const start = pos - offset;
      const m = /^#[^\s#[\]()!,.;:?"'`~*_=<>|\\]+/u.exec(text.slice(start));
      if (!m || m[0].length < 2) return -1;
      const end = pos + m[0].length;
      return cx.addElement(cx.elt("Tag", pos, end, [cx.elt("TagMark", pos, pos + 1)]));
    }
  }]
};

const dadosLinguagemMd = defineLanguageFacet({ commentTokens: { block: { open: "<!--", close: "-->" } } });

const parserMdConfigurado = mdParser.configure([
  GFM, Highlight, WikiLink, Tag,
  {
    props: [
      indentNodeProp.add({ Document: () => null }),
      languageDataProp.add({ Document: dadosLinguagemMd })
    ]
  }
]);

const basaltMarkdownLanguage = new Language(dadosLinguagemMd, parserMdConfigurado, [], "markdown");
const markdownExt = new LanguageSupport(basaltMarkdownLanguage);

/* ------------------------------------------------------------------------
 * 1b. Enter continua listas/citações; Tab/Shift-Tab indenta (leve, sem
 *     depender de @codemirror/lang-markdown — ver nota acima)
 * ---------------------------------------------------------------------- */

const RE_ITEM_LISTA = /^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\]\s+)?/;
const RE_CITACAO = /^(\s*(?:>\s?)+)/;

function continuarListaOuCitacao({ state, dispatch }) {
  const range = state.selection.main;
  if (!range.empty) return false;
  const linha = state.doc.lineAt(range.from);
  const texto = linha.text;

  const mLista = RE_ITEM_LISTA.exec(texto);
  if (mLista) {
    if (texto.trim() === mLista[0].trim()) {
      // item de lista vazio: Enter remove o marcador em vez de o repetir
      dispatch(state.update({
        changes: { from: linha.from, to: linha.to, insert: "" },
        selection: { anchor: linha.from },
        userEvent: "delete"
      }));
      return true;
    }
    let marcador = mLista[2];
    const numerica = /^(\d+)([.)])$/.exec(marcador);
    if (numerica) marcador = (parseInt(numerica[1], 10) + 1) + numerica[2];
    const prefixo = mLista[1] + marcador + mLista[3] + (mLista[4] ? "[ ] " : "");
    dispatch(state.update({
      changes: { from: range.from, insert: "\n" + prefixo },
      selection: { anchor: range.from + 1 + prefixo.length },
      scrollIntoView: true,
      userEvent: "input"
    }));
    return true;
  }

  const mCitacao = RE_CITACAO.exec(texto);
  if (mCitacao) {
    if (texto.trim() === mCitacao[0].trim()) {
      dispatch(state.update({
        changes: { from: linha.from, to: linha.to, insert: "" },
        selection: { anchor: linha.from },
        userEvent: "delete"
      }));
      return true;
    }
    dispatch(state.update({
      changes: { from: range.from, insert: "\n" + mCitacao[0] },
      selection: { anchor: range.from + 1 + mCitacao[0].length },
      scrollIntoView: true,
      userEvent: "input"
    }));
    return true;
  }

  return false;
}

/* ------------------------------------------------------------------------
 * 2. Widgets (caixa de tarefa, título de callout)
 * ---------------------------------------------------------------------- */

const ROTULOS_CALLOUT = {
  nota: "Nota", note: "Nota", info: "Informação", informacao: "Informação",
  aviso: "Aviso", warning: "Aviso", cuidado: "Cuidado", caution: "Cuidado",
  erro: "Erro", error: "Erro", perigo: "Perigo", danger: "Perigo",
  sucesso: "Sucesso", success: "Sucesso", check: "Sucesso",
  dica: "Dica", tip: "Dica", hint: "Dica",
  exemplo: "Exemplo", example: "Exemplo",
  citacao: "Citação", quote: "Citação", cite: "Citação",
  importante: "Importante", important: "Importante",
  pergunta: "Pergunta", question: "Pergunta", faq: "Pergunta", help: "Pergunta",
  falha: "Falha", fail: "Falha", failure: "Falha",
  resumo: "Resumo", summary: "Resumo", tldr: "Resumo", abstract: "Resumo",
  tarefa: "Tarefa", todo: "Tarefa"
};

function rotuloCallout(tipo) {
  const chave = tipo.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  if (ROTULOS_CALLOUT[chave]) return ROTULOS_CALLOUT[chave];
  return tipo.charAt(0).toUpperCase() + tipo.slice(1);
}

class CalloutBadgeWidget extends WidgetType {
  constructor(tipo) { super(); this.tipo = tipo; }
  eq(outro) { return outro.tipo === this.tipo; }
  toDOM() {
    const span = document.createElement("span");
    span.className = "cm-callout-titulo";
    span.textContent = rotuloCallout(this.tipo);
    return span;
  }
  ignoreEvent() { return true; }
}

class TaskCheckboxWidget extends WidgetType {
  constructor(marcada, de) { super(); this.marcada = marcada; this.de = de; }
  eq(outro) { return outro.marcada === this.marcada && outro.de === this.de; }
  toDOM() {
    const span = document.createElement("span");
    span.className = "cm-task-checkbox" + (this.marcada ? " cm-task-checkbox-marcada" : "");
    span.setAttribute("data-de", String(this.de));
    span.setAttribute("role", "checkbox");
    span.setAttribute("aria-checked", this.marcada ? "true" : "false");
    span.setAttribute("aria-label", this.marcada ? "Tarefa concluída" : "Tarefa por concluir");
    return span;
  }
  ignoreEvent(event) { return event.type !== "mousedown" && event.type !== "touchstart"; }
}

/* ------------------------------------------------------------------------
 * 3. Live preview — esconde marcas fora da linha do cursor, mostra o efeito
 * ---------------------------------------------------------------------- */

function linhaFimFrontmatter(doc) {
  if (doc.lines < 2) return -1;
  if (doc.line(1).text.trim() !== "---") return -1;
  const max = Math.min(doc.lines, 500);
  for (let n = 2; n <= max; n++) {
    if (doc.line(n).text.trim() === "---") return n;
  }
  return -1;
}

function identificadorCss(s) {
  const t = String(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9-]/g, "-").slice(0, 24);
  return t || "geral";
}

function construirDecoracoes(view) {
  try {
    return construirDecoracoesInterno(view);
  } catch (erro) {
    console.error("[BasaltEditor] falha ao construir a live preview:", erro);
    return Decoration.none;
  }
}

function construirDecoracoesInterno(view) {
  const state = view.state;
  const doc = state.doc;
  const tree = syntaxTree(state);
  const linhasActivas = new Set();
  for (const r of state.selection.ranges) {
    const l1 = doc.lineAt(r.from).number, l2 = doc.lineAt(r.to).number;
    for (let n = l1; n <= l2; n++) linhasActivas.add(n);
  }
  const activa = (pos) => linhasActivas.has(doc.lineAt(pos).number);

  const itens = [];
  const esconder = (de, ate) => { if (ate > de) itens.push(Decoration.replace({}).range(de, ate)); };
  const estilo = (de, ate, cls) => { if (ate > de) itens.push(Decoration.mark({ class: cls }).range(de, ate)); };
  const estiloWikilink = (de, ate, nome) => {
    if (ate > de) itens.push(Decoration.mark({ class: "cm-wikilink", attributes: { "data-wikilink": nome } }).range(de, ate));
  };
  const classeLinhas = (l1, l2, cls) => {
    for (let n = l1; n <= l2 && n <= doc.lines; n++) itens.push(Decoration.line({ class: cls }).range(doc.line(n).from));
  };

  const fimFm = linhaFimFrontmatter(doc);
  if (fimFm > 0) classeLinhas(1, fimFm, "cm-frontmatter");

  tree.iterate({
    enter(ref) {
      const nome = ref.type.name;

      if (nome[0] === "A" && /^ATXHeading[1-6]$/.test(nome)) {
        const nivel = nome.slice(-1);
        const ln = doc.lineAt(ref.from).number;
        if (!linhasActivas.has(ln)) {
          const n = ref.node;
          const marca = n.firstChild;
          if (marca && marca.type.name === "HeaderMark") {
            let fim = marca.to;
            if (doc.sliceString(fim, fim + 1) === " ") fim += 1;
            esconder(marca.from, fim);
          }
        }
        classeLinhas(ln, ln, "cm-h" + nivel);   // como no Obsidian: o título fica sempre grande; só o «#» aparece com o cursor
        return;
      }

      switch (nome) {
        case "Emphasis":
        case "StrongEmphasis": {
          if (!activa(ref.from)) {
            const n = ref.node;
            const primeiro = n.firstChild, ultimo = n.lastChild;
            if (primeiro && ultimo && primeiro !== ultimo &&
                primeiro.type.name === "EmphasisMark" && ultimo.type.name === "EmphasisMark") {
              esconder(primeiro.from, primeiro.to);
              esconder(ultimo.from, ultimo.to);
              estilo(primeiro.to, ultimo.from, nome === "Emphasis" ? "cm-em" : "cm-strong");
            }
          }
          break;
        }
        case "InlineCode": {
          if (!activa(ref.from)) {
            const n = ref.node;
            const primeiro = n.firstChild, ultimo = n.lastChild;
            if (primeiro && ultimo && primeiro !== ultimo) {
              esconder(primeiro.from, primeiro.to);
              esconder(ultimo.from, ultimo.to);
              estilo(primeiro.to, ultimo.from, "cm-inlinecode");
            }
          }
          break;
        }
        case "Highlight": {
          if (!activa(ref.from)) {
            const n = ref.node;
            const primeiro = n.firstChild, ultimo = n.lastChild;
            if (primeiro && ultimo && primeiro !== ultimo) {
              esconder(primeiro.from, primeiro.to);
              esconder(ultimo.from, ultimo.to);
              estilo(primeiro.to, ultimo.from, "cm-highlight");
            }
          }
          break;
        }
        case "Link": {
          if (!activa(ref.from)) {
            const n = ref.node;
            const marcas = [];
            for (let c = n.firstChild; c; c = c.nextSibling) if (c.type.name === "LinkMark") marcas.push(c);
            if (marcas.length >= 2) {
              esconder(marcas[0].from, marcas[0].to);
              esconder(marcas[1].from, n.to);
              estilo(marcas[0].to, marcas[1].from, "cm-link");
            }
          }
          break;
        }
        case "WikiLink": {
          const n = ref.node;
          const bruto = doc.sliceString(n.from, n.to);
          const m = /^\[\[([^\]|]*)(?:\|([^\]]*))?\]\]$/.exec(bruto);
          if (m && !activa(ref.from)) {
            const pagina = m[1], alias = m[2];
            esconder(n.from, n.from + 2);
            esconder(n.to - 2, n.to);
            if (alias != null) {
              const posPipe = n.from + 2 + pagina.length;
              esconder(n.from + 2, posPipe + 1);
              estiloWikilink(posPipe + 1, n.to - 2, pagina);
            } else {
              estiloWikilink(n.from + 2, n.to - 2, pagina);
            }
          }
          break;
        }
        case "Tag": {
          const n = ref.node;
          estilo(n.from, n.to, "cm-tag");
          break;
        }
        case "TaskMarker": {
          const ch = doc.sliceString(ref.from + 1, ref.from + 2);
          const marcada = ch === "x" || ch === "X";
          itens.push(Decoration.replace({ widget: new TaskCheckboxWidget(marcada, ref.from) }).range(ref.from, ref.to));
          break;
        }
        case "QuoteMark": {
          if (!activa(ref.from)) {
            let fim = ref.to;
            if (doc.sliceString(fim, fim + 1) === " ") fim += 1;
            esconder(ref.from, fim);
          }
          break;
        }
        case "FencedCode": {
          const l1 = doc.lineAt(ref.from).number, l2 = doc.lineAt(ref.to).number;
          classeLinhas(l1, l2, "cm-codeblock");
          break;
        }
        case "Blockquote": {
          const n = ref.node;
          const l1 = doc.lineAt(n.from).number, l2 = doc.lineAt(Math.max(n.from, n.to - 1)).number;
          const primeiraLinha = doc.line(l1);
          const mQuote = /^(\s*>\s?)/.exec(primeiraLinha.text);
          const offAntes = mQuote ? mQuote[1].length : 0;
          const resto = primeiraLinha.text.slice(offAntes);
          const mCallout = /^\[!([^\]\s]+)\]\s*/.exec(resto);
          if (mCallout) {
            const tipo = mCallout[1].toLowerCase();
            classeLinhas(l1, l2, "cm-callout cm-callout-" + identificadorCss(tipo));
            if (!linhasActivas.has(l1)) {
              const de = primeiraLinha.from + offAntes;
              const ate = de + mCallout[0].length;
              itens.push(Decoration.replace({ widget: new CalloutBadgeWidget(tipo) }).range(de, ate));
            }
          } else {
            classeLinhas(l1, l2, "cm-blockquote");
          }
          break;
        }
      }
    }
  });

  return Decoration.set(itens, true);
}

const livePreviewPlugin = ViewPlugin.fromClass(class {
  constructor(view) { this.decorations = construirDecoracoes(view); }
  update(update) {
    if (update.docChanged || update.selectionSet || update.viewportChanged) {
      this.decorations = construirDecoracoes(update.view);
    }
  }
}, { decorations: (v) => v.decorations });

/* ------------------------------------------------------------------------
 * 4. Interacção: alternar tarefas, abrir [[wikilinks]] (Ctrl/Cmd+clique, toque longo)
 * ---------------------------------------------------------------------- */

function alternarTarefa(view, de) {
  const ch = view.state.sliceDoc(de + 1, de + 2);
  const novo = /[xX]/.test(ch) ? " " : "x";
  view.dispatch({ changes: { from: de + 1, to: de + 2, insert: novo } });
}

function ligarInteraccoes(opts) {
  let temporizador = null;
  let alvoPressao = null;

  return EditorView.domEventHandlers({
    mousedown(event, view) {
      const caixa = event.target.closest && event.target.closest(".cm-task-checkbox");
      if (caixa) {
        event.preventDefault();
        alternarTarefa(view, parseInt(caixa.getAttribute("data-de"), 10));
        return true;
      }
      const ligacao = event.target.closest && event.target.closest(".cm-wikilink");
      if (ligacao && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        const nomeNota = ligacao.getAttribute("data-wikilink");
        if (nomeNota && typeof opts.abrirNota === "function") opts.abrirNota(nomeNota);
        return true;
      }
      return false;
    },
    click(event) {
      if (event.target.closest && event.target.closest(".cm-task-checkbox")) {
        event.preventDefault();
        return true;
      }
      return false;
    },
    touchstart(event) {
      const ligacao = event.target.closest && event.target.closest(".cm-wikilink");
      if (!ligacao) return false;
      alvoPressao = ligacao;
      const nomeNota = ligacao.getAttribute("data-wikilink");
      temporizador = setTimeout(() => {
        temporizador = null;
        if (nomeNota && typeof opts.abrirNota === "function") opts.abrirNota(nomeNota);
      }, 550);
      return false;
    },
    touchend() {
      if (temporizador) { clearTimeout(temporizador); temporizador = null; }
      alvoPressao = null;
      return false;
    },
    touchmove() {
      if (temporizador) { clearTimeout(temporizador); temporizador = null; }
      alvoPressao = null;
      return false;
    }
  });
}

/* ------------------------------------------------------------------------
 * 5. Autocompletar — [[notas]] e #etiquetas
 * ---------------------------------------------------------------------- */

function semAcentos(s) {
  return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function fonteWikilink(opts) {
  return (context) => {
    if (typeof opts.listarNotas !== "function") return null;
    const m = context.matchBefore(/\[\[[^\]\n|]*$/);
    if (!m) return null;
    if (m.from === m.to && !context.explicit) return null;
    const consulta = semAcentos(m.text.slice(2));
    let notas = [];
    try { notas = opts.listarNotas() || []; } catch (_e) { notas = []; }
    const opcoes = notas
      .filter((nt) => !consulta || semAcentos(nt.nome).includes(consulta) || semAcentos(nt.titulo || "").includes(consulta))
      .slice(0, 50)
      .map((nt) => ({
        label: nt.titulo || nt.nome,
        detail: nt.titulo && nt.titulo !== nt.nome ? nt.nome : undefined,
        apply(view, _completion, de, ate) {
          const inserir = nt.nome + "]]";
          view.dispatch({
            changes: { from: de, to: ate, insert: inserir },
            selection: { anchor: de + inserir.length }
          });
        }
      }));
    if (!opcoes.length) return null;
    return { from: m.from + 2, options: opcoes, validFor: /^[^\]\n|]*$/ };
  };
}

function fonteEtiqueta(opts) {
  return (context) => {
    if (typeof opts.etiquetas !== "function") return null;
    const m = context.matchBefore(/#[\p{L}\d_-]*/u);
    if (!m) return null;
    const antes = context.state.sliceDoc(Math.max(0, m.from - 1), m.from);
    if (antes && !/\s/.test(antes)) return null;
    if (m.from === m.to && !context.explicit) return null;
    const consulta = semAcentos(m.text.slice(1));
    let lista = [];
    try { lista = opts.etiquetas() || []; } catch (_e) { lista = []; }
    const opcoes = lista
      .filter((tg) => !consulta || semAcentos(tg).includes(consulta))
      .slice(0, 50)
      .map((tg) => ({ label: "#" + tg, apply: "#" + tg }));
    if (!opcoes.length) return null;
    return { from: m.from, options: opcoes };
  };
}

/* ------------------------------------------------------------------------
 * 6. Painel de procura em português
 * ---------------------------------------------------------------------- */

const FRASES_PT = {
  "Find": "Procurar",
  "Replace": "Substituir",
  "next": "seguinte",
  "previous": "anterior",
  "all": "tudo",
  "match case": "maiúsculas",
  "by word": "palavra inteira",
  "regexp": "expressão regular",
  "replace": "substituir",
  "replace all": "substituir tudo",
  "close": "fechar",
  "current match": "resultado actual",
  "replaced $ matches": "substituídos $ resultados",
  "replaced match on line $": "resultado substituído na linha $",
  "on line": "na linha",
  "Go to line": "Ir para a linha",
  "go": "ir"
};

/* ------------------------------------------------------------------------
 * 7. Tema — cores copiadas de basalt/static/index.html (:root / [data-theme="light"])
 * ---------------------------------------------------------------------- */

const FONTE_TEXTO = '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif';
const FONTE_MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace';

const CORES_ESCURO = {
  bg: "#0f1115", bgElev: "#171a21", bgElev2: "#1e222b", bgElev3: "#262b36",
  texto: "#e8eaed", textoFraco: "#9aa2af", borda: "#2a2f3a",
  accent: "#6ea8fe", accentForte: "#4c8dff", accentTexto: "#04121f", accentSuave: "#6ea8fe33",
  perigo: "#ff6b6b", sucesso: "#4ade80", aviso: "#fbbf24"
};
const CORES_CLARO = {
  bg: "#f5f6f8", bgElev: "#ffffff", bgElev2: "#eef0f3", bgElev3: "#e4e7eb",
  texto: "#161a20", textoFraco: "#5b6270", borda: "#dfe3e8",
  accent: "#2563eb", accentForte: "#1d4ed8", accentTexto: "#ffffff", accentSuave: "#2563eb22",
  perigo: "#dc2626", sucesso: "#16a34a", aviso: "#b45309"
};

function extensaoTema(nomeTema) {
  const c = nomeTema === "claro" ? CORES_CLARO : CORES_ESCURO;
  return EditorView.theme({
    "&": {
      "--b-bg": c.bg, "--b-bg-elev": c.bgElev, "--b-bg-elev2": c.bgElev2, "--b-bg-elev3": c.bgElev3,
      "--b-texto": c.texto, "--b-texto-fraco": c.textoFraco, "--b-borda": c.borda,
      "--b-accent": c.accent, "--b-accent-forte": c.accentForte, "--b-accent-texto": c.accentTexto,
      "--b-accent-suave": c.accentSuave, "--b-perigo": c.perigo, "--b-sucesso": c.sucesso, "--b-aviso": c.aviso,
      "--b-fonte": FONTE_TEXTO, "--b-mono": FONTE_MONO,
      backgroundColor: c.bg, color: c.texto, height: "100%"
    },
    ".cm-content": { caretColor: c.accent },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: c.accent },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection": { backgroundColor: c.accentSuave },
    ".cm-activeLine": { backgroundColor: c.bgElev2 },
    ".cm-gutters": { backgroundColor: "transparent", border: "none", color: c.textoFraco },
    ".cm-searchMatch": { backgroundColor: c.aviso + "55" },
    ".cm-searchMatch-selected": { backgroundColor: c.accent + "88" }
  }, { dark: nomeTema !== "claro" });
}

const temaBase = EditorView.baseTheme({
  "&": { fontFamily: "var(--b-fonte)", fontSize: "15px", height: "100%" },
  "@media (pointer: coarse)": {
    ".cm-content": { fontSize: "16px" }
  },
  "@media (max-width: 600px)": {
    ".cm-content": { fontSize: "16px" }
  },
  ".cm-scroller": { fontFamily: "inherit", lineHeight: "1.6", overflow: "auto" },
  ".cm-content": { padding: "14px 16px", maxWidth: "860px", margin: "0 auto" },
  "&.cm-focused": { outline: "none" },

  ".cm-h1": { fontSize: "1.7em", fontWeight: "700", lineHeight: "1.3" },
  ".cm-h2": { fontSize: "1.42em", fontWeight: "700", lineHeight: "1.3" },
  ".cm-h3": { fontSize: "1.22em", fontWeight: "700" },
  ".cm-h4": { fontSize: "1.1em", fontWeight: "700" },
  ".cm-h5": { fontSize: "1.02em", fontWeight: "700" },
  ".cm-h6": { fontSize: "0.95em", fontWeight: "700", opacity: "0.85" },

  ".cm-em": { fontStyle: "italic" },
  ".cm-strong": { fontWeight: "700" },
  ".cm-inlinecode": {
    fontFamily: "var(--b-mono)", backgroundColor: "var(--b-bg-elev3)",
    padding: "0.05em 0.35em", borderRadius: "4px", fontSize: "0.9em"
  },
  ".cm-highlight": { backgroundColor: "var(--b-aviso)", color: "#000", borderRadius: "2px", padding: "0 0.15em" },
  ".cm-link": { color: "var(--b-accent)", textDecoration: "underline", textUnderlineOffset: "2px" },
  ".cm-wikilink": {
    color: "var(--b-accent)", backgroundColor: "var(--b-accent-suave)",
    borderRadius: "4px", padding: "0.05em 0.3em", cursor: "pointer"
  },
  ".cm-tag": {
    color: "var(--b-accent-forte)", backgroundColor: "var(--b-accent-suave)",
    borderRadius: "10px", padding: "0.03em 0.55em", fontSize: "0.9em"
  },
  ".cm-frontmatter": { color: "var(--b-texto-fraco)", fontSize: "0.85em", opacity: "0.75" },
  ".cm-codeblock": { backgroundColor: "var(--b-bg-elev2)", fontFamily: "var(--b-mono)" },
  ".cm-blockquote": { borderLeft: "3px solid var(--b-borda)", color: "var(--b-texto-fraco)", paddingLeft: "0.6em" },
  ".cm-callout": {
    borderLeft: "3px solid var(--b-accent)", backgroundColor: "var(--b-accent-suave)", paddingLeft: "0.6em"
  },
  ".cm-callout-aviso, .cm-callout-warning, .cm-callout-cuidado, .cm-callout-caution": { borderLeftColor: "var(--b-aviso)" },
  ".cm-callout-erro, .cm-callout-error, .cm-callout-perigo, .cm-callout-danger, .cm-callout-falha, .cm-callout-fail, .cm-callout-failure": { borderLeftColor: "var(--b-perigo)" },
  ".cm-callout-sucesso, .cm-callout-success, .cm-callout-dica, .cm-callout-tip, .cm-callout-hint": { borderLeftColor: "var(--b-sucesso)" },
  ".cm-callout-titulo": { fontWeight: "700", display: "inline-block" },

  ".cm-task-checkbox": {
    display: "inline-block", width: "1em", height: "1em", boxSizing: "border-box",
    border: "2px solid var(--b-texto-fraco)", borderRadius: "3px",
    verticalAlign: "middle", marginRight: "0.4em", cursor: "pointer",
    position: "relative", top: "-0.05em", minWidth: "16px", minHeight: "16px"
  },
  ".cm-task-checkbox-marcada": { backgroundColor: "var(--b-accent)", borderColor: "var(--b-accent)" },
  ".cm-task-checkbox-marcada::after": {
    content: '"✓"', position: "absolute", left: "1px", top: "-4px",
    fontSize: "0.9em", lineHeight: "1", color: "var(--b-accent-texto)"
  },

  ".cm-panels": { backgroundColor: "var(--b-bg-elev2)", color: "var(--b-texto)" },
  ".cm-panel.cm-search": { padding: "6px 8px" },
  ".cm-panel input, .cm-panel button": {
    backgroundColor: "var(--b-bg-elev3)", color: "var(--b-texto)",
    border: "1px solid var(--b-borda)", borderRadius: "4px", fontSize: "14px"
  },
  ".cm-tooltip-autocomplete": {
    backgroundColor: "var(--b-bg-elev2)", border: "1px solid var(--b-borda)", color: "var(--b-texto)"
  },
  ".cm-tooltip-autocomplete ul li[aria-selected]": {
    backgroundColor: "var(--b-accent)", color: "var(--b-accent-texto)"
  }
});

/* ------------------------------------------------------------------------
 * 8. API pública — window.BasaltEditor.criar(elemento, opcoes)
 * ---------------------------------------------------------------------- */

function limiteParagrafo(state, pos) {
  const doc = state.doc;
  let linhaInicio = doc.lineAt(pos), linhaFim = linhaInicio;
  while (linhaInicio.number > 1) {
    const anterior = doc.line(linhaInicio.number - 1);
    if (anterior.text.trim() === "") break;
    linhaInicio = anterior;
  }
  while (linhaFim.number < doc.lines) {
    const seguinte = doc.line(linhaFim.number + 1);
    if (seguinte.text.trim() === "") break;
    linhaFim = seguinte;
  }
  return { de: linhaInicio.from, ate: linhaFim.to };
}

export function criar(elemento, opcoes) {
  const opts = opcoes || {};
  let temaAtual = opts.tema === "claro" ? "claro" : "escuro";
  const temaCompartimento = new Compartment();
  let temporizadorMudanca = null;

  const extensoes = [
    temaBase,
    temaCompartimento.of(extensaoTema(temaAtual)),
    history(),
    drawSelection(),
    dropCursor(),
    EditorView.lineWrapping,
    indentUnit.of("  "),
    markdownExt,
    livePreviewPlugin,
    ligarInteraccoes(opts),
    autocompletion({ override: [fonteWikilink(opts), fonteEtiqueta(opts)], activateOnTyping: true }),
    search({ top: true }),
    EditorState.phrases.of(FRASES_PT),
    EditorView.contentAttributes.of({ "aria-label": "Editor de markdown", spellcheck: "true", autocapitalize: "sentences" }),
    Prec.high(keymap.of([
      { key: "Enter", run: continuarListaOuCitacao }
    ])),
    keymap.of([
      { key: "Mod-s", preventDefault: true, run: () => { if (typeof opts.aoGravar === "function") opts.aoGravar(); return true; } },
      indentWithTab,
      ...searchKeymap,
      ...historyKeymap,
      ...completionKeymap,
      ...defaultKeymap
    ]),
    EditorView.updateListener.of((update) => {
      if (update.docChanged && typeof opts.aoMudar === "function") {
        if (temporizadorMudanca) clearTimeout(temporizadorMudanca);
        const texto = update.state.doc.toString();
        temporizadorMudanca = setTimeout(() => opts.aoMudar(texto), 400);
      }
    })
  ];

  const state = EditorState.create({
    doc: opts.texto != null ? String(opts.texto) : "",
    extensions: extensoes
  });
  const view = new EditorView({ state, parent: elemento });

  return {
    obterTexto() {
      return view.state.doc.toString();
    },
    definirTexto(texto) {
      const t = texto == null ? "" : String(texto);
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: t } });
    },
    focar() {
      view.focus();
    },
    destruir() {
      if (temporizadorMudanca) clearTimeout(temporizadorMudanca);
      view.destroy();
    },
    selecao() {
      const sel = view.state.selection.main;
      if (sel.from !== sel.to) {
        return { texto: view.state.sliceDoc(sel.from, sel.to), de: sel.from, ate: sel.to };
      }
      const { de, ate } = limiteParagrafo(view.state, sel.from);
      return { texto: view.state.sliceDoc(de, ate), de, ate };
    },
    substituir(de, ate, texto) {
      view.dispatch({ changes: { from: de, to: ate, insert: texto == null ? "" : String(texto) } });
    },
    inserirNoCursor(texto) {
      const pos = view.state.selection.main.from;
      const t = texto == null ? "" : String(texto);
      view.dispatch({ changes: { from: pos, insert: t }, selection: { anchor: pos + t.length } });
    },
    definirTema(nomeTema) {
      temaAtual = nomeTema === "claro" ? "claro" : "escuro";
      view.dispatch({ effects: temaCompartimento.reconfigure(extensaoTema(temaAtual)) });
    }
  };
}
