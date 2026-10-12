# 054 の設計の経緯

## Round 0（2026-10-12）

ユーザーの依頼: 「167 と Splunk のツール追加、両方 QUEUE に積んで進めて」。その前の会話で、Splunk だけの構成（Grafana の一式を立てない）ではエージェントが `search_logs` / `query_metrics` で何も引けないことを確かめていた。

### 質問と回答

1. ツールの形
   - 選んだもの: **決めた検索の新しいツール `search_splunk`**（PM の推奨）。SPL は固定で、引数を検証して埋める。`search_logs` / `query_metrics` はいまのまま
   - 却下: SPL をそのまま受ける汎用のツール。エージェントが `| delete` などを出せ、admin の資格情報と組み合わさると危ない
   - 却下: `search_logs` / `query_metrics` が格納先に応じて Splunk に切り替わる形。同じツール名で戻りの形が構成ごとに変わり、テストと docs が 2 通りになる
2. 資格情報
   - 選んだもの: **既存の admin のパスワード**（SSM の `/<prefix>/splunk/admin-password`。PM の推奨）。読み取り専用のユーザーは QUEUE に積んで後で
   - 却下（いまはやらない）: 読み取り専用のユーザーをこのサイクルで作る。Splunk のタスクの起動時にユーザーを作る仕組みと、そのパスワードの SSM が要り、サイクルが 2 倍になる

## Round 2（2026-10-12）

Round 1 のエンジニアのセルフレビューが、設計側の Should fix として 2 件を PM に回した（`build.md` の Round 1）。PM が直す形を決めた。

- S-5（`| search device="<id>*"` の前方一致で `10.0.0.1` が `10.0.0.10` に、`spine-1` が `spine-10` に当たる）: 完全一致と `<id>.`（FQDN）だけにする。前方一致は FQDN の sysName を拾うためだったので、`.` が続く形に限れば目的は残る
- S-2（機器の絞り込みが `eval` のあとなので、`earliest` の範囲を全部読む。`TIMEOUT = 20` はソケットの 1 回ごとで、全体は Gateway の 30 秒を超えうる）
  - 本文の語 `"<id>"` を基本の検索に足す。エンジニアは「`_raw` に機器名が無いイベント（host だけ）を落とすおそれ」を挙げたが、`splunk_events` の HEC の `host` は `tags.host` か `agent_host`（どちらも本文の `tags` にある）なので、落ちるのは `host` が `unknown` のものだけ（`snmp_sinks.py:619` / `:321` / `:334` を PM が読んだ）
  - 全体の締め切り 25 秒を Python 側に持つ。Splunk の `max_time` は export が受けるかを確かめていないので使わない
- T-10 は「動いていれば」をやめて、使い捨ての Splunk を立てて必ず確かめる（SPL を本物に一度も通していないため）
- SG `lambda` を 3 つの Lambda が共有する件（Should fix 4）: SG は分けない。2 つの Lambda のロールは SSM を読めず、admin のパスワードを持たない。専用の SG は読み取り専用のユーザーと一緒に QUEUE へ
