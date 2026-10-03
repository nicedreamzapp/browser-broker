# Supervisor the logon task runs: make sure the browser is up with CDP, then
# keep proxy.py alive (restart 10s after any exit, like launchd's KeepAlive).
# Runs under pythonw so no console window appears.
$ErrorActionPreference = "Continue"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dir = Split-Path -Parent $Here
$Log = Join-Path $Dir "launcher.log"
function Say($m) { Add-Content -Path $Log -Value ("[{0}] run-broker: {1}" -f (Get-Date -Format s), $m) }

$py = $env:BROKER_PYTHONW
if (-not $py) { $py = Get-Content (Join-Path $Here "pythonw.txt") -ErrorAction SilentlyContinue | Select-Object -First 1 }
if (-not $py) { $py = "pythonw.exe" }

& (Join-Path $Here "launch-browser.ps1") | Out-Null
while ($true) {
  Say "starting proxy.py with $py"
  $p = Start-Process -FilePath $py -ArgumentList "`"$(Join-Path $Dir 'proxy.py')`"" -WorkingDirectory $Dir `
        -RedirectStandardError (Join-Path $Dir "proxy.err") -PassThru -WindowStyle Hidden
  $p.WaitForExit()
  Say "proxy.py exited with code $($p.ExitCode); restarting in 10s"
  Start-Sleep -Seconds 10
}
