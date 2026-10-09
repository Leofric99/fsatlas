$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$BuildEnv = Join-Path $Root '.build-venv'
$BuildDir = Join-Path $Root '.build-pyinstaller'
$DistDir = Join-Path $Root '.dist-pyinstaller'

$IconPath = Join-Path $Root 'run\images\FSAtlas.ico'
$OutputPath = Join-Path $Root 'FSAtlas.exe'
$Python = Get-Command py -ErrorAction SilentlyContinue

if (-not $Python) {
    throw 'Python launcher "py" was not found. Install Python for Windows and try again.'
}

Set-Location $Root

try {
    & $Python.Source -3 -m venv $BuildEnv
    $VenvPython = Join-Path $BuildEnv 'Scripts\python.exe'
    $PyInstaller = Join-Path $BuildEnv 'Scripts\pyinstaller.exe'

    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r (Join-Path $Root 'requirements.txt') pyinstaller pillow

    $PyInstallerArgs = @(
        '--noconfirm',
        '--clean',
        '--onefile',
        '--windowed',
        '--name', 'FSAtlas',
        '--icon', $IconPath,
        '--workpath', $BuildDir,
        '--distpath', $DistDir,
        '--specpath', $PSScriptRoot,
        '--add-data', ((Join-Path $Root 'run\database\flights.csv') + ';run\database'),
        '--add-data', ((Join-Path $Root 'run\settings.json') + ';run'),
        '--add-data', ($IconPath + ';run\images'),
        '--add-data', ((Join-Path $Root 'run\webapp\templates') + ';run\webapp\templates'),
        '--add-data', ((Join-Path $Root 'run\webapp\static') + ';run\webapp\static'),
        '--collect-all', 'country_converter',
        '--collect-all', 'pystray',
        'run\__main__.py'
    )

    & $PyInstaller @PyInstallerArgs

    $BuiltExe = Join-Path $DistDir 'FSAtlas.exe'
    if (-not (Test-Path $BuiltExe)) {
        throw "PyInstaller completed without producing $BuiltExe."
    }

    Copy-Item -Path $BuiltExe -Destination $OutputPath -Force
    Remove-Item -Path $BuiltExe -Force
    Write-Host "Built $OutputPath" -ForegroundColor Green
}
finally {
    Remove-Item -Path $BuildEnv -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $BuildDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $DistDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path (Join-Path $PSScriptRoot 'FSAtlas.spec') -Force -ErrorAction SilentlyContinue
}