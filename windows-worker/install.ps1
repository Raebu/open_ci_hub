# Open CI Hub Windows ARM64 edge worker installer
# Run from the reviewed Open CI Hub checkout in a normal, non-elevated PowerShell.
$ErrorActionPreference = 'Stop'
$ExpectedHub = 'https://theraeburngroup.tail8450a3.ts.net'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$InstallRoot = Join-Path $env:LOCALAPPDATA 'OpenCIHub\Worker'
if ($env:OS -ne 'Windows_NT') { throw 'This installer requires Windows.' }
$arch = (Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Architecture)
if ($arch -ne 12) { throw "This worker profile requires Windows ARM64 (architecture code 12); detected $arch." }
# Use verified native Python, never the Microsoft Store python.exe alias.
$pythonExe = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313-arm64\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
  throw 'Native ARM64 Python 3.13 executable is missing.'
}
$pythonCheck = & $pythonExe -c 'import platform, sys; print(platform.machine()); print(sys.version_info.major, sys.version_info.minor)' 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $pythonCheck -notmatch '(?m)^ARM64\s*$' -or $pythonCheck -notmatch '(?m)^3 13\s*$') {
  throw 'Native ARM64 Python 3.13 failed its startup check.'
}
$tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
if (-not $tailscale) {
  $candidate = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
  if (Test-Path -LiteralPath $candidate) { $tailscale = @{ Source = $candidate } }
}
if (-not $tailscale) { throw 'Install the official Tailscale Windows client and sign into the Raeburn tailnet first.' }
$ts = & $tailscale.Source status 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $ts -notmatch 'theraeburngroup') { throw 'Tailscale is not connected to the authorised tailnet.' }
$ping = & $tailscale.Source ping 100.72.235.100 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { throw "Razer is not reachable over Tailscale: $ping" }
$health = Invoke-WebRequest -Uri ($ExpectedHub + '/health') -Method Get -TimeoutSec 8 -UseBasicParsing
if ($health.StatusCode -ne 200) { throw 'Open CI Hub HTTPS health check did not return HTTP 200.' }
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $RepoRoot 'portable_edge_worker.py') -Destination $InstallRoot -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot 'application_client.py') -Destination $InstallRoot -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run-worker.ps1') -Destination $InstallRoot -Force
$pythonExe | Set-Content -LiteralPath (Join-Path $InstallRoot 'python-path.txt') -Encoding UTF8
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $InstallRoot /inheritance:r /grant:r "$($identity):(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict worker directory permissions.' }
$secureToken = Read-Host 'Enter the one-time samsung-edge worker token from the trusted Razer owner session' -AsSecureString
if ($secureToken.Length -lt 48) { throw 'Token was not accepted; no worker task was registered.' }
$encryptedToken = ConvertFrom-SecureString $secureToken
$tokenPath = Join-Path $InstallRoot 'worker-token.dpapi'
$encryptedToken | Set-Content -LiteralPath $tokenPath -Encoding ASCII
& icacls.exe $tokenPath /inheritance:r /grant:r "$($identity):F" 'SYSTEM:F' | Out-Null
if ($LASTEXITCODE -ne 0) { Remove-Item $tokenPath -Force -ErrorAction SilentlyContinue; throw 'Could not restrict token-file permissions.' }
Push-Location $InstallRoot
try {
  & $pythonExe (Join-Path $InstallRoot 'portable_edge_worker.py') --self-test
  if ($LASTEXITCODE -ne 0) { throw 'Worker self-test failed.' }
} finally { Pop-Location }
$taskArgs = '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "' + (Join-Path $InstallRoot 'run-worker.ps1') + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Register-ScheduledTask -TaskName 'OpenCIHub-RestrictedEdgeWorker' -InputObject $task -Force | Out-Null
Write-Output 'Installed restricted Open CI Hub edge worker. Start now with: Start-ScheduledTask -TaskName OpenCIHub-RestrictedEdgeWorker'
Write-Output 'This worker only supports python.syntax, json.inspect and hash.benchmark. It is not a general build or browser executor.'
