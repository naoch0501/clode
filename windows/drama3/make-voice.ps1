<#
.SYNOPSIS
  参照音声（自分の声の wav）から Drama-3 用の声ファイル (voices\<Name>.voice.gguf) を作る。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\drama3\make-voice.ps1 -Wav ref.wav -Transcript "今日はいい天気ですね。" -Name myvoice
  # 文字起こしをファイルで渡す
  powershell -ExecutionPolicy Bypass -File windows\drama3\make-voice.ps1 -Wav ref.wav -TranscriptFile ref.txt -Name myvoice

.NOTES
  参照音声は 10〜30 秒程度、雑音や BGM が無く 1 人だけが喋っているものが良い。
  Transcript は参照音声で実際に喋っている内容と一字一句合わせること（品質に直結する）。
  使ってよいのは自分の声、または本人から明確な許可を得た声だけ。
#>
param(
  [Parameter(Mandatory)][string]$Wav,
  [string]$Transcript,
  [string]$TranscriptFile,
  [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Name
)

. "$PSScriptRoot\common.ps1"

if (-not (Test-Path $Wav)) { throw "wav がありません: $Wav" }
if ($TranscriptFile) { $Transcript = (Get-Content -Raw -Encoding UTF8 $TranscriptFile).Trim() }
if (-not $Transcript) { throw "-Transcript か -TranscriptFile で参照音声の文字起こしを指定してください" }

New-Item -ItemType Directory -Force -Path $Drama3Voices | Out-Null
$model = Get-Drama3Model
$out = Join-Path $Drama3Voices "$Name.voice.gguf"

Write-Host "==> 声ファイルを作成: $out"
Invoke-Speech -Arguments @('voice', $model, (Resolve-Path $Wav).Path, '--transcript', $Transcript, $out)
Write-Host "完了。使い方:  speak.ps1 -Voice $Name -Text `"...`""
