# SPDX-License-Identifier: MIT
# Parse DATA only. Never invoke a supplied script, even with -CheckOnly.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$source=([Console]::In.ReadToEnd() | ConvertFrom-Json).source
$tokens=$null; $errors=$null
$tree=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
$aliases=@{}
$assign=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst]},$true))
# Infer only the one ProcessStartInfo -> Process.StartInfo pipeline.
$si=@($assign | Where-Object { $_.Left -is [Management.Automation.Language.VariableExpressionAst] -and $_.Right.Extent.Text -match '^New-Object\s+System\.Diagnostics\.ProcessStartInfo$' })
if($si.Count -eq 1){
 $siName=$si[0].Left.VariablePath.UserPath
 $aliases[$siName]='injectstartinfo'
 $binding=@($assign | Where-Object { $_.Left -is [Management.Automation.Language.MemberExpressionAst] -and $_.Left.Member.Value -ieq 'StartInfo' -and $_.Right.Extent.Text -ieq ('$'+$siName) })
 if($binding.Count -eq 1){$aliases[$binding[0].Left.Expression.VariablePath.UserPath]='injectprocess'}
 $arguments=@($assign | Where-Object {$_.Left.Extent.Text -ieq ('$'+$siName+'.Arguments')})
 if($arguments.Count -eq 1){
  $v=@($arguments[0].Right.FindAll({param($n) $n -is [Management.Automation.Language.VariableExpressionAst]},$true))
  if($v.Count -eq 1){
   $qName=$v[0].VariablePath.UserPath; $aliases[$qName]='quotedlauncharguments'
   $q=@($assign | Where-Object {$_.Left.Extent.Text -ieq ('$'+$qName)})
   if($q.Count -eq 1){
    $v=@($q[0].Right.FindAll({param($n) $n -is [Management.Automation.Language.VariableExpressionAst] -and $n.VariablePath.UserPath -ne '_'},$true))
    if($v.Count -eq 1){$aliases[$v[0].VariablePath.UserPath]='launcharguments'}
   }
  }
 }
}
$sigCache=@{}
function Sig($node){
 $cacheKey=[string]$node.Extent.StartOffset+':'+[string]$node.Extent.EndOffset
 if($sigCache.ContainsKey($cacheKey)){return $sigCache[$cacheKey]}
 $a=@($tokens | Where-Object {$_.Extent.StartOffset -ge $node.Extent.StartOffset -and $_.Extent.EndOffset -le $node.Extent.EndOffset -and $_.Kind.ToString() -notin @('Comment','NewLine','LineContinuation','EndOfInput')} | ForEach-Object {
  $k=$_.Kind.ToString(); $v=$_.Text
  if($k -in @('Variable','Identifier','Generic')){$v=$v.ToLowerInvariant()}
  if($k -eq 'Variable' -and $aliases.ContainsKey($_.Name)){$v='$'+$aliases[$_.Name]}
  if($k -eq 'StringLiteral' -or ($k -eq 'StringExpandable' -and !$_.NestedTokens)){$k='StringConstant';$v=$_.Value}
  @($k,$v)
 })
 # Explicitly supported equivalent: already-quoted string[] joined by one space.
 $s=ConvertTo-Json -InputObject $a -Depth 6 -Compress
 $sigCache[$cacheKey]=$s
 return $s
}
function Context($n){
 $a=@();$p=$n.Parent
 while($p){
  if($p -is [Management.Automation.Language.StatementBlockAst]){
   $o=$p.Parent
   if($o -is [Management.Automation.Language.IfStatementAst]){
    $branch='else';foreach($c in $o.Clauses){if($c.Item2 -eq $p){$branch='if:'+(Sig $c.Item1)}};$a+=$branch
   }elseif($o -is [Management.Automation.Language.TryStatementAst]){
    if($o.Body -ne $p){$a+='finally'}else{
     $handlers=@($o.CatchClauses|ForEach-Object {Sig $_});$fin=if($o.Finally){Sig $o.Finally}else{''}
     # Only a bare rethrow wrapper adds no alternate continuation or mutation.
     $bare=$o.CatchClauses.Count -eq 1 -and !$o.Finally -and $o.CatchClauses[0].CatchTypes.Count -eq 0 -and $o.CatchClauses[0].Body.Statements.Count -eq 1 -and $o.CatchClauses[0].Body.Statements[0] -is [Management.Automation.Language.ThrowStatementAst] -and !$o.CatchClauses[0].Body.Statements[0].Pipeline
     if(!$bare){$a+=('try:'+($handlers -join '|')+':'+$fin)}
    }
   }else{$a+=$o.GetType().Name}
  }elseif($p -is [Management.Automation.Language.FunctionDefinitionAst]){$a+='function:'+$p.Name.ToLowerInvariant()}
  elseif($p -is [Management.Automation.Language.ScriptBlockExpressionAst]){$a+='scriptblock'}
  $p=$p.Parent
 }
 ConvertTo-Json -InputObject $a -Compress
}
function Row($n,$role){
 [pscustomobject]@{role=$role;signature=(Sig $n);context=(Context $n);start=$n.Extent.StartOffset;end=$n.Extent.EndOffset;line=$n.Extent.StartLineNumber;text=$n.Extent.Text}
}
$facts=@();$seen=@{}
$protected=@('launcharguments','quotedlauncharguments','injectstartinfo','injectprocess','usechinese','uselocalization','gamelanguage','chinesehookpath','agopath','injectpath','chinese','launcherconfig','executioncontext','env:fgo_localization_language','env:fgo_zh_enabled','env:fgo_ko_labels_enable')
foreach($v in @($tree.FindAll({param($n)$n -is [Management.Automation.Language.VariableExpressionAst]},$true))){
 $name=$v.VariablePath.UserPath.ToLowerInvariant()
 # Script/global/local/private scopes still refer to protected launch inputs.
 $name=$name -replace '^(script|global|local|private):',''
 if($aliases.ContainsKey($name)){$name=$aliases[$name]}
 if($name -notin $protected){continue}
 $n=$v
 while($n.Parent -and $n.Parent -isnot [Management.Automation.Language.StatementBlockAst] -and $n.Parent -isnot [Management.Automation.Language.NamedBlockAst]){$n=$n.Parent}
 # Static unrelated environment slots cannot alias Arguments/StartInfo/language.
 if($n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left -is [Management.Automation.Language.IndexExpressionAst] -and $n.Left.Target.Extent.Text -match '\.EnvironmentVariables$' -and $n.Left.Index -is [Management.Automation.Language.StringConstantExpressionAst] -and $n.Left.Index.Value -notmatch '^FGO_(LOCALIZATION|ZH|KO_)') {continue}
 $key=[string]$n.Extent.StartOffset
 if(!$seen.ContainsKey($key)){$facts+=Row $n 'flow';$seen[$key]=$true}
}
$facts=@($facts|Sort-Object start)
$special=@()
foreach($n in @($tree.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true))){if($n.Name -ieq 'ConvertTo-WindowsCommandLineArgument'){$special+=Row $n 'quote_function'}}
foreach($n in $assign){
 $left=$n.Left.Extent.Text.ToLowerInvariant()
 $role=switch($left){'$usechinese'{'chinese'} '$uselocalization'{'localization'} default{''}}
 if($left -match '\.environmentvariables\[["'']FGO_ZH_ENABLED["'']\]'){$role='zh_environment'}
 if($left -match '\.environmentvariables\[["'']FGO_LOCALIZATION_LANGUAGE["'']\]'){$role='language_environment'}
 if($role){$special+=Row $n $role}
}
$hooks=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.InvokeMemberExpressionAst] -and $n.Member.Value -ieq 'Add' -and $n.Arguments.Count -eq 1 -and $n.Arguments[0].Extent.Text -ieq '$chineseHookPath'},$true))
foreach($n in $hooks){
 $special+=Row $n 'hook'
 $p=$n.Parent;while($p -and $p -isnot [Management.Automation.Language.IfStatementAst]){$p=$p.Parent}
 if($p -and $p.Clauses.Count -eq 1){$special+=Row $p.Clauses[0].Item1 'hook_condition'}
}
$commands=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.CommandAst]},$true))
$danger=@($commands|Where-Object {$_.GetCommandName() -in @('Invoke-Expression','iex','Get-Variable','gv','Set-Variable','sv','New-Variable','Remove-Variable','Get-Item','gi','Set-Item','si','Set-Alias','sal','New-Alias','Import-Module') -or (!$_.GetCommandName())}|ForEach-Object {Row $_ 'dynamic'})
$danger+=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)|Where-Object {
 $_.Member -isnot [Management.Automation.Language.StringConstantExpressionAst] -or $_.Member.Value -in @('SetEnvironmentVariable','GetEnvironmentVariable','Invoke','InvokeReturnAsIs','AddScript','GetVariable','GetValue','SetValue','GetProperty','GetField')
}|ForEach-Object {Row $_ 'indirect'})
# Invocation names and contexts are bounded too: a new apparently unrelated
# function can mutate caller state through PowerShell's dynamic scope.
$calls=@($commands|ForEach-Object {
 [pscustomobject]@{role='call';signature=([string]$_.GetCommandName()).ToLowerInvariant();context=(Context $_);start=$_.Extent.StartOffset;end=$_.Extent.EndOffset;text=$_.Extent.Text}
})
$control=@($tree.FindAll({param($n)$n.GetType().Name -in @('ReturnStatementAst','ExitStatementAst','ThrowStatementAst','BreakStatementAst','ContinueStatementAst')},$true)|Where-Object {
 $n=$_;$block=$n.Parent;$catch=$block.Parent
 # Ignore only the bare rethrow already validated by Context.
 !($n -is [Management.Automation.Language.ThrowStatementAst] -and !$n.Pipeline -and $catch -is [Management.Automation.Language.CatchClauseAst] -and $catch.CatchTypes.Count -eq 0 -and $block.Statements.Count -eq 1 -and !$catch.Parent.Finally)
}|ForEach-Object {Row $_ 'control'})
[pscustomobject]@{errors=@($errors|ForEach-Object{[pscustomobject]@{line=$_.Extent.StartLineNumber;code=$_.ErrorId}});facts=$facts;special=$special;danger=$danger;control=$control;calls=$calls;aliases=$aliases} | ConvertTo-Json -Depth 15 -Compress
