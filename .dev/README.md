# Development Build

`build-exe.ps1` builds FSAtlas as a standalone Windows executable.

## Usage

From the project root, run:

```powershell
powershell -ExecutionPolicy Bypass -File .dev\build-exe.ps1
```

The script creates a temporary Python environment, installs the project dependencies plus PyInstaller and Pillow, embeds the flight data, map template, settings, and logo, and uses the logo as the executable icon.

The finished executable is written to `FSAtlas.exe` in the project root. Temporary environments, build directories, generated icon files, and PyInstaller metadata are removed automatically.

Python 3 and the Windows Python launcher (`py`) must be installed. Internet access is required to download packages during the build if they are not already cached.
