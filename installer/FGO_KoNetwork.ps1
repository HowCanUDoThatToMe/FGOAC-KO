# Project-owned process-scoped network policy. Windows PowerShell 5.1.
# Dot-source this helper; no network activity occurs until an explicit call.
function Get-FgoKoNetworkPlan {
    [CmdletBinding()]
    param([string]$ServerHost = 'auto',
          [ValidateSet('Auto','Local','Remote')][string]$Mode = 'Auto',
          $Ports = @{ http=80; billing=8443; aime=22345 },
          [scriptblock]$Resolver = {
              param($Name)
              $pending=[Net.Dns]::BeginGetHostAddresses($Name,$null,$null)
              try {
                  if (-not $pending.AsyncWaitHandle.WaitOne(3000)) { throw 'DNS deadline exceeded' }
                  [Net.Dns]::EndGetHostAddresses($pending)
              } finally { $pending.AsyncWaitHandle.Close() }
          })
    $hostName = $ServerHost.Trim()
    $localNames = @('','auto','local','localhost','127.0.0.1','::1')
    $isLocalName = $hostName -in $localNames
    if ($Mode -eq 'Remote' -and $hostName -in @('','auto','local')) {
        throw '[NETWORK_REMOTE_DESTINATION_REQUIRED] Specify the intended server.'
    }
    if ($Mode -eq 'Local' -and -not $isLocalName) {
        throw '[NETWORK_LOCAL_REMOTE_CONFLICT] Local mode conflicts with the configured destination.'
    }
    $local = $Mode -eq 'Local' -or ($Mode -eq 'Auto' -and $isLocalName)
    $servicePorts = [ordered]@{}
    foreach ($name in @('http','billing','aime')) {
        $value = $Ports.$name
        $number = 0
        if (-not [int]::TryParse([string]$value,[ref]$number) -or $number -lt 1 -or $number -gt 65535) {
            throw "[NETWORK_PORT_INVALID] Invalid $name service port."
        }
        $servicePorts[$name] = $number
    }
    if (@($servicePorts.Values | Select-Object -Unique).Count -ne 3) {
        throw '[NETWORK_PORT_CONFLICT] Service ports must be distinct.'
    }
    if ($local -and $null -ne $Ports.database) {
        $databasePort=0
        if (-not [int]::TryParse([string]$Ports.database,[ref]$databasePort) -or $databasePort -lt 1 -or $databasePort -gt 65535) {throw '[NETWORK_PORT_INVALID] Invalid database service port.'}
        if ($databasePort -in @($servicePorts.Values)) {throw '[NETWORK_PORT_CONFLICT] Database and title services must be distinct.'}
        $servicePorts['database']=$databasePort
    }
    if ($local) { $addresses = @([Net.IPAddress]::Parse('127.0.0.1')) }
    else {
        if ($hostName -match '[\s/\\@?#]' -or $hostName.Length -gt 253) {
            throw '[NETWORK_CONFIG_INVALID] Expected a host name or IP literal, without a URL or credentials.'
        }
        $literal = $null
        if ([Net.IPAddress]::TryParse($hostName,[ref]$literal)) {
            if ($literal.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and $literal.ToString() -ne $hostName) {
                throw '[NETWORK_CONFIG_INVALID] IPv4 literals require four decimal octets without leading zeroes.'
            }
            $addresses = @($literal)
        }
        else {
            if ([Uri]::CheckHostName($hostName) -ne [UriHostNameType]::Dns) {
                throw '[NETWORK_CONFIG_INVALID] Invalid host name.'
            }
            try { $addresses = @(& $Resolver $hostName) }
            catch { throw '[NETWORK_DNS_FAILED] The configured destination could not be resolved.' }
        }
        $addresses = @($addresses | Where-Object {
            $_ -is [Net.IPAddress] -and
            $_.AddressFamily -in @([Net.Sockets.AddressFamily]::InterNetwork,[Net.Sockets.AddressFamily]::InterNetworkV6) -and
            -not $_.Equals([Net.IPAddress]::Any) -and -not $_.Equals([Net.IPAddress]::IPv6Any) -and
            -not $_.IsIPv6Multicast -and
            -not ($_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and $_.GetAddressBytes()[0] -ge 224)
        } | Select-Object -Unique | Select-Object -First 64)
        if ($addresses.Count -eq 0) { throw '[NETWORK_DESTINATION_INVALID] No usable destination address.' }
        # A malformed/unscoped answer must not hide another usable address for
        # the same hostname. An explicit unscoped literal still fails clearly.
        $addresses=@($addresses|Where-Object {-not $_.IsIPv6LinkLocal -or $_.ScopeId -ne 0}|Select-Object -First 8)
        if ($addresses.Count -eq 0) { throw '[NETWORK_SCOPE_REQUIRED] An IPv6 link-local destination requires its interface scope.' }
    }
    # /24 is the verified native cabinet model, never the physical NIC prefix.
    [pscustomobject]@{
        Policy = 'destination-separated-v1'; Local = $local
        Intent = $(if ($local) { 'local' } else { 'remote' })
        RequestedHost = $hostName; Destinations = $addresses; Ports = $servicePorts
        GamePorts = @{ http=80; billing=8443; aime=22345 }
        Server = $(if ($local) { '192.168.100.1' } else { $hostName })
        ProbeHost = $(if ($local) { '127.0.0.1' } else { $hostName })
        Cabinet = '192.168.100.11'; Subnet = '192.168.100.0'
        AddressSuffix = 11; RouterSuffix = 1; Broadcast = '127.0.0.1'
        RuntimeLocalNetwork = $local; Transport = $(if ($local) { 'native-loopback' } else { 'direct-ipv4-or-relay' })
        Session = $null
    }
}

function Initialize-FgoKoRelayType {
    if ('FgoKoNetwork.Relay' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Collections.Generic;
namespace FgoKoNetwork {
  // Bytes are opaque: HTTP Host, TLS SNI, certificates and authentication survive.
  public sealed class Relay : IDisposable {
    readonly TcpListener listener;
    readonly IPAddress destination;
    readonly int destinationPort;
    readonly List<TcpClient> clients = new List<TcpClient>();
    readonly Thread acceptThread;
    volatile bool stopped;
    public int Port { get { return ((IPEndPoint)listener.LocalEndpoint).Port; } }
    public string ListenAddress { get { return "127.0.0.1"; } }
    public Relay(IPAddress address, int port) {
      destination = address; destinationPort = port;
      listener = new TcpListener(IPAddress.Loopback, 0);
      listener.Start(16);
      acceptThread = new Thread(Accept); acceptThread.IsBackground = true; acceptThread.Start();
    }
    void Accept() {
      while (!stopped) {
        try {
          TcpClient incoming = listener.AcceptTcpClient();
          lock(clients) {
            if (stopped || clients.Count >= 32) { incoming.Close(); continue; }
            clients.Add(incoming);
          }
          ThreadPool.QueueUserWorkItem(delegate { Forward(incoming); });
        } catch (SocketException) { if (!stopped) Dispose(); }
          catch (ObjectDisposedException) { return; }
      }
    }
    void Forward(TcpClient incoming) {
      TcpClient outgoing = new TcpClient(destination.AddressFamily);
      try {
        lock(clients) { if (stopped) return; clients.Add(outgoing); }
        IAsyncResult connect = outgoing.BeginConnect(destination, destinationPort, null, null);
        try {
          if (!connect.AsyncWaitHandle.WaitOne(3000)) return;
          outgoing.EndConnect(connect);
        } finally { connect.AsyncWaitHandle.Close(); }
        incoming.ReceiveTimeout = outgoing.ReceiveTimeout = 30000;
        incoming.SendTimeout = outgoing.SendTimeout = 30000;
        Thread reverse = new Thread(delegate() { Pump(outgoing, incoming); });
        reverse.IsBackground = true; reverse.Start();
        Pump(incoming, outgoing);
        // A peer may half-close its request before a delayed response arrives.
        // Give the reverse pump its full idle deadline instead of truncating it.
        reverse.Join(31000);
      } catch (Exception) { /* Peer failures are surfaced by readiness/client I/O. */ }
      finally {
        incoming.Close(); outgoing.Close();
        lock(clients) { clients.Remove(incoming); clients.Remove(outgoing); }
      }
    }
    static void Pump(TcpClient source, TcpClient target) {
      try {
        byte[] buffer = new byte[16384]; int count;
        NetworkStream input = source.GetStream(), output = target.GetStream();
        while ((count = input.Read(buffer,0,buffer.Length)) > 0) output.Write(buffer,0,count);
        try { target.Client.Shutdown(SocketShutdown.Send); } catch (SocketException) { }
      } catch (Exception) { }
    }
    public void Dispose() {
      stopped = true; listener.Stop();
      lock(clients) { foreach (TcpClient client in clients) client.Close(); clients.Clear(); }
      if (Thread.CurrentThread != acceptThread) acceptThread.Join(1000);
    }
  }
}
'@
}

function Test-FgoKoEndpoint {
    param([Net.IPAddress]$Address, [int]$Port, [int]$TimeoutMilliseconds=1000,
          [switch]$HttpHealth, [string]$HostIdentity='localhost')
    if ($TimeoutMilliseconds -lt 1 -or $TimeoutMilliseconds -gt 10000) { throw '[NETWORK_TIMEOUT_INVALID]' }
    if ($Port -lt 1 -or $Port -gt 65535) {throw '[NETWORK_PORT_INVALID]'}
    if ($HostIdentity -match '[\r\n]' -or $HostIdentity.Length -gt 253) {throw '[NETWORK_CONFIG_INVALID]'}
    $client = [Net.Sockets.TcpClient]::new($Address.AddressFamily)
    $result = $null
    try {
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $result = $client.BeginConnect($Address,$Port,$null,$null)
        if (-not $result.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) { return 'NETWORK_TIMEOUT' }
        $client.EndConnect($result)
        if ($HttpHealth) {
            $client.ReceiveTimeout = $TimeoutMilliseconds; $client.SendTimeout = $TimeoutMilliseconds
            $stream = $client.GetStream()
            # Host identity is preserved for name-based services. No proxy or redirect.
            $request = [Text.Encoding]::ASCII.GetBytes("GET / HTTP/1.0`r`nHost: $HostIdentity`r`nConnection: close`r`n`r`n")
            $stream.Write($request,0,$request.Length)
            $buffer = New-Object byte[] 8192
            $response = New-Object Text.StringBuilder
            do {
                $remaining = $TimeoutMilliseconds - [int]$timer.ElapsedMilliseconds
                if ($remaining -le 0) { return 'NETWORK_SERVICE_TIMEOUT' }
                $stream.ReadTimeout = $remaining
                $count = $stream.Read($buffer,0,[Math]::Min($buffer.Length,16384-$response.Length))
                if ($count -gt 0) { [void]$response.Append([Text.Encoding]::ASCII.GetString($buffer,0,$count)) }
                $current=$response.ToString();$headerEnd=$current.IndexOf("`r`n`r`n")
                if ($headerEnd -ge 0) {
                    $header=$current.Substring(0,$headerEnd)
                    $length=[Regex]::Match($header,'(?im)^Content-Length:\s*([0-9]+)\s*$')
                    if ($length.Success) {
                        $expected=0
                        if (-not [int]::TryParse($length.Groups[1].Value,[ref]$expected) -or $expected -gt 16384) {return 'NETWORK_SERVICE_MISMATCH'}
                        if ($current.Length-$headerEnd-4 -ge $expected) {break}
                    }
                }
            } while ($count -gt 0 -and $response.Length -lt 16384)
            $text = $response.ToString()
            $separator=$text.IndexOf("`r`n`r`n")
            if ($separator -lt 0 -or $text -notmatch '^HTTP/1\.[01] 200\s' -or $text.Substring($separator+4) -notmatch 'Service OK') {
                return 'NETWORK_SERVICE_MISMATCH'
            }
        }
        return 'NETWORK_READY'
    } catch [Net.Sockets.SocketException] { return 'NETWORK_CONNECT_FAILED' }
      catch [IO.IOException] { return 'NETWORK_SERVICE_TIMEOUT' }
    finally {
        if ($result) { $result.AsyncWaitHandle.Close() }
        $client.Dispose()
    }
}

function Start-FgoKoNetworkSession {
    [CmdletBinding()]
    param($Plan, [switch]$NativeLocalHookSupported, [int]$TimeoutMilliseconds=1000,
          [int]$Attempts=3,[int]$RetryDelayMilliseconds=150,[int]$DeadlineMilliseconds=10000)
    if ($Attempts -lt 1 -or $Attempts -gt 5 -or $RetryDelayMilliseconds -lt 0 -or $RetryDelayMilliseconds -gt 1000 -or
        $DeadlineMilliseconds -lt 1 -or $DeadlineMilliseconds -gt 30000 -or $TimeoutMilliseconds -lt 1 -or $TimeoutMilliseconds -gt 10000) { throw '[NETWORK_TIMEOUT_INVALID]' }
    if ($Plan.Session) { throw '[NETWORK_SESSION_EXISTS] Stop the owned session before recalculating.' }
    if ($Plan.Local -and -not $NativeLocalHookSupported) {
        throw '[NETWORK_NATIVE_HOOK_UNVERIFIED] The local IPv4 redirect hook must be verified for this DLL.'
    }
    $selected = $null; $failures = New-Object Collections.Generic.List[string]
    $timer=[Diagnostics.Stopwatch]::StartNew();$attempted=0
    # Resolver order is retained; no physical adapter name/order or Internet probe.
    for ($attempt=0;$attempt -lt $Attempts -and -not $selected;$attempt++) {
        foreach ($address in $Plan.Destinations) {
            $ready = $true;$attempted++
            foreach ($name in @('http','billing','aime')) {
                $remaining=$DeadlineMilliseconds-[int]$timer.ElapsedMilliseconds
                if ($remaining -le 0) {$ready=$false;break}
                $timeout=[Math]::Min($TimeoutMilliseconds,$remaining)
                $check = Test-FgoKoEndpoint -Address $address -Port $Plan.Ports.$name -TimeoutMilliseconds $timeout -HttpHealth:($name -eq 'http') -HostIdentity $(if ($Plan.Local) { 'localhost' } else { $Plan.RequestedHost })
                if ($check -ne 'NETWORK_READY') { $ready=$false; if ($failures.Count -lt 32) {$failures.Add("${name}:$check")}; break }
            }
            if ($ready) { $selected=$address; break }
            if ($timer.ElapsedMilliseconds -ge $DeadlineMilliseconds) {break}
        }
        if ($timer.ElapsedMilliseconds -ge $DeadlineMilliseconds) {break}
        if (-not $selected -and $attempt -lt $Attempts-1) {Start-Sleep -Milliseconds ([Math]::Max(0,[Math]::Min($RetryDelayMilliseconds,$DeadlineMilliseconds-[int]$timer.ElapsedMilliseconds)))}
    }
    if (-not $selected) { throw ('[NETWORK_DESTINATION_UNAVAILABLE] '+($failures -join ', ')+'; no destination substitution attempted.') }
    $relays = New-Object Collections.Generic.List[object]
    $ports = [ordered]@{}; foreach ($name in @('http','billing','aime')) { $ports[$name] = $Plan.Ports.$name }
    try {
        if (-not $Plan.Local -and $selected.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6) {
            if (-not $NativeLocalHookSupported) { throw '[NETWORK_NATIVE_HOOK_UNVERIFIED] IPv6 transport requires the verified native IPv4 redirect hook.' }
            Initialize-FgoKoRelayType
            foreach ($name in @('http','billing','aime')) {
                $relay = [FgoKoNetwork.Relay]::new($selected,[int]$Plan.Ports.$name)
                $relays.Add($relay); $ports[$name]=$relay.Port
                $remaining=[Math]::Min($TimeoutMilliseconds,$DeadlineMilliseconds-[int]$timer.ElapsedMilliseconds)
                if ($remaining -le 0) {throw '[NETWORK_TIMEOUT] Relay preparation deadline exceeded.'}
                $check = Test-FgoKoEndpoint -Address ([Net.IPAddress]::Loopback) -Port $relay.Port -TimeoutMilliseconds $remaining -HttpHealth:($name -eq 'http') -HostIdentity $Plan.RequestedHost
                if ($check -ne 'NETWORK_READY') { throw "[NETWORK_RELAY_FAILED] ${name}:$check" }
            }
            $Plan.RuntimeLocalNetwork=$true; $Plan.Transport='owned-loopback-tcp-relay'
            $Plan.Server='192.168.100.1'; $Plan.ProbeHost='127.0.0.1'
        } elseif (-not $Plan.Local) {
            # DNS target is also consumed by WinHttpConnect. Keep the configured
            # hostname so HTTP Host/TLS identity is not replaced by a probe IP.
            $Plan.Server=$Plan.RequestedHost; $Plan.ProbeHost=$selected.ToString(); $Plan.Transport='direct-ipv4'
        }
        $Plan.Session = [pscustomobject]@{ Relays=$relays; Ports=$ports; ActualAddress=$selected;CandidateAttempts=$attempted }
        return $Plan.Session
    } catch { foreach ($relay in $relays) { $relay.Dispose() }; throw }
}

function Stop-FgoKoNetworkSession {
    param($Plan)
    if ($Plan -and $Plan.Session) {
        foreach ($relay in $Plan.Session.Relays) { $relay.Dispose() }
        $Plan.Session=$null
    }
}

function Get-FgoKoNetworkDiagnostic {
    param($Plan, [switch]$LocalDetails)
    # Public output never contains host/IP, file paths or credentials.
    $result = [ordered]@{ Code='NETWORK_PLAN'; Policy=$Plan.Policy; Intent=$Plan.Intent;
        Transport=$Plan.Transport; CandidateCount=@($Plan.Destinations).Count;
        PhysicalAddressRestriction=$false; Ready=($null -ne $Plan.Session) }
    if ($LocalDetails) { $result.RequestedHost=$Plan.RequestedHost; $result.Destinations=@($Plan.Destinations | ForEach-Object { $_.ToString() }) }
    [pscustomobject]$result
}

function Assert-FgoKoPath {
    param([Parameter(Mandatory=$true)][string]$Path, [switch]$MustExist)
    $full=[IO.Path]::GetFullPath($Path)
    $entry=$full
    while ($entry) {
        $item=Get-Item -LiteralPath $entry -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw '[NETWORK_SERVER_LAYOUT_INVALID] Redirected helper or parent path.'
        }
        $parent=[IO.Path]::GetDirectoryName($entry)
        if ($parent -eq $entry) { break }
        $entry=$parent
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $full)) { throw '[NETWORK_SERVER_LAYOUT_INVALID] Missing helper or component.' }
    return $full
}

function Get-FgoKoBytesHash {
    param([byte[]]$Bytes)
    $hash=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
}

function ConvertTo-FgoKoArgument {
    param([AllowEmptyString()][string]$Value)
    $escaped=[Regex]::Replace($Value,'(\\*)"','${1}${1}\"')
    $escaped=[Regex]::Replace($escaped,'(\\+)$','${1}${1}')
    return '"'+$escaped+'"'
}

function Invoke-FgoKoPythonJson {
    param([string]$Python,[byte[]]$ScriptBytes,[string[]]$Arguments,[int]$TimeoutSeconds=15)
    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 30) { throw '[NETWORK_TIMEOUT_INVALID]' }
    $encoded=[Convert]::ToBase64String($ScriptBytes)
    $code="import base64;exec(compile(base64.b64decode('"+$encoded+"'),'<managed-network-config>','exec'))"
    $argv=@('-c',$code)+$Arguments
    $info=[Diagnostics.ProcessStartInfo]::new()
    $info.FileName=$Python; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.Arguments=(@($argv|ForEach-Object {ConvertTo-FgoKoArgument ([string]$_)}) -join ' ')
    if ($info.Arguments.Length -gt 30000) { throw '[NETWORK_HELPER_ARGUMENT_LIMIT]' }
    $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $info.StandardOutputEncoding=[Text.Encoding]::UTF8; $info.StandardErrorEncoding=[Text.Encoding]::UTF8
    $process=[Diagnostics.Process]::Start($info)
    try {
        $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds*1000)) {
            # Only this exact helper process handle was created by this call.
            $process.Kill();$process.WaitForExit(1000) | Out-Null
            throw '[NETWORK_HELPER_TIMEOUT] Private configuration helper timed out.'
        }
        if (-not $out.Wait(1000) -or -not $err.Wait(1000)) { throw '[NETWORK_HELPER_OUTPUT_TIMEOUT]' }
        if ($process.ExitCode -ne 0) {
            $code=([Regex]::Match($err.Result,'NETWORK_[A-Z_]+')).Value
            if (-not $code) { $code='NETWORK_SERVER_CONFIG_FAILED' }
            throw ('['+$code+'] Private configuration operation failed.')
        }
        if ($out.Result.Length -gt 32768) { throw '[NETWORK_HELPER_OUTPUT_LIMIT]' }
        if ([string]::IsNullOrWhiteSpace($out.Result)) { return $null }
        return ($out.Result | ConvertFrom-Json)
    } finally {
        try {if (-not $process.HasExited) {$process.Kill();$process.WaitForExit(1000)|Out-Null}} catch {}
        $process.Dispose()
    }
}

function ConvertTo-FgoKoServerLauncher {
    param([Parameter(Mandatory=$true)][string]$Source,
          [Parameter(Mandatory=$true)][string]$SessionConfig,$LocalSettings)
    $coreAnchor='$coreConfig = Join-Path $artemisRoot "config\core.yaml"'
    $argsAnchor='ArgumentList = @("index.py", "--config", "config")'
    $stopAnchor="& (Join-Path `$serverRoot 'Stop-FGOLocalServer.ps1') -TargetServerRoot `$serverRoot"
    $dbAnchor='"--defaults-file=$mariaIni"'
    if (($Source.Split([string[]]@($coreAnchor),[StringSplitOptions]::None).Length -ne 2) -or
        ($Source.Split([string[]]@($argsAnchor),[StringSplitOptions]::None).Length -ne 2) -or
        ($Source.Split([string[]]@($stopAnchor),[StringSplitOptions]::None).Length -ne 2) -or
        ($Source.Split([string[]]@($dbAnchor),[StringSplitOptions]::None).Length -ne 2)) {
        throw '[NETWORK_SERVER_ADAPTER_SHAPE] Reviewed server anchors missing or ambiguous.'
    }
    $escaped=$SessionConfig.Replace("'","''")
    $derived=$Source.Replace($coreAnchor, ('$coreConfig = Join-Path '''+$escaped+''' ''core.yaml'''))
    $derived=$derived.Replace($argsAnchor, ('ArgumentList = @("index.py", "--config", '''+$escaped+''')'))
    $derived=$derived.Replace($stopAnchor,"throw '[NETWORK_SERVER_OWNER_CONFLICT] An existing service belongs to a different installation marker; it was not stopped.'")
    $dbOverride=', "--bind-address=127.0.0.1"'
    if ($LocalSettings) {
        $settingAnchor='$serverSettings = Get-FgoServerSettings'
        $dotAnchor='. (Join-Path $serverRoot "ServerSettings.ps1")'
        if ($Source.Split([string[]]@($settingAnchor),[StringSplitOptions]::None).Length -ne 2 -or
            $Source.Split([string[]]@($dotAnchor),[StringSplitOptions]::None).Length -ne 2) { throw '[NETWORK_SERVER_ADAPTER_SHAPE]' }
        $pairs=@("host='local'","address='192.168.100.1'")
        foreach ($name in @('http','billing','aime','database')) {
            $port=0
            if (-not [int]::TryParse([string]$LocalSettings.$name,[ref]$port) -or $port -lt 1 -or $port -gt 65535) { throw '[NETWORK_PORT_INVALID]' }
            $pairs+=($name+'='+$port)
        }
        $derived=$derived.Replace($dotAnchor,'# Managed settings are bound to the private session config.')
        $derived=$derived.Replace($settingAnchor,('$serverSettings = [pscustomobject]@{'+($pairs -join ';')+'}'))
        $dbOverride+=', "--port='+[int]$LocalSettings.database+'"'
    }
    $derived=$derived.Replace($dbAnchor,($dbAnchor+$dbOverride))
    $tokens=$null; $errors=$null
    $null=[Management.Automation.Language.Parser]::ParseInput($derived,[ref]$tokens,[ref]$errors)
    if ($errors.Count -gt 0) { throw '[NETWORK_SERVER_ADAPTER_PARSE_FAILED]' }
    return $derived
}

function Get-FgoKoLocalServerSettings {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$InstallRoot,[hashtable]$HelperHashes,$Ports)
    $root=Assert-FgoKoPath $InstallRoot -MustExist
    $python=Assert-FgoKoPath (Join-Path $root 'Server\python\python.exe') -MustExist
    $helper=Assert-FgoKoPath (Join-Path $root 'App\FGO_KoServerConfig.py') -MustExist
    $core=Assert-FgoKoPath (Join-Path $root 'Server\artemis\config\core.yaml') -MustExist
    $raw=[IO.File]::ReadAllBytes($helper)
    $expected=[string]$HelperHashes.'FGO_KoServerConfig.py'
    if ($expected -notmatch '^[0-9a-f]{64}$' -or (Get-FgoKoBytesHash $raw) -ne $expected) { throw '[NETWORK_HELPER_CHANGED] Configuration helper binding missing or changed.' }
    $arguments=@('settings',$core)
    if ($null -ne $Ports) { $arguments+=($Ports|ConvertTo-Json -Compress) }
    return Invoke-FgoKoPythonJson -Python $python -ScriptBytes $raw -Arguments $arguments
}

function Test-FgoKoServerProcessIdentity {
    param($ProcessInfo,[string]$Python,[string]$SessionConfig)
    return ($null -ne $ProcessInfo -and
        [String]::Equals([string]$ProcessInfo.ExecutablePath,$Python,[StringComparison]::OrdinalIgnoreCase) -and
        ([string]$ProcessInfo.CommandLine).IndexOf($SessionConfig,[StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        ([string]$ProcessInfo.CommandLine) -match '(^|[\s"\x27])index\.py([\s"\x27]|$)')
}

function Get-FgoKoServerReusePlan {
    param($Processes,$Listeners,[string]$Python,$LocalSettings)
    $project=@($Processes|Where-Object {
        [String]::Equals([string]$_.ExecutablePath,$Python,[StringComparison]::OrdinalIgnoreCase) -and
        ([string]$_.CommandLine) -match '(^|[\s"\x27\\/])index\.py([\s"\x27]|$)'
    })
    if ($project.Count -eq 0) {return $false}
    foreach ($name in @('http','billing','aime')) {
        $matches=@($Listeners|Where-Object {$_.LocalPort -eq $LocalSettings.$name})
        if ($matches.Count -eq 0) {throw '[NETWORK_SERVER_SETTINGS_CONFLICT] An existing project server uses different or incomplete ports; a duplicate was not started.'}
        if (@($matches|Where-Object {$_.OwningProcess -notin @($project.ProcessId)}).Count -gt 0) {throw '[NETWORK_SERVER_OWNER_CONFLICT] Required ports belong to another process.'}
        if (@($matches|Where-Object {$_.LocalAddress -ne '127.0.0.1'}).Count -gt 0) {throw '[NETWORK_SERVER_RESTART_REQUIRED] Existing service is not exclusively loopback-bound; stop it normally before managed startup.'}
    }
    return $true
}

function Invoke-FgoKoLocalServer {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$InstallRoot,$Ports,[hashtable]$HelperHashes)
    $InstallRoot=Assert-FgoKoPath $InstallRoot -MustExist
    $serverRoot=Assert-FgoKoPath (Join-Path $InstallRoot 'Server') -MustExist
    $source=Join-Path $serverRoot 'Start-FGOLocalServer.ps1'
    $python=Join-Path $serverRoot 'python\python.exe'
    $configHelper=Join-Path $InstallRoot 'App\FGO_KoServerConfig.py'
    $cleanupHelper=Join-Path $InstallRoot 'App\FGO_KoServerCleanup.ps1'
    $expected='037d9db222e897ce4394876eb809abd6b9296b88a9629ecf6da380c1e4461d5e'
    foreach ($path in @($source,$python,$configHelper,$cleanupHelper)) { $null=Assert-FgoKoPath $path -MustExist }
    $raw=[IO.File]::ReadAllBytes($source)
    $actual=Get-FgoKoBytesHash $raw
    if ($actual -ne $expected) {
        throw '[NETWORK_SERVER_ADAPTER_UNVERIFIED] Local server startup source differs from the reviewed adapter.'
    }
    $helperBytes=@{}
    foreach ($name in @('FGO_KoServerConfig.py','FGO_KoServerCleanup.ps1')) {
        $bytes=[IO.File]::ReadAllBytes((Join-Path $InstallRoot ('App\'+$name)))
        $pinned=[string]$HelperHashes.$name
        if ($pinned -notmatch '^[0-9a-f]{64}$' -or (Get-FgoKoBytesHash $bytes) -ne $pinned) { throw '[NETWORK_HELPER_CHANGED] Server helper binding missing or changed.' }
        $helperBytes[$name]=$bytes
    }
    $stateRoot=Assert-FgoKoPath (Join-Path $serverRoot 'state')
    [void][IO.Directory]::CreateDirectory($stateRoot)
    $null=Assert-FgoKoPath $stateRoot -MustExist
    $text=[Text.Encoding]::UTF8.GetString($raw).TrimStart([char]0xfeff)
    $arguments=@('prepare',(Join-Path $serverRoot 'artemis\config'),$stateRoot)
    if ($null -ne $Ports) { $arguments+=($Ports|ConvertTo-Json -Compress) }
    $record=Invoke-FgoKoPythonJson -Python $python -ScriptBytes $helperBytes.'FGO_KoServerConfig.py' -Arguments $arguments
    $sessionConfig=[string]$record.folder
    $manifestSha=[string]$record.manifest_sha256
    $localSettings=$record.settings
    $null=Assert-FgoKoPath $sessionConfig -MustExist
    if ($manifestSha -notmatch '^[0-9a-f]{64}$' -or [IO.Path]::GetDirectoryName($sessionConfig) -ne $stateRoot -or
        [IO.Path]::GetFileName($sessionConfig) -notmatch '^\.ko-network-[0-9a-f]{32}$') { throw '[NETWORK_SERVER_CONFIG_OWNER_INVALID]' }
    $tempLauncher=Join-Path $serverRoot ('.ko-server-'+[Guid]::NewGuid().ToString('N')+'.ps1')
    $watching=$false
    $scriptStream=$null;$scriptBytes=$null;$serviceRecord=$null
    $serverProcess=$null;$dbProcess=$null
    try {
        $marker=Join-Path $stateRoot 'fgo-install-root.txt'
        if (Test-Path -LiteralPath $marker) {
            $null=Assert-FgoKoPath $marker -MustExist
            if ((Get-Item -LiteralPath $marker).Length -gt 8192) {throw '[NETWORK_SERVER_OWNER_UNVERIFIED] Ownership marker exceeds the supported size.'}
            $previous=[IO.File]::ReadAllText($marker).Trim()
            if ($previous -and -not [String]::Equals($previous,$InstallRoot,[StringComparison]::OrdinalIgnoreCase)) {
                $listeners=@(Get-NetTCPConnection -State Listen -LocalPort @($localSettings.http,$localSettings.billing,$localSettings.aime) -ErrorAction SilentlyContinue)
                if ($listeners.Count -gt 0) {throw '[NETWORK_SERVER_OWNER_CONFLICT] Existing service ownership marker differs; it was not changed or stopped.'}
            }
        }
        $processes=@(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop)
        $listeners=@(Get-NetTCPConnection -State Listen -LocalPort @($localSettings.http,$localSettings.billing,$localSettings.aime) -ErrorAction SilentlyContinue)
        if (Get-FgoKoServerReusePlan -Processes $processes -Listeners $listeners -Python $python -LocalSettings $localSettings) {
            # No startup script, mutable marker or new watcher is needed when
            # reusing an already owned, correctly bound project service.
            return [pscustomobject]@{LocalSettings=$localSettings;Created=$false;Reused=$true;OwnedService=$null}
        }
        $escaped=$sessionConfig.Replace("'","''")
        $text=ConvertTo-FgoKoServerLauncher -Source $text -SessionConfig $sessionConfig -LocalSettings $localSettings
        # Keep a read-sharing handle open: the derived script can be read by
        # PowerShell while replacement, deletion and outside writes are denied.
        # Close the writer first: Windows PowerShell opens scripts without
        # sharing writes, so even our own outstanding writer blocks execution.
        $scriptStream=[IO.File]::Open($tempLauncher,[IO.FileMode]::CreateNew,[IO.FileAccess]::ReadWrite,[IO.FileShare]::Read)
        $scriptBytes=[Text.UTF8Encoding]::new($true).GetPreamble()+[Text.Encoding]::UTF8.GetBytes($text)
        $scriptStream.Write($scriptBytes,0,$scriptBytes.Length);$scriptStream.Flush()
        $scriptStream.Dispose();$scriptStream=$null
        $scriptStream=[IO.File]::Open($tempLauncher,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        # Validate after acquiring the read lease, closing the writer-to-reader
        # gap without allowing changed bytes to execute.
        if ((Get-FgoKoBytesHash ([IO.File]::ReadAllBytes($tempLauncher))) -ne (Get-FgoKoBytesHash $scriptBytes)) {
            throw '[NETWORK_SERVER_SCRIPT_CHANGED] Temporary server launcher changed before execution.'
        }
        $startupFailure=$null
        # Dot-source inside this function scope to capture only the objects the
        # inspected startup actually created, rather than guessing from PID age.
        try { . $tempLauncher -ServerHost '192.168.100.1' -InstallRoot $InstallRoot }
        catch { $startupFailure=$_ }
        if ($serverProcess) {
            $service=Get-Process -Id $serverProcess.Id -ErrorAction SilentlyContinue
            if ($service) {
                # Keep live configuration if identity cannot be established.
                $watching=$true
                $processInfo=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$serverProcess.Id) -ErrorAction Stop
                if (-not (Test-FgoKoServerProcessIdentity $processInfo $python $sessionConfig)) { throw '[NETWORK_SERVER_OWNER_UNVERIFIED] Live configuration was preserved.' }
                $serviceRecord=[pscustomobject]@{Id=$service.Id;StartTicks=$service.StartTime.ToUniversalTime().Ticks;Executable=$python}
                $cleanupText=[Text.Encoding]::UTF8.GetString($helperBytes.'FGO_KoServerCleanup.ps1').TrimStart([char]0xfeff)
                $command='& {'+"`n"+$cleanupText+"`n"+'} -OwnerProcessId '+$service.Id+
                        ' -OwnerStartTicks '+$service.StartTime.ToUniversalTime().Ticks+
                        ' -Python '''+$python.Replace("'","''")+''' -ConfigHelper '''+$configHelper.Replace("'","''")+
                        ''' -SessionConfig '''+$escaped+''' -ManifestSha256 '''+$manifestSha+''' -ConfigHelperSha256 '''+$HelperHashes.'FGO_KoServerConfig.py'+''''
                $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
                Start-Process -FilePath (Get-Process -Id $PID).Path -WindowStyle Hidden -ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand',$encoded) | Out-Null
            }
        }
        if ($startupFailure) { throw $startupFailure }
        # An already running server is reused only if it is loopback-bound.
        foreach ($port in @($localSettings.http,$localSettings.billing,$localSettings.aime)) {
            $listeners=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
            if ($listeners.Count -eq 0) {throw '[NETWORK_SERVER_NOT_READY] Local service closed before startup completed.'}
            if (@($listeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) {
                throw '[NETWORK_SERVER_RESTART_REQUIRED] The existing service is not bound exclusively to loopback; stop it normally before relaunching.'
            }
        }
        return [pscustomobject]@{LocalSettings=$localSettings;Created=($null -ne $serviceRecord);Reused=($null -eq $serverProcess);OwnedService=$serviceRecord}
    } finally {
        if ($scriptStream) {$scriptStream.Dispose()}
        if ($scriptBytes -and (Test-Path -LiteralPath $tempLauncher) -and (Get-FgoKoBytesHash ([IO.File]::ReadAllBytes($tempLauncher))) -eq (Get-FgoKoBytesHash $scriptBytes)) { Remove-Item -LiteralPath $tempLauncher }
        # Cancellation can jump here before watcher setup. A possibly live
        # process keeps its inputs; a failed lookup is not deletion authority.
        if (-not $watching -and $serverProcess) {
            try {$watching=$null -ne (Get-Process -Id $serverProcess.Id -ErrorAction Stop)}
            catch {$watching=$true}
        }
        if (-not $watching) { $null=Invoke-FgoKoPythonJson -Python $python -ScriptBytes $helperBytes.'FGO_KoServerConfig.py' -Arguments @('cleanup',$sessionConfig,$manifestSha) }
    }
}
