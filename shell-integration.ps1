<#
.SYNOPSIS
    PowerShell functions for Music Visualiser (musicvis, musicvis-esp32) on Windows 11.
.DESCRIPTION
    Dot-source from your PowerShell $PROFILE:
      . "C:\path\to\music-visualiser\shell-integration.ps1"
#>
$script:MusicVisRoot = $PSScriptRoot

function musicvis {
    & (Join-Path $script:MusicVisRoot "macos\run.ps1") @args
}

function musicvis-esp32 {
    param(
        [Parameter(Position = 0)]
        [string]$Action = "build",
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Extra
    )
    Push-Location (Join-Path $script:MusicVisRoot "esp32")
    try {
        $pio = Get-Command pio -ErrorAction SilentlyContinue
        $exe = if ($pio) { $pio.Source } else { Join-Path $script:MusicVisRoot "esp32\.venv-pio\Scripts\pio.exe" }
        switch ($Action) {
            "build" { & $exe run @Extra }
            "upload" { & $exe run -t upload @Extra }
            "monitor" { & $exe device monitor -b 115200 @Extra }
            default { & $exe $Action @Extra }
        }
    } finally {
        Pop-Location
    }
}
