# Windows 側の準備

## 1. AMD ドライバ
- **AMD Software: Adrenalin Edition** を最新版に更新する（https://www.amd.com/ja/support）。
- WSL 上の ROCm は Windows のドライバ経由で GPU を使うため、**WSL 内に GPU ドライバは入れない**（`01_install_rocm_wsl.sh` は `--no-dkms` で入れるのでこれで正しい）。
- 古いドライバでは WSL から RX 9070 XT が見えないことがある。うまくいかないときはまずドライバを更新する。

## 2. WSL2 と Ubuntu 24.04
PowerShell を **管理者として** 開いて実行する:

```powershell
wsl --install -d Ubuntu-24.04
wsl --update
```

再起動後、スタートメニューから「Ubuntu 24.04」を開き、ユーザー名とパスワードを設定する。

## 3. WSL に割り当てるメモリを増やす（推奨）
学習の前処理と Whisper による文字起こしはメインメモリを多く使う。
`C:\Users\<ユーザー名>\.wslconfig` を作成（またはエディタで開いて）次のように書く:

```ini
[wsl2]
memory=24GB      # PC のメモリの 7〜8 割程度（32GB 搭載なら 24GB）
swap=16GB
```

保存したら PowerShell で `wsl --shutdown` を実行し、Ubuntu を開き直す。

## 4. ファイルの置き場所
- このリポジトリは **WSL 側のホーム（例: `~/clode`）に clone** する。`/mnt/c/...` 上だと I/O が極端に遅くなる。
- Windows のエクスプローラーからは `\\wsl$\Ubuntu-24.04\home\<ユーザー名>\clode` で開ける。録音ファイルはここの `inputs\<モデル名>\` にドラッグ＆ドロップすればよい。
