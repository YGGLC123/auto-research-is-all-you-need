# auto-research one-click entry (Windows).
#   .\install.ps1            -> install (frozen copy into ~/.claude/skills)
#   .\install.ps1 uninstall  -> remove installed copy
#   .\install.ps1 status     -> source vs installed versions
#   .\install.ps1 release    -> build portable zip under releases/
param([ValidateSet("install", "uninstall", "status", "release")][string]$Action = "install")
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
py "$root\scripts\deploy.py" $Action
exit $LASTEXITCODE
