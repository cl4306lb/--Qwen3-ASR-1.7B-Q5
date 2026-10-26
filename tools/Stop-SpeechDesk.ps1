$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
try {
    $token = [IO.File]::ReadAllText((Join-Path $projectRoot 'desktop\data\api-token.txt')).Trim()
    Invoke-RestMethod -Uri 'http://127.0.0.1:8765/v1/shutdown' -Method Post `
        -Headers @{Authorization="Bearer $token"} -TimeoutSec 10 | Out-Null
    Write-Host 'Speech Desk is closing.'
} catch {
    Write-Host 'Wait for current tasks to finish, then close the app using X.'
}
