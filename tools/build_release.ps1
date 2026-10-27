# Builds the JFG-Studio release zip: dist/JFG-Studio-<version>-windows.zip, holding JFG-Studio.exe, jfgstudio.exe and
# the sample projects. Needs the decomp: jfgformats is copied from its tools/ at a pinned commit (default: its HEAD)
# into the build only, never into this repo. Run from anywhere:
#   powershell -File tools\build_release.ps1 [-Decomp <decomp checkout>] [-Commit <decomp commit>]
param(
    [string]$Decomp = (Join-Path $PSScriptRoot "..\..\..\Decomp"),
    [string]$Commit = "HEAD"
)
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$python = Join-Path $root ".venv\Scripts\python.exe"
$work = Join-Path $root "build\release"
$dist = Join-Path $root "dist"
$Decomp = Resolve-Path $Decomp

# jfgformats at the pinned commit, as committed (no local edits, no tests).
$pinned = (git -C $Decomp rev-parse $Commit).Trim()
if ($LASTEXITCODE -ne 0) { throw "not a decomp commit: $Commit" }
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force (Join-Path $work "tools") | Out-Null
$archive = Join-Path $work "jfgformats.tar"
git -C $Decomp archive --format=tar -o $archive $pinned tools/jfgformats
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
tar -xf $archive -C $work
Remove-Item $archive
Remove-Item -Recurse -Force (Join-Path $work "tools\jfgformats\tests") -ErrorAction SilentlyContinue
$formatsVersion = (& $python -c "import sys; sys.path.insert(0, r'$work\tools'); import jfgformats; print(jfgformats.__version__)").Trim()
$version = (& $python -c "import jfgstudio; print(jfgstudio.__version__)").Trim()
$studio = (git -C $root rev-parse --short HEAD).Trim()
$dirty = if (git -C $root status --porcelain -- jfgstudio tools pyproject.toml) { " (with uncommitted changes)" } else { "" }
Write-Host "JFG-Studio $version ($studio$dirty), jfgformats $formatsVersion (decomp $pinned)"

# The exes.
$env:JFGFORMATS_PATH = Join-Path $work "tools"
& $python -m PyInstaller --noconfirm --clean --distpath $dist --workpath (Join-Path $work "pyinstaller") `
    (Join-Path $root "tools\jfgstudio.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$out = Join-Path $dist "JFG-Studio"

# The sample projects, as committed (not their generator scripts or any build output).
$samples = Join-Path $out "samples"
New-Item -ItemType Directory -Force $samples | Out-Null
$sampleTar = Join-Path $work "samples.tar"
$projects = git -C $root ls-files samples | Where-Object { $_ -match '^samples/[^/]+\.jfgproj/' } |
    ForEach-Object { ($_ -split '/')[0..1] -join '/' } | Sort-Object -Unique
git -C $root archive --format=tar -o $sampleTar HEAD @projects
if ($LASTEXITCODE -ne 0) { throw "git archive of the samples failed" }
tar -xf $sampleTar -C $out
Remove-Item $sampleTar

# Licences and what went into this build.
$licences = Join-Path $out "licences"
New-Item -ItemType Directory -Force $licences | Out-Null
Copy-Item (Join-Path $root "LICENSE") (Join-Path $licences "JFG-Studio.txt")
Copy-Item (Join-Path $work "tools\jfgformats\LICENSE") (Join-Path $licences "jfgformats.txt")
@"
JFG-Studio $version
Studio commit: $studio$dirty
jfgformats $formatsVersion, decomp commit $pinned
Built: $(Get-Date -Format "yyyy-MM-dd HH:mm")
"@ | Set-Content -Encoding utf8 (Join-Path $out "BUILD.txt")

$zip = Join-Path $dist "JFG-Studio-$version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $out -DestinationPath $zip
Write-Host "Wrote $zip ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"
