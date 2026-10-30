$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Target = Join-Path $RepoRoot "Ryozan Dev.cmd"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Ryozan Dev.lnk"
$IconPath = Join-Path $RepoRoot "app\public\assets\images\logo-icon.png"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $Target
$shortcut.WorkingDirectory = $RepoRoot
$shortcut.Description = "Start Ryozan local dev API and app"
if (Test-Path $IconPath) {
  $shortcut.IconLocation = $IconPath
}
$shortcut.Save()

Write-Host "Created desktop shortcut: $ShortcutPath"
