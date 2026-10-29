param([Parameter(Mandatory=$true)][int]$OwnerProcessId,
      [Parameter(Mandatory=$true)][long]$OwnerStartTicks,
      [Parameter(Mandatory=$true)][string]$Python,
      [Parameter(Mandatory=$true)][string]$ConfigHelper,
      [Parameter(Mandatory=$true)][string]$SessionConfig,
      [Parameter(Mandatory=$true)][string]$ManifestSha256,
      [Parameter(Mandatory=$true)][string]$ConfigHelperSha256)
$ErrorActionPreference='Stop'
try {
    $Python=[IO.Path]::GetFullPath($Python)
    $ConfigHelper=[IO.Path]::GetFullPath($ConfigHelper)
    $SessionConfig=[IO.Path]::GetFullPath($SessionConfig)
    $serverRoot=[IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($Python))
    $expectedHelper=Join-Path ([IO.Path]::GetDirectoryName($serverRoot)) 'App\FGO_KoServerConfig.py'
    if ([IO.Path]::GetFileName($Python) -ne 'python.exe' -or
        -not [String]::Equals($ConfigHelper,$expectedHelper,[StringComparison]::OrdinalIgnoreCase) -or
        -not [String]::Equals([IO.Path]::GetDirectoryName($SessionConfig),(Join-Path $serverRoot 'state'),[StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($SessionConfig) -notmatch '^\.ko-network-[0-9a-f]{32}$' -or
        $ConfigHelperSha256 -notmatch '^[0-9a-f]{64}$' -or $ManifestSha256 -notmatch '^[0-9a-f]{64}$') { throw 'OWNER' }
    foreach ($path in @($Python,$ConfigHelper,$SessionConfig)) {
        $entry=$path
        while ($entry) {
            $item=Get-Item -LiteralPath $entry -Force -ErrorAction SilentlyContinue
            if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw 'PATH'}
            $entry=[IO.Path]::GetDirectoryName($entry)
        }
    }
    $owner=Get-Process -Id $OwnerProcessId -ErrorAction SilentlyContinue
    if ($owner -and $owner.StartTime.ToUniversalTime().Ticks -eq $OwnerStartTicks -and
        [String]::Equals($owner.Path,$Python,[StringComparison]::OrdinalIgnoreCase)) {
        $owner.WaitForExit()
    }
    # Recheck after waiting: never execute a changed helper on delayed cleanup.
    $raw=[IO.File]::ReadAllBytes($ConfigHelper)
    $hash=[Security.Cryptography.SHA256]::Create()
    try {$actual=([BitConverter]::ToString($hash.ComputeHash($raw))).Replace('-','').ToLowerInvariant()}
    finally {$hash.Dispose()}
    if ($actual -ne $ConfigHelperSha256) {throw 'HASH'}
    # Execute the exact checked bytes, without changing execution policy or
    # allowing a later path replacement to choose different Python source.
    $encoded=[Convert]::ToBase64String($raw)
    $code="import base64;exec(compile(base64.b64decode('"+$encoded+"'),'<managed-network-config>','exec'))"
    $args=@('-c',$code,'cleanup',$SessionConfig,$ManifestSha256)
    $quoted=@($args|ForEach-Object {
        $value=[Regex]::Replace([string]$_,'(\\*)"','${1}${1}\"')
        $value=[Regex]::Replace($value,'(\\+)$','${1}${1}')
        '"'+$value+'"'
    })
    $info=[Diagnostics.ProcessStartInfo]::new()
    $info.FileName=$Python;$info.Arguments=$quoted -join ' ';$info.UseShellExecute=$false;$info.CreateNoWindow=$true
    if ($info.Arguments.Length -gt 30000) {throw 'LIMIT'}
    $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $process=[Diagnostics.Process]::Start($info)
    try {
        $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) {$process.Kill();$process.WaitForExit(1000)|Out-Null;throw 'TIMEOUT'}
        if (-not $out.Wait(1000) -or -not $err.Wait(1000)) {throw 'OUTPUT'}
        exit $process.ExitCode
    } finally {
        try {if (-not $process.HasExited) {$process.Kill();$process.WaitForExit(1000)|Out-Null}} catch {}
        $process.Dispose()
    }
} catch { exit 1 }
