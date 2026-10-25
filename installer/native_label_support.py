"""Optional, hash-bound Korean label bridge; no live-process attachment.

The DLL is owned by the localization overlay. A temporary derived launcher is
created only for a user-requested launch, alongside the original script so its
PSScriptRoot remains unchanged. The temporary launch path leaves the original script unchanged. Offline installation
may also use the persistent renderer with transaction-managed backup and restore.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

INDEX = 'native-labels.json'
PAYLOAD = 'native/fgoko-labels.dll'
KIND = 'ko-bond-tab-v1'
BASE_HASHES = {
    'ago.exe': '852119518ebf1244ead307d1bd7e0ce5ce7a2efcd820136e03810419dd827240',
    'fgohook.dll': '01382853521d0a713d7dbd8ca1f53e8b54c3313a0497de62b86145ca704c484b',
    'fgozh.dll': 'b7791cbd8ab9d27f02b10136d5f27e8cf0ca4a7886f288cac4eb3ba3c01ed571',
    'FGO_Launcher.ps1': '74cf22e51f5262a6692dc7b82e4d1fd0c6ad7191076a7b06b592fd948a985658',
    'inject.exe': '2008b77602bcd5afd03734f14a6d8681a00769f8809c8a1aee2eaba21827f93a',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_native_labels(package, manifest, child):
    index = manifest.get('native_label_index')
    if index is None:
        return None
    if index != INDEX:
        raise ValueError('Unsupported native label index')
    tracked = manifest['package_files']
    if index not in tracked or PAYLOAD not in tracked:
        raise ValueError('Untracked native label payload')
    raw = child(package, index).read_bytes()
    if digest(raw) != tracked[index]:
        raise ValueError('Native label index integrity mismatch')
    doc = json.loads(raw.decode('utf-8-sig'))
    if (doc.get('format') != 1 or doc.get('kind') != KIND or
            doc.get('payload') != PAYLOAD or doc.get('installed_relative') != PAYLOAD or
            doc.get('base_files') != BASE_HASHES or doc.get('runtime_verified') is not False):
        raise ValueError('Unsupported native label descriptor')
    if any(manifest['base_files'].get(r) != h for r, h in BASE_HASHES.items()):
        raise ValueError('Native label base files are not bound to compatibility checks')
    payload = child(package, PAYLOAD).read_bytes()
    if digest(payload) != doc.get('sha256') or doc['sha256'] != tracked[PAYLOAD]:
        raise ValueError('Native label DLL integrity mismatch')
    if payload[:2] != b'MZ':
        raise ValueError('Native label payload is not a Windows module')
    return doc


def verify_native_base(game, doc, child):
    for relative, expected in doc['base_files'].items():
        if digest(child(game / 'App', relative).read_bytes()) != expected:
            raise ValueError('Native label base mismatch: ' + relative)


def materialize_native_labels(game, destination, package, doc, child):
    if doc is None:
        return
    verify_native_base(game, doc, child)
    raw = child(package, PAYLOAD).read_bytes()
    if digest(raw) != doc['sha256']:
        raise ValueError('Native label payload changed during installation')
    out = child(destination, PAYLOAD)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Refuse collisions instead of overwriting an untracked module.
    with out.open('xb') as stream:
        stream.write(raw)


def render_launcher(source, doc, *, persistent=False):
    if digest(source) != BASE_HASHES['FGO_Launcher.ps1']:
        raise ValueError('Native label launcher source changed')
    text = source.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in text else '\n'
    anchor = '    $launchArguments.Add($chineseHookPath)'
    environment = '        $injectStartInfo.EnvironmentVariables["FGO_LOCALIZATION_LANGUAGE"] = $gameLanguage'
    if text.count(anchor) != 1 or text.count(environment) != 1:
        raise ValueError('Native label launch insertion point is ambiguous')
    # No caller-controlled shell text or executable path is interpolated.
    expected = doc['sha256']
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('Invalid native module hash')
    addition = newline.join([
        '',
        '    # Project-owned Korean label bridge, loaded before game entry.',
        '    if ($gameLanguage -ne "ko-fgoac") { throw "Korean label language changed" }',
        "    $koLabelPath = Join-Path $PSScriptRoot 'Localization\\ko-fgoac\\native\\fgoko-labels.dll'",
        "    if ((Get-FileHash -LiteralPath $koLabelPath -Algorithm SHA256).Hash -ne '" + expected + "') { throw 'Korean label module changed' }",
        '    $launchArguments.Add("-k")',
        '    $launchArguments.Add($koLabelPath)',
        '    Write-Output "[ko-labels] Startup module queued; in-game display is unverified."',
    ])
    text = text.replace(anchor, anchor + addition)
    text = text.replace(environment, environment + newline +
                        '        $injectStartInfo.EnvironmentVariables["FGO_KO_LABELS_ENABLE"] = "1"')
    if persistent:
        language_anchor = "$useLocalization = $gameLanguage -ne 'ja'"
        if text.count(language_anchor) != 1:
            raise ValueError('Platform language insertion point is ambiguous')
        text = text.replace(language_anchor, newline.join([
            '# The platform exposes the built-in Korean choice as ko.',
            'if ($gameLanguage -eq "ko") { $gameLanguage = "ko-fgoac" }',
            language_anchor,
        ]))
        # The ordinary platform keeps its original entrypoint. Other languages
        # remain usable; only Korean sessions load the project-owned module.
        text = text.replace(
            '    if ($gameLanguage -ne "ko-fgoac") { throw "Korean label language changed" }',
            '    if ($gameLanguage -eq "ko-fgoac") {')
        text = text.replace(
            '    Write-Output "[ko-labels] Startup module queued; in-game display is unverified."',
            '    Write-Output "[ko-labels] Startup module queued."' + newline + '    }')
        text = text.replace(
            '        $injectStartInfo.EnvironmentVariables["FGO_KO_LABELS_ENABLE"] = "1"',
            '        $injectStartInfo.EnvironmentVariables["FGO_KO_LABELS_ENABLE"] = $(if ($gameLanguage -eq "ko-fgoac") { "1" } else { "0" })')
    return text.encode('utf-8-sig')


@contextmanager
def temporary_launcher(target, doc, child):
    app = child(target, 'App')
    source_path = child(app, 'FGO_Launcher.ps1')
    if doc is None:
        yield source_path
        return
    verify_native_base(target, doc, child)
    dll = child(app, 'Localization/ko-fgoac/' + PAYLOAD)
    if digest(dll.read_bytes()) != doc['sha256']:
        raise ValueError('Installed Korean label module changed')
    content = render_launcher(source_path.read_bytes(), doc)
    fd, filename = tempfile.mkstemp(prefix='.ko-fgoac-launch-', suffix='.ps1', dir=app)
    generated = Path(filename)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        yield generated
    finally:
        # Remove only our exact file, never an unexpected replacement.
        checked = child(app, generated.name)
        if checked.exists():
            if digest(checked.read_bytes()) != digest(content):
                raise ValueError('Temporary launcher changed; retained for inspection: ' + str(checked))
            checked.unlink()


LAUNCH_WRAPPER = '''param([Parameter(Mandatory=$true)][string]$Target)
$ErrorActionPreference = 'Stop'
& python -B (Join-Path $PSScriptRoot 'tools/ko_patch.py') launch --package $PSScriptRoot --target $Target
exit $LASTEXITCODE
'''
