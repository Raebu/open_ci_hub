$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Hub = 'https://theraeburngroup.tail8450a3.ts.net'
$TokenFile = Join-Path $Root 'worker-token.dpapi'
$Secure = Get-Content -LiteralPath $TokenFile -Raw | ConvertTo-SecureString
$Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
try {
    $env:OCI_EDGE_URL = $Hub
    $env:OCI_EDGE_WORKER_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr)
    Set-Location $Root
    $PythonExe = (Get-Content -LiteralPath (Join-Path $Root 'python-path.txt') -Raw).Trim()
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Pinned native ARM64 Python executable is unavailable.' }
    $Arch = & $PythonExe -c 'import platform; print(platform.machine())'
    if ($LASTEXITCODE -ne 0 -or $Arch.Trim().ToUpperInvariant() -notin @('ARM64', 'AARCH64')) { throw 'Pinned Python is not native ARM64.' }
    & $PythonExe (Join-Path $Root 'portable_edge_worker.py') --interval 60
    exit $LASTEXITCODE
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
    Remove-Item Env:OCI_EDGE_WORKER_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:OCI_EDGE_URL -ErrorAction SilentlyContinue
}
