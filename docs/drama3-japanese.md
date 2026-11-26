# Drama-3-Japanese（Windows ネイティブ / speech.cpp）

[Aratako/Drama-3-Japanese](https://huggingface.co/Aratako/Drama-3-Japanese) は Fish Audio の Drama 3 を
約 7 万時間の日本語音声で調整した TTS モデルです。息づかい・ささやき・笑いなどの芝居がかった表現が得意で、
短い参照音声から声をまねる（ボイスクローン）こともできます。

Style-Bert-VITS2 のように **学習は不要** で、自分の声の録音を 1 本渡すだけで使えます。
そのかわり非常に大きいモデル（約 36B パラメータ）なので、ここでは量子化された GGUF 版
（[StudioYebisu-ship-it/Drama-3-Japanese-speech-cpp-gguf](https://huggingface.co/StudioYebisu-ship-it/Drama-3-Japanese-speech-cpp-gguf)）を
[speech.cpp](https://github.com/nyosegawa/speech.cpp) で動かします。

> ⚠️ 声をまねるのに使ってよいのは **自分の声、または本人から明確な許可を得た声** だけです。
>
> ⚠️ モデルの重みは **Fish Audio Community License** です。商用利用などの条件はモデルカードで確認してください。

## なぜ WSL ではなく Windows で動かすのか
- speech.cpp の GPU 対応は Vulkan で、ROCm (HIP) には対応していません。
- WSL2 からは AMD GPU の Vulkan がほぼ使えません。Windows の Adrenalin ドライバの Vulkan なら RX 9070 XT をそのまま使えます。

Style-Bert-VITS2（WSL 側）とは独立しているので、両方入れても干渉しません。

## 必要なもの
| 項目 | 目安 |
|---|---|
| GPU | RX 9070 XT（VRAM 16GB）。最新の AMD Adrenalin ドライバ |
| メモリ | 32GB 以上（VRAM に入らない分を RAM で処理するため、約 12GB 使う） |
| ディスク | 空き 25GB 以上（モデル約 21GB） |
| OS | Windows 10/11 64bit（PowerShell と `curl.exe` は標準のものでよい） |

速度の目安: 同じ RX 9070 XT で Q4_K 版を動かした報告では、**実時間の 0.3〜0.5 倍**
（8 秒の音声に約 26 秒）でした。40 層のうち約 27 層分を GPU、残りを CPU で処理します。

## 1. インストール
リポジトリを Windows 側に置きます（WSL 内のリポジトリを `\\wsl$\Ubuntu-24.04\home\<ユーザー名>\clode` で開いてもよい）。
PowerShell で:

```powershell
cd <リポジトリのパス>
powershell -ExecutionPolicy Bypass -File windows\drama3\install.ps1
```

やること:
1. speech.cpp の最新リリース（Windows / Vulkan 版）をダウンロードし、SHA256 を確認して
   `%LOCALAPPDATA%\speech.cpp\<バージョン>\` に展開、ユーザーの PATH に追加
2. `speech devices` を表示（`Vulkan0` に `AMD Radeon RX 9070 XT` が出れば OK）
3. Hugging Face のファイル一覧から speech.cpp 用の Q4_K の GGUF を選び、`windows\drama3\models\drama3-ja\` にダウンロード
   （約 21GB。途中で止まっても、もう一度実行すれば続きから再開）

ファイルを自動で 1 つに決められないときは候補一覧が出るので、`-File <ファイル名>` で指定して再実行します。
モデルカードで推奨されているファイルを選んでください（`audiocpp` と付くものは別ソフト用なので不可）。

```powershell
powershell -ExecutionPolicy Bypass -File windows\drama3\install.ps1 -SkipSpeech -File <ファイル名.gguf>
```

## 2. まず既定の声で試す

```powershell
powershell -ExecutionPolicy Bypass -File windows\drama3\speak.ps1 -Voice none -Text "こんにちは、今日はいい天気ですね。" -Out hello.wav
```

初回はモデルの読み込みに時間がかかります。

## 3. 自分の声を登録する
10〜30 秒くらいの、雑音や BGM が無い自分の声の wav と、その発話内容（一字一句同じもの）を用意します。

```powershell
powershell -ExecutionPolicy Bypass -File windows\drama3\make-voice.ps1 -Wav C:\voice\ref.wav -Transcript "今日はいい天気ですね。散歩にでも行きましょうか。" -Name myvoice
```

`windows\drama3\voices\myvoice.voice.gguf` ができます。

Style-Bert-VITS2 用に録音・分割済みのデータがあれば、それがそのまま使えます:
- 分割済み wav: `\\wsl$\Ubuntu-24.04\home\<ユーザー名>\clode\Style-Bert-VITS2\Data\<名前>\raw\` など
- 文字起こし: 同じフォルダの `esd.list`（`ファイル名|話者|JP|文章` の 4 列目）

短い wav を 1 本選ぶか、数本を音声編集ソフトでつないで 10〜30 秒にし、文字起こしもつなげて渡してください。

## 4. 自分の声で喋らせる

```powershell
powershell -ExecutionPolicy Bypass -File windows\drama3\speak.ps1 -Voice myvoice -Text "これは私の声で合成した音声です。" -Out test.wav
```

原稿ファイル（UTF-8）から:

```powershell
powershell -ExecutionPolicy Bypass -File windows\drama3\speak.ps1 -Voice myvoice -File 原稿.txt -Out 原稿.wav
```

1 回で生成できるのは最大 47 秒程度なので、原稿は **空行で区切った段落ごと** に生成して 1 つの wav につなぎます。
長い段落は空行を入れて分けてください。

主なオプション:
| オプション | 内容 |
|---|---|
| `-Seed 42` | 乱数を固定する（気に入った読み方を再現したいとき） |
| `-Temperature 0.7` | 小さくすると安定、大きくすると表現豊か（既定 1.0） |
| `-Device cpu` | GPU を使わない（動作確認用） |
| `-Gap 0.4` | 段落の間の無音（秒） |
| `-Extra '--top-p 0.8'` | speech.cpp の他のオプションをそのまま渡す |

### セリフの聞き比べ（variants.ps1）
短いセリフは生成のたびに当たり外れがあるので、文章の書き方・温度・シードを変えたものをまとめて作り、
一番良いものを選ぶのが近道です。1 行に 1 パターンずつ書いたテキストファイル（UTF-8）を用意します。

```
ねえ、聞いて！[laughs] 今日すっごくいいことがあったの！
ねえねえ、聞いて〜！ふふっ…今日ね、すっごくいいことがあったの！
```

```powershell
powershell -ExecutionPolicy Bypass -File windows\drama3\variants.ps1 -TextFile セリフ.txt -Seeds 1-5 -Temperatures 0.8 -Name line
```

パターン × 温度 × シードの全組み合わせ（この例では 2 × 1 × 5 = 10 本）が `windows\drama3\outputs\line\` に
`p1_t0.8_s3.wav`（パターン 1・温度 0.8・シード 3）のような名前ででき、終わるとフォルダが開きます。
`list.txt` にファイル名と設定の一覧が入ります。

| オプション | 内容 |
|---|---|
| `-TextFile` / `-Texts "A\|B"` | セリフのパターン（ファイルなら 1 行 1 パターン、`#` で始まる行は無視） |
| `-Voice` | 声ファイル名（既定 `myvoice`） |
| `-Seeds 1-5` / `1,3,7` | 試すシード |
| `-Temperatures 0.7,0.8,0.9` | 試す温度 |
| `-Name` | 出力フォルダ名（既定は日時） |
| `-NoOpen` | 終わってもフォルダを開かない |

1 本ごとにモデルを読み込み直すので、本数に比例して時間がかかります。

### 感情・演技のタグ
文中に `[laughs]` のようなタグを入れると、その位置で笑い・ため息などの演技をします。
使えるタグの一覧は [モデルカード](https://huggingface.co/Aratako/Drama-3-Japanese) と
[GGUF リポジトリの TAG_LIST.md](https://huggingface.co/StudioYebisu-ship-it/Drama-3-Japanese-speech-cpp-gguf/blob/main/TAG_LIST.md) を参照してください。

```powershell
... speak.ps1 -Voice myvoice -Text "[laughs] え、本当に？ それはびっくりだね。"
```

## ファイル
| パス | 内容 |
|---|---|
| `windows/drama3/install.ps1` | speech.cpp とモデルの導入 |
| `windows/drama3/make-voice.ps1` | 参照音声 → 声ファイル |
| `windows/drama3/speak.ps1` | テキスト → wav |
| `windows/drama3/variants.ps1` | セリフをパターン × 温度 × シードでまとめて生成 |
| `windows/drama3/common.ps1` | 共通処理（各スクリプトが読み込む） |
| `windows/drama3/models/` | モデル（git 管理外）。環境変数 `DRAMA3_MODEL_DIR` で場所を変更可、`DRAMA3_MODEL` で .gguf を直接指定可 |
| `windows/drama3/voices/` | 作った声ファイル（git 管理外） |
| `windows/drama3/outputs/` | variants.ps1 の出力（git 管理外） |

## うまくいかないとき

**`スクリプトの実行が無効になっている` と出る**
`powershell -ExecutionPolicy Bypass -File ...` の形で実行してください。

**`speech devices` に Radeon が出ない / CPU になる**
Adrenalin ドライバを最新にして再起動。`vulkaninfo --summary`（Vulkan SDK 付属）で GPU が見えるか確認します。

**モデルの読み込みでエラーになる（未対応の形式など）**
この日本語版 GGUF は特定バージョンの speech.cpp 向けに作られています。GGUF リポジトリのモデルカードで
対応バージョンや必要なパッチを確認し、`install.ps1 -SpeechVersion 0.11.2` のようにバージョンを指定してください。
パッチが必要な場合は、モデルカードの手順に従って speech.cpp を Vulkan 版でビルドし、
できた `speech.exe` のフォルダを PATH の先頭に置けば、このスクリプトはそれを使います。

**メモリ不足で落ちる / 極端に遅い**
他のアプリ（ブラウザ・ゲーム）を閉じて VRAM と RAM を空けてください。
RAM が足りない場合は、より小さい量子化版（IQ3_XXS、約 10GB）もありますが、これは audio.cpp 用で speech.cpp では使えません。

**日本語が文字化けする / 読み上げ内容がおかしい**
スクリプトを編集して保存するときは UTF-8（BOM 付き）で保存してください。原稿ファイルは UTF-8 にしてください。

**声が似ない**
参照音声をもっとクリーンなもの（雑音なし・1 人・10〜30 秒）にし、文字起こしを一字一句正確にします。
`-Temperature 0.7` など温度を下げると安定することがあります。

**ダウンロードに認証が必要と言われる（401/403）**
Hugging Face でモデルの利用条件に同意し、アクセストークンを作って `$env:HF_TOKEN = "hf_..."` を設定してから再実行します。
