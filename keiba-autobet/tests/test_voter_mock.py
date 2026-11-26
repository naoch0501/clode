"""模擬の投票サイト（ローカル HTML）で RakutenVoter の手順実行・残高照合を確認する。

Playwright が無い環境ではスキップ。Chromium の場所は環境変数 KEIBA_TEST_CHROMIUM で指定可。
"""

import os
from pathlib import Path

import pytest
import yaml

pytest.importorskip("playwright.sync_api")

from keiba_autobet.models import Bet  # noqa: E402
from keiba_autobet.voter import rakuten as rk  # noqa: E402
from keiba_autobet.voter.base import HaltError  # noqa: E402

MOCK_HTML = """<!doctype html><meta charset="utf-8"><body>
<div id="login"><input id="uid"><input id="pw" type="password"><button id="go">ログイン</button></div>
<div id="app" style="display:none">
  <a>ログアウト</a> <span class="balance">10,000円</span>
  <div id="race"></div>
  <button>単勝</button><button>馬複</button>
  <div class="col1">%s</div><div class="col2">%s</div>
  <input class="amount"><button id="set">セット</button>
  <button id="cfm">投票内容を確認</button><div id="summary"></div>
  <input id="pin" type="password" style="display:none"><button id="vote">投票する</button><div id="done"></div>
</div>
<script>
let bal = 10000, list = [], type = '', legs = [];
const q = s => document.querySelector(s);
q('#go').onclick = () => { if (q('#uid').value==='me' && q('#pw').value==='secret') {
  q('#login').style.display='none'; q('#app').style.display='block'; } };
q('#race').textContent = (location.hash.slice(1) || '0') + 'R';
document.querySelectorAll('button').forEach(b => { if (['単勝','馬複'].includes(b.textContent)) b.onclick = () => { type=b.textContent; legs=[]; }; });
document.querySelectorAll('.col1 input, .col2 input').forEach(i => i.onclick = () => legs.push(i.value));
q('#set').onclick = () => { list.push({type, legs:[...legs], amt: Number(q('.amount').value)*100}); legs=[]; };
q('#cfm').onclick = () => { const t = list.reduce((a,b)=>a+b.amt,0);
  q('#summary').textContent = `合計 ${t.toLocaleString()}円 ${list.length}点`; q('#pin').style.display='inline'; };
q('#vote').onclick = () => { if (q('#pin').value!=='1234') return;
  const t = list.reduce((a,b)=>a+b.amt,0); bal -= (window.CHEAT ? t+100 : t); list=[];
  q('.balance').textContent = bal.toLocaleString()+'円'; q('#done').textContent='受け付けました'; };
</script>"""


def flows(url):
    return {
        "urls": {"bet_top": url},
        "logged_in_check": [{"wait_for": {"selector": "text=ログアウト", "timeout_sec": 1}}],
        "login": [
            {"goto": "{bet_top}"},
            {"fill": {"selector": "#uid", "value": "{user_id}"}},
            {"fill": {"selector": "#pw", "value": "{password}"}},
            {"click": "#go"},
            {"wait_for": "text=ログアウト"},
        ],
        "balance": [{"read_text": {"selector": ".balance", "var": "balance"}}],
        "open_race": [{"goto": "{bet_top}#{race_no}"}, {"optional": [{"click": "#go"}]},
                      {"wait_for": "text={race_no}R"}],
        "add_bet": [
            {"click": "button:text-is('{bet_type}')"},
            {"for_each_leg": [{"click": ".col{pos} input[value='{n}']"}]},
            {"fill": {"selector": ".amount", "value": "{units}"}},
            {"click": "#set"},
        ],
        "confirm": [{"click": "#cfm"}, {"expect_text": "{total_fmt}円"}, {"expect_text": "{points}点"}],
        "submit": [{"fill": {"selector": "#pin", "value": "{pin}"}}, {"click": "#vote"},
                   {"wait_for": "text=受け付けました"}],
    }


@pytest.fixture
def voter(tmp_path, monkeypatch):
    radios = "".join(f'<input type="radio" value="{i}">' for i in range(1, 13))
    page = tmp_path / "mock.html"
    page.write_text(MOCK_HTML % (radios, radios), encoding="utf-8")
    sel = tmp_path / "sel.yaml"
    # login 後もセッションが残るよう、同一ページ内のハッシュ遷移だけで画面を切り替える
    f = flows(page.as_uri())
    f["open_race"] = [{"wait_for": "text=ログアウト"}]
    sel.write_text(yaml.safe_dump(f, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(rk.credentials, "load", lambda: {"user_id": "me", "password": "secret", "pin": "1234"})
    exe = os.environ.get("KEIBA_TEST_CHROMIUM") or next(
        (str(p) for p in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome")), None)
    v = rk.RakutenVoter(sel, profile_dir=tmp_path / "prof", shot_dir=tmp_path / "shots",
                        headless=True, slow_mo_ms=0, executable_path=exe)
    v.start()
    yield v
    v.close()


def test_place_and_verify_balance(voter):
    assert voter.balance() == 10000
    bets = [Bet("202611261813080312", "s", "単勝", (3,), 200), Bet("202611261813080312", "s", "馬複", (3, 11), 100)]
    res = voter.place(bets)
    assert res.ok, res.message
    assert voter.balance() == 9700


def test_balance_mismatch_halts(voter):
    voter.page.evaluate("window.CHEAT = true")
    with pytest.raises(HaltError):
        voter.place([Bet("202611261813080312", "s", "単勝", (3,), 100)])


def test_submit_false_stops_at_confirm(voter):
    voter.submit = False
    res = voter.place([Bet("202611261813080312", "s", "単勝", (3,), 100)])
    assert not res.ok and voter.balance() == 10000
