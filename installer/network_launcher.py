"""Bounded AST network adapter; source is parsed as data, never executed.

Profiles cover the reviewed complete network sections and every network use,
INI consumer, process lifetime statement and dynamic mutation statement.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
from functools import lru_cache
import network_compat
import launcher_profiles

# Generated from the local, read-only references by the fixed AST extractor.
# No original game launcher source is redistributed here.
PROFILES = {'chinese-current': {'section': 'f7f99f23facfc4c2991a9ac94c2452231679c7fc94839dc504c813687a612aa2', 'facts': 'f34c9df7a1c3d112f036398570cc04548265127eecd21403141409e4817ec9e7', 'ini': '66252f9ca66737209b693c4f5a995cd1f10ec04c6cfb7b069fb0ac3b7f712256', 'lifecycle': '122e5eda006952bee19fe7b3abe4382c03c9d9d0db4a936e34ba8d8d8e3fd6e3', 'danger': 'c97d5cfdf44c5bcf35c188b8224ca1d041cbe0b7177f50810a9fe2803062648e', 'dependencies': '4e0b0bb4a0b37768cb4ca90219308feb4f7b73bb476b19ecbed5fc4341059632'}, 'multilingual-current': {'section': '3d487419cef0addda9e1860acf7a07949f5c57e91ac4035eea7bfdf4f29c1371', 'facts': 'acb6d1a6dcb47bb8be3ab1d8d50d1cd755bbfb4d27b6c84464984bb8d5918053', 'ini': '5e418eee93cd22b47f09b976220b44c5eb112df8049e168c8c77c8943478aa5b', 'lifecycle': '7595f6bb5c231b3251cc928b19ce9df5a5cb578a69b0014eb6b49d8e260fe5be', 'danger': '85ad7fa0fe7e084d31950d123d6e80cf87f582d8cf8fa27cc0eabb7a8df8c2c2', 'dependencies': '4e0b0bb4a0b37768cb4ca90219308feb4f7b73bb476b19ecbed5fc4341059632'}, 'physical-102': {'section': 'bd515420822a26ed7a1826935ea92f068413daf0fcbda918d97b232b819d6186', 'facts': 'e32c97fb9c207495f2727afb1ab08812a1a24bb9bee7c6a1f4a4c04f6c8a86d7', 'ini': '5d8bb0ee7a5d123e78259b571df5251c9a7119b58d4f30cb49adfa8a4c148df0', 'lifecycle': '122e5eda006952bee19fe7b3abe4382c03c9d9d0db4a936e34ba8d8d8e3fd6e3', 'danger': '4a4e1b09db8a57eda441f2a31f2534d3f4cf7118ad50dcec7ba0dc576b1aac87', 'dependencies': '296a9cf3d6aa6b5a913f2ed2d00cf41d022b88e0dd37ee89309a4fcb46e3e6d4'}}


def decode(raw):
    if raw.startswith(b'\xff\xfe'):
        return raw[2:].decode('utf-16le'), 'utf-16le', b'\xff\xfe'
    if raw.startswith(b'\xfe\xff'):
        return raw[2:].decode('utf-16be'), 'utf-16be', b'\xfe\xff'
    bom=b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b''
    return raw[len(bom):].decode('utf-8'), 'utf-8', bom


@lru_cache(maxsize=24)
def analyze(text):
    parser=Path(__file__).with_name('network_analysis.ps1').read_text(encoding='utf-8-sig')
    exe=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(exe),'-NoProfile','-NonInteractive','-EncodedCommand',
                           base64.b64encode(parser.encode('utf-16le')).decode('ascii')],
                          input=json.dumps({'source':text}).encode('ascii'),capture_output=True,
                          timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('[NETWORK:PARSER] Static network analysis failed.')
    data=json.loads(result.stdout.decode('utf-8-sig'))
    if data['errors']:
        raise ValueError('[NETWORK:SYNTAX] '+','.join(data['errors']))
    return data


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=True,separators=(',',':')).encode('ascii')).hexdigest()


def shape(data):
    section=data['section']
    if not section:
        raise ValueError('[NETWORK:SECTION_UNPROVEN] Network block boundaries are ambiguous.')
    return dict(section=digest([section['context'],section['signatures']]),
                **{key:digest([[row['role'],row['signature'],row['context']] for row in data[key]])
                   for key in ('facts','ini','lifecycle','danger','dependencies')})


def select(data):
    actual=shape(data)
    for name,expected in PROFILES.items():
        if actual==expected:
            return name
    raise ValueError('[NETWORK:FLOW_UNPROVEN] Network section, consumers, mutation or synchronous process lifetime differs from the reviewed profiles.')


def index(text,offset):
    return len(text.encode('utf-16le')[:offset*2].decode('utf-16le'))


def _initialization(nl):
    pins={Path(rel).name:hashlib.sha256(raw).hexdigest() for rel,raw in network_compat.helper_files().items()}
    lines=['# FGOAC-KO managed network policy v1; helper bytes are pinned.',
           '$fgoKoNetwork = $null', '$fgoKoNetworkEnvironment = @{}',
           "foreach ($fgoKoNetworkName in @('FGO_LOCAL_NETWORK','FGO_INSTALL_ROOT','FGO_LOCAL_HTTP_PORT','FGO_LOCAL_BILLING_PORT','FGO_LOCAL_AIME_PORT')) {",
           '    $fgoKoNetworkEnvironment[$fgoKoNetworkName] = [Environment]::GetEnvironmentVariable($fgoKoNetworkName,\'Process\')','}',
           'try {']
    for name,sha in pins.items():
        lines += ["    $fgoKoNetworkHelper = Join-Path $PSScriptRoot '"+name+"'",
                  "    if ((Get-FileHash -LiteralPath $fgoKoNetworkHelper -Algorithm SHA256).Hash -ne '"+sha+"') { throw '[NETWORK_HELPER_CHANGED] Managed network helper changed.' }"]
    lines += ["    . (Join-Path $PSScriptRoot 'FGO_KoNetwork.ps1')",
              "    if ((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'fgohook.dll') -Algorithm SHA256).Hash -ne '"+network_compat.NATIVE_HOOK_SHA256+"') { throw '[NETWORK_NATIVE_HOOK_UNVERIFIED] Network native ABI differs from the inspected version.' }",
              "    $fgoKoNetworkServerHashes = @{ 'FGO_KoServerConfig.py'='"+pins['FGO_KoServerConfig.py']+"'; 'FGO_KoServerCleanup.ps1'='"+pins['FGO_KoServerCleanup.ps1']+"' }",
              "    $fgoKoNetworkPorts = @{ http=80; billing=8443; aime=22345 }",
              '    if ($null -ne $launcherConfig.serverPorts) {',
              "        if ($launcherConfig.serverPorts -isnot [Collections.IDictionary] -and $launcherConfig.serverPorts -isnot [pscustomobject]) { throw '[NETWORK_PORT_INVALID] serverPorts must be an object containing http, billing and aime.' }",
              '        $fgoKoNetworkPorts = $launcherConfig.serverPorts', '    }',
              "    $fgoKoNetworkMode = 'Auto'",
              '    if (-not [string]::IsNullOrWhiteSpace([string]$launcherConfig.serverMode)) { $fgoKoNetworkMode = [string]$launcherConfig.serverMode }',
              '    $fgoKoNetwork = Get-FgoKoNetworkPlan -ServerHost ([string]$launcherConfig.serverHost) -Mode $fgoKoNetworkMode -Ports $fgoKoNetworkPorts',
              '    $fgoKoNetworkRequiredPorts = @()',
              '    if ($null -ne $launcherConfig.requiredPorts) {',
              '        foreach ($fgoKoNetworkRequired in @($launcherConfig.requiredPorts)) {',
              '            $fgoKoNetworkRequiredPort = 0',
              "            if (-not [int]::TryParse([string]$fgoKoNetworkRequired,[ref]$fgoKoNetworkRequiredPort) -or $fgoKoNetworkRequiredPort -lt 1 -or $fgoKoNetworkRequiredPort -gt 65535) { throw '[NETWORK_PORT_INVALID] Invalid required service port.' }",
              '            $fgoKoNetworkRequiredPorts += $fgoKoNetworkRequiredPort', '        }', '    }',
              "    if ($fgoKoNetwork.Local -and $null -eq $launcherConfig.serverPorts -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\\Server\\artemis\\config\\core.yaml') -PathType Leaf)) {",
              '        $fgoKoNetworkSettings = Get-FgoKoLocalServerSettings -InstallRoot (Split-Path -Parent $PSScriptRoot) -HelperHashes $fgoKoNetworkServerHashes',
              '        $fgoKoNetworkPorts = @{ http=$fgoKoNetworkSettings.http; billing=$fgoKoNetworkSettings.billing; aime=$fgoKoNetworkSettings.aime }',
              '        $fgoKoNetwork = Get-FgoKoNetworkPlan -ServerHost ([string]$launcherConfig.serverHost) -Mode $fgoKoNetworkMode -Ports $fgoKoNetworkPorts', '    }',
              '    if (-not $fgoKoNetwork.Local) {',
              '        $fgoKoNetworkLiteral = $null',
              '        $fgoKoNetworkIsLiteral = [Net.IPAddress]::TryParse($fgoKoNetwork.RequestedHost,[ref]$fgoKoNetworkLiteral)',
              '        $fgoKoNetworkV4 = @($fgoKoNetwork.Destinations | Where-Object { $_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork })',
              '        if (($fgoKoNetworkIsLiteral -and $fgoKoNetworkLiteral.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) -or $fgoKoNetworkV4.Count -eq 0) {',
              "            throw '[NETWORK_ENDPOINT_IDENTITY_UNVERIFIED] The specified hostname/IPv6 endpoint is preserved. Game DNS, HTTP Host and TLS identity through the IPv6 relay require additional protocol verification; no local substitution was attempted.'",'        }',
              '        $fgoKoNetwork.Destinations = $fgoKoNetworkV4','    }',
              '    if (-not $CheckOnly) {',
              '        if ($fgoKoNetwork.Local -and [bool]$launcherConfig.autoStartLocalServer) {',
              "            $fgoKoNetworkExpectedStart = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\\Server\\Start-FGOLocalServer.ps1'))",
              '            if (-not [string]::IsNullOrWhiteSpace([string]$launcherConfig.localServerLauncher)) {',
              '                $fgoKoNetworkConfiguredStart = [string]$launcherConfig.localServerLauncher',
              '                if (-not [IO.Path]::IsPathRooted($fgoKoNetworkConfiguredStart)) { $fgoKoNetworkConfiguredStart = Join-Path $PSScriptRoot $fgoKoNetworkConfiguredStart }',
              '                $fgoKoNetworkConfiguredStart = [IO.Path]::GetFullPath($fgoKoNetworkConfiguredStart)',
              "                if (-not [String]::Equals($fgoKoNetworkExpectedStart,$fgoKoNetworkConfiguredStart,[StringComparison]::OrdinalIgnoreCase)) { throw '[NETWORK_LOCAL_STARTUP_CUSTOM_UNVERIFIED] The explicit local startup script is preserved; a managed adapter has not been verified for it.' }",'            }',
              '            # Reusing a service does not confer process ownership. Do not',
              '            # schedule the old installation-wide game/server stop watcher.',
              '            # The managed watcher only cleans its private configuration',
              '            # after the identified server process exits normally.',
              '            $fgoKoNetworkLocalServer = Invoke-FgoKoLocalServer -InstallRoot (Split-Path -Parent $PSScriptRoot) -Ports $fgoKoNetwork.Ports -HelperHashes $fgoKoNetworkServerHashes','        }',
              '        $null = Start-FgoKoNetworkSession -Plan $fgoKoNetwork -NativeLocalHookSupported',
              '        foreach ($fgoKoNetworkRequiredPort in $fgoKoNetworkRequiredPorts) {',
              '            $fgoKoNetworkResult = Test-FgoKoEndpoint -Address $fgoKoNetwork.Session.ActualAddress -Port $fgoKoNetworkRequiredPort',
              "            if ($fgoKoNetworkResult -ne 'NETWORK_READY') { throw ('[NETWORK_REQUIRED_SERVICE_FAILED] ' + $fgoKoNetworkResult) }",'        }','    }',
              '    $network = $fgoKoNetwork',
              '    $localIp = $network.Cabinet', '    $subnet = $network.Subnet',
              '    $addrSuffix = $network.AddressSuffix', '    $routerSuffix = $network.RouterSuffix',
              '    $broadcast = $network.Broadcast', '    $serverHost = $network.Server',
              "    $env:FGO_LOCAL_NETWORK = if ($network.RuntimeLocalNetwork) { '1' } else { '0' }",
              '    $env:FGO_INSTALL_ROOT = Split-Path -Parent $PSScriptRoot',
              '    $fgoKoNetworkActualPorts = $network.Ports',
              '    if ($network.Session) { $fgoKoNetworkActualPorts = $network.Session.Ports }',
              '    $env:FGO_LOCAL_HTTP_PORT = [string]$fgoKoNetworkActualPorts.http',
              '    $env:FGO_LOCAL_BILLING_PORT = [string]$fgoKoNetworkActualPorts.billing',
              '    $env:FGO_LOCAL_AIME_PORT = [string]$fgoKoNetworkActualPorts.aime',
              '    $fgoKoNetworkGamePorts = $network.Ports',
              '    if ($network.RuntimeLocalNetwork) { $fgoKoNetworkGamePorts = $network.GamePorts }',
              "    Write-Host '[NETWORK] Physical address/adapter restrictions: none; destination and service checks are active.'"]
    return nl.join(lines)


def render(original, language_patched):
    before,_,_=decode(original)
    text,encoding,bom=decode(language_patched)
    # Network equivalence alone cannot prove quoting or injection argument flow.
    # Validate the original through the independent launch-flow parser as well.
    launcher_profiles.select(before)
    profile=select(analyze(before))
    data=analyze(text)
    if select(data)!=profile:
        raise ValueError('[NETWORK:LANGUAGE_PATCH_CONFLICT] Language patch changed the verified network flow.')
    nl='\r\n' if '\r\n' in text else '\n'
    edits=[(data['section']['start'],data['section']['end'],_initialization(nl))]
    for row in data['ini']:
        service={'ini:dns:startupPort':'http','ini:dns:billingPort':'billing','ini:dns:aimedbPort':'aime'}.get(row['role'])
        if service:
            key={'http':'startupPort','billing':'billingPort','aime':'aimedbPort'}[service]
            value='Set-IniValue -Path $runtimeIni -Section "dns" -Key "'+key+'" -Value ([string]$fgoKoNetworkGamePorts.'+service+')'
            edits.append((row['start'],row['end'],value))
    if profile=='physical-102':
        default=next(row for row in data['ini'] if row['role']=='ini:dns:default')
        additions=nl+'Set-IniValue -Path $runtimeIni -Section "netenv" -Key "enable" -Value "1"'+nl+'Set-IniValue -Path $runtimeIni -Section "netenv" -Key "routerSuffix" -Value ([string]$routerSuffix)'
        edits.append((default['end'],default['end'],additions))
    # The entire suffix is inside this finally, including synchronous WaitForExit,
    # source catch/cleanup and exit. Exit also executes PowerShell finally.
    cleanup=nl+'} finally {'+nl+'    try {'+nl+'        if ($fgoKoNetwork) { Stop-FgoKoNetworkSession -Plan $fgoKoNetwork }'+nl+'    } finally {'+nl+'        foreach ($fgoKoNetworkName in $fgoKoNetworkEnvironment.Keys) {'+nl+"            [Environment]::SetEnvironmentVariable($fgoKoNetworkName,$fgoKoNetworkEnvironment[$fgoKoNetworkName],'Process')"+nl+'        }'+nl+'    }'+nl+'}'+nl
    edits.append((data['rootEnd'],data['rootEnd'],cleanup))
    for start,end,value in sorted(edits,reverse=True):
        a,b=index(text,start),index(text,end)
        text=text[:a]+value+text[b:]
    final=analyze(text)
    if (not final['cleanupCoverage'] or any(not item['covered'] for item in final['cleanupCoverage'])
            or not any(item['member'].lower()=='waitforexit' for item in final['cleanupCoverage'])):
        raise ValueError('[NETWORK:POSTCHECK] Network cleanup does not enclose synchronous game lifetime.')
    raw=bom+text.encode(encoding)
    metadata=dict(format=1,profile=profile,policy=network_compat.POLICY,
                  source_sha256=hashlib.sha256(original).hexdigest(),
                  language_sha256=hashlib.sha256(language_patched).hexdigest(),
                  installed_sha256=hashlib.sha256(raw).hexdigest(),
                  files={rel:hashlib.sha256(blob).hexdigest() for rel,blob in network_compat.helper_files().items()},
                  native_hook_sha256=network_compat.NATIVE_HOOK_SHA256,
                  verification='static-render-and-loopback-helper; actual-game-unverified',
                  remote_hostname_ipv6='hostname-direct-ipv4-keeps-identity; ipv6-only-relay-game-protocol-unverified-explicit-diagnostic')
    return raw,metadata
