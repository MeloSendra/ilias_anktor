$ErrorActionPreference = "Stop"

# cmd.exe needs a mapped drive when the repository lives on a UNC path.
& $env:ComSpec /c "pushd `"$PSScriptRoot`" && npm run package"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
