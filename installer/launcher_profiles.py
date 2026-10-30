"""Bounded launch-flow profiles. Not a general PowerShell equivalence proof."""
import base64
import hashlib
import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=16)
def analyze(text):
    parser = Path(__file__).with_name('launcher_analysis.ps1').read_text('utf-8-sig')
    exe = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-EncodedCommand',
                            base64.b64encode(parser.encode('utf-16-le')).decode()],
                           input=json.dumps({'source': text}).encode('ascii'), capture_output=True,
                           timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('[LAUNCHER:PARSER] 구조 분석 실패; 원본을 변경하지 않았습니다.')
    data = json.loads(result.stdout.decode('utf-8-sig'))
    if data['errors']:
        raise ValueError('[LAUNCHER:SYNTAX] ' + json.dumps(data['errors']))
    return data


def fingerprint(row, aliases):
    signature = row['signature']
    # Only the quoted vector, explicit string[] cast and ASCII space separator.
    text = row['text'].lower()
    for actual, canonical in sorted(aliases.items(), key=lambda x: -len(x[0])):
        text = re.sub(r'\$'+re.escape(actual.lower())+r'\b', '$'+canonical, text)
    patterns = (r'\$injectstartinfo\.arguments\s*=\s*\[string\]::join\(\s*["\'] ["\']\s*,\s*\[string\[\]\]\$quotedlauncharguments\s*\)',
                r'\$injectstartinfo\.arguments\s*=\s*\[string\[\]\]\$quotedlauncharguments\s+-join\s+["\'] ["\']')
    if any(re.fullmatch(p, text.strip()) for p in patterns):
        signature = 'quoted-string-array-space-join-v1'
    return hashlib.sha256(json.dumps([signature, row['context']], ensure_ascii=True).encode()).hexdigest()


def shape(data):
    return {key: [fingerprint(row, data['aliases']) for row in data[key]]
            for key in ('facts', 'special', 'danger', 'control', 'calls')}


def select(text):
    data = analyze(text)
    profiles = json.loads(Path(__file__).with_name('launcher-profiles.json').read_text('utf-8'))
    actual = shape(data)
    candidates = []
    for profile in profiles['profiles']:
        problems = [key for key in actual if actual[key] != profile['shape'].get(key)]
        if not problems:
            return profile, data
        candidates.append(profile['id']+':'+','.join(problems))
    raise ValueError('[LAUNCHER:FLOW_UNPROVEN] 실행 관계/인용 함수/분기 또는 동적 코드가 지원 패턴과 다릅니다. '
                     + '; '.join(candidates))


def legacy_render(source, descriptor, profile, data):
    from launcher_compat import decode, index_from_utf16, inspect_source
    text = decode(source)
    if profile['id'] not in ('chinese-psi-v1', 'chinese-psi-physical-v1', 'multilingual-psi-v1', 'three-colors-psi-v1'):
        raise ValueError('Unsupported adapter')
    if any(word in text.lower() for word in ('$fgokoenabled', '$fgokolanguage')):
        raise ValueError('[LAUNCHER:CONFLICT] 프로젝트 변수 이름 충돌')
    sha = descriptor['sha256']
    if not re.fullmatch('[0-9a-f]{64}', sha):
        raise ValueError('Invalid native module hash')
    aliases = {canonical: actual for actual, canonical in data['aliases'].items()}
    si = '$'+aliases['injectstartinfo']; args = '$'+aliases['launcharguments']
    def one(role):
        rows = [r for r in data['special'] if r['role'] == role]
        if len(rows) != 1: raise ValueError('Ambiguous adapter position: '+role)
        return rows[0]
    nl = '\r\n' if '\r\n' in text else '\n'
    language = nl.join([
        '# FGOAC-KO legacy adapter: platform config selects ko; -Chinese still wins.',
        '$fgoKoLanguage = [string]$launcherConfig.gameLanguage',
        '$fgoKoEnabled = !$Chinese -and $fgoKoLanguage -in @("ko","ko-fgoac")', ''])
    module = nl+nl.join([
        '    if ($fgoKoEnabled) {',
        "        $koLabelPath = Join-Path $PSScriptRoot 'Localization\\ko-fgoac\\native\\fgoko-labels.dll'",
        "        if ((Get-FileHash -LiteralPath $koLabelPath -Algorithm SHA256).Hash -ne '"+sha+"') { throw 'Korean label module changed' }",
        '        '+args+'.Add("-k")', '        '+args+'.Add($koLabelPath)', '    }', ''])
    env = nl+nl.join([
        '        '+si+'.EnvironmentVariables["FGO_LOCALIZATION_LANGUAGE"] = $(if ($fgoKoEnabled) { "ko-fgoac" } elseif ($useChinese) { "zh" } else { "ja" })',
        '        if ($fgoKoEnabled) {',
        '            '+si+'.EnvironmentVariables["FGO_ZH_ENABLED"] = "0"',
        '        }',
        '        '+si+'.EnvironmentVariables["FGO_KO_LABELS_ENABLE"] = $(if ($fgoKoEnabled) { "1" } else { "0" })', ''])
    if profile['id'] in ('chinese-psi-v1', 'chinese-psi-physical-v1'):
        chinese, hook, condition, environment = [one(r) for r in ('chinese','hook','hook_condition','zh_environment')]
        edits = [(chinese['start'], chinese['start'], language),
                 (condition['start'], condition['end'], '('+condition['text']+') -or $fgoKoEnabled'),
                 (hook['end'], hook['end'], module),
                 (environment['end'], environment['end'], env)]
    else:
        localization, hook, environment = [one(r) for r in ('localization','hook','language_environment')]
        edits = [(localization['start'], localization['start'],
                  'if ($gameLanguage -eq "ko") { $gameLanguage = "ko-fgoac" }'+nl),
                 (hook['end'], hook['end'], module.replace('$fgoKoEnabled', '$gameLanguage -eq "ko-fgoac"')),
                 (environment['end'], environment['end'], nl+'        '+si+'.EnvironmentVariables["FGO_KO_LABELS_ENABLE"] = $(if ($gameLanguage -eq "ko-fgoac") { "1" } else { "0" })'+nl)]
    for start,end,replacement in sorted(edits, reverse=True):
        a,b=index_from_utf16(text,start),index_from_utf16(text,end)
        text=text[:a]+replacement+text[b:]
    inspect_source(text)
    final = analyze(text)
    if len([r for r in final['special'] if r['role']=='language_environment']) != 1:
        raise ValueError('[LAUNCHER:POSTCHECK] 언어 연결 결과 불일치')
    return (b'\xef\xbb\xbf' if source.startswith(b'\xef\xbb\xbf') else b'')+text.encode('utf-8')
