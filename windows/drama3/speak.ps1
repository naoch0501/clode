<#
.SYNOPSIS
  Drama-3-Japanese で文章を読み上げて wav に保存する。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\drama3\speak.ps1 -Voice none -Text "こんにちは、今日はいい天気ですね。" -Out hello.wav
  powershell -ExecutionPolicy Bypass -File windows\drama3\speak.ps1 -Voice myvoice -File 原稿.txt -Out 原稿.wav
  powershell -ExecutionPolicy Bypass -File windows\drama3\speak.ps1 -Voice myvoice -Text "[laughs] 本当に？" -Seed 42

.PARAMETER Voice  voices\<名前>.voice.gguf の名前（make-voice.ps1 で作成）。none で参照なし
.PARAMETER File   原稿ファイル（UTF-8）。空行で区切った段落ごとに生成して 1 つの wav に連結する
.PARAMETER Extra  speech tts にそのまま渡す追加オプション（例: -Extra '--top-p 0.8'）
#>
param(
  [string]$Text,
  [string]$File,
  [string]$Voice = 'none',
  [string]$Out = 'out.wav',
  [Nullable[int]]$Seed,
  [Nullable[double]]$Temperature,
  [string]$Device,
  [double]$Gap = 0.4,
  [string[]]$Extra = @()
)

. "$PSScriptRoot\common.ps1"

if ($File) { $Text = Get-Content -Raw -Encoding UTF8 $File }
if (-not $Text -or -not $Text.Trim()) { throw "-Text か -File で読み上げる文章を指定してください" }

$model = Get-Drama3Model
$common = @()
if ($Voice -eq 'none') {
  $common += @('--voice', 'none')
} else {
  $vf = if (Test-Path $Voice) { (Resolve-Path $Voice).Path } else { Join-Path $Drama3Voices "$Voice.voice.gguf" }
  if (-not (Test-Path $vf)) { throw "声ファイルがありません: $vf（make-voice.ps1 で作成してください）" }
  $common += @('--add-voice', "v=$vf", '--voice', 'v')
}
if ($null -ne $Seed) { $common += @('--seed', "$Seed") }
if ($null -ne $Temperature) { $common += @('--temperature', ([string]$Temperature)) }
if ($Device) { $common += @('--device', $Device) }
# -Extra は  '--top-p','0.8'  /  '--top-p 0.8'  /  '--top-p,0.8'  のどれで渡しても分けて speech に渡す
# （powershell -File 経由だと配列が 1 つの文字列 "--top-p,0.8" になるため）
foreach ($e in $Extra) { $common += @($e -split '[,\s]+' | Where-Object { $_ }) }

# 1 回の生成は最大 ~47 秒なので、空行区切りの段落ごとに分けて生成する
$parts = @(($Text -replace "`r", '') -split "\n\s*\n" | ForEach-Object { ($_ -replace '\s*\n\s*', '').Trim() } | Where-Object { $_ })
$OutFull = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine((Get-Location).Path, $Out))

if ($parts.Count -eq 1) {
  Invoke-Speech -Arguments (@('tts', $model) + $common + @('-o', $OutFull, $parts[0]))
} else {
  $tmpDir = Join-Path $env:TEMP ("drama3-" + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmpDir | Out-Null
  try {
    $wavs = @()
    for ($i = 0; $i -lt $parts.Count; $i++) {
      Write-Host ("==> [{0}/{1}] {2}" -f ($i + 1), $parts.Count, $parts[$i])
      $w = Join-Path $tmpDir ("{0:D4}.wav" -f $i)
      Invoke-Speech -Arguments (@('tts', $model) + $common + @('-o', $w, $parts[$i]))
      $wavs += $w
    }
    Join-WavFiles -Inputs $wavs -Output $OutFull -GapSeconds $Gap
  } finally {
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
  }
}
Write-Host "==> 保存しました: $OutFull"
