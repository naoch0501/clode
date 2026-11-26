<#
.SYNOPSIS
  speech.cpp (Windows / Vulkan 版) と Drama-3-Japanese の GGUF を導入する。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\drama3\install.ps1
  powershell -ExecutionPolicy Bypass -File windows\drama3\install.ps1 -SkipModel        # speech.cpp だけ
  powershell -ExecutionPolicy Bypass -File windows\drama3\install.ps1 -File <ファイル名.gguf>  # 取得するファイルを指定

.PARAMETER Repo     GGUF を取得する Hugging Face リポジトリ
.PARAMETER Pattern  ファイル名の候補を絞る正規表現（既定: Q4_K）
.PARAMETER File     取得するファイル名を直接指定（候補が複数あるとき用）
.PARAMETER SpeechVersion  speech.cpp のバージョン（例: 0.11.2。既定: 最新リリース）
#>
param(
  [string]$Repo = 'StudioYebisu-ship-it/Drama-3-Japanese-speech-cpp-gguf',
  [string]$Revision = 'main',
  [string]$Pattern = 'Q4_K',
  [string]$File,
  [string]$SpeechVersion,
  [switch]$SkipSpeech,
  [switch]$SkipModel
)

. "$PSScriptRoot\common.ps1"
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest の進捗表示は非常に遅いので消す
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$hfHeaders = @{}
if ($env:HF_TOKEN) { $hfHeaders['Authorization'] = "Bearer $($env:HF_TOKEN)" }

# --- speech.cpp ----------------------------------------------------------------
function Install-SpeechCpp {
  $api = if ($SpeechVersion) { "https://api.github.com/repos/nyosegawa/speech.cpp/releases/tags/v$SpeechVersion" }
         else { 'https://api.github.com/repos/nyosegawa/speech.cpp/releases/latest' }
  Write-Host "==> speech.cpp のリリース情報を取得: $api"
  $rel = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'drama3-installer' }
  $ver = $rel.tag_name -replace '^v', ''
  $asset = $rel.assets | Where-Object { $_.name -match 'windows' -and $_.name -match 'vulkan' -and $_.name -match '\.zip$' } |
    Select-Object -First 1
  if (-not $asset) {
    Write-Host "リリース $($rel.tag_name) に Windows Vulkan 版の zip が見つかりません。含まれるファイル:"
    $rel.assets | ForEach-Object { Write-Host "  $($_.name)" }
    throw "https://github.com/nyosegawa/speech.cpp/blob/main/docs/install.md の手順で手動インストールしてください。"
  }

  $dest = Join-Path $SpeechHome $ver
  if (Test-Path (Join-Path $dest 'speech.exe') -PathType Leaf) {
    Write-Host "==> speech.cpp $ver は導入済み: $dest"
  } else {
    $tmp = Join-Path $env:TEMP $asset.name
    Write-Host "==> ダウンロード: $($asset.name)"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmp -UseBasicParsing

    $sums = $rel.assets | Where-Object { $_.name -eq 'SHA256SUMS' } | Select-Object -First 1
    if ($sums) {
      $sumText = (Invoke-WebRequest -Uri $sums.browser_download_url -UseBasicParsing).Content
      if ($sumText -is [byte[]]) { $sumText = [System.Text.Encoding]::UTF8.GetString($sumText) }
      $line = ($sumText -split "`n") | Where-Object { $_ -match [regex]::Escape($asset.name) } | Select-Object -First 1
      if (-not $line) { throw "SHA256SUMS に $($asset.name) がありません" }
      $expected = ($line.Trim() -split '\s+')[0].ToLower()
      $actual = (Get-FileHash -Algorithm SHA256 $tmp).Hash.ToLower()
      if ($expected -ne $actual) { throw "SHA256 が一致しません: $($asset.name)" }
      Write-Host "    SHA256 OK"
    } else {
      Write-Warning "SHA256SUMS がリリースに無いため検証をスキップします"
    }

    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Expand-Archive -Path $tmp -DestinationPath $dest -Force
    Remove-Item $tmp -Force
    # zip 内のサブフォルダに入っている場合もあるので探す
    $exe = Get-ChildItem -Path $dest -Recurse -Filter 'speech.exe' | Select-Object -First 1
    if (-not $exe) { throw "展開したファイルに speech.exe がありません: $dest" }
    Write-Host "==> 展開しました: $($exe.FullName)"
  }

  $exePath = (Get-ChildItem -Path $dest -Recurse -Filter 'speech.exe' | Select-Object -First 1).FullName
  $binDir = Split-Path $exePath
  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  # 古いバージョンのパスを除いて新しいものを追加
  $parts = @($userPath -split ';' | Where-Object { $_ -and ($_ -notlike "$SpeechHome*") })
  $newPath = (@($binDir) + $parts) -join ';'
  if ($newPath -ne $userPath) {
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Host "==> ユーザーの PATH に追加: $binDir（新しいターミナルから有効）"
  }
  $env:Path = "$binDir;$env:Path"

  Write-Host "==> 使えるデバイス（Vulkan0 に Radeon RX 9070 XT が出れば OK）"
  & $exePath devices
}

# --- モデル (GGUF) -----------------------------------------------------------
function Install-Model {
  Write-Host "==> $Repo のファイル一覧を取得"
  $info = Invoke-RestMethod -Uri "https://huggingface.co/api/models/$Repo/tree/$Revision`?recursive=true" -Headers $hfHeaders
  $ggufs = @($info | Where-Object { $_.type -eq 'file' -and $_.path -like '*.gguf' })
  if ($ggufs.Count -eq 0) { throw "$Repo に .gguf がありません" }

  if ($File) {
    $target = $ggufs | Where-Object { $_.path -eq $File -or (Split-Path $_.path -Leaf) -eq $File } | Select-Object -First 1
    if (-not $target) { throw "$File が見つかりません" }
  } else {
    # audio.cpp 用・small 版・音声ファイルを除き、Pattern に合うものを選ぶ
    $cands = @($ggufs | Where-Object {
      $_.path -match $Pattern -and $_.path -notmatch 'audiocpp|audio\.cpp|small|IQ3|\.voice\.gguf'
    })
    if ($cands.Count -ne 1) {
      Write-Host "取得するファイルを一意に決められませんでした。候補 ($Repo):"
      $ggufs | ForEach-Object { Write-Host ("  {0,8:N1} GB  {1}" -f ($_.size / 1GB), $_.path) }
      throw "-File <ファイル名> で指定して再実行してください（speech.cpp 用の Q4_K を推奨。モデルカードも確認してください）。"
    }
    $target = $cands[0]
  }

  New-Item -ItemType Directory -Force -Path $Drama3Models | Out-Null
  $files = @($target)
  # 同名の .json（speech.cpp のサイドカー）があれば一緒に取る
  $jsonPath = [System.IO.Path]::ChangeExtension($target.path, '.json')
  $json = $info | Where-Object { $_.path -eq $jsonPath } | Select-Object -First 1
  if ($json) { $files += $json }

  foreach ($f in $files) {
    $out = Join-Path $Drama3Models (Split-Path $f.path -Leaf)
    if ((Test-Path $out) -and ((Get-Item $out).Length -eq $f.size)) {
      Write-Host "==> 取得済み: $out"; continue
    }
    $url = "https://huggingface.co/$Repo/resolve/$Revision/$($f.path)"
    Write-Host ("==> ダウンロード ({0:N1} GB、中断しても再実行で続きから): {1}" -f ($f.size / 1GB), $f.path)
    $curlArgs = @('-L', '--fail', '-C', '-', '--retry', '5', '-o', $out)
    if ($env:HF_TOKEN) { $curlArgs += @('-H', "Authorization: Bearer $($env:HF_TOKEN)") }
    & curl.exe @curlArgs $url
    if ($LASTEXITCODE -ne 0) { throw "ダウンロードに失敗しました (curl exit $LASTEXITCODE)。再実行すると続きから再開します。" }
    if ((Get-Item $out).Length -ne $f.size) { throw "サイズが一致しません: $out。再実行してください。" }
  }
  Write-Host "==> モデル: $(Join-Path $Drama3Models (Split-Path $target.path -Leaf))"
}

if (-not $SkipSpeech) { Install-SpeechCpp }
if (-not $SkipModel) { Install-Model }

Write-Host ""
Write-Host "完了。試しに:"
Write-Host "  powershell -ExecutionPolicy Bypass -File windows\drama3\speak.ps1 -Voice none -Text `"こんにちは、今日はいい天気ですね。`" -Out hello.wav"
