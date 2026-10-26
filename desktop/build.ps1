$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compilerPath = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compilerPath)) { throw '.NET Framework compiler not found.' }
$referencePaths = @('System.dll', 'System.Core.dll', 'System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Net.Http.dll', 'System.Web.Extensions.dll',
    (Join-Path $projectRoot 'Microsoft.Web.WebView2.Core.dll'), (Join-Path $projectRoot 'Microsoft.Web.WebView2.WinForms.dll'))
$compilerArguments = @(('/win32icon:' + (Join-Path $projectRoot 'app.ico')), '/nologo', '/target:winexe', '/platform:x64', '/optimize+', ('/out:' + (Join-Path $projectRoot '제작으로부자되자.exe')))
foreach ($referencePath in $referencePaths) { $compilerArguments += '/reference:' + $referencePath }
$compilerArguments += Join-Path $PSScriptRoot 'Program.cs'
& $compilerPath @compilerArguments
if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
Write-Host 'Desktop build succeeded.'
