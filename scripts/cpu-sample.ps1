<#
.SYNOPSIS
  CPU and memory of a running PicoBot host, sampled once a second.

.DESCRIPTION
  Finds the host by its command line ("picobot" in a python.exe or
  picobot.exe process) unless -ProcessId is given, samples for -Seconds,
  and prints average/peak CPU (% of one core) and working set. Run it once
  per scenario (dashboard idle, streaming at 10 fps, bot running) for each
  host to compare them.

.EXAMPLE
  .\scripts\cpu-sample.ps1 -Seconds 60 -Label "python, bot running"
#>
param(
    [int]$ProcessId = 0,
    [int]$Seconds = 60,
    [string]$Label = ""
)

if ($ProcessId -eq 0) {
    $proc = Get-CimInstance Win32_Process |
        Where-Object {
            ($_.Name -in @("python.exe", "pythonw.exe") -and $_.CommandLine -match "picobot") -or
            $_.Name -eq "picobot.exe"
        } | Select-Object -First 1
    if (-not $proc) { Write-Error "No running PicoBot host found."; exit 1 }
    $ProcessId = $proc.ProcessId
}

$p = Get-Process -Id $ProcessId -ErrorAction Stop
$cores = [Environment]::ProcessorCount
$samples = @()
$prevCpu = $p.TotalProcessorTime.TotalSeconds
$prevAt = Get-Date
for ($i = 0; $i -lt $Seconds; $i++) {
    Start-Sleep -Seconds 1
    $p.Refresh()
    $now = Get-Date
    $cpu = $p.TotalProcessorTime.TotalSeconds
    $pct = 100.0 * ($cpu - $prevCpu) / ($now - $prevAt).TotalSeconds
    $samples += [pscustomobject]@{ Cpu = $pct; Mem = $p.WorkingSet64 / 1MB }
    $prevCpu = $cpu
    $prevAt = $now
}

$avg = ($samples | Measure-Object Cpu -Average).Average
$peak = ($samples | Measure-Object Cpu -Maximum).Maximum
$mem = ($samples | Measure-Object Mem -Average).Average
"{0}pid {1}, {2}s: CPU avg {3:N1}% peak {4:N1}% of one core ({5} cores), memory {6:N0} MB" -f `
    ($(if ($Label) { "$Label - " } else { "" })), $ProcessId, $Seconds, $avg, $peak, $cores, $mem
