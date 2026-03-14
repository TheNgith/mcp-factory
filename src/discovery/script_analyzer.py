"""
script_analyzer.py - Invocable extractor for JIT / scripting languages.

Supported file types
--------------------
  .py    Python 3 (AST-based extraction)
  .ps1   Windows PowerShell (regex-based extraction)
  .bat   Windows Batch script
  .cmd   Windows Batch script (same engine as .bat)
  .vbs   VBScript
  .sh    POSIX shell
  .bash  Bash
  .zsh   Z-shell

Each analyzer returns a List[Invocable] using the unified schema so
main.py can write the same MCP JSON regardless of source type.
"""

import ast
import inspect
import logging
import re
import textwrap
from pathlib import Path
from typing import List, Optional

from schema import Invocable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def analyze_script(script_path: Path) -> List[Invocable]:
    """Dispatch to the correct sub-analyzer based on file extension.

    Args:
        script_path: Path to the script file.

    Returns:
        List[Invocable] – empty if the file cannot be read or parsed.
    """
    ext = script_path.suffix.lower()
    dispatch = {
        '.py':   _analyze_python,
        '.ps1':  _analyze_powershell,
        '.bat':  _analyze_batch,
        '.cmd':  _analyze_batch,
        '.vbs':  _analyze_vbscript,
        '.sh':   _analyze_shell,
        '.bash': _analyze_shell,
        '.zsh':  _analyze_shell,
        '.rb':   _analyze_ruby,
        '.php':  _analyze_php,
    }
    handler = dispatch.get(ext)
    if handler is None:
        logger.warning("No script analyzer for extension '%s'", ext)
        return []
    try:
        return handler(script_path)
    except Exception as exc:
        logger.error("Script analysis failed for %s: %s", script_path.name, exc)
        return []


# ---------------------------------------------------------------------------
# Python analyzer (ast-based)
# ---------------------------------------------------------------------------

def _analyze_python(path: Path) -> List[Invocable]:
    """Extract top-level functions and public methods from a .py file."""
    source = path.read_text(encoding='utf-8', errors='replace')
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        logger.warning("Python syntax error in %s: %s", path.name, exc)
        return []

    invocables: List[Invocable] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Skip private helpers
        if node.name.startswith('_'):
            continue

        doc = ast.get_docstring(node) or ''
        params = _python_params(node)
        ret = _python_return_type(node)

        sig = f"def {node.name}({params})"
        if ret:
            sig += f" -> {ret}"

        confidence = 'guaranteed' if doc and ret else ('high' if doc or ret else 'medium')

        invocables.append(Invocable(
            name=node.name,
            source_type='python_function',
            signature=sig,
            parameters=params,
            return_type=ret,
            doc_comment=_one_liner(doc),
            confidence=confidence,
            dll_path=str(path),
        ))

    logger.info("Python: %d public functions found in %s", len(invocables), path.name)
    return invocables


def _python_params(node: ast.FunctionDef) -> str:
    """Render the parameter list of an AST function node as a string."""
    parts = []
    args = node.args

    # Calculate offsets for defaults (Python stores defaults right-aligned)
    n_no_default = len(args.args) - len(args.defaults)

    for i, arg in enumerate(args.args):
        if arg.arg == 'self' or arg.arg == 'cls':
            continue
        part = arg.arg
        # Type annotation
        if arg.annotation:
            part += f': {ast.unparse(arg.annotation)}'
        # Default value
        default_idx = i - n_no_default
        if default_idx >= 0:
            part += f' = {ast.unparse(args.defaults[default_idx])}'
        parts.append(part)

    # *args
    if args.vararg:
        part = f'*{args.vararg.arg}'
        if args.vararg.annotation:
            part += f': {ast.unparse(args.vararg.annotation)}'
        parts.append(part)

    # **kwargs
    if args.kwarg:
        part = f'**{args.kwarg.arg}'
        if args.kwarg.annotation:
            part += f': {ast.unparse(args.kwarg.annotation)}'
        parts.append(part)

    return ', '.join(parts)


def _python_return_type(node: ast.FunctionDef) -> Optional[str]:
    if node.returns:
        try:
            return ast.unparse(node.returns)
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# PowerShell analyzer
# ---------------------------------------------------------------------------

# Matches:  function Verb-Noun       or   function Verb-Noun {
_PS_FUNC_RE = re.compile(
    r'^\s*function\s+([\w-]+)\s*(?:\(([^)]*)\))?\s*\{?',
    re.IGNORECASE | re.MULTILINE,
)

# Matches param block: param(\n  [type]$name = default, ...\n)
_PS_PARAM_RE = re.compile(
    r'\[(?P<type>[^\]]+)\]\s*\$(?P<name>\w+)(?:\s*=\s*(?P<default>[^\n,)]+))?',
    re.MULTILINE,
)


def _ps_extract_param_block(text: str) -> Optional[str]:
    """Return the inner content of a PowerShell param(...) block.

    A plain ``[^)]*`` regex stops at the first ``)`` it finds — which breaks
    on ``[Parameter(Mandatory=$true)]`` decorators.  This depth-counter
    approach handles any level of nesting.
    """
    m = re.search(r'\bparam\s*\(', text, re.IGNORECASE)
    if not m:
        return None
    start = m.end()   # character immediately after the opening '('
    depth = 1
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return text[start:i]
    return None

# CmdletBinding synopsis from comment-based help: # .SYNOPSIS text
_PS_SYNOPSIS_RE = re.compile(r'\.SYNOPSIS\s*\n\s*(.+)', re.IGNORECASE)


def _analyze_powershell(path: Path) -> List[Invocable]:
    """Extract functions and parameters from a .ps1 file."""
    source = path.read_text(encoding='utf-8', errors='replace')
    invocables: List[Invocable] = []

    for m in _PS_FUNC_RE.finditer(source):
        func_name = m.group(1)
        # Skip private (starts with _) or internal helpers
        if func_name.startswith('_'):
            continue

        # Extract param block that follows the opening brace
        func_start = m.end()
        func_body = source[func_start:func_start + 2000]  # look ahead 2 kB

        params_str = ''
        raw_params = _ps_extract_param_block(func_body)
        if raw_params:
            params = []
            for p in _PS_PARAM_RE.finditer(raw_params):
                param_type = p.group('type').strip().split('.')[-1]  # strip namespace
                param_name = p.group('name')
                default = p.group('default')
                # Emit in "name: type" form so _parse_single_parameter extracts
                # the correct name (rather than the PS [Type]$Name notation which
                # looks like a single token and gets renamed to arg0).
                entry = f'{param_name}: {param_type}'
                if default:
                    entry += f' = {default.strip()}'
                params.append(entry)
            params_str = ', '.join(params)

        # Look for .SYNOPSIS comment-based help immediately before the function
        preamble = source[max(0, m.start() - 300):m.start()]
        synopsis_m = _PS_SYNOPSIS_RE.search(preamble)
        doc = synopsis_m.group(1).strip() if synopsis_m else ''

        sig = f"{func_name}({params_str})"
        confidence = 'high' if doc else ('medium' if params_str else 'low')

        invocables.append(Invocable(
            name=func_name,
            source_type='powershell_function',
            signature=sig,
            parameters=params_str,
            doc_comment=doc or None,
            confidence=confidence,
            dll_path=str(path),
        ))

    logger.info("PowerShell: %d functions found in %s", len(invocables), path.name)
    return invocables


# ---------------------------------------------------------------------------
# Batch / CMD analyzer
# ---------------------------------------------------------------------------

# :LabelName  — callable targets in batch files
_BATCH_LABEL_RE = re.compile(r'^:(?!:)(\w+)', re.MULTILINE)
# REM <doc> immediately before the label
_BATCH_REM_RE = re.compile(r'(?:^REM\s+(.+)\n)+', re.IGNORECASE | re.MULTILINE)


def _analyze_batch(path: Path) -> List[Invocable]:
    """Extract CALL-able labels from a .bat/.cmd file."""
    source = path.read_text(encoding='utf-8', errors='replace')
    invocables: List[Invocable] = []

    labels_seen: set = set()
    for m in _BATCH_LABEL_RE.finditer(source):
        label = m.group(1)
        # Skip reserved labels like :EOF
        if label.upper() in ('EOF', 'END', 'EXIT'):
            continue
        if label in labels_seen:
            continue
        labels_seen.add(label)

        # Look for leading REM comment
        preamble = source[max(0, m.start() - 300):m.start()]
        rem_lines = re.findall(r'^REM\s+(.+)', preamble, re.IGNORECASE | re.MULTILINE)
        doc = ' '.join(rem_lines[-3:]).strip() if rem_lines else ''

        invocables.append(Invocable(
            name=label,
            source_type='batch_label',
            signature=f'CALL :{label}',
            doc_comment=doc or None,
            confidence='medium' if doc else 'low',
            dll_path=str(path),
        ))

    # If no labels, register the script itself as a single invocable
    if not invocables:
        invocables.append(Invocable(
            name=path.stem,
            source_type='batch_script',
            signature=f'{path.name} [arguments]',
            doc_comment='Batch script (no labeled subroutines detected)',
            confidence='low',
            dll_path=str(path),
        ))

    logger.info("Batch: %d labels found in %s", len(invocables), path.name)
    return invocables


# ---------------------------------------------------------------------------
# VBScript analyzer
# ---------------------------------------------------------------------------

_VBS_SUB_RE = re.compile(
    r'^\s*(?:Public\s+|Private\s+)?(?P<kind>Sub|Function)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)',
    re.IGNORECASE | re.MULTILINE,
)
_VBS_COMMENT_RE = re.compile(r"^'\s*(.+)", re.MULTILINE)


def _analyze_vbscript(path: Path) -> List[Invocable]:
    """Extract Sub and Function declarations from a .vbs file."""
    source = path.read_text(encoding='utf-8', errors='replace')
    invocables: List[Invocable] = []

    for m in _VBS_SUB_RE.finditer(source):
        kind = m.group('kind').capitalize()   # Sub | Function
        name = m.group('name')
        raw_params = m.group('params').strip()
        params = ', '.join(p.strip() for p in raw_params.split(',')) if raw_params else ''

        # Leading comment line
        preamble = source[max(0, m.start() - 300):m.start()]
        comment_lines = re.findall(r"^'[ \t]*(.+)", preamble, re.MULTILINE)
        doc = _extract_description(comment_lines) if comment_lines else ''

        sig = f"{kind} {name}({params})"
        confidence = 'high' if kind == 'Function' and doc else ('medium' if doc else 'low')

        invocables.append(Invocable(
            name=name,
            source_type='vbscript_' + kind.lower(),
            signature=sig,
            parameters=params,
            return_type='Variant' if kind == 'Function' else None,
            doc_comment=doc or None,
            confidence=confidence,
            dll_path=str(path),
        ))

    logger.info("VBScript: %d subs/functions found in %s", len(invocables), path.name)
    return invocables


# ---------------------------------------------------------------------------
# Shell script analyzer
# ---------------------------------------------------------------------------

# Matches:  my_func() {   OR   function my_func {   OR   function my_func() {
_SH_FUNC_RE = re.compile(
    r'^\s*(?:function\s+)?(\w+)\s*\(\s*\)\s*\{?'
    r'|^\s*function\s+(\w+)\s*\{?',
    re.MULTILINE,
)
_SH_COMMENT_RE = re.compile(r'^#\s*(.+)', re.MULTILINE)


def _analyze_shell(path: Path) -> List[Invocable]:
    """Extract functions from a shell script (.sh/.bash/.zsh)."""
    source = path.read_text(encoding='utf-8', errors='replace')
    invocables: List[Invocable] = []

    seen: set = set()
    for m in _SH_FUNC_RE.finditer(source):
        name = m.group(1) or m.group(2)
        if not name or name in seen:
            continue
        # Skip private helpers
        if name.startswith('_'):
            continue
        seen.add(name)

        # Leading comment
        preamble = source[max(0, m.start() - 500):m.start()]
        comment_lines = re.findall(r'^#[ \t]*(.+)', preamble, re.MULTILINE)
        doc = _extract_description(comment_lines) if comment_lines else ''

        invocables.append(Invocable(
            name=name,
            source_type='shell_function',
            signature=f'{name}()',
            doc_comment=doc or None,
            confidence='medium' if doc else 'low',
            dll_path=str(path),
        ))

    if not invocables:
        # File-level invocable when no named functions exist
        # Try to read file-header comments (shebang + leading comments)
        header_comments = re.findall(r'^#[^!]\s*(.+)', source[:500], re.MULTILINE)
        doc = ' '.join(header_comments[:2]).strip()
        invocables.append(Invocable(
            name=path.stem,
            source_type='shell_script',
            signature=f'{path.name} [arguments]',
            doc_comment=doc or 'Shell script',
            confidence='low',
            dll_path=str(path),
        ))

    logger.info("Shell: %d functions found in %s", len(invocables), path.name)
    return invocables


# ---------------------------------------------------------------------------
# Ruby analyzer
# ---------------------------------------------------------------------------

#  def method_name(params)  or  def self.class_method(params)
_RB_METHOD_RE = re.compile(
    r'^\s*def\s+(?:self\.)?(?P<name>[a-zA-Z_]\w*[!?]?)\s*(?:\((?P<params>[^)]*)\))?',
    re.MULTILINE,
)
_RB_COMMENT_RE = re.compile(r'^#\s*(.+)', re.MULTILINE)
# YARD-style: "@param name [Type] description"
_RB_YARD_PARAM_RE = re.compile(r'@param\s+(\w+)\s*(?:\[([^\]]+)\])?')


def _analyze_ruby(path: Path) -> List[Invocable]:
    """Extract method definitions from a .rb file."""
    source = path.read_text(encoding='utf-8', errors='replace')
    invocables: List[Invocable] = []
    seen: set = set()

    for m in _RB_METHOD_RE.finditer(source):
        name = m.group('name')
        # Skip private-convention helpers
        if name.startswith('_'):
            continue
        if name in seen:
            continue
        seen.add(name)

        raw_params = (m.group('params') or '').strip()
        sig_params = [p.strip() for p in raw_params.split(',') if p.strip()]

        preamble = source[max(0, m.start() - 300):m.start()]
        comment_lines = re.findall(r'^#\s*(.+)', preamble, re.MULTILINE)
        doc = ' '.join(comment_lines[-2:]).strip() if comment_lines else ''

        # Parse YARD @param tags to get named+typed params
        yard_types: dict = {}
        for cl in comment_lines:
            ym = _RB_YARD_PARAM_RE.search(cl)
            if ym:
                yard_types[ym.group(1)] = (ym.group(2) or 'object').strip()

        if yard_types:
            # Annotate each signature param with its YARD type
            params = ', '.join(
                f"{p}: {yard_types.get(p, 'object')}"
                for p in sig_params
            )
        else:
            # No YARD docs — prefix with "object" so _parse_single_parameter's
            # C-style rule (last token = name) extracts the correct param name
            params = ', '.join(f"object {p}" for p in sig_params)

        sig = f"{name}({', '.join(sig_params)})"
        confidence = 'high' if doc and params else ('medium' if doc or params else 'low')

        invocables.append(Invocable(
            name=name,
            source_type='ruby_method',
            signature=sig,
            parameters=params or None,
            doc_comment=doc or None,
            confidence=confidence,
            dll_path=str(path),
        ))

    logger.info('Ruby: %d methods found in %s', len(invocables), path.name)
    return invocables


# ---------------------------------------------------------------------------
# PHP analyzer
# ---------------------------------------------------------------------------

# function foo($a, $b)  or  public function foo(int $a): string
_PHP_FUNC_RE = re.compile(
    r'(?:public|protected|private|static|\s)*'  # optional visibility / static
    r'function\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)'
    r'(?:\s*:\s*(?P<rettype>[\\\w|?]+))?',
    re.IGNORECASE | re.MULTILINE,
)
_PHP_COMMENT_RE = re.compile(r'//\s*(.+)|#\s*(.+)', re.MULTILINE)
_PHP_DOCBLOCK_RE = re.compile(r'/\*\*(?P<body>.*?)\*/', re.DOTALL)


def _analyze_php(path: Path) -> List[Invocable]:
    """Extract function and method definitions from a .php file."""
    source = path.read_text(encoding='utf-8', errors='replace')
    invocables: List[Invocable] = []
    seen: set = set()

    for m in _PHP_FUNC_RE.finditer(source):
        name = m.group('name')
        if name.startswith('_'):
            continue
        if name in seen:
            continue
        seen.add(name)

        raw_params = (m.group('params') or '').strip()
        # Clean PHP $var notation: strip default values then remove $ sigils
        params = ', '.join(
            re.sub(r'\$', '', re.sub(r'\s*=\s*\S+', '', p)).strip()
            for p in raw_params.split(',') if p.strip()
        )
        rettype = (m.group('rettype') or '').strip()

        preamble = source[max(0, m.start() - 400):m.start()]
        # Prefer PHPDoc block
        docblocks = _PHP_DOCBLOCK_RE.findall(preamble)
        if docblocks:
            raw_doc = docblocks[-1]
            doc_lines = [
                re.sub(r'^\s*\*\s?', '', l).strip()
                for l in raw_doc.splitlines()
                if re.sub(r'^\s*\*\s?', '', l).strip() and not l.strip().startswith('@')
            ]
            doc = ' '.join(doc_lines[:2])
        else:
            comment_lines = re.findall(r'(?://|#)\s*(.+)', preamble, re.MULTILINE)
            doc = ' '.join(comment_lines[-2:]).strip() if comment_lines else ''

        has_doc = bool(doc)
        has_ret = bool(rettype)
        confidence = 'guaranteed' if has_doc and has_ret else \
                     'high' if has_doc or has_ret else \
                     'medium' if params else 'low'

        sig = f"{name}({params})"
        if rettype:
            sig += f": {rettype}"

        invocables.append(Invocable(
            name=name,
            source_type='php_function',
            signature=sig,
            parameters=params or None,
            return_type=rettype or None,
            doc_comment=doc or None,
            confidence=confidence,
            dll_path=str(path),
        ))

    logger.info('PHP: %d functions found in %s', len(invocables), path.name)
    return invocables


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _one_liner(text: str) -> Optional[str]:
    """Return first non-empty line of a docstring, stripped."""
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return None


# Lines that look like parameter-table entries or decorative separators rather
# than actual description sentences.
_DOC_NOISE_RE = re.compile(
    r'^\s*(?:'
    r'\$\d+\b'                                          # $1  param  desc (shell)
    r'|[-\u2500-\u2502\u2550\u254c=]{3,}'              # ----  ────  ════
    r"|\w[\w\s]*\s{2,}(?:String|Integer|Boolean|Array|number|path|str|int|bool)\b"  # VBS table
    r"|Returns?:"                                       # Returns: or Returns (with or without trailing ws)
    r"|(?:Arguments?|Parameters?|Outputs?|Usage|Notes?|Examples?):\s*$"  # section headers
    r"|'[ \t]"                                          # leading quote artefact from cross-line match
    r')',
    re.IGNORECASE,
)


def _extract_description(lines: List[str]) -> str:
    """Return the first comment line that looks like a real description.

    For separator-delimited comment blocks (shell, VBScript), locates the block
    between the last two separators and returns its first non-noisy line — this
    prevents file-level header comments or parameter tables from leaking in.
    Falls back to the first non-noisy line across all lines if no separators.
    """
    _SEP_RE = re.compile(r'^[-\u2500-\u2502\u2550\u254c=]{3,}$')

    def _is_sep(s: str) -> bool:
        return bool(_SEP_RE.match(s.strip()))

    # Locate the last (trailing) separator.
    last_sep = -1
    for i in range(len(lines) - 1, -1, -1):
        if _is_sep(lines[i].strip()):
            last_sep = i
            break

    # Lines before the trailing separator.
    before_trailing = lines[:last_sep] if last_sep >= 0 else lines

    # Locate the previous (opening) separator within those lines.
    block_start = 0
    for i in range(len(before_trailing) - 1, -1, -1):
        if _is_sep(before_trailing[i].strip()):
            block_start = i + 1
            break

    # First non-noisy line in the identified block.
    for line in before_trailing[block_start:]:
        s = line.strip()
        if not s or len(s) < 4:
            continue
        if _DOC_NOISE_RE.match(s):
            continue
        return s

    # Fallback: first non-noisy line anywhere.
    for line in lines:
        s = line.strip()
        if not s or len(s) < 4:
            continue
        if _DOC_NOISE_RE.match(s):
            continue
        return s

    return ''
