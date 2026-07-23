$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$ConstantsFile = Join-Path $ProjectRoot "app\constants.py"
$SpecFile = Join-Path $ProjectRoot "EPL Inventory Checker.spec"
$InstallerScript = Join-Path $ProjectRoot "Installer.iss"
$DistFolder = Join-Path $ProjectRoot "dist\EPL Inventory Checker"
$BrowserFolder = Join-Path $DistFolder "ms-playwright"

Write-Host ""
Write-Host "EPL Inventory Checker release build" -ForegroundColor Cyan
Write-Host "-----------------------------------"

foreach ($RequiredFile in @($ConstantsFile, $SpecFile, $InstallerScript)) {
    if (-not (Test-Path $RequiredFile)) {
        throw "Required file not found: $RequiredFile"
    }
}

$ConstantsText = Get-Content $ConstantsFile -Raw
$VersionMatch = [regex]::Match(
    $ConstantsText,
    'APP_VERSION\s*=\s*["''](?<version>[^"'']+)["'']'
)

if (-not $VersionMatch.Success) {
    throw "Could not read APP_VERSION from app\constants.py"
}

$Version = $VersionMatch.Groups["version"].Value
Write-Host "Version detected: $Version" -ForegroundColor Green

$InstallerText = Get-Content $InstallerScript -Raw
$UpdatedInstallerText = [regex]::Replace(
    $InstallerText,
    '(?m)^#define MyAppVersion\s+"[^"]+"\s*$',
    "#define MyAppVersion `"$Version`""
)

if ($UpdatedInstallerText -eq $InstallerText -and
    $InstallerText -notmatch "#define MyAppVersion\s+`"$([regex]::Escape($Version))`"") {
    throw "Could not update MyAppVersion in Installer.iss"
}

Set-Content -Path $InstallerScript -Value $UpdatedInstallerText -Encoding UTF8
Write-Host "Installer.iss synchronized to version $Version."

Write-Host ""
Write-Host "Cleaning old build output..." -ForegroundColor Yellow
Remove-Item (Join-Path $ProjectRoot "build") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $DistFolder -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Building application with PyInstaller..." -ForegroundColor Yellow
& python -m PyInstaller --clean --noconfirm $SpecFile
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$AppExe = Join-Path $DistFolder "EPL Inventory Checker.exe"
if (-not (Test-Path $AppExe)) {
    throw "PyInstaller completed, but the application executable was not found: $AppExe"
}

Write-Host ""
Write-Host "Installing the matching Playwright Chromium build..." -ForegroundColor Yellow
$PreviousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
try {
    $env:PLAYWRIGHT_BROWSERS_PATH = $BrowserFolder
    & python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        throw "Playwright browser installation failed with exit code $LASTEXITCODE"
    }
}
finally {
    $env:PLAYWRIGHT_BROWSERS_PATH = $PreviousBrowserPath
}

if (-not (Test-Path $BrowserFolder)) {
    throw "The Playwright browser folder was not created: $BrowserFolder"
}

Write-Host ""
Write-Host "Locating Inno Setup compiler..." -ForegroundColor Yellow
$IsccCandidates = @(
    @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    ) | Where-Object { $_ -and (Test-Path $_) }
)

$IsccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($IsccCommand) {
    $Iscc = $IsccCommand.Source
}
elseif ($IsccCandidates.Count -gt 0) {
    $Iscc = $IsccCandidates[0]
}
else {
    throw "Inno Setup 6 compiler (ISCC.exe) was not found. Install Inno Setup 6 or add ISCC.exe to PATH."
}

Write-Host "Using: $Iscc"
Write-Host ""
Write-Host "Compiling installer..." -ForegroundColor Yellow
& $Iscc $InstallerScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed with exit code $LASTEXITCODE"
}

$InstallerExe = Join-Path $ProjectRoot "Installer\EPL-Inventory-Checker-Setup-v$Version.exe"
if (-not (Test-Path $InstallerExe)) {
    throw "Compilation completed, but the expected installer was not found: $InstallerExe"
}

Write-Host ""
Write-Host "Release build completed successfully." -ForegroundColor Green
Write-Host "Application: $AppExe"
Write-Host "Installer:   $InstallerExe"
Write-Host ""
