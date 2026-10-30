"""Reviewed-source proposal: persistent outside-overlay contract for cached upgrades.

No work occurs on import. Installed-package hooks are registered explicitly below
the frozen installer's definitions. Normal rebuild/uninstall fails closed; this
package is an exact-parent, prebuilt-cache local upgrade, not a fresh installer.
"""
from pathlib import Path
import base64
import contextlib
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid

SERVER_FILES = (
    'Server/python/Lib/site-packages/fgo_ko_text_routes.py',
    'Server/python/Lib/site-packages/fgo_ko_text_bootstrap.py',
    'Server/python/Lib/site-packages/fgo_ko_text_routes.pth',
)
INDEX = 'nonimage-text/runtime-index.json'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read(path):
    return json.loads(Path(path).read_text('utf-8-sig'))

def semantic(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def permitted_state(path, journal):
    # rglob insertion order may differ; accept only an entire pre-pinned JSON document.
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() in journal['allowed_state_sha256']: return True
    current = json.loads(raw.decode('utf-8-sig'))
    return any(semantic(current) == semantic(expected) for expected in journal['allowed_state_documents'])

def put(path, data):
    path = Path(path)
    temp = path.with_name(path.name + '.nonimage-' + uuid.uuid4().hex)
    with temp.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)

def save(path, value):
    put(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())

def checked_path(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError('Invalid relative companion path')
    parts = relative.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Unsafe companion path')
    path = Path(root).joinpath(*parts)
    for item in (path, *path.parents):
        if item.exists() and (item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & 1024):
            raise ValueError('Linked companion path')
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Companion escaped root')
    return path

def inventory(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in Path(root).rglob('*') if p.is_file()}

def contract(package, manifest):
    if not manifest.get('nonimage_runtime_index'):
        return None
    if manifest['nonimage_runtime_index'] != INDEX:
        raise ValueError('Unexpected companion index')
    index_path = checked_path(package, INDEX)
    if sha(index_path) != manifest['package_files'][INDEX]:
        raise ValueError('Companion index hash mismatch')
    doc = read(index_path)
    if doc['format'] != 1 or set(doc['server_files']) != set(SERVER_FILES):
        raise ValueError('Companion scope mismatch')
    for relative, item in doc['server_files'].items():
        source = checked_path(package, item['package_file'])
        if sha(source) != item['sha256'] or manifest['package_files'][item['package_file']] != item['sha256']:
            raise ValueError('Companion package bytes mismatch')
    if sha(checked_path(package, 'translations.json')) != doc['translations_sha256']:
        raise ValueError('Companion canonical binding mismatch')
    return doc

def ledger(package, manifest, doc):
    return {'format': 1, 'index': INDEX, 'index_sha256': manifest['package_files'][INDEX],
            'files': {name: item['sha256'] for name, item in doc['server_files'].items()}}

def verify_runtime(target, package, manifest, state, require_installed=True):
    doc = contract(package, manifest)
    if doc is None:
        if state.get('runtime_companions'):
            raise ValueError('Cannot silently discard installed runtime companions')
        return
    if not state.get('runtime_companions') and not require_installed:
        return
    if state.get('format') != 2:
        raise ValueError('Runtime companions require installation state format2')
    if state.get('runtime_companions') != ledger(package, manifest, doc):
        raise ValueError('State and package companion contract differ')
    for relative, item in doc['server_files'].items():
        if sha(checked_path(target, relative)) != item['sha256']:
            raise ValueError('Outside-overlay companion changed: ' + relative)
    app = Path(target) / 'App/Localization/ko-fgoac'
    for relative, expected in doc['app_files'].items():
        if state['files'].get(relative) != expected or sha(checked_path(app, relative)) != expected:
            raise ValueError('Managed text/route output missing or changed: ' + relative)

def refuse_running(target, *, launching=False):
    # CIM failures are errors, never an empty-process fallback. Does not signal any process.
    script = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); Get-CimInstance Win32_Process | Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    probe = subprocess.Popen(['powershell','-NoProfile','-NonInteractive','-Command',script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8-sig')
    output, error = probe.communicate()
    if probe.returncode: raise ValueError('Process enumeration failed; normal host permission required')
    if not output.strip(): raise ValueError('Empty process enumeration is not a valid observation')
    rows = json.loads(output)
    if isinstance(rows, dict): rows = [rows]
    if not isinstance(rows, list) or not rows: raise ValueError('Incomplete process enumeration')
    target_name = str(Path(target).resolve()).lower()
    owned_observers = {os.getpid(), probe.pid}
    for row in rows:
        if not isinstance(row, dict) or not row.get('Name'):
            raise ValueError('Incomplete process identity')
        pid = row.get('ProcessId')
        if type(pid) is not int or pid < 0:
            raise ValueError('Invalid process identifier')
        if pid == 0:
            if row['Name'] != 'System Idle Process' or row.get('ExecutablePath') not in (None, '') or row.get('CommandLine') not in (None, ''):
                raise ValueError('Unexpected PID zero process identity')
            continue  # Exact Windows idle pseudo-process; never a game/server.
        if row['ProcessId'] in owned_observers: continue
        name = row.get('ExecutablePath')
        command = row.get('CommandLine')
        relevant = row['Name'].lower() in {'ago.exe','amdaemon.exe','fgolocalplatform.exe','fgo-dualsoul.exe',
            'fgoac scooby.exe','python.exe','pythonw.exe','powershell.exe','pwsh.exe'}
        if relevant and (not name or command is None):
            from process_identity import limited_identity
            native_name, native_command = limited_identity(pid)
            if name and str(name).casefold() != native_name.casefold():
                raise ValueError('Process identity changed during observation: '+str(pid))
            name, command = native_name, native_command
        normalized_command = str(command or '').replace('/', '\\').lower()
        # A diagnostic/installer --target argument alone is not a running server.
        server_command = bool(re.search(r'(?:start-fgolocalserver[.]ps1|fgo_launcher[.]ps1|fgo_runpowershell[.]cmd)', normalized_command))
        external_python_server = row['Name'].lower() in {'python.exe','pythonw.exe'} and bool(
            re.search(r'(?:^|[\\\s\"])(?:index[.]py|artemis)(?:[\s\"]|$)', normalized_command))
        target_reference = target_name in normalized_command or (target_name + '\\server') in normalized_command
        if launching:
            # Only this launch check permits the platform and its server stack.
            # Mutation operations continue using the strict default policy.
            image = str(name or '').replace('/', '\\').lower()
            platform = image in {target_name + '\\fgolocalplatform.exe', target_name + '\\fgoac scooby.exe', target_name + '\\fgo-dualsoul.exe'}
            server_image = image.startswith(target_name + '\\server\\') and row['Name'].lower() in {
                'python.exe', 'pythonw.exe', 'mariadbd.exe', 'mysqld.exe'}
            game_command = bool(re.search(r'(?:fgo_launcher[.]ps1|fgo_runpowershell[.]cmd|[.]ko-fgoac-launch-[^\\\s"]+[.]ps1)', normalized_command))
            server_host = (row['Name'].lower() in {'powershell.exe', 'pwsh.exe'} and
                           target_reference and 'start-fgolocalserver.ps1' in normalized_command and not game_command)
            if platform or server_image or server_host:
                continue
            if target_reference and game_command:
                raise ValueError('게임이 이미 실행 중이거나 시작 중입니다: ' + str(pid))
        ambiguous_relative_server = external_python_server and '--config' in normalized_command
        if (name and str(name).lower().startswith(target_name + '\\')) or (
            target_reference and (server_command or external_python_server)) or ambiguous_relative_server:
            raise ValueError('Target game/server process must already be stopped: ' + str(row['ProcessId']))

def refuse_game_running(target):
    refuse_running(target, launching=True)


@contextlib.contextmanager
def server_start_lock(target):
    # Same exclusive file as Start-FGOLocalServer.ps1. Never starts/stops a server.
    if os.name != 'nt':
        raise ValueError('Windows startup-lock adapter required')
    path = checked_path(target, 'Server/state/server-start.lock')
    if not path.parent.is_dir(): raise ValueError('Missing existing server state directory')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    api.CreateFileW.restype = ctypes.c_void_p
    handle = api.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), 'Server startup/another installer owns the lock')
    try:
        refuse_running(target)
        yield
    finally:
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        api.CloseHandle(handle)

def _rollback_unlocked(journal, target):
    """Caller owns server_start_lock; all mutation paths are journal-bound."""
    journal, target = Path(journal), Path(target)
    doc = read(journal / 'journal.json')
    if str(target.resolve()) != doc['target']:
        raise ValueError('Recovery target mismatch')
    refuse_running(target)
    overlay = checked_path(target, 'App/Localization/ko-fgoac')
    state_path = target / 'App/.ko-fgoac-state.json'
    old_state = read(journal / 'before-state.json')
    if sha(journal / 'before-state.json') != doc['before_state_sha256']:
        raise ValueError('Recovery state backup changed')
    if not permitted_state(state_path, doc):
        raise ValueError('State changed outside this transaction; manual recovery required')
    actual = inventory(overlay)
    allowed = [old_state['files'], doc['expected_new_files']]
    retained = checked_path(target, doc['recovery_retained'])
    staging = journal / 'restore-staging'
    if overlay.exists() and actual not in allowed:
        raise ValueError('Overlay differs from both pinned transaction revisions; manual inspection required')
    if not overlay.exists():
        previous = Path(doc['official_previous']) if doc.get('official_previous') else None
        if previous is not None:
            if previous.parent.resolve() != (target/'App/Localization').resolve() or not previous.name.startswith('.ko-fgoac-upgrade-') or not previous.name.endswith('-previous'):
                raise ValueError('Recovery previous path outside official journal scope')
            checked_path(target, previous.relative_to(target).as_posix())
        have_owned_previous = previous is not None and previous.exists() and inventory(previous) == old_state['files']
        have_retained = retained.exists() and inventory(retained) in allowed
        if not (have_owned_previous or have_retained):
            raise ValueError('Missing active overlay without a journal-bound complete retained revision')
    if inventory(journal / 'before-overlay') != old_state['files']:
        raise ValueError('Recovery overlay backup changed')
    state_temp = checked_path(target, doc['official_state_temp'])
    if state_temp.exists() and not permitted_state(state_temp, doc):
        raise ValueError('Unknown official state-temp revision; manual inspection required')
    # Fully stage and hash the recovery copy before any active overlay rename.
    if staging.exists() and inventory(staging) != old_state['files']:
        staging.rename(journal / ('restore-partial-retained-' + uuid.uuid4().hex))
    if not staging.exists(): shutil.copytree(journal / 'before-overlay', staging)
    if inventory(staging) != old_state['files']: raise ValueError('Incomplete staged recovery')
    doc['status'] = 'RECOVERY_STAGED'; save(journal / 'journal.json', doc)
    # No index.py, DB, player/config, original game or backup file is written.
    for relative in reversed(SERVER_FILES):
        path = checked_path(target, relative)
        before = doc['server_before'][relative]
        current = sha(path) if path.exists() else None
        if current not in (before['sha256'], doc['server_after'][relative]):
            raise ValueError('Externally changed companion; refuse overwrite: ' + relative)
    for relative in reversed(SERVER_FILES):
        path = checked_path(target, relative); before = doc['server_before'][relative]
        if before['present']:
            put(path, base64.b64decode(before['base64'], validate=True))
        elif path.exists():
            path.unlink()  # Exact newly installed file only; no directory deletion.
    if actual != old_state['files']:
        if overlay.exists():
            if retained.exists(): raise ValueError('Recovery retained path already occupied')
            overlay.rename(retained)
        doc['status'] = 'ACTIVE_RETAINED'; save(journal / 'journal.json', doc)
        staging.rename(overlay)
        doc['status'] = 'OVERLAY_RESTORED'; save(journal / 'journal.json', doc)
    put(state_path, (journal / 'before-state.json').read_bytes())
    if state_temp.exists():
        state_temp.rename(journal / ('retained-official-state-temp-' + uuid.uuid4().hex))
    if inventory(overlay) != old_state['files']:
        raise ValueError('Restored overlay does not match original state')
    doc['status'] = 'ROLLED_BACK_EXACT'; save(journal / 'journal.json', doc)

def rollback(journal, target):
    """Public recovery holds the same startup lock as installation/normal launch."""
    with server_start_lock(target):
        _rollback_unlocked(journal, target)

def install_hooks(ns, source_file):
    """Minimal appended hook; original installer implementation stays in place."""
    if ns.get('_durable_nonimage_bound'): raise ValueError('Duplicate companion hooks')
    ns['_durable_nonimage_bound'] = True
    original = {name: ns[name] for name in ('materialize', 'upgrade', 'preflight', 'state_checked', 'uninstall')}
    active_owner = {'journal': None, 'token': None}  # Process-local closure, no CLI/persisted bypass flag.

    def state_checked(args):
        result = original['state_checked'](args)
        target, state, _, _ = result
        pending = state.get('nonimage_transaction')
        if pending and (active_owner['token'] is None or active_owner['journal'] != pending.get('journal')):
            raise ValueError('Pending non-image transaction; explicit recovery required')
        manifest = read(args.package / 'manifest.json')
        verify_runtime(target, args.package, manifest, state, require_installed=bool(state.get('runtime_companions')))
        return result

    def materialize(game, destination, rows, font, license_file, package=None):
        if package is not None and read(Path(package) / 'manifest.json').get('nonimage_runtime_index'):
            raise ValueError('Use the approved exact-parent prebuilt cache; generic regeneration would lose reviewed aliases')
        return original['materialize'](game, destination, rows, font, license_file, package=package)

    def preflight(args):
        original['preflight'](args)
        manifest = read(args.package / 'manifest.json')
        verify_runtime(args.target, args.package, manifest, read(args.target / 'App/.ko-fgoac-state.json'))

    def upgrade(args):
        manifest, _ = ns['load_package'](args.package)
        doc = contract(args.package, manifest)
        target = ns['check_target'](args.target, manifest)
        state_path = target / 'App/.ko-fgoac-state.json'
        with server_start_lock(target):
            # One fresh, fully checked state capture inside the held startup lock.
            _, checked_before, _, _ = original['state_checked'](args)
            before_raw = state_path.read_bytes()
            before = json.loads(before_raw.decode('utf-8-sig'))
            captured_sha = hashlib.sha256(before_raw).hexdigest()
            if semantic(before) != semantic(checked_before):
                raise ValueError('State changed while validating the current parent')
            expected_parent = ns.get('_nonimage_expected_parent_state_sha')
            if expected_parent is not None and captured_sha != expected_parent:
                raise ValueError('Not the independently approved parent-state revision')
            if before.get('nonimage_transaction'):
                raise ValueError('Pending non-image transaction; do not retry an upgrade')
            if doc is None:
                if before.get('runtime_companions'):
                    raise ValueError('Use the retained first-install recovery journal to remove companion scope')
                return original['upgrade'](args)
            # Later image upgrades copy this contract/index and physical companions unchanged.
            if before.get('runtime_companions'):
                verify_runtime(target, args.package, manifest, before)
                materializer = ns['materialize']
                def checked_followup_materialize(game, destination, rows, font, license_file, package=None):
                    materializer(game, destination, rows, font, license_file, package=package)
                    staged = inventory(destination)
                    for relative, expected_sha in doc['app_files'].items():
                        if staged.get(relative) != expected_sha:
                            raise ValueError('Follow-up cache would discard or change managed text: ' + relative)
                # Validate the staged successor before the official installer swaps it in.
                # This also guards callers other than the source-pinned coordinator.
                ns['materialize'] = checked_followup_materialize
                try:
                    original['upgrade'](args); preflight(args)
                finally:
                    ns['materialize'] = materializer
                return
            expected = ns.get('_nonimage_expected_cache_files')
            if not isinstance(expected, dict): raise ValueError('First install requires approved cache-file pins')
            if expected_parent is None:
                raise ValueError('First install requires an externally approved parent-state SHA')
            for relative in SERVER_FILES:
                if checked_path(target, relative).exists():
                    raise ValueError('Untracked pre-existing companion; capture/review its baseline before installation')
            state_temp = state_path.with_suffix('.upgrade-tmp')
            if state_temp.exists(): raise ValueError('Existing official upgrade state-temp requires inspection')
            root = target / 'App/.ko-nonimage-journals'
            root.mkdir(exist_ok=True)
            journal = root / uuid.uuid4().hex; journal.mkdir()
            shutil.copytree(target / 'App/Localization/ko-fgoac', journal / 'before-overlay')
            put(journal / 'before-state.json', before_raw)
            if sha(journal / 'before-state.json') != captured_sha:
                raise ValueError('State backup differs from the checked capture')
            if inventory(journal / 'before-overlay') != before['files']:
                raise ValueError('Parent backup does not match exact installed state')
            j = {'status': 'PREPARED', 'target': str(target.resolve()),
                 'expected_new_files': expected, 'before_state_sha256': captured_sha,
                 'allowed_state_sha256': [captured_sha], 'allowed_state_documents': [before],
                 'official_staging': None, 'official_previous': None,
                 'official_state_temp': state_temp.relative_to(target).as_posix(),
                 'recovery_retained': 'App/Localization/.nonimage-failed-' + journal.name,
                 'server_before': {name: {'present': False, 'sha256': None, 'base64': None} for name in SERVER_FILES},
                 'server_after': {name: item['sha256'] for name, item in doc['server_files'].items()}}
            # Save every legal state revision before the official transaction can write.
            # This covers a power loss immediately after official state commit.
            pending = dict(before, format=2,
                nonimage_transaction={'journal': str(journal.resolve()), 'status': 'PENDING'})
            intermediate = dict(pending, version=manifest['version'],
                package_manifest_sha256=sha(args.package / 'manifest.json'),
                base_files=manifest['base_files'], files=expected)
            final = dict(intermediate, format=2,
                runtime_companions=ledger(args.package, manifest, doc))
            final.pop('nonimage_transaction')
            for value in (pending, intermediate, final):
                raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
                j['allowed_state_sha256'].append(hashlib.sha256(raw).hexdigest())
                j['allowed_state_documents'].append(value)
            save(journal / 'journal.json', j)
            ns['_nonimage_last_journal'] = journal
            active_owner.update(journal=str(journal.resolve()), token=object())
            materializer = ns['materialize']
            def tracked_materialize(game, destination, rows, font, license_file, package=None):
                destination = Path(destination).resolve()
                if destination.parent != (target/'App/Localization').resolve() or not destination.name.startswith('.ko-fgoac-upgrade-'):
                    raise ValueError('Unexpected official staging path')
                j['official_staging'] = str(destination)
                j['official_previous'] = str(destination.with_name(destination.name+'-previous'))
                save(journal/'journal.json', j)
                return materializer(game,destination,rows,font,license_file,package=package)
            try:
                # Old installers reject format2 before any official overlay/Server mutation.
                if sha(state_path) != captured_sha:
                    raise ValueError('Parent state changed after backup; refuse pending write')
                put(state_path, (json.dumps(pending,ensure_ascii=False,indent=2)+'\n').encode())
                ns['materialize'] = tracked_materialize
                original['upgrade'](args)
                current = read(state_path)
                j['allowed_state_sha256'].append(sha(state_path)); save(journal / 'journal.json', j)
                if current['files'] != expected: raise ValueError('Official upgrade cache output mismatch')
                refuse_running(target)
                j['status'] = 'INSTALLING_COMPANIONS'; save(journal / 'journal.json', j)
                for relative in SERVER_FILES:  # .pth last, module code first.
                    path = checked_path(target, relative)
                    if path.exists(): raise ValueError('Companion appeared during transaction')
                    source = checked_path(args.package, doc['server_files'][relative]['package_file'])
                    put(path, source.read_bytes())
                current['format'] = 2
                current['runtime_companions'] = ledger(args.package, manifest, doc)
                current.pop('nonimage_transaction', None)
                new_state = (json.dumps(current, ensure_ascii=False, indent=2) + '\n').encode()
                j['allowed_state_sha256'].append(hashlib.sha256(new_state).hexdigest())
                save(journal / 'journal.json', j); put(state_path, new_state)
                preflight(args)
                j['status'] = 'COMMITTED'; j['state_sha256'] = sha(state_path)
                save(journal / 'journal.json', j)
            except Exception:
                _rollback_unlocked(journal, target)
                raise
            finally:
                ns['materialize'] = materializer
                active_owner.update(journal=None, token=None)

    def uninstall(args):
        state = read(args.target / 'App/.ko-fgoac-state.json')
        if state.get('runtime_companions'):
            raise ValueError('Use explicit reviewed recovery; generic uninstall cannot orphan server companions')
        return original['uninstall'](args)

    ns.update(state_checked=state_checked, materialize=materialize, upgrade=upgrade, preflight=preflight, uninstall=uninstall)
