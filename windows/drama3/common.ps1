# Drama-3-Japanese 用スクリプトの共通設定。他の .ps1 から  . "$PSScriptRoot\common.ps1"  で読み込む。
# 注意: このファイルは UTF-8 (BOM 付き) で保存すること（Windows PowerShell 5.1 で日本語が化けないように）。

$ErrorActionPreference = 'Stop'

# 日本語を正しく表示・受け渡しする
try {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$Drama3Root   = $PSScriptRoot
$Drama3Models = if ($env:DRAMA3_MODEL_DIR) { $env:DRAMA3_MODEL_DIR } else { Join-Path $Drama3Root 'models\drama3-ja' }
$Drama3Voices = Join-Path $Drama3Root 'voices'
$SpeechHome   = Join-Path $env:LOCALAPPDATA 'speech.cpp'

function Get-SpeechExe {
  # 1) PATH 上の speech  2) install.ps1 が入れた場所（最新バージョン）
  $cmd = Get-Command speech.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  if (Test-Path $SpeechHome) {
    $exe = Get-ChildItem -Path $SpeechHome -Recurse -Filter 'speech.exe' -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($exe) { return $exe.FullName }
  }
  throw "speech.exe が見つかりません。先に  powershell -ExecutionPolicy Bypass -File windows\drama3\install.ps1  を実行してください。"
}

function Get-Drama3Model {
  # 環境変数 DRAMA3_MODEL で .gguf を直接指定できる
  if ($env:DRAMA3_MODEL) {
    if (-not (Test-Path $env:DRAMA3_MODEL)) { throw "DRAMA3_MODEL のファイルがありません: $env:DRAMA3_MODEL" }
    return (Resolve-Path $env:DRAMA3_MODEL).Path
  }
  $files = @(Get-ChildItem -Path $Drama3Models -Filter '*.gguf' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike '*.voice.gguf' } | Sort-Object Length -Descending)
  if ($files.Count -eq 0) {
    throw "モデルが見つかりません ($Drama3Models)。install.ps1 を実行してダウンロードしてください。"
  }
  if ($files.Count -gt 1) {
    Write-Host "複数のモデルがあるため最大のものを使います: $($files[0].Name)（DRAMA3_MODEL で変更可）"
  }
  return $files[0].FullName
}

function Invoke-Speech {
  param([Parameter(Mandatory)][string[]]$Arguments)
  $exe = Get-SpeechExe
  & $exe @Arguments
  if ($LASTEXITCODE -ne 0) { throw "speech がエラー終了しました (exit $LASTEXITCODE): speech $($Arguments -join ' ')" }
}

function Join-WavFiles {
  # 同じフォーマット (PCM) の wav を連結する。各ファイルの data チャンクをつなぎ、ヘッダのサイズを書き直す。
  param([Parameter(Mandatory)][string[]]$Inputs, [Parameter(Mandatory)][string]$Output, [double]$GapSeconds = 0.3)
  $fmt = $null; $chunks = New-Object System.Collections.Generic.List[byte[]]
  foreach ($path in $Inputs) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    if ([System.Text.Encoding]::ASCII.GetString($bytes, 0, 4) -ne 'RIFF') { throw "wav ではありません: $path" }
    $pos = 12; $thisFmt = $null; $data = $null
    while ($pos + 8 -le $bytes.Length) {
      $id = [System.Text.Encoding]::ASCII.GetString($bytes, $pos, 4)
      $size = [BitConverter]::ToUInt32($bytes, $pos + 4)
      $avail = [Math]::Min([int64]$size, [int64]($bytes.Length - $pos - 8))
      if ($id -eq 'fmt ') { $thisFmt = New-Object byte[] $avail; [Array]::Copy($bytes, $pos + 8, $thisFmt, 0, $avail) }
      elseif ($id -eq 'data') { $data = New-Object byte[] $avail; [Array]::Copy($bytes, $pos + 8, $data, 0, $avail) }
      $pos += 8 + $size + ($size % 2)
    }
    if (-not $thisFmt -or -not $data) { throw "wav の解析に失敗: $path" }
    if ($null -eq $fmt) { $fmt = $thisFmt }
    elseif ([Convert]::ToBase64String($fmt) -ne [Convert]::ToBase64String($thisFmt)) { throw "wav のフォーマットが違います: $path" }
    if ($chunks.Count -gt 0 -and $GapSeconds -gt 0) {
      $byteRate = [BitConverter]::ToUInt32($fmt, 8); $blockAlign = [BitConverter]::ToUInt16($fmt, 12)
      $gap = [int]([Math]::Floor($byteRate * $GapSeconds / $blockAlign) * $blockAlign)
      $chunks.Add((New-Object byte[] $gap))
    }
    $chunks.Add($data)
  }
  $dataLen = 0; foreach ($c in $chunks) { $dataLen += $c.Length }
  $fs = [System.IO.File]::Create($Output)
  try {
    $w = New-Object System.IO.BinaryWriter($fs)
    $w.Write([System.Text.Encoding]::ASCII.GetBytes('RIFF')); $w.Write([uint32](4 + 8 + $fmt.Length + 8 + $dataLen))
    $w.Write([System.Text.Encoding]::ASCII.GetBytes('WAVE'))
    $w.Write([System.Text.Encoding]::ASCII.GetBytes('fmt ')); $w.Write([uint32]$fmt.Length); $w.Write($fmt)
    $w.Write([System.Text.Encoding]::ASCII.GetBytes('data')); $w.Write([uint32]$dataLen)
    foreach ($c in $chunks) { $w.Write($c) }
    $w.Flush()
  } finally { $fs.Dispose() }
}
