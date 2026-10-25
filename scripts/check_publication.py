"""Check the exact Git index before commit/push. No third-party dependencies."""
import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SINGLE_FILE = '24OCT2026.py'
ARCHIVED_SINGLE_FILES = {'Hydrogeomatics version 22 OCT 2026.py': 'ff8a4fcd71ae5c7af79133b57e02bd4722e175859f61dcf0cb668c1ff3923221'}
SOURCE_MODULES = {
    'QT_WIDGET_SOURCE': 'modules/qt_widget_source.py',
    'SURVEY_SOURCE': 'modules/survey_source.py',
    'MAP_SOURCE': 'modules/map_source.py',
    'ENGINE_SOURCE': 'modules/engine_source.py',
    'INDUS_KTD_SOURCE': 'modules/indus_ktd_source.py',
    'CHART_PLOT_SOURCE': 'modules/chart_plot_source.py',
    'ENC_EDITOR_SOURCE': 'modules/enc_editor_source.py',
    'DREDGE_VOLUME_SOURCE': 'modules/dredge_volume_source.py',
    'HYDRO_TIDE_PAYLOAD': 'modules/hydro_tide_payload.py',
}
ALLOWED = {
    '.gitignore', '.gitattributes', 'README.md', 'SECURITY.md', 'requirements.txt',
    'hydrographic_package.py', 'scripts/check_publication.py',
    'start_windows.cmd', 'tests/test_input_filter.py', 'tests/test_source_bundle.py', 'tests/test_single_file.py', SINGLE_FILE,
    'LICENSE', 'CONTRIBUTING.md', 'THIRD_PARTY_NOTICES.md',
    '.github/PULL_REQUEST_TEMPLATE.md',
    '.github/ISSUE_TEMPLATE/bug_report.yml',
    '.github/ISSUE_TEMPLATE/feature_request.yml',
    '.githooks/pre-commit', '.githooks/pre-push',
    '.github/workflows/security.yml',
} | set(ARCHIVED_SINGLE_FILES) | {f'modules/{name}.py' for name in (
    'qt_widget_source', 'survey_source', 'map_source', 'engine_source',
    'indus_ktd_source', 'chart_plot_source', 'enc_editor_source',
    'dredge_volume_source', 'hydro_tide_payload',
)}
PATTERNS = [
    r'gh[pousr]_[A-Za-z0-9]{30,}',
    r'github_pat_[A-Za-z0-9_]{40,}',
    r'AKIA[A-Z0-9]{16}',
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
    r'(?i)(?:api[_-]?key|password|client_secret|access_token)\s*[:=]\s*[\"\x27][^\"\x27\s]{8,}[\"\x27]',
    r'(?i)[A-Z0-9._%+-]{1,64}@(?:gmail|yahoo|hotmail|outlook)\.[A-Z]{2,}',
    r'(?i)[A-Z]:\\Users\\[^\\\s]+',
    r'(?i)https?://[^\s/]+:[^\s/]+@',
    r'(?i)wa\.me/\d+',
]

def single_file_source(files):
    """Build a readable standalone edition from the reviewed public sources."""
    source = files['hydrographic_package.py']
    lines = source.splitlines(keepends=True)
    edits = []
    found = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in SOURCE_MODULES:
                name = target.id
                # One literal per code line keeps the embedded source inspectable.
                chunks = files[SOURCE_MODULES[name]].splitlines(keepends=True)
                replacement = name + ' = (\n' + ''.join('    ' + repr(chunk) + '\n' for chunk in chunks) + ')\n'
                edits.append((node.lineno - 1, node.end_lineno, replacement))
                found.add(name)
        if isinstance(node, ast.FunctionDef) and node.name == '_check_source_bundle':
            edits.append((node.lineno - 1, node.end_lineno, ''))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == '_check_source_bundle':
                edits.append((node.lineno - 1, node.end_lineno, ''))
    if found != set(SOURCE_MODULES):
        raise ValueError('Launcher source module assignments changed; review the builder.')
    doc = ast.parse(source).body[0]
    documentation = '''HYDROGEOMATICS - version 24 OCT 2026, public single-file edition.

Contains the reviewed public application code; no separate modules folder is needed.
Source metadata refreshed 25 October 2026. The complete Desktop edition uses existing uploaded-constituent descriptions; this public edition retains its reference-data exclusions.
Python 3.10+ and the packages in requirements.txt are still required.
Install: python -m pip install PyQt6 pyqtgraph numpy pyserial pyproj ezdxf matplotlib utide sonarlight pandas
Run: python "24OCT2026.py"
Project code: MIT License. Dependencies retain their own licenses.
Embedded private/reference datasets are excluded. Indus reference-data and tide/astronomy features remain disabled in this public edition.
Contribute: https://github.com/hydrogeomatics-ui/hydrogeomatics-hydrographic-package
'''
    edits.append((doc.lineno - 1, doc.end_lineno, repr(documentation) + '\n'))
    for start, end, value in sorted(edits, reverse=True):
        lines[start:end] = [value]
    return ''.join(lines)

def check_text(name, text):
    issues = []
    if any(re.search(pattern, text) for pattern in PATTERNS):
        issues.append(f'{name}: potential credential or personal information')
    if name.endswith('.py'):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return issues + [f'{name}: invalid Python syntax']
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
                value = node.value
                if len(value) > 100_000:
                    issues.append(f'{name}:{node.lineno}: large embedded payload requires review')
                if isinstance(value, str) and value != text:
                    if any(re.search(pattern, value) for pattern in PATTERNS):
                        issues.append(f'{name}:{node.lineno}: potential sensitive literal')
    return issues

def main():
    result = subprocess.run(['git', 'ls-files', '-s', '-z'], cwd=ROOT, check=True, capture_output=True)
    issues = []
    count = 0
    indexed_text = {}
    for entry in result.stdout.split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        mode, blob, stage = metadata.decode().split()
        name = raw_name.decode('utf-8')
        count += 1
        if name not in ALLOWED or mode not in ('100644', '100755') or stage != '0':
            issues.append(f'{name}: file or index mode is not approved')
            continue
        data = subprocess.run(['git', 'cat-file', 'blob', blob], cwd=ROOT, check=True, capture_output=True).stdout
        try:
            text = data.decode('utf-8-sig')
            indexed_text[name] = text
            if name in ARCHIVED_SINGLE_FILES:
                if hashlib.sha256(data).hexdigest() != ARCHIVED_SINGLE_FILES[name]:issues.append(f'{name}: archived release was modified')
            elif name != SINGLE_FILE:
                issues.extend(check_text(name, text))
        except UnicodeDecodeError:
            issues.append(f'{name}: binary content is not approved')
    if SINGLE_FILE in indexed_text:
        try:
            expected = single_file_source(indexed_text)
            if indexed_text[SINGLE_FILE] != expected:
                issues.append(f'{SINGLE_FILE}: differs from the reviewed public sources; rebuild it')
        except (KeyError, ValueError, SyntaxError):
            issues.append(f'{SINGLE_FILE}: cannot verify required public source files')
    if not count:
        issues.append('No tracked files to review')
    for issue in issues:
        print(issue)
    print(f'Publication check: {count} indexed files; {len(issues)} findings')
    return bool(issues)

if __name__ == '__main__':
    if sys.argv[1:] == ['--build-single-file']:
        paths = ['hydrographic_package.py', *SOURCE_MODULES.values()]
        files = {name: (ROOT / name).read_text(encoding='utf-8') for name in paths}
        (ROOT / SINGLE_FILE).write_text(single_file_source(files), encoding='utf-8', newline='\n')
        print('Built:', SINGLE_FILE)
    else:
        sys.exit(main())
