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
    $Python = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $Python) { $Python = Get-Command python.exe -ErrorAction Stop }
    & $Python.Source (Join-Path $Root 'portable_edge_worker.py') --interval 60
    exit $LASTEXITCODE
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
    Remove-Item Env:OCI_EDGE_WORKER_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:OCI_EDGE_URL -ErrorAction SilentlyContinue
}
