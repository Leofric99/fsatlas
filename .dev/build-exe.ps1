$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$BuildEnv = Join-Path $Root '.build-venv'
$BuildDir = Join-Path $Root '.build-pyinstaller'
$DistDir = Join-Path $Root '.dist-pyinstaller'
$IconPath = Join-Path $PSScriptRoot 'FSAtlas.ico'
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

    & $VenvPython -c "from PIL import Image; Image.open(r'images\FSAtlas Logo.png').convert('RGBA').save(r'.dev\FSAtlas.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"

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
        '--add-data', ((Join-Path $Root 'run\html\map.html') + ';run\html'),
        '--add-data', ((Join-Path $Root 'run\settings.json') + ';run'),
        '--add-data', ((Join-Path $Root 'images\FSAtlas Logo.png') + ';images'),
        '--collect-all', 'country_converter',
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
    Remove-Item -Path $IconPath -Force -ErrorAction SilentlyContinue
}
