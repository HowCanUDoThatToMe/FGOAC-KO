"""Parse PowerShell without executing it; insert only at verified AST boundaries.

SPDX-License-Identifier: MIT
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
from functools import lru_cache
from common import read

PARSER = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$source = ([Console]::In.ReadToEnd() | ConvertFrom-Json).source
$tokens = $null; $errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
function Signature($node) {
    $parts = @($tokens | Where-Object {
        $_.Extent.StartOffset -ge $node.Extent.StartOffset -and $_.Extent.EndOffset -le $node.Extent.EndOffset -and
        $_.Kind.ToString() -notin @('Comment','NewLine','LineContinuation','EndOfInput')
    } | ForEach-Object {
        $kind = $_.Kind.ToString(); $value = $_.Text
        if ($kind -eq 'Variable' -or $kind -eq 'Identifier' -or $kind -eq 'Generic') { $value = $value.ToLowerInvariant() }
        if ($kind -eq 'StringLiteral' -or ($kind -eq 'StringExpandable' -and !$_.NestedTokens)) { $kind='StringConstant'; $value=$_.Value }
        [pscustomobject]@{kind=$kind; value=$value}
    })
    ConvertTo-Json -InputObject $parts -Depth 8 -Compress
}
function Context($node) {
    $parts = @(); $cursor = $node.Parent
    while ($null -ne $cursor) {
        $kind = $cursor.GetType().Name
        if ($kind -eq 'StatementBlockAst') {
            $owner = $cursor.Parent
            if ($owner -is [System.Management.Automation.Language.IfStatementAst]) {
                $branch = 'else'
                for ($i=0; $i -lt $owner.Clauses.Count; $i++) {
                    if ($owner.Clauses[$i].Item2 -eq $cursor) { $branch = 'if:' + (Signature $owner.Clauses[$i].Item1) }
                }
                $parts += $branch
            } elseif ($owner -is [System.Management.Automation.Language.TryStatementAst]) {
                if ($owner.Body -eq $cursor) { $parts += 'try' } else { $parts += 'finally' }
            } else { $parts += $owner.GetType().Name }
        } elseif ($kind -eq 'FunctionDefinitionAst' -or $kind -eq 'ScriptBlockExpressionAst' -or $kind -eq 'NamedBlockAst') {
            $parts += $kind
        }
        $cursor = $cursor.Parent
    }
    ConvertTo-Json -InputObject @($parts) -Compress
}
function Role($node) {
    if ($node -is [System.Management.Automation.Language.AssignmentStatementAst]) {
        $left = $node.Left
        if ($left -is [System.Management.Automation.Language.VariableExpressionAst]) {
            $name = $left.VariablePath.UserPath.ToLowerInvariant()
            if ($name -in @('uselocalization','launcharguments','quotedlauncharguments','injectstartinfo')) { return $name }
            if ($name -eq 'gamelanguage') { return 'gamelanguage' }
        }
        if ($left -is [System.Management.Automation.Language.MemberExpressionAst] -and $left.Expression -is [System.Management.Automation.Language.VariableExpressionAst]) {
            $var = $left.Expression.VariablePath.UserPath.ToLowerInvariant(); $member = $left.Member.Value.ToLowerInvariant()
            if ($var -eq 'injectstartinfo' -and $member -in @('useshellexecute','arguments')) { return $member }
            if ($var -eq 'injectprocess' -and $member -eq 'startinfo') { return 'startinfo' }
        }
        if ($left -is [System.Management.Automation.Language.IndexExpressionAst] -and $left.Target -is [System.Management.Automation.Language.MemberExpressionAst]) {
            $obj = $left.Target
            if ($obj.Expression -is [System.Management.Automation.Language.VariableExpressionAst] -and
                $obj.Expression.VariablePath.UserPath -ieq 'injectStartInfo' -and $obj.Member.Value -ieq 'EnvironmentVariables' -and
                $left.Index -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
                $left.Index.Value -ieq 'FGO_LOCALIZATION_LANGUAGE') { return 'language_environment' }
        }
    }
    if ($node -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and
        $node.Expression -is [System.Management.Automation.Language.VariableExpressionAst] -and
        $node.Expression.VariablePath.UserPath -ieq 'launchArguments' -and $node.Member.Value -ieq 'Add' -and $node.Arguments.Count -eq 1 -and
        $node.Arguments[0] -is [System.Management.Automation.Language.VariableExpressionAst]) {
        $arg = $node.Arguments[0].VariablePath.UserPath.ToLowerInvariant()
        if ($arg -eq 'chinesehookpath') { return 'hook' }
        if ($arg -eq 'agopath') { return 'game_argument' }
    }
    return $null
}
$nodes = @($tree.FindAll({param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -or $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst]}, $true) | ForEach-Object {
    $role = Role $_
    if ($role) {
        $stmt = $_
        while ($stmt.Parent -and $stmt.Parent -isnot [System.Management.Automation.Language.StatementBlockAst] -and $stmt.Parent -isnot [System.Management.Automation.Language.NamedBlockAst]) { $stmt = $stmt.Parent }
        [pscustomobject]@{role=$role; signature=(Signature $_); context=(Context $_); start=$_.Extent.StartOffset; end=$_.Extent.EndOffset; statementStart=$stmt.Extent.StartOffset; statementEnd=$stmt.Extent.EndOffset; statementSignature=(Signature $stmt)}
    }
})
$problems = @($errors | ForEach-Object { [pscustomobject]@{line=$_.Extent.StartLineNumber; column=$_.Extent.StartColumnNumber; id=$_.ErrorId} })
if (@($tokens | Where-Object { $_.Kind.ToString() -ne 'Comment' -and $_.Text -match '(?i)FGO_KO_LABELS_ENABLE|fgoko-labels\.dll|ko-fgoac' }).Count) {
    $nodes += [pscustomobject]@{role='unmanaged_patch_marker'}
}
[pscustomobject]@{errors=$problems; nodes=$nodes} | ConvertTo-Json -Depth 12 -Compress
'''


@lru_cache(maxsize=16)
def inspect_source(text):
    """Only our constant parser script executes. Input travels as stdin JSON data."""
    if len(text.encode('utf-8')) > 2 * 1024 * 1024:
        raise ValueError('런처 파일이 지원 크기를 넘습니다: App/FGO_Launcher.ps1 (최대 2 MiB). 원본과 버전을 확인해 주세요.')
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    executable = system/'System32/WindowsPowerShell/v1.0/powershell.exe'
    if not executable.is_file():
        raise ValueError('Windows PowerShell 문법 검사기를 찾을 수 없습니다. Windows 구성 요소를 확인해 주세요. 실행 정책 변경은 필요하지 않습니다.')
    command = base64.b64encode(PARSER.encode('utf-16-le')).decode('ascii')
    try:
        result = subprocess.run([str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', command],
            input=json.dumps(dict(source=text), ensure_ascii=True).encode('ascii'), capture_output=True,
            timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise ValueError('런처 문법 검사 시간이 초과되었습니다: App/FGO_Launcher.ps1. 파일 크기와 내용을 확인해 주세요.') from None
    if result.returncode:
        raise ValueError('PowerShell 문법 검사기를 실행하지 못했습니다. App/FGO_Launcher.ps1은 변경하지 않았습니다. 오류 기록과 Windows 환경을 제보해 주세요.')
    try:
        answer = json.loads(result.stdout.decode('utf-8-sig'))
    except (UnicodeError, ValueError):
        raise ValueError('PowerShell 문법 검사 결과를 읽지 못했습니다. 런처를 변경하지 않았습니다.') from None
    if answer['errors']:
        problem = answer['errors'][0]
        raise ValueError('런처 문법 오류: App/FGO_Launcher.ps1, %s행 %s열 (%s). 해당 위치를 수정하거나 정상 백업을 복원해 주세요.' %
                         (problem['line'], problem['column'], problem['id']))
    return answer['nodes']


def fingerprint(node):
    return hashlib.sha256(json.dumps([json.loads(node[k]) for k in ('signature','context','statementSignature')],
                         ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()


def decode(source):
    try:
        if source.startswith((b'\xff\xfe', b'\xfe\xff')):
            raise ValueError('런처 인코딩 미지원: App/FGO_Launcher.ps1. UTF-8 형식으로 저장해 주세요.')
        return source.decode('utf-8-sig')
    except UnicodeError:
        raise ValueError('런처 인코딩 오류: App/FGO_Launcher.ps1. UTF-8 형식의 정상 파일을 사용해 주세요.') from None


@lru_cache(maxsize=8)
def _strict_compatible(source):
    text = decode(source)
    nodes = inspect_source(text)
    if any(n['role'] == 'unmanaged_patch_marker' for n in nodes):
        raise ValueError('관리 기록 없는 한글 런처 수정이 발견되었습니다: App/FGO_Launcher.ps1. 기존 패치 관리 도구로 복구한 뒤 다시 설치해 주세요.')
    contract = read(Path(__file__).with_name('launcher-contract.json'))
    selected = {}
    for role, expected in contract['roles'].items():
        matches = [n for n in nodes if n['role'] == role]
        # injectStartInfo also has a cleanup assignment to null in some releases;
        # the contract explicitly counts every assignment, including that case.
        if len(matches) != len(expected):
            raise ValueError('런처 연결 위치 누락 또는 중복: App/FGO_Launcher.ps1 (%s). 지원 런처와 비교하거나 이 파일을 제보해 주세요.' % role)
        if [fingerprint(n) for n in matches] != expected:
            raise ValueError('런처 실행 구조 호환성 미확인: App/FGO_Launcher.ps1 (%s). 필요한 연결 부분이 달라 원본을 유지합니다. 런처 버전을 제보해 주세요.' % role)
        selected[role] = matches[0]
    ordering = contract['order']
    if [selected[r]['start'] for r in ordering] != sorted(selected[r]['start'] for r in ordering):
        raise ValueError('런처 연결 순서가 다릅니다: App/FGO_Launcher.ps1. 지원 런처 버전을 확인해 주세요.')
    return text, selected


@lru_cache(maxsize=8)
def compatible(source):
    text = decode(source)
    nodes = inspect_source(text)
    if any(n['role'] == 'unmanaged_patch_marker' for n in nodes):
        raise ValueError('관리 기록 없는 한글 런처 수정이 발견되었습니다. 원본을 복원해 주세요.')
    # The old anchor contract proves insertion positions only. Every custom
    # launcher must also prove its complete bounded execution-flow profile.
    from launcher_profiles import select
    profile, data = select(text)
    return text, {'profile': profile, 'analysis': data}


def index_from_utf16(text, offset):
    # PowerShell offsets count UTF-16 code units, unlike Python Unicode indexes.
    return len(text.encode('utf-16-le')[:offset*2].decode('utf-16-le'))


def render(source, descriptor):
    text, nodes = compatible(source)
    if 'profile' in nodes:
        from launcher_profiles import legacy_render
        return legacy_render(source, descriptor, nodes['profile'], nodes['analysis'])
    expected = descriptor['sha256']
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('한글 모듈 해시 형식이 올바르지 않습니다.')
    nl = '\r\n' if '\r\n' in text else '\n'
    edits = [
        (nodes['uselocalization']['statementStart'], 'if ($gameLanguage -eq "ko") { $gameLanguage = "ko-fgoac" }'+nl),
        (nodes['hook']['statementEnd'], nl+nl.join([
            '    # FGOAC-KO managed startup bridge.',
            '    if ($gameLanguage -eq "ko-fgoac") {',
            "        $koLabelPath = Join-Path $PSScriptRoot 'Localization\\ko-fgoac\\native\\fgoko-labels.dll'",
            "        if ((Get-FileHash -LiteralPath $koLabelPath -Algorithm SHA256).Hash -ne '"+expected+"') { throw 'Korean label module changed' }",
            '        $launchArguments.Add("-k")',
            '        $launchArguments.Add($koLabelPath)',
            '        Write-Output "[ko-labels] Startup module queued."',
            '    }',
        ])+nl),
        (nodes['language_environment']['statementEnd'], nl+'        $injectStartInfo.EnvironmentVariables["FGO_KO_LABELS_ENABLE"] = $(if ($gameLanguage -eq "ko-fgoac") { "1" } else { "0" })'+nl),
    ]
    for offset, addition in sorted(edits, reverse=True):
        index = index_from_utf16(text, offset)
        text = text[:index]+addition+text[index:]
    inspect_source(text)  # Parse the final result before any game file write.
    # Preserve UTF-8 BOM presence and all original bytes outside insertions.
    return (b'\xef\xbb\xbf' if source.startswith(b'\xef\xbb\xbf') else b'')+text.encode('utf-8')
