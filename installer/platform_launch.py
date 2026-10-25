"""Reversible integration with the platform's existing launcher entrypoint."""
import base64
import copy
from pathlib import Path
import zipfile
from common import child, digest, read
from native_label_support import BASE_HASHES, digest as byte_digest, render_launcher

LAUNCHER = 'App/FGO_Launcher.ps1'
ROUTES = 'Server/python/Lib/site-packages/fgo_ko_text_routes.py'


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
