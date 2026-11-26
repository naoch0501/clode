<#
.SYNOPSIS
  セリフを「文章のパターン × 温度 × シード」の全組み合わせで生成して聞き比べる。

.EXAMPLE
  # セリフ.txt に 1 行 1 パターンで書いておく（UTF-8）
  powershell -ExecutionPolicy Bypass -File windows\drama3\variants.ps1 -TextFile セリフ.txt -Seeds 1-5 -Temperatures 0.8 -Name line

  # パターンを | で区切って直接書く
  powershell -ExecutionPolicy Bypass -File windows\drama3\variants.ps1 -Texts "おはよう！|おはよう〜♪" -Seeds 1-3 -Temperatures 0.7,0.9

.PARAMETER TextFile      1 行 = 1 パターンのテキストファイル。空行と # で始まる行は無視
.PARAMETER Texts         パターンを | で区切った文字列（TextFile の代わり）
.PARAMETER Voice         声ファイルの名前（既定: myvoice）。none で参照なし
.PARAMETER Seeds         "1-5" や "1,3,7" の形式（既定: 1-5）
.PARAMETER Temperatures  "0.8" や "0.7,0.8,0.9" の形式（既定: 0.8）
.PARAMETER Name          出力フォルダ名（windows\drama3\outputs\<Name>）。既定は日時
.PARAMETER Extra         speak.ps1 の -Extra にそのまま渡す（例: '--top-p 0.8'）
.PARAMETER NoOpen        終わってもフォルダを開かない
#>
param(
  [string]$TextFile,
  [string]$Texts,
  [string]$Voice = 'myvoice',
  [string]$Seeds = '1-5',
  [string]$Temperatures = '0.8',
  [string]$Name,
  [string]$Extra,
  [switch]$NoOpen
)

. "$PSScriptRoot\common.ps1"
$inv = [System.Globalization.CultureInfo]::InvariantCulture

# --- パターン ------------------------------------------------------------------
if ($TextFile) {
  if (-not (Test-Path $TextFile)) { throw "ファイルがありません: $TextFile" }
  $lines = Get-Content -Encoding UTF8 $TextFile
} elseif ($Texts) {
  $lines = $Texts -split '\|'
} else {
  throw "-TextFile か -Texts でセリフを指定してください"
}
$patterns = @($lines | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
if ($patterns.Count -eq 0) { throw "セリフが 1 つもありません" }

# --- シード: "1-5" / "1,3,7" / "1-3,10" ----------------------------------------
$seedList = @()
foreach ($tok in ($Seeds -split '[,\s]+' | Where-Object { $_ })) {
  if ($tok -match '^(\d+)-(\d+)$') { $seedList += ([int]$Matches[1])..([int]$Matches[2]) }
  elseif ($tok -match '^\d+$') { $seedList += [int]$tok }
  else { throw "-Seeds の書き方が違います: $tok（例: 1-5 や 1,3,7）" }
}

# --- 温度: "0.8" / "0.7,0.8,0.9" ---------------------------------------------
$tempList = @()
foreach ($tok in ($Temperatures -split '[,\s]+' | Where-Object { $_ })) {
  $v = 0.0
  if (-not [double]::TryParse($tok, [System.Globalization.NumberStyles]::Float, $inv, [ref]$v)) {
    throw "-Temperatures の書き方が違います: $tok（例: 0.8 や 0.7,0.9）"
  }
  $tempList += $v
}

if (-not $Name) { $Name = Get-Date -Format 'yyyyMMdd-HHmmss' }
$outDir = Join-Path $Drama3Root "outputs\$Name"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$total = $patterns.Count * $tempList.Count * $seedList.Count
Write-Host "==> $($patterns.Count) パターン × 温度 $($tempList.Count) 種 × シード $($seedList.Count) 種 = $total 本 → $outDir"

$list = New-Object System.Collections.Generic.List[string]
$list.Add("ファイル`t温度`tシード`tセリフ")
$failed = @()
$n = 0
for ($p = 0; $p -lt $patterns.Count; $p++) {
  foreach ($temp in $tempList) {
    foreach ($seed in $seedList) {
      $n++
      $tStr = $temp.ToString($inv)
      $file = "p$($p + 1)_t$($tStr)_s$seed.wav"
      $out = Join-Path $outDir $file
      Write-Host ("==> [{0}/{1}] p{2} t{3} s{4}: {5}" -f $n, $total, ($p + 1), $tStr, $seed, $patterns[$p])
      try {
        $args2 = @{ Voice = $Voice; Text = $patterns[$p]; Out = $out; Seed = $seed; Temperature = $temp }
        if ($Extra) { $args2['Extra'] = @($Extra) }
        & "$PSScriptRoot\speak.ps1" @args2
        $list.Add("$file`t$tStr`t$seed`t$($patterns[$p])")
      } catch {
        Write-Warning "失敗: $file — $($_.Exception.Message)"
        $failed += $file
      }
    }
  }
}

[System.IO.File]::WriteAllLines((Join-Path $outDir 'list.txt'), $list, (New-Object System.Text.UTF8Encoding($true)))

Write-Host ""
Write-Host "==> 完了: $($total - $failed.Count) / $total 本 → $outDir（一覧: list.txt）"
if ($failed.Count -gt 0) {
  Write-Host "失敗したもの:"
  $failed | ForEach-Object { Write-Host "  $_" }
}
if (-not $NoOpen) { Start-Process explorer.exe $outDir }
