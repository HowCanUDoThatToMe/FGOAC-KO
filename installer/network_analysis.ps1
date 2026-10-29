# Fixed parser: supplied launcher source is DATA and is never invoked.
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$source=([Console]::In.ReadToEnd() | ConvertFrom-Json).source
$tokens=$null;$errors=$null
$tree=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
$assign=@($tree.FindAll({param($node)$node -is [Management.Automation.Language.AssignmentStatementAst]},$true))
$aliases=@{};$processName='injectprocess'
foreach($spec in @(@('System.Diagnostics.Process','injectprocess'),@('System.Diagnostics.ProcessStartInfo','injectstartinfo'))) {
 $creators=@($assign|Where-Object {$_.Left -is [Management.Automation.Language.VariableExpressionAst] -and $_.Right.Extent.Text -match ('^New-Object\s+'+[Regex]::Escape($spec[0])+'$')})
 if($creators.Count -eq 1) {
  $actual=$creators[0].Left.VariablePath.UserPath.ToLowerInvariant();$aliases[$actual]=$spec[1]
  if($spec[1] -eq 'injectprocess'){$processName=$actual}
 }
}
$sigCache=@{}
$relevantTokens=@($tokens | Where-Object {$_.Kind.ToString() -notin @('Comment','NewLine','LineContinuation','EndOfInput')})
function Sig($node) {
 $cacheKey=[string]$node.Extent.StartOffset+':'+$node.Extent.EndOffset
 if($sigCache.ContainsKey($cacheKey)){return $sigCache[$cacheKey]}
 $parts=New-Object Collections.Generic.List[object]
 foreach($token in $relevantTokens) {
  if($token.Extent.StartOffset -lt $node.Extent.StartOffset){continue}
  if($token.Extent.EndOffset -gt $node.Extent.EndOffset){break}
  $kind=$token.Kind.ToString();$value=$token.Text
  if($kind -in @('Variable','Identifier','Generic')){$value=$value.ToLowerInvariant()}
  if($kind -eq 'Variable' -and $aliases.ContainsKey($token.Name)){$value='$'+$aliases[$token.Name]}
  if($kind -eq 'StringLiteral' -or ($kind -eq 'StringExpandable' -and !$token.NestedTokens)){$kind='StringConstant';$value=$token.Value}
  $parts.Add(@($kind,$value))
 }
 $result=ConvertTo-Json -InputObject @($parts.ToArray()) -Compress -Depth 8
 $sigCache[$cacheKey]=$result
 return $result
}
function Stmt($node) {
 while($node.Parent -and $node.Parent -isnot [Management.Automation.Language.StatementBlockAst] -and $node.Parent -isnot [Management.Automation.Language.NamedBlockAst]){$node=$node.Parent}
 $node
}
function Context($node) {
 $parts=@();$cursor=$node.Parent
 while($cursor) {
  if($cursor -is [Management.Automation.Language.StatementBlockAst]) {
   $owner=$cursor.Parent
   if($owner -is [Management.Automation.Language.TryStatementAst]) {
    $bare=$owner.CatchClauses.Count -eq 1 -and !$owner.Finally -and $owner.CatchClauses[0].CatchTypes.Count -eq 0 -and $owner.CatchClauses[0].Body.Statements.Count -eq 1 -and $owner.CatchClauses[0].Body.Statements[0] -is [Management.Automation.Language.ThrowStatementAst] -and !$owner.CatchClauses[0].Body.Statements[0].Pipeline
    if(!$bare){if($owner.Body -eq $cursor){$parts+='try-body'}else{$parts+='try-finally'} }
   } elseif($owner -is [Management.Automation.Language.IfStatementAst]) {
    $branch='else';foreach($clause in $owner.Clauses){if($clause.Item2 -eq $cursor){$branch='if:'+(Sig $clause.Item1)}};$parts+=$branch
   } else {$parts+=$owner.GetType().Name}
  } elseif($cursor -is [Management.Automation.Language.CatchClauseAst]) {$parts+='catch:'+(@($cursor.CatchTypes|ForEach-Object {$_.TypeName.FullName}) -join ',')}
  elseif($cursor -is [Management.Automation.Language.FunctionDefinitionAst]){$parts+='function:'+$cursor.Name.ToLowerInvariant()}
  elseif($cursor -is [Management.Automation.Language.ScriptBlockExpressionAst]){$parts+='scriptblock'}
  $cursor=$cursor.Parent
 }
 ConvertTo-Json -InputObject $parts -Compress
}
function Row($node,$role){[pscustomobject]@{role=$role;start=$node.Extent.StartOffset;end=$node.Extent.EndOffset;signature=(Sig $node);context=(Context $node);text=$node.Extent.Text}}
function VariableName($node) { $node.VariablePath.UserPath.ToLowerInvariant() -replace '^(script|global|local|private):','' }
$starts=@($assign|Where-Object {$_.Left.Extent.Text -in @('$network','$primaryAddress') -and ($_.Right.Extent.Text -match '^Get-FgoNetworkPlan\b|^Get-PrimaryIPv4\b')})
$next=@($assign|Where-Object {$_.Left.Extent.Text -eq '$effectiveInputMode' -and $starts.Count -eq 1 -and $_.Parent -eq $starts[0].Parent})
$section=$null
if($starts.Count -eq 1 -and $next.Count -eq 1 -and $starts[0].Parent -eq $next[0].Parent) {
 $siblings=@($starts[0].Parent.Statements)
 $first=[Array]::IndexOf($siblings,$starts[0]);$last=[Array]::IndexOf($siblings,$next[0])
 if($first -gt 0 -and $siblings[$first-1] -is [Management.Automation.Language.PipelineAst] -and $siblings[$first-1].Extent.Text -match 'FGO_LocalNetwork\.ps1'){$first--}
 if($first -ge 0 -and $last -gt $first) {
  $selected=@($siblings[$first..($last-1)])
  $section=[pscustomobject]@{start=$selected[0].Extent.StartOffset;end=$selected[-1].Extent.EndOffset;context=(Context $selected[0]);signatures=@($selected|ForEach-Object {Sig $_});text=$source.Substring($selected[0].Extent.StartOffset,$selected[-1].Extent.EndOffset-$selected[0].Extent.StartOffset)}
 }
}
$facts=@{};$protected=@('network','localip','subnet','addrsuffix','routersuffix','broadcast','serverhost','primaryaddress','octets','autoserverrequested','shouldstartlocalserver','localserversetting','localserverlauncher','closedports')
foreach($v in @($tree.FindAll({param($node)$node -is [Management.Automation.Language.VariableExpressionAst]},$true))) {
 $name=VariableName $v
 if($name -in $protected -or $name -match '^env:fgo_(local_|install_root)' -or $name -like 'fgokonetwork*') {
  $stmt=Stmt $v;$facts[[string]$stmt.Extent.StartOffset]=Row $stmt 'network-use'
 }
}
foreach($member in @($tree.FindAll({param($node)$node -is [Management.Automation.Language.MemberExpressionAst] -and $node.Member.Value -in @('serverHost','serverMode','serverPorts','requiredPorts')},$true))) {
 $stmt=Stmt $member;$facts[[string]$stmt.Extent.StartOffset]=Row $stmt 'network-use'
}
foreach($slot in @($tree.FindAll({param($node)$node -is [Management.Automation.Language.IndexExpressionAst] -and $node.Target -is [Management.Automation.Language.MemberExpressionAst] -and $node.Target.Member.Value -ieq 'EnvironmentVariables'},$true))) {
 if($slot.Index -isnot [Management.Automation.Language.StringConstantExpressionAst] -or $slot.Index.Value -match '^FGO_(LOCAL_|INSTALL_ROOT)') {
  $stmt=Stmt $slot;$facts[[string]$stmt.Extent.StartOffset]=Row $stmt 'network-use'
 }
}
$commands=@($tree.FindAll({param($node)$node -is [Management.Automation.Language.CommandAst]},$true))
$ini=@()
foreach($cmd in $commands) {
 if($cmd.GetCommandName() -ieq 'Set-IniValue') {
  $texts=@($cmd.CommandElements|ForEach-Object {$_.Extent.Text.Trim('"',"'").ToLowerInvariant()})
  if(@($texts|Where-Object {$_ -in @('dns','netenv','keychip')}).Count -gt 0) {
   $key='';$sec=''
   for($i=0;$i -lt $cmd.CommandElements.Count-1;$i++) {
    if($cmd.CommandElements[$i] -is [Management.Automation.Language.CommandParameterAst]) {
     if($cmd.CommandElements[$i].ParameterName -ieq 'Key'){$key=$cmd.CommandElements[$i+1].Value}
     if($cmd.CommandElements[$i].ParameterName -ieq 'Section'){$sec=$cmd.CommandElements[$i+1].Value}
    }
   }
   $ini+=Row (Stmt $cmd) ('ini:'+$sec+':'+$key)
  }
 }
}
$lifecycle=@($tree.FindAll({param($node)$node -is [Management.Automation.Language.VariableExpressionAst] -and (VariableName $node) -ieq $processName},$true)|ForEach-Object {Stmt $_}|Sort-Object {$_.Extent.StartOffset} -Unique|ForEach-Object {Row $_ 'lifecycle'})
$danger=@($commands|Where-Object {$_.InvocationOperator.ToString() -in @('Dot','Ampersand') -or $_.GetCommandName() -in @('Invoke-Expression','iex','Invoke-Command','Start-Job','Start-ThreadJob','New-Module','Import-Module','Set-Alias','New-Alias','Add-Type','Set-Variable','New-Variable','Remove-Variable','Set-Item','New-ItemProperty')}|ForEach-Object {Row $_ 'dynamic'})
$danger+=@($tree.FindAll({param($node)$node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and $node.Member.Value -in @('InvokeScript','SetEnvironmentVariable','SetValue','Set','Invoke')},$true)|ForEach-Object {Row $_ 'dynamic'})
$dependencies=@($tree.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and ($node.Name -in @('Set-IniValue','Stop-WithMessage','Get-FileHash','Join-Path','Split-Path','Get-Process','Start-Process','Get-NetTCPConnection','Get-Item','Test-Path','Remove-Item') -or $node.Name -match 'FgoKoNetwork|FgoKoLocalServer|FgoKoEndpoint')},$true)|ForEach-Object {Row $_ 'dependency'})
$rootEnd=$tree.EndBlock.Extent.EndOffset
$stop=@($commands|Where-Object {$_.GetCommandName() -eq 'Stop-FgoKoNetworkSession'})
$coverage=@()
foreach($call in @($tree.FindAll({param($node)$node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and $node.Expression -is [Management.Automation.Language.VariableExpressionAst] -and $node.Expression.VariablePath.UserPath -ieq $processName -and $node.Member.Value -in @('Start','WaitForExit')},$true))) {
 $covered=$false;$cursor=$call.Parent
 while($cursor) {
  if($cursor -is [Management.Automation.Language.TryStatementAst] -and $cursor.Finally) {
   foreach($s in $stop) {
    if($s.Extent.StartOffset -ge $cursor.Finally.Extent.StartOffset -and $s.Extent.EndOffset -le $cursor.Finally.Extent.EndOffset -and $call.Extent.StartOffset -ge $cursor.Body.Extent.StartOffset -and $call.Extent.EndOffset -le $cursor.Body.Extent.EndOffset){$covered=$true}
   }
  }
  $cursor=$cursor.Parent
 }
 $coverage+=[pscustomobject]@{member=$call.Member.Value;covered=$covered}
}
[pscustomobject]@{errors=@($errors|ForEach-Object {$_.ErrorId});section=$section;facts=@($facts.Values|Sort-Object start);ini=$ini;lifecycle=$lifecycle;danger=$danger;dependencies=$dependencies;rootEnd=$rootEnd;boundary=@($starts.Count,$next.Count,$first,$last);cleanupCoverage=$coverage} | ConvertTo-Json -Compress -Depth 12
