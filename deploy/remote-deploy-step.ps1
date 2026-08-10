param(
    [Parameter(Mandatory = $true)][string]$RepoDir,
    [Parameter(Mandatory = $true)][string]$InfisicalClientId,
    [Parameter(Mandatory = $true)][string]$InfisicalClientSecret,
    [Parameter(Mandatory = $true)][string]$InfisicalProjectId,
    [Parameter(Mandatory = $true)][string]$VpsPassword
)

$ErrorActionPreference = 'Stop'
Set-Location $RepoDir

Write-Host '[STEP 3] capture deployed commit'
$deployedSha = (git rev-parse HEAD)
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: git rev-parse failed'; exit 1 }
Write-Host "Deployed commit: $deployedSha"

Write-Host '[STEP 4] drain in-flight orders on the running instance'
try {
    $r = Invoke-RestMethod -Uri 'http://localhost:8000/drain' -Method Post -TimeoutSec 110 -UseBasicParsing
    Write-Host ('Drain result: ' + ($r | ConvertTo-Json -Compress))
} catch {
    Write-Host 'No running instance to drain, continuing'
}

Write-Host '[STEP 5] kill old process (and its child processes, e.g. browser)'
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ('Killing PID ' + $_.Id + ' and its child tree')
    taskkill /PID $_.Id /F /T
}

Write-Host '[STEP 6] pip install'
& '.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: pip install failed'; exit 1 }

Write-Host '[STEP 7] setx env vars'
setx INFISICAL_CLIENT_ID $InfisicalClientId /M
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: setx CLIENT_ID failed'; exit 1 }
setx INFISICAL_CLIENT_SECRET $InfisicalClientSecret /M
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: setx CLIENT_SECRET failed'; exit 1 }
setx INFISICAL_PROJECT_ID $InfisicalProjectId /M
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: setx PROJECT_ID failed'; exit 1 }
setx INFISICAL_ENVIRONMENT 'prod' /M
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: setx ENV failed'; exit 1 }
setx INFISICAL_SECRET_PATH '/coin-automation' /M
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: setx SECRET_PATH failed'; exit 1 }
setx GIT_COMMIT $deployedSha /M
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: setx GIT_COMMIT failed'; exit 1 }

Write-Host '[STEP 8] schtasks create'
schtasks /create /tn coin-automation /tr "$RepoDir\.venv\Scripts\python.exe $RepoDir\run.py" /sc onlogon /ru Administrator /rp $VpsPassword /rl highest /it /f
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: schtasks create failed'; exit 1 }

Write-Host '[STEP 9] schtasks run'
schtasks /run /tn coin-automation
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: schtasks run failed'; exit 1 }

Write-Host '[STEP 10] health check (must be new commit, not a stale instance still answering)'
$ok = $false
for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 5
    try {
        $health = Invoke-RestMethod -Uri 'http://localhost:8000/health' -TimeoutSec 3 -UseBasicParsing
        if ($health.status -eq 'ok' -and $health.commit -eq $deployedSha) {
            $ok = $true
            Write-Host 'HEALTH_OK'
            break
        } elseif ($health.status -eq 'ok') {
            Write-Host ('Got mismatched commit: ' + $health.commit + ' expected ' + $deployedSha)
        }
    } catch {}
}
if (-not $ok) {
    Write-Host 'ERROR: health check failed: instance running expected commit did not come up within 60s'
    exit 1
}
