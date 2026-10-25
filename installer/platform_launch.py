"""Reversible integration with the platform's existing launcher entrypoint."""
import ast
import base64
import copy
import json
from pathlib import Path
import zipfile
from common import child, digest, read
from native_label_support import BASE_HASHES, digest as byte_digest, render_launcher

LAUNCHER = 'App/FGO_Launcher.ps1'
ROUTES = 'Server/python/Lib/site-packages/fgo_ko_text_routes.py'
BOOTSTRAP = 'Server/python/Lib/site-packages/fgo_ko_text_bootstrap.py'
SERVER_INDEX = 'Server/artemis/titles/fgo/index.py'
SERVER_BASE_SHA = '832de39ef5ac80fc133d1047fea5dd80484ecc9f3007ddd058b4701c25fff99d'
# Whole-module syntax fingerprints: no user server source is redistributed.
# Known versions are fast paths; other variants use the display-method contract.
SERVER_PROFILES = {
    SERVER_BASE_SHA: ('baseline', '438e99f7177f9833dc3d2c9e707c553579c1dca297cfb74a65f1010653562142'),
    '31ed82e1470fec4e2493ed341878e2888307088583adca8e1f9af9702e6add2a':
        ('server-update-20261026', 'c6712c413d6fb6d1291fcfa02e8145ba73273e16cbe2f7e31e9564f1e1484eda'),
}
MAX_SERVER_BYTES = 4 * 1024 * 1024
SERVER_DISPLAY_SHA = 'ffb3f74d4c42d72e15a3dc5eb0a2a62e0ee909120289d9d20daaf7b3d76d0d9d'
DISPLAY_METHODS = ('_flavor_settings', '_material_info_list')


def server_syntax_sha(raw):
    """Ignore layout/comments only; retain strings, docstrings and all code."""
    tree = ast.parse(raw, filename='index.py', feature_version=(3, 10))
    return _server_ast_sha(tree)


def _server_ast_sha(tree):
    def canonical(value):
        if isinstance(value, ast.AST):
            return [type(value).__name__, [
                [name, canonical(item)] for name, item in ast.iter_fields(value)
                if not (name == 'type_params' and not item)]]
        if isinstance(value, list):
            return [canonical(item) for item in value]
        if isinstance(value, bytes):
            return ['bytes', value.hex()]
        if isinstance(value, complex):
            return ['complex', repr(value)]
        if value is Ellipsis:
            return ['ellipsis']
        return value
    return byte_digest(json.dumps(canonical(tree), ensure_ascii=True,
                                 separators=(',', ':')).encode('ascii'))


def server_display_sha(raw):
    """Pin only the two wrapped methods, allowing unrelated server updates.

    The runtime still validates response types, identities and original strings.
    This is a compatibility check, not a security review of arbitrary server code.
    """
    tree = ast.parse(raw, filename='index.py', feature_version=(3, 10))
    classes = [node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == 'FgoServlet']
    if len(classes) != 1 or classes[0].decorator_list:
        raise ValueError('Ambiguous or decorated server class')
    methods = []
    for name in DISPLAY_METHODS:
        matches = [node for node in classes[0].body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
        if len(matches) != 1 or not isinstance(matches[0], ast.FunctionDef):
            raise ValueError('Missing, duplicated or asynchronous display method: ' + name)
        methods.append(matches[0])
    return _server_ast_sha(ast.Module(body=methods, type_ignores=[]))


def server_source(target):
    path = child(target, SERVER_INDEX)
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_SERVER_BYTES + 1)
    except FileNotFoundError:
        raise ValueError('서버 파일 누락: ' + SERVER_INDEX) from None
    except OSError:
        raise ValueError('서버 파일을 읽을 수 없습니다: ' + SERVER_INDEX) from None
    if len(raw) > MAX_SERVER_BYTES:
        raise ValueError('지원 범위를 넘는 서버 파일 크기: ' + SERVER_INDEX)
    return raw


def verify_server_base(target, expected):
    raw = server_source(target)
    actual = byte_digest(raw)
    profile, syntax = SERVER_PROFILES.get(actual, ('release-exact', None))
    match = 'exact'
    if actual != expected:
        if expected != SERVER_BASE_SHA:
            raise ValueError('지원 서버 원본과 다른 파일: ' + SERVER_INDEX)
        if actual not in SERVER_PROFILES:
            try:
                syntax = server_syntax_sha(raw)
            except (SyntaxError, ValueError, TypeError, RecursionError):
                raise ValueError('서버 소스 문법 또는 인코딩 오류: ' + SERVER_INDEX) from None
            profiles = [name for name, fingerprint in SERVER_PROFILES.values()
                        if fingerprint == syntax]
            if profiles:
                profile = profiles[0]
                match = 'syntax-equivalent'
            else:
                try:
                    compatible = server_display_sha(raw) == SERVER_DISPLAY_SHA
                except (SyntaxError, ValueError, TypeError, RecursionError):
                    compatible = False
                if not compatible:
                    raise ValueError('서버 한글화 연결 함수의 호환성 미확인: ' + SERVER_INDEX +
                        '\n주석·줄바꿈과 무관한 서버 기능 변경은 허용하지만, 한글화 대상 함수가 달라 중단했습니다.' +
                        '\n현재 SHA-256: ' + actual + '\n이 index.py 원본 파일과 서버 버전을 제보해 주세요.')
                profile = 'display-api-v1'
                match = 'display-compatible'
        else:
            match = 'reviewed-version'
    return dict(format=1, path=SERVER_INDEX, baseline_sha256=expected,
                installed_sha256=actual, profile=profile, match=match,
                syntax_sha256=syntax)


def verify_server_unchanged(target, record):
    if record and byte_digest(server_source(target)) != record['installed_sha256']:
        raise ValueError('설치 도중 서버 파일이 변경되었습니다: ' + SERVER_INDEX)


def prepare_server_binding(cache, doc, record, companions):
    """Bind the startup checks to the exact server accepted by this install."""
    if not record:
        return None, companions
    rows = [row for row in doc['files'] if row['path'] == BOOTSTRAP]
    if len(rows) != 1 or rows[0]['base'] is not None:
        raise ValueError('Unsupported server bootstrap source')
    row = rows[0]
    with zipfile.ZipFile(child(cache, row['asset'])) as archive:
        if archive.getinfo(row['entry']).file_size != row['size'] or row['size'] > 1024 * 1024:
            raise ValueError('Unexpected server bootstrap source size')
        raw = archive.read(row['entry'])
    if byte_digest(raw) != row['sha256'] or row['sha256'] != doc['runtime_companions']['files'][BOOTSTRAP]:
        raise ValueError('Server bootstrap source changed')
    tree = ast.parse(raw)
    bindings = [node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'BINDING' for t in node.targets)]
    if len(bindings) != 1 or len(bindings[0].targets) != 1:
        raise ValueError('Ambiguous server bootstrap binding')
    node = bindings[0]
    binding = ast.literal_eval(node.value)
    if (binding.get('module') != 'titles.fgo.index' or
            binding.get('module_sha256') != record['baseline_sha256'] or
            doc.get('server_base_files', {}).get(SERVER_INDEX) != record['baseline_sha256']):
        raise ValueError('Server bootstrap baseline differs from compatibility check')
    if record['installed_sha256'] != record['baseline_sha256']:
        binding['module_sha256'] = record['installed_sha256']
        lines = raw.decode('utf-8').splitlines(keepends=True)
        lines[node.lineno-1:node.end_lineno] = ['BINDING = ' + repr(binding) + '\n']
        raw = ''.join(lines).encode('utf-8')
        compile(raw, '<server-bootstrap-binding>', 'exec')
    companions = copy.deepcopy(companions)
    companions['files'][BOOTSTRAP] = byte_digest(raw)
    return base64.b64encode(raw).decode('ascii'), companions


def prepare_language(cache, doc):
    """Derive the built-in Korean alias from the verified, frozen server source."""
    rows = [r for r in doc['files'] if r['path'] == ROUTES]
    if len(rows) != 1 or rows[0]['base'] is not None:
        raise ValueError('Unsupported server language source')
    row = rows[0]
    with zipfile.ZipFile(child(cache, row['asset'])) as archive:
        if archive.getinfo(row['entry']).file_size != row['size'] or row['size'] > 1024 * 1024:
            raise ValueError('Unexpected server language source size')
        source = archive.read(row['entry'])
    if byte_digest(source) != row['sha256'] or row['sha256'] != doc['runtime_companions']['files'][ROUTES]:
        raise ValueError('Server language source changed')
    text = source.decode('utf-8')
    anchor = "d.get('gameLanguage')=='ko-fgoac'"
    if text.count(anchor) != 1:
        raise ValueError('Unsupported server language condition')
    derived = text.replace(anchor, "d.get('gameLanguage') in ('ko','ko-fgoac')").encode('utf-8')
    compile(derived, '<server-language-alias>', 'exec')
    record = dict(format=1, selected='ko', overlay='ko-fgoac',
                  routes_source_sha256=row['sha256'], routes_installed_sha256=byte_digest(derived))
    companions = copy.deepcopy(doc['runtime_companions'])
    companions['files'][ROUTES] = byte_digest(derived)
    companions['platform_language_derivation'] = record
    return record, base64.b64encode(derived).decode('ascii'), companions


def prepare(target, previous=None):
    descriptor = read(Path(__file__).with_name('native-labels.json'))
    old = (previous or {}).get('platform_launcher')
    if old:
        verify(target, old)
        original = base64.b64decode(old['original_base64'], validate=True)
    else:
        original = child(target, LAUNCHER).read_bytes()
    if byte_digest(original) != BASE_HASHES['FGO_Launcher.ps1']:
        raise ValueError('지원 원본과 다른 플랫폼 실행 스크립트입니다.')
    patched = render_launcher(original, descriptor, persistent=True)
    record = dict(format=1, path=LAUNCHER,
                  original_base64=base64.b64encode(original).decode('ascii'),
                  original_sha256=byte_digest(original),
                  installed_sha256=byte_digest(patched))
    return record, base64.b64encode(patched).decode('ascii')


def verify(target, record):
    if record.get('format') != 1 or record.get('path') != LAUNCHER:
        raise ValueError('플랫폼 실행 스크립트 관리 기록이 올바르지 않습니다.')
    original = base64.b64decode(record['original_base64'], validate=True)
    if record.get('original_sha256') != BASE_HASHES['FGO_Launcher.ps1'] or byte_digest(original) != record['original_sha256']:
        raise ValueError('플랫폼 실행 스크립트 원본 백업이 변경되었습니다.')
    if digest(child(target, LAUNCHER)) != record['installed_sha256']:
        raise ValueError('플랫폼 실행 스크립트가 변경되어 자동 덮어쓰기/제거를 거부합니다.')


def verify_base(target, record, expected):
    verify(target, record)
    if record['original_sha256'] != expected:
        raise ValueError('플랫폼 실행 스크립트의 원본 버전이 다릅니다.')
