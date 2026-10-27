# Back up Galaxy Book Go firmware/driver files to a separate external drive.
# Never erases, resets, partitions or uninstalls anything.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ExternalDrive)
$ErrorActionPreference = 'Stop'
$Resolved = (Resolve-Path -LiteralPath $ExternalDrive).Path
if ($Resolved -notmatch '^[A-Za-z]:\\') { throw 'Use an external drive letter such as E:\' }
$Letter = $Resolved.Substring(0,1).ToUpperInvariant()
if ($Letter -eq $env:SystemDrive.Substring(0,1).ToUpperInvariant()) { throw 'Refusing system drive.' }
$Volume = Get-Volume -DriveLetter $Letter
if ($Volume.DriveType -ne 'Removable') {
  Write-Warning 'Destination not classified as removable. Confirm it is a SEPARATE external device.'
  if ((Read-Host 'Type BACKUP to continue') -cne 'BACKUP') { throw 'Cancelled.' }
}
$Out = Join-Path $Resolved ('GalaxyBookGo-Firmware-' + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $Out -Force | Out-Null
Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory |
  ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Out 'hardware.json')
Get-CimInstance Win32_BIOS | Select-Object SMBIOSBIOSVersion,Manufacturer |
  ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Out 'bios.json')
Get-CimInstance Win32_PnPSignedDriver | Select-Object DeviceName,DriverVersion,InfName |
  Export-Csv -NoTypeInformation -LiteralPath (Join-Path $Out 'drivers.csv')
$Source = Join-Path $env:SystemRoot 'System32\DriverStore\FileRepository'
$Destination = Join-Path $Out 'DriverStore\FileRepository'
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
& robocopy.exe $Source $Destination /E /R:1 /W:1 /XJ /COPY:DAT /DCOPY:DAT
if ($LASTEXITCODE -ge 8) { throw "Robocopy failed with code $LASTEXITCODE" }
$Count = (Get-ChildItem -LiteralPath $Destination -Recurse -File | Measure-Object).Count
if ($Count -lt 100) { throw "Only $Count files copied; check backup." }
Write-Host "DriverStore backup complete: $Count files at $Destination"
Write-Host 'Verify contents on a different PC; make a separate Windows Recovery USB too.'
