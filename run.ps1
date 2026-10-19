# Starts Mash Loop on http://127.0.0.1:8765 and opens it in the browser.
# A server left running from an earlier start (another window, an older
# version) is stopped first, so running this script always serves the
# current code.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONWARNINGS = "ignore"
$port = 8765
$old = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
foreach ($c in $old) {
    $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    if ($p) {
        Write-Host "Stopping the server already running on port $port (pid $($p.Id), started $($p.StartTime))"
        Stop-Process -Id $p.Id -Force
    }
}
if ($old) { Start-Sleep -Milliseconds 800 }
& "$PSScriptRoot\.venv\Scripts\python.exe" -m loopbox.server --open @args
