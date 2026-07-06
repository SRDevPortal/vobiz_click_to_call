param(
    [string]$Remote = "https://erps.eternityecommerce.com",
    [string]$KeyFile = "C:\Users\Amit\Downloads\frappe_api_keys_jagmohan_eternity.csv",
    [string]$OutputFile = "\\wsl.localhost\Ubuntu-22.04\home\mit\frappe-bench\sites\site1.local\private\files\vobiz_analytics_snapshot.json",
    [int]$PageLength = 500
)

$ErrorActionPreference = "Stop"

$row = Import-Csv -Path $KeyFile | Select-Object -First 1
if (-not $row.api_key -or -not $row.api_secret) {
    throw "CSV must contain api_key and api_secret columns."
}

$headers = @{
    Authorization = "token $($row.api_key):$($row.api_secret)"
    Accept = "application/json"
    "User-Agent" = "Mozilla/5.0 VobizAnalyticsLocalImport/1.0"
}

function Invoke-FrappeGet {
    param(
        [string]$Path,
        [hashtable]$Query = @{}
    )

    $builder = [System.UriBuilder]::new(($Remote.TrimEnd("/") + $Path))
    if ($Query.Count) {
        $pairs = foreach ($key in $Query.Keys) {
            "{0}={1}" -f [uri]::EscapeDataString($key), [uri]::EscapeDataString([string]$Query[$key])
        }
        $builder.Query = ($pairs -join "&")
    }
    Invoke-RestMethod -Method Get -Uri $builder.Uri.AbsoluteUri -Headers $headers -TimeoutSec 60
}

function Get-FrappeRows {
    param([string]$Doctype)

    Write-Host "Fetching $doctype..."
    $docs = New-Object System.Collections.Generic.List[object]
    $start = 0
    while ($true) {
        $listPath = "/api/resource/$([uri]::EscapeDataString($doctype))"
        try {
            $payload = Invoke-FrappeGet -Path $listPath -Query @{
                fields = '["*"]'
                limit_start = $start
                limit_page_length = $PageLength
            }
        } catch {
            Write-Warning "Skipping $doctype : $($_.Exception.Message)"
            break
        }
        $rows = @($payload.data)
        if (-not $rows.Count) { break }
        foreach ($item in $rows) {
            if ($item.name) { $docs.Add($item) }
        }
        if ($rows.Count -lt $PageLength) { break }
        $start += $PageLength
        if (($start % 500) -eq 0) {
            Write-Host "$doctype rows: $start"
        }
    }
    Write-Host "$doctype fetched: $($docs.Count)"
    @($docs.ToArray())
}

function Get-FrappeDoc {
    param(
        [string]$Doctype,
        [string]$Name
    )
    $docPath = "/api/resource/$([uri]::EscapeDataString($Doctype))/$([uri]::EscapeDataString($Name))"
    try {
        $payload = Invoke-FrappeGet -Path $docPath
        $payload.data
    } catch {
        Write-Warning "Could not fetch $Doctype $Name : $($_.Exception.Message)"
        $null
    }
}

function Get-ReferencedDocs {
    param(
        [string]$Doctype,
        [string[]]$Names
    )
    $unique = @($Names | Where-Object { $_ } | Sort-Object -Unique)
    Write-Host "Fetching referenced $Doctype docs: $($unique.Count)"
    $docs = New-Object System.Collections.Generic.List[object]
    $index = 0
    foreach ($name in $unique) {
        $index += 1
        if (($index % 100) -eq 0) {
            Write-Host "$Doctype referenced $index/$($unique.Count)"
        }
        $doc = Get-FrappeDoc -Doctype $Doctype -Name $name
        if ($doc) { $docs.Add($doc) }
    }
    Write-Host "$Doctype referenced fetched: $($docs.Count)"
    @($docs.ToArray())
}

$snapshot = [ordered]@{}
$callLogs = Get-FrappeRows -Doctype "Vobiz Call Log"
$snapshot["Vobiz Call Log"] = $callLogs
$snapshot["Vobiz User Mapping"] = Get-FrappeRows -Doctype "Vobiz User Mapping"
$snapshot["Team"] = Get-FrappeRows -Doctype "Team"
$snapshot["Team User"] = Get-FrappeRows -Doctype "Team User"

$crmLeadNames = @($callLogs | Where-Object { $_.reference_doctype -eq "CRM Lead" -and $_.reference_name } | ForEach-Object { [string]$_.reference_name })
$patientNames = @($callLogs | Where-Object { $_.reference_doctype -eq "Patient" -and $_.reference_name } | ForEach-Object { [string]$_.reference_name })
$snapshot["CRM Lead"] = Get-ReferencedDocs -Doctype "CRM Lead" -Names $crmLeadNames
$snapshot["Patient"] = Get-ReferencedDocs -Doctype "Patient" -Names $patientNames

$parent = Split-Path -Parent $OutputFile
if (-not (Test-Path $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
$snapshot | ConvertTo-Json -Depth 100 | Set-Content -Path $OutputFile -Encoding UTF8
Write-Host "Saved snapshot to $OutputFile"
