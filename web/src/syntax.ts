/** Lightweight, zero-dependency Python syntax highlighter & markdown formatter for lesson guides. */

const PY_KEYWORDS = new Set([
  'async',
  'await',
  'def',
  'class',
  'for',
  'in',
  'while',
  'if',
  'elif',
  'else',
  'return',
  'import',
  'from',
  'as',
  'with',
  'try',
  'except',
  'finally',
  'assert',
  'break',
  'continue',
  'pass',
  'True',
  'False',
  'None',
  'and',
  'or',
  'not',
  'is',
  'raise',
  'yield',
  'lambda',
]);

const PY_BUILTINS = new Set([
  'range',
  'len',
  'sum',
  'min',
  'max',
  'int',
  'str',
  'float',
  'list',
  'dict',
  'set',
  'print',
  'TimeoutError',
]);

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function highlightPython(source: string): string {
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  const tokenRegex =
    /(#.*$)|("""[\s\S]*?"""|'''[\s\S]*?'''|f?"(?:\\.|[^"\\])*"|f?'(?:\\.|[^'\\])*')|\b(0x[0-9a-fA-F_]+|\d+(?:\.\d+)?)\b|\b([A-Za-z_][A-Za-z0-9_]*)\b/gm;

  // Handle multi-line module docstrings cleanly first by tokenizing the whole text, then adding line numbers
  const highlightedFull = source.replace(tokenRegex, (match, comment, strLit, numLit, ident) => {
    if (comment) {
      return `<span class="tok-comment">${escapeHtml(comment)}</span>`;
    }
    if (strLit) {
      return `<span class="tok-string">${escapeHtml(strLit)}</span>`;
    }
    if (numLit) {
      return `<span class="tok-number">${escapeHtml(numLit)}</span>`;
    }
    if (ident) {
      if (PY_KEYWORDS.has(ident)) {
        return `<span class="tok-kw">${ident}</span>`;
      }
      if (PY_BUILTINS.has(ident)) {
        return `<span class="tok-builtin">${ident}</span>`;
      }
      return ident;
    }
    return escapeHtml(match);
  });

  // Escape any raw `<` or `>` outside tags safely by line-by-line fallback if needed:
  // Better: escape first on non-matched segments!
  void lines;
  void highlightedFull;
  return highlightSafeTokenStream(source);
}

function highlightSafeTokenStream(source: string): string {
  const tokenRegex =
    /("""[\s\S]*?"""|'''[\s\S]*?''')|(#[^\n]*)|(f?"(?:\\.|[^"\\\n])*"|f?'(?:\\.|[^'\\\n])*')|\b(0x[0-9a-fA-F_]+|\d+(?:\.\d+)?)\b|\b([A-Za-z_][A-Za-z0-9_]*)\b/g;

  let out = '';
  let lastIndex = 0;
  let m: RegExpExecArray | null;

  while ((m = tokenRegex.exec(source)) !== null) {
    if (m.index > lastIndex) {
      out += escapeHtml(source.slice(lastIndex, m.index));
    }
    const [full, docStr, comment, strLit, numLit, ident] = m;
    if (docStr) {
      out += `<span class="tok-doc">${escapeHtml(docStr)}</span>`;
    } else if (comment) {
      out += `<span class="tok-comment">${escapeHtml(comment)}</span>`;
    } else if (strLit) {
      out += `<span class="tok-string">${escapeHtml(strLit)}</span>`;
    } else if (numLit) {
      out += `<span class="tok-number">${escapeHtml(numLit)}</span>`;
    } else if (ident) {
      if (PY_KEYWORDS.has(ident)) {
        out += `<span class="tok-kw">${ident}</span>`;
      } else if (PY_BUILTINS.has(ident)) {
        out += `<span class="tok-builtin">${ident}</span>`;
      } else {
        out += escapeHtml(ident);
      }
    } else {
      out += escapeHtml(full);
    }
    lastIndex = tokenRegex.lastIndex;
  }
  if (lastIndex < source.length) {
    out += escapeHtml(source.slice(lastIndex));
  }

  return out
    .split('\n')
    .map(
      (line, idx) =>
        `<div class="code-line"><span class="line-no">${idx + 1}</span><span class="line-body">${line || ' '}</span></div>`
    )
    .join('');
}

export function renderMiniMarkdown(md: string): string {
  return md
    .split(/\n\n+/)
    .map((block) => {
      const trimmed = block.trim();
      if (trimmed.startsWith('- ')) {
        const items = trimmed
          .split('\n')
          .map((l) => l.replace(/^- /, '').trim())
          .map((item) => `<li>${inlineFormat(item)}</li>`)
          .join('');
        return `<ul class="concept-list">${items}</ul>`;
      }
      return `<p>${inlineFormat(trimmed)}</p>`;
    })
    .join('');
}

function inlineFormat(text: string): string {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}
