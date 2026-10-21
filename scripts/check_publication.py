"""Check the exact Git index before commit/push. No third-party dependencies."""
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    '.gitignore', '.gitattributes', 'README.md', 'SECURITY.md', 'requirements.txt',
    'hydrographic_package.py', 'scripts/check_publication.py',
    'LICENSE', 'CONTRIBUTING.md', 'THIRD_PARTY_NOTICES.md',
    '.github/PULL_REQUEST_TEMPLATE.md',
    '.github/ISSUE_TEMPLATE/bug_report.yml',
    '.github/ISSUE_TEMPLATE/feature_request.yml',
    '.githooks/pre-commit', '.githooks/pre-push',
    '.github/workflows/security.yml',
} | {f'modules/{name}.py' for name in (
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
            issues.extend(check_text(name, data.decode('utf-8-sig')))
        except UnicodeDecodeError:
            issues.append(f'{name}: binary content is not approved')
    if not count:
        issues.append('No tracked files to review')
    for issue in issues:
        print(issue)
    print(f'Publication check: {count} indexed files; {len(issues)} findings')
    return bool(issues)

if __name__ == '__main__':
    sys.exit(main())
