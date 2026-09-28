$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Speech
$output = Join-Path $PSScriptRoot 'synthetic_fixture.wav'
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice.Rate = 0
$voice.Volume = 100
$voice.SetOutputToWaveFile($output)
$voice.Speak(@'
Violet is a local desktop dictation tool. It should put well-punctuated text into the focused field immediately. Please send the final report to Maya at noon tomorrow. The Threadripper has thirty-two physical cores and two hundred fifty-six gigabytes of memory.
'@)
$voice.Dispose()

Write-Host "Created synthetic English fixture: $output"
