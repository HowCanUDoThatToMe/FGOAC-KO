"""Local installer engine. New packaging never modifies approved source packages."""
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
import zipfile
from common import child, digest, encoded, inventory, no_links, put, read, save, xor
from offline_support import cancelled, local_path, verify_bundle

OVERLAY = 'App/Localization/ko-fgoac'
STATE = 'App/.ko-fgoac-state.json'
CONFIG = 'App/fgo-launcher.json'
HOME = '.fgoac-korean'
PREFIX = OVERLAY + '/'
SERVER_FILES = {'Server/python/Lib/site-packages/' + n for n in
                ('fgo_ko_text_routes.py','fgo_ko_text_bootstrap.py','fgo_ko_text_routes.pth')}


def emit(message):
    print(message, flush=True)


PROTECTED_ROOTS = ()
PROTECTED_PATH_DIGESTS = frozenset(('2aa0fc4531e556ef75f2056d9f35dbafe958845694534fdf0c9e8c8ea503429b', '71ab60f6e05ec5b6c3c4736fbfdf49ce2ac5436c3be03a1266c29997d80eb0bc', 'cf04eea9fa808da70b2a44af6c8cf82246b723968d9341f43b357e8eca1e7e34'))


def target_path(value):
    target = Path(value).absolute(); no_links(target)
    # Resolve existing Windows aliases (including short names) before comparing.
    # pathlib's Windows comparison is case insensitive and component aware.
    target = target.resolve()
    if os.name == 'nt':
        text = str(target)
        if text.startswith('\\\\.\\'):
            raise ValueError('Windows device paths are not supported')
        if text.startswith('\\\\?\\UNC\\'):
            target = Path('\\\\' + text[8:])
        elif text.startswith('\\\\?\\'):
            target = Path(text[4:])
    for ancestor in (target, *target.parents):
        if hashlib.sha256(str(ancestor).casefold().encode('utf-8')).hexdigest() in PROTECTED_PATH_DIGESTS:
            raise ValueError('보호된 원본·백업·현재 사용본 경로에는 적용하지 않습니다.')
    for name in (CONFIG, 'App/inject.exe', 'Server/Start-FGOLocalServer.ps1'):
        if not child(target, name).is_file():
            raise ValueError('게임 실행 환경 누락: ' + name)
    for name in ('AMFS', 'DEVICE', 'GameData', 'App/option', 'Server/state'):
        if not child(target, name).is_dir():
            raise ValueError('게임 폴더 누락: ' + name)
    return target


@contextlib.contextmanager
def locked(target):
    home = child(target, HOME); home.mkdir(exist_ok=True)
    import msvcrt
    with (home / 'installer.lock').open('a+b') as stream:
        stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            from process_guard import server_start_lock
            with server_start_lock(target):
                yield home
        finally:
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def file_state(path):
    return digest(path) if path.is_file() else None


def verify_owned(target, state):
    if state.get('platform_launcher'):
        from platform_launch import verify
        verify(target, state['platform_launcher'])
    if state.get('format') != 2 or state.get('language') != 'ko-fgoac':
        raise ValueError('Unknown installation state')
    if set(state.get('runtime_companions',{}).get('files',{})) != SERVER_FILES:
        raise ValueError('Unexpected server ownership scope')
    if inventory(child(target, OVERLAY)) != state['files']:
        raise ValueError('설치 파일이 변경되어 자동 덮어쓰기/제거를 거부합니다.')
    for rel, sha in state.get('runtime_companions', {}).get('files', {}).items():
        if file_state(child(target, rel)) != sha:
            raise ValueError('서버 보조 파일 변경: ' + rel)


def validate_release(doc):
    if doc.get('format') != 1 or not isinstance(doc.get('files'), list):
        raise ValueError('Unsupported release')
    if set(doc['runtime_companions']['files']) != SERVER_FILES:
        raise ValueError('Unsupported server companion scope')
    if doc.get('server_base_files') is not None and set(doc['server_base_files']) != {'Server/artemis/titles/fgo/index.py'}:
        raise ValueError('Unexpected original server compatibility scope')
    paths, entries = set(), set()
    assets = {a['name']:a for a in doc['assets']}
    if len(assets) != len(doc['assets']):
        raise ValueError('Duplicate asset')
    for a in assets.values():
        if a['name'] != a['sha256'] + '.zip' or not 0 < a['size'] < 1024 ** 3:
            raise ValueError('Unsafe asset')
    for row in doc['files']:
        rel = row['path']
        child(Path.cwd(), rel)
        if rel in paths or rel.casefold() in {p.casefold() for p in paths}:
            raise ValueError('Duplicate output')
        paths.add(rel)
        if not (rel.startswith(PREFIX) or rel in doc['runtime_companions']['files']):
            raise ValueError('Outside approved output scope')
        if row['base']:
            child(Path.cwd(), row['base'])
            if doc['base_files'].get(row['base']) != row['base_sha256']:
                raise ValueError('Base binding mismatch')
        if row['asset'] not in assets or (row['asset'], row['entry']) in entries:
            raise ValueError('Duplicate/unknown archive entry')
        entries.add((row['asset'], row['entry']))
        if not isinstance(row['size'], int) or not 0 <= row['size'] < 512 * 1024 ** 2:
            raise ValueError('Invalid output size')


def acquire(args):
    package = local_path(args.package)
    release = verify_bundle(package, args.cancel)
    doc = read(release); validate_release(doc)
    args.cache = child(package, 'payload')
    args.release_sha = digest(release)
    # Keep the frozen translation payload identity separate from installer releases.
    version = read(package / 'offline-manifest.json').get('version', doc['version'])
    if not isinstance(version, str) or len(version) > 32 or not all(
            part.isascii() and part.isdigit() for part in version.split('.')) or len(version.split('.')) != 3:
        raise ValueError('배포 버전 형식이 올바르지 않습니다.')
    doc = dict(doc, version=version)
    for i, asset in enumerate(doc['assets']):
        cancelled(args.cancel)
        path = child(args.cache, asset['name'])
        if not path.is_file():
            raise ValueError('패치 자료 누락: 모든 배포 ZIP을 같은 폴더에 풀어 주세요: ' + asset['name'])
        if path.stat().st_size != asset['size'] or digest(path) != asset['sha256']:
            raise ValueError('패치 자료 손상: ' + asset['name'])
        emit('오프라인 자료 검사 %d/%d' % (i + 1, len(doc['assets'])))
    return doc


def verify_base(target, doc, cancel=None, previous=None):
    for i, (rel, sha) in enumerate(doc['base_files'].items(), 1):
        cancelled(cancel)
        if rel == 'App/FGO_Launcher.ps1':
            from platform_launch import verify_base as verify_launcher_base
            verify_launcher_base(target, (previous or {}).get('platform_launcher'), sha)
        elif file_state(child(target, rel)) != sha:
            raise ValueError('지원 원본과 다르거나 없는 파일: ' + rel)
        if i % 500 == 0 or i == len(doc['base_files']):
            emit('게임 원본 검사 %d/%d' % (i, len(doc['base_files'])))
    config = read(child(target, CONFIG))
    if not isinstance(config, dict) or 'gameVersion' not in config:
        raise ValueError('게임 설정 형식 또는 gameVersion 항목 오류: App/fgo-launcher.json. 정상 설정 백업을 확인해 주세요.')
    if config.get('gameVersion') != doc['game_version']:
        raise ValueError('지원 게임 버전 불일치')
    server_record = None
    for rel, sha in doc.get('server_base_files', {}).items():
        from platform_launch import SERVER_INDEX, verify_server_base
        if rel != SERVER_INDEX:
            raise ValueError('Unexpected original server compatibility scope')
        server_record = verify_server_base(target, sha)
        emit('서버 호환성 검사 통과: ' + server_record['profile'] + ' / ' + server_record['match'])
    return server_record


def reconstruct(target, staging, cache, doc, cancel=None):
    for asset in doc['assets']:
        rows = [r for r in doc['files'] if r['asset'] == asset['name']]
        archive_path = child(cache, asset['name'])
        if digest(archive_path) != asset['sha256']:
            raise ValueError('Cached asset changed')
        with zipfile.ZipFile(archive_path) as archive:
            if len(archive.infolist()) != len(rows) or set(archive.namelist()) != {r['entry'] for r in rows}:
                raise ValueError('Archive entry scope mismatch')
            for r in rows:
                cancelled(cancel)
                info = archive.getinfo(r['entry'])
                if info.file_size != r['size']:
                    raise ValueError('Archive declared size mismatch')
                out = child(staging, r['path']); out.parent.mkdir(parents=True, exist_ok=True)
                base = child(target, r['base']).open('rb') if r['base'] else None
                try:
                    with archive.open(info) as src, out.open('xb') as dst:
                        count = 0
                        while True:
                            block = src.read(65536)
                            if not block:
                                break
                            count += len(block)
                            if count > r['size']:
                                raise ValueError('Oversized archive entry')
                            dst.write(xor(block, base.read(len(block))) if base else block)
                        dst.flush(); os.fsync(dst.fileno())
                finally:
                    if base:
                        base.close()
                if count != r['size'] or digest(out) != r['sha256']:
                    raise ValueError('승인 결과와 재구성 파일 불일치: ' + r['path'])
        emit('한글 파일 재구성: ' + asset['name'][:12])


def snapshot(path):
    return base64.b64encode(path.read_bytes()).decode() if path.is_file() else None


def blob_sha(blob):
    import hashlib
    return hashlib.sha256(base64.b64decode(blob)).hexdigest() if blob is not None else None


def replace_optional(path, blob):
    if blob is None:
        if path.exists():
            path.unlink()
    else:
        put(path, base64.b64decode(blob))


def recover(target, home):
    jp = home / 'pending.json'
    if not jp.exists():
        emit('복구 대기 작업 없음'); return
    j = read(jp)
    if j['target'] != str(target.resolve()):
        raise ValueError('Recovery target mismatch')
    allowed = SERVER_FILES | {STATE, CONFIG, HOME + '/Manage-KoreanPatch.ps1', 'App/FGO_Launcher.ps1'}
    if set(j['before']) != set(j['after']) or not set(j['before']) <= allowed:
        raise ValueError('Recovery ownership scope mismatch')
    if not j['transaction'].startswith('transaction-') or '/' in j['transaction']:
        raise ValueError('Invalid recovery transaction')
    tx = child(home, j['transaction'])
    old = child(tx, 'previous-overlay'); overlay = child(target, OVERLAY)
    actual = inventory(overlay) if overlay.exists() else None
    if actual is not None and actual not in (j['before_overlay'], j['after_overlay']):
        raise ValueError('Recovery: unexpected overlay changes; preserved')
    if old.exists() and inventory(old) != j['before_overlay']:
        raise ValueError('Recovery backup changed')
    for rel in j['before']:
        current = file_state(child(target, rel))
        if current not in (blob_sha(j['before'][rel]), blob_sha(j['after'][rel])):
            raise ValueError('복구 중 사용자 변경 발견, 보존: ' + rel)
    # Validate everything before any recovery mutation.
    if old.exists():
        if overlay.exists():
            retained = tx / 'rolled-back-overlay'
            if retained.exists():
                raise ValueError('Prior recovery output exists')
            overlay.rename(retained)
        old.rename(overlay)
    elif j['before_overlay'] is None and overlay.exists():
        overlay.rename(tx / 'rolled-back-overlay')
    elif actual != j['before_overlay']:
        raise ValueError('Required overlay backup missing')
    for rel, blob in j['before'].items():
        replace_optional(child(target, rel), blob)
    save(tx / 'recovered.json', {'status':'RECOVERED'})
    jp.unlink()
    emit('중단된 설치를 이전 상태로 복구했습니다.')


def commit_transaction(target, home, staging, state, after, before_overlay, after_overlay, action, retain_overlay=False):
    if retain_overlay and (before_overlay is None or before_overlay != after_overlay):
        raise ValueError('Cannot retain a changed overlay')
    tx = staging.parent
    touched = set(after) | {STATE}
    after[STATE] = base64.b64encode(encoded(state)).decode() if state else None
    j = dict(target=str(target.resolve()), transaction=tx.name,
             action=action, before_overlay=before_overlay, after_overlay=after_overlay,
             before={r:snapshot(child(target,r)) for r in touched}, after=after)
    save(tx / 'journal.json', j); save(home / 'pending.json', j)
    overlay = child(target, OVERLAY)
    try:
        if overlay.exists() and not retain_overlay:
            overlay.rename(tx / 'previous-overlay')
        if after_overlay is not None and not retain_overlay:
            overlay.parent.mkdir(parents=True, exist_ok=True)
            child(staging, OVERLAY).rename(overlay)
        for rel, blob in after.items():
            replace_optional(child(target, rel), blob)
        if state:
            verify_owned(target, state)
        elif overlay.exists():
            raise ValueError('Removal did not deactivate overlay')
        for rel, blob in after.items():
            if file_state(child(target, rel)) != blob_sha(blob):
                raise ValueError('Final transaction verification failed')
        save(tx / 'committed.json', dict(status='COMMITTED', action=action))
        (home / 'pending.json').unlink()
    except BaseException:
        emit('설치가 중단되었습니다. 복구 명령으로 이전 상태를 복원할 수 있습니다.')
        raise


def verify_addon_upgrade(previous, doc, files):
    """Require explicit, pinned integration before replacing an installed addon."""
    integrated = doc.get('integrated_addons', {})
    old = dict(previous.get('integrated_addons', {}))
    old.update(previous.get('addons', {}))
    for name, record in old.items():
        new = integrated.get(name)
        if (not new or new.get('manifest_sha256') != record.get('manifest_sha256') or
                not new.get('files') or any(files.get(p) != h for p, h in new['files'].items())):
            raise ValueError('기존 추가 수정의 유지 여부를 확인할 수 없습니다: ' + name +
                             '. 해당 수정이 포함된 정식 업데이트를 사용해 주세요. 설치 파일은 유지됩니다.')


def install(args, target, home, doc):
    if (home / 'pending.json').exists():
        raise ValueError('중단 기록이 있습니다. 먼저 복구를 실행하세요.')
    state_path = child(target, STATE)
    previous = read(state_path) if state_path.exists() else None
    server_record = verify_base(target, doc, args.cancel, previous)
    # Synthetic packages without the native bridge retain their existing scope.
    launcher_record = launcher_blob = None
    language_record = routes_blob = None
    companions = doc['runtime_companions']
    if 'App/FGO_Launcher.ps1' in doc['base_files']:
        from platform_launch import prepare, prepare_language
        launcher_record, launcher_blob = prepare(target, previous)
        language_record, routes_blob, companions = prepare_language(args.cache, doc)
    from platform_launch import BOOTSTRAP, prepare_server_binding, verify_server_unchanged
    bootstrap_blob, companions = prepare_server_binding(args.cache, doc, server_record, companions)
    files = {r['path'][len(PREFIX):]:r['sha256'] for r in doc['files'] if r['path'].startswith(PREFIX)}
    same_release = False
    retain_overlay = False
    if previous:
        verify_owned(target, previous)
        if not previous.get('installer_local'):
            raise ValueError('기존 개발용 설치본은 자동 이전하지 않습니다. 별도 이전 검증이 필요합니다.')
        same_release = previous['installer_local']['release_sha256'] == args.release_sha
        if same_release and previous['files'] != files:
            raise ValueError('같은 배포본의 번역 파일 기록이 다릅니다.')
        verify_addon_upgrade(previous, doc, files)
        retain_overlay = previous['files'] == files
        # Derived routing/bootstrap files are prepared above. Other companions
        # must already match before their installed copy can be reused.
        retain_overlay = retain_overlay and all(
            rel in (BOOTSTRAP, 'Server/python/Lib/site-packages/fgo_ko_text_routes.py') or
            file_state(child(target, rel)) == sha for rel, sha in companions['files'].items())
        if (same_release and previous.get('version') == doc['version'] and
                previous.get('platform_launcher') == launcher_record and
                previous.get('platform_language') == language_record and
                previous.get('server_compatibility') == server_record):
            verify_server_unchanged(target, server_record)
            emit('이미 같은 승인본이 설치되어 있습니다. 파일 검사 통과.'); return
    elif child(target, OVERLAY).exists():
        raise ValueError('관리되지 않은 같은 이름 폴더가 있습니다.')
    need = (0 if retain_overlay else sum(r['size'] for r in doc['files'])) + 1024 ** 3
    if shutil.disk_usage(target).free < need:
        raise ValueError('임시 설치·복구 공간 부족')
    tx = home / ('transaction-' + uuid.uuid4().hex); tx.mkdir()
    staging = tx / 'staging'; staging.mkdir()
    if not retain_overlay:
        reconstruct(target, staging, args.cache, doc, args.cancel)
    before_config = child(target, CONFIG).read_bytes()
    cfg = json.loads(before_config.decode('utf-8-sig'))
    initial_config = previous['original_config_base64'] if previous else base64.b64encode(before_config).decode()
    initial = json.loads(base64.b64decode(initial_config).decode('utf-8-sig'))
    after = {}
    if launcher_record:
        after[launcher_record['path']] = launcher_blob
    for rel in doc['runtime_companions']['files']:
        if not previous and child(target, rel).exists():
            raise ValueError('이미 존재하는 서버 모듈은 덮어쓰지 않습니다: ' + rel)
        if routes_blob is not None and rel == 'Server/python/Lib/site-packages/fgo_ko_text_routes.py':
            after[rel] = routes_blob
        elif bootstrap_blob is not None and rel == BOOTSTRAP:
            after[rel] = bootstrap_blob
        else:
            after[rel] = snapshot(child(target if retain_overlay else staging, rel))
        if blob_sha(after[rel]) != companions['files'][rel]:
            raise ValueError('서버 언어 연결 결과 불일치: ' + rel)
    cfg['gameLanguage'] = 'ko' if language_record else 'ko-fgoac'
    after[CONFIG] = base64.b64encode(encoded(cfg)).decode()
    state = dict(format=2, version=doc['version'], language='ko-fgoac', files=files,
        package_manifest_sha256=doc['approved_manifest_sha256'],
        base_files={k[4:]:v for k,v in doc['base_files'].items()}, protected_roots=[],
        previous_present='gameLanguage' in initial, previous_language=initial.get('gameLanguage'),
        original_config_base64=initial_config, installed_config_sha256=blob_sha(after[CONFIG]),
        runtime_companions=companions,
        installer_local=dict(format=1, release_sha256=args.release_sha))
    if launcher_record:
        state['platform_launcher'] = launcher_record
        state['platform_language'] = language_record
    if server_record:
        state['server_compatibility'] = server_record
    if doc.get('integrated_addons'):
        state['integrated_addons'] = doc['integrated_addons']
    cancelled(args.cancel)
    from process_guard import refuse_running
    refuse_running(target)
    if previous:
        verify_owned(target, previous)
        if read(state_path) != previous:
            raise ValueError('설치 도중 기존 상태가 변경되었습니다.')
    elif state_path.exists() or child(target, OVERLAY).exists():
        raise ValueError('설치 도중 같은 이름의 파일이 생겼습니다.')
    if child(target, CONFIG).read_bytes() != before_config:
        raise ValueError('설치 도중 설정이 변경되었습니다.')
    if launcher_record:
        expected_launcher = (previous or {}).get('platform_launcher', {}).get('installed_sha256', launcher_record['original_sha256'])
        if file_state(child(target, launcher_record['path'])) != expected_launcher:
            raise ValueError('설치 도중 플랫폼 실행 스크립트가 변경되었습니다.')
    verify_server_unchanged(target, server_record)
    # Preserve executable manager before activating anything. Never delete its package dependency.
    source_home = Path(__file__).resolve().parent
    import hashlib
    manager_files = inventory(source_home)
    manager_id = hashlib.sha256(encoded(manager_files)).hexdigest()
    engine_home = home / 'managers' / manager_id
    if not engine_home.exists():
        shutil.copytree(source_home, engine_home, ignore=shutil.ignore_patterns('__pycache__'))
    if inventory(engine_home) != manager_files:
        raise ValueError('로컬 관리 엔진 복사 검사 실패')
    management = '''param([ValidateSet('status','uninstall','recover','launch')][string]$Action='status')
$ErrorActionPreference='Stop'
$target=Split-Path -Parent $PSScriptRoot
$manager=Join-Path $PSScriptRoot 'managers/__MANAGER__'
& (Join-Path $manager 'runtime/python.exe') -I -X utf8 -B (Join-Path $manager 'engine.py') $Action --target $target
exit $LASTEXITCODE
'''.replace('__MANAGER__',manager_id)
    after[HOME + '/Manage-KoreanPatch.ps1'] = base64.b64encode(management.encode('utf-8-sig')).decode()
    save(home / 'release.json', doc)
    commit_transaction(target, home, staging, state, after, previous['files'] if previous else None,
                       files, 'upgrade' if previous else 'install', retain_overlay=retain_overlay)
    emit('설치 완료: %s / %d개 파일. 실제 게임 화면 검수와는 별도입니다.' % (doc['version'], len(files)))


def uninstall(target, home):
    if (home / 'pending.json').exists():
        raise ValueError('먼저 복구를 실행하세요.')
    state = read(child(target, STATE)); verify_owned(target, state)
    if not state.get('installer_local'):
        raise ValueError('개발용 설치본의 자동 제거는 지원하지 않습니다.')
    current = child(target, CONFIG).read_bytes(); cfg = json.loads(current.decode('utf-8-sig'))
    if file_state(child(target, CONFIG)) == state['installed_config_sha256']:
        config = base64.b64decode(state['original_config_base64'])
    else:
        if cfg.get('gameLanguage') == 'ko-fgoac' or (state.get('platform_language') and cfg.get('gameLanguage') == 'ko'):
            if state['previous_present']:
                cfg['gameLanguage'] = state['previous_language']
            else:
                cfg.pop('gameLanguage', None)
        config = encoded(cfg)
    after = {rel:None for rel in state['runtime_companions']['files']}
    after[CONFIG] = base64.b64encode(config).decode()
    if state.get('platform_launcher'):
        launcher = state['platform_launcher']
        after[launcher['path']] = launcher['original_base64']
    tx = home / ('transaction-' + uuid.uuid4().hex); tx.mkdir()
    staging = tx / 'staging'; staging.mkdir()
    commit_transaction(target, home, staging, None, after, state['files'], None, 'uninstall')
    emit('제거 완료. 원본·개인 설정 보존. 이전 한글 파일은 복구 기록에 보관했습니다.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['install','status','uninstall','recover','verify','launch'])
    p.add_argument('--target', type=Path)
    p.add_argument('--package', type=Path, default=Path(__file__).resolve().parent.parent)
    p.add_argument('--cancel', type=Path)
    a = p.parse_args()
    try:
        doc = None
        if a.action in ('verify','install'):
            doc = acquire(a)
        if a.action == 'verify':
            emit('오프라인 배포 자료 검사 완료. 게임 폴더는 변경하지 않았습니다.')
            return 0
        if not a.target:
            raise ValueError('게임 폴더를 지정하세요.')
        target = target_path(local_path(a.target))
        if a.action == 'launch':
            # Explicit launch only; installation and verification never start the game.
            state = read(child(target, STATE)); verify_owned(target, state)
            if (child(target,HOME) / 'pending.json').exists():
                raise ValueError('복구 필요')
            from process_guard import refuse_game_running
            refuse_game_running(target)
            from native_label_support import temporary_launcher
            import subprocess
            launch_context = (contextlib.nullcontext(child(target, 'App/FGO_Launcher.ps1'))
                              if state.get('platform_launcher') else
                              temporary_launcher(target, read(Path(__file__).parent/'native-labels.json'), child))
            with launch_context as script:
                return subprocess.call(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)])
        with locked(target) as home:
            if a.action == 'install':
                install(a, target, home, doc)
            elif a.action == 'recover':
                recover(target, home)
            elif a.action == 'uninstall':
                uninstall(target, home)
            else:
                if (home / 'pending.json').exists():
                    raise ValueError('복구 필요')
                if not child(target, STATE).exists():
                    emit('한글패치 미설치')
                else:
                    state = read(child(target, STATE)); verify_owned(target, state)
                    emit(json.dumps(dict(installed=True, version=state['version'], integrity=True,
                         manifest_sha256=state['package_manifest_sha256']), ensure_ascii=False))
        return 0
    except KeyError as e:
        emit('ERROR: 설정 또는 배포 기록의 필수 항목이 없습니다: ' + str(e) +
             '. 선택한 게임 폴더와 배포 파일을 확인하고 오류 기록을 제보해 주세요.'); return 2
    except OSError as e:
        emit('ERROR: 파일 접근 또는 저장 실패: ' + str(e) +
             '\n게임·서버 종료 여부, 파일 권한과 남은 디스크 공간을 확인해 주세요. 중단 기록이 있으면 복구를 실행하세요.'); return 2
    except (ValueError, zipfile.BadZipFile) as e:
        emit('ERROR: ' + str(e)); return 2


if __name__ == '__main__':
    sys.exit(main())
