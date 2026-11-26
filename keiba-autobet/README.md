# keiba-autobet — 地方競馬（楽天競馬）条件付き自動投票ツール

楽天競馬の出馬表・オッズを取得し、YAML で書いた条件（オッズ・人気／レース条件／馬・騎手・調教師／独自スコア）に
合ったときだけ、締切前に自動で投票します。

- **既定は模擬投票**です。`config.yaml` の `live_enabled: true` と `run --live` の **両方** がそろったときだけ実際に投票します。
- 1 点・1 レース・1 日の購入上限と残高下限を必ずチェックし、超える買い目は買いません。
- 投票後は残高を読み直し、減った額が投票額と一致しなければ **その場で停止** します。
- 同じレース・同じ戦略で二重に買いません（失敗した場合も再試行しません）。
- 楽天の ID・パスワード・暗証番号は Windows 資格情報マネージャーに保存し、ファイルやログには残しません。

> ⚠ 注意
> - 馬券の購入は自己責任です。このツールは利益を保証しません。20 歳未満は馬券を購入できません。
> - 楽天競馬の利用規約で自動操作が制限されている可能性があります。利用前に規約を確認してください。
> - **ログイン後の投票画面の操作手順（`selectors_rakuten.yaml`）は推測で書かれています。**
>   下の「4. 投票画面の手順を確定する」を済ませるまで `--live` は使えない前提です。

## 1. セットアップ（Windows）

[Python 3.10 以上](https://www.python.org/downloads/windows/) を入れ（インストール時に「Add python.exe to PATH」にチェック）、
PowerShell で:

```powershell
cd keiba-autobet
py -m venv .venv
.venv\Scripts\Activate.ps1          # 実行できない場合: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[live]"
python -m playwright install chromium
copy config.example.yaml config.yaml
```

## 2. 動作確認（投票しない）

```powershell
keiba-autobet races                         # 今日の全レース（RACEID・発走時刻・状態）
keiba-autobet races --date 2026-11-27
keiba-autobet show 202611261813080312       # 出走馬・単勝/複勝オッズ・人気
keiba-autobet eval 202611261813080312 -s strategies\example.yaml   # 条件判定だけ
keiba-autobet run --once                    # 今投票できる全レースを 1 回判定して模擬投票
keiba-autobet run                           # 一日中監視して模擬投票（発走 5 分前に判定）
keiba-autobet history                       # 投票履歴（模擬・実の両方）
```

ログは `logs\YYYYMMDD.log`、履歴は `data\ledger.sqlite3` に残ります。

## 3. 条件（戦略）の書き方

`strategies\example.yaml` をコピーして編集します。

```yaml
strategies:
  - name: 南関_上位3頭馬複BOX         # 名前（重複不可。二重投票防止のキーにもなる）
    enabled: true
    when:
      race:                            # レース条件
        track: [大井, 川崎, 船橋, 浦和]
        surface: ダ
        distance: {min: 1200, max: 1600}
        favorite_odds: {min: 3.0}      # 1番人気の単勝オッズ
      entry:                           # 買う候補の馬の条件
        popularity: {max: 3}
    select: {min_candidates: 3}        # 候補が 3 頭そろわなければ見送り
    bet: {type: 馬複, combine: box, amount: 100}
```

**値の書き方**: `値`（一致） / `[値1, 値2]`（いずれか） / `{min: , max: }`（範囲） / `{not_in: [...]}` / `{contains: 文字列}`（部分一致）

| レース条件 `when.race` | 内容 |
|---|---|
| `track` | 競馬場（大井・川崎・船橋・浦和・名古屋・園田・金沢・門別・盛岡・水沢・笠松・姫路・高知・佐賀 など） |
| `race_no` / `name` | レース番号 / レース名（`{contains: 特別}` など） |
| `surface` / `distance` | ダ・芝 / 距離（m） |
| `runners` | 出走頭数（取消を除く） |
| `weather` / `going` | 天候 / 馬場（良・稍重・重・不良） |
| `post_hour` | 発走時刻の「時」 |
| `favorite_odds` / `favorite_place_odds` / `second_odds` | 1番人気の単勝・複勝(下限)オッズ / 2番人気の単勝オッズ |

| 馬の条件 `when.entry` | 内容 |
|---|---|
| `popularity` / `win_odds` / `place_odds` / `place_odds_max` | 人気 / 単勝 / 複勝の下限・上限 |
| `number` / `frame` | 馬番 / 枠番 |
| `horse` / `jockey` / `trainer` / `belong` | 馬名 / 騎手 / 調教師 / 騎手の所属（楽天の表記は略称なので `contains` 推奨。`show` で確認） |
| `sex` / `age` / `burden` | 性（牡・牝・セ）/ 年齢 / 負担重量 |
| `score` | 独自スコア `{source: 名前, min: 70}` |

**`select`**: `sort`（`popularity`, `-win_odds`, `-score:名前` など。`-` は降順）・`top`（上位 N 頭）・`min_candidates`・`max_candidates`

**`bet`**

| 項目 | 内容 |
|---|---|
| `type` | 単勝・複勝・枠複(枠連)・枠単・馬複(馬連)・馬単・ワイド・3連複・3連単 |
| `amount` | 1 点あたりの金額（100 円単位） |
| `combine` | `single`（候補ごとに 1 点。単勝・複勝の既定）/ `box`（候補のボックス。その他の既定）/ `nagashi` / `formation` |
| `axis` | `nagashi` の軸馬の条件（例 `{popularity: 1}`）。相手は `when.entry` の候補 |
| `multi` | 馬単・3連単の流しをマルチにする |
| `legs` | `formation` の各着の条件（`candidates`=候補全体、`all`=全馬、または条件。`sort`/`top` も可） |
| `max_points` | これより点数が多ければ見送り（既定 100） |

### 独自スコア

```yaml
scores:
  my_scores:
    csv: ../data/scores.csv           # 列: race_id,number,score（戦略ファイルからの相対パス）
  my_model:
    python: my_model:predict          # def predict(card) -> {馬番: スコア}
    path: ../plugins
```

CSV は実行中に更新しても次の判定から反映されます。Python 関数には `RaceCard`（`card.race`, `card.entries` に
最新オッズ入り）が渡ります。

## 4. 投票画面の手順を確定する（実投票の前に必須）

```powershell
keiba-autobet set-credentials                               # 楽天会員ID・パスワード・暗証番号を保存
keiba-autobet check --headed                                # ログイン → 残高表示 → Inspector で一時停止
keiba-autobet check --headed --race <RACEID> --number 3     # 単勝3番 100円を確認画面まで（確定はしない）
```

1. `src\keiba_autobet\voter\selectors_rakuten.yaml` を `my_selectors_rakuten.yaml` としてコピーし、
   `config.yaml` の `voter.selectors: my_selectors_rakuten.yaml` で指定します。
2. `check --headed` で開いたブラウザで、Playwright Inspector の **Pick locator** を使って
   ログイン・残高・券種・馬番・金額・確認・投票ボタンのセレクタを調べ、「要確認」の行を直します。
   （`python -m playwright codegen https://keiba.rakuten.co.jp/` で操作を記録してセレクタを拾うのも便利です）
3. 2 段階認証が出た場合はブラウザで手入力してください（最大 3 分待ちます）。
   ログイン状態は `data\browser-profile` に保存されるので、次回以降は不要になることが多いです。
4. `check --race ...` で確認画面まで進み、`logs\screenshots\confirm_*.png` の内容が正しいことを確認します。
5. 最後に `limits.max_per_day: 100` などごく小さい上限で `run --live --once` を 1 回だけ試し、
   楽天競馬の投票履歴と `keiba-autobet history` が一致することを確認します。

画面が失敗したときは `logs\screenshots\error_*.png` に画面が保存されます。

## 5. 実投票で毎日動かす

```yaml
# config.yaml
live_enabled: true
limits: {max_per_bet: 500, max_per_race: 2000, max_per_day: 5000, min_balance: 0}
tracks: [大井, 川崎, 船橋, 浦和]
webhook_url: https://discord.com/api/webhooks/...   # 任意: 投票結果・停止を通知
```

```powershell
keiba-autobet run --live -s strategies\my.yaml
```

**タスクスケジューラで毎日自動起動**（PC はスリープしない設定に）:

1. 「タスクの作成」→ トリガー: 毎日 10:00
2. 操作: プログラム `C:\path\to\keiba-autobet\.venv\Scripts\keiba-autobet.exe`、
   引数 `run --live -s strategies\my.yaml`、開始: `C:\path\to\keiba-autobet`
3. 「ユーザーがログオンしているときのみ実行する」を選ぶ（ブラウザを表示するため）

当日最後のレースの締切を過ぎると自動で終了します。残高不一致などで停止した場合は終了コード 2 で終わり、通知が届きます。
その場合は必ず楽天競馬の投票履歴を手動で確認してください。

## 構成

```
src/keiba_autobet/
  datasource/rakuten.py   楽天競馬の公開ページ（出馬表・単複オッズ）の取得と解析
  strategy/rules.py       条件ルールの評価と買い目生成     strategy/scores.py  独自スコア
  risk.py                 上限チェック                    ledger.py           履歴 (SQLite)
  scheduler.py            発走時刻に合わせた判定・投票     notify.py           Webhook 通知
  voter/dryrun.py         模擬投票                        voter/rakuten.py    Playwright で投票
  voter/steps.py          YAML の画面手順を実行            voter/selectors_rakuten.yaml
```

SPAT4 など別の投票先は `voter/base.py` の `Voter`、別のデータ取得元は `datasource/base.py` の `DataSource` を
実装すれば差し替えられます。

## テスト

```powershell
pip install -e ".[dev,live]"
pytest
```

`tests/fixtures` の HTML は 2026/11/26 に保存した楽天競馬のページです。サイトの構造が変わったら
保存し直してパーサ（`datasource/rakuten.py`）を直してください。
