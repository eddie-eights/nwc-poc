# 設計の経緯: lab の EC2 から NLB の syslog 5140 への受信ルールを足す（037）

## Round 0（2026-10-10、PM）

きっかけ: ユーザーの「並行して進められるタスクは並行して進めて」。`docs/cycles/QUEUE.md` の未着手は 3 行で、Kafbat UI の計測と AWS の動作確認は AWS を立てないと進まない。この行だけが手元で閉じるので、Temporal の履歴を RDS に残す（036）と並行して始めた。

質問は出していない。決定は PR #61 の調査（`docs/troubleshooting.md`）と 2026-10-10 のユーザー決定（AWS の動作確認は最後にまとめて）で尽きている。

調べて分かったこと:

- `sg_rules` の鍵は `from-to-protocol-port` で `only` を含まない（`security_groups.tf` 124 行目）。`only` を外しても送信ルールは作り直されず、受信ルールが 1 本増えるだけ。
- `tests/test_analytics.py` の `EXPECTED_FLOWS` が `only` を 6 列目に持つので、直す場所は 133 行目の 1 タプル。行数は変わらないので `count("{ from = ")` の期待はそのまま。
- docs で「送信だけ」と書いているのは `troubleshooting.md` 419 行目と `faq-fukuda-nwc-poc.md` 266 行目。表で `lab` の送り分に触れているのは `vpc-perimeter.md` 62 行目と `lab-ec2.md` 38 行目で、`core.md` 81 行目は `lab_mgmt` の行しか無い。

却下した案:

- trap の 162 も両側にする: EC2 から直接 trap を送る手順が無い（`lab.sh trap-test` は TRex の netns から送る）。要らない受信は開けない。
- lab の EC2 で OUTPUT チェインにも DNAT を足して `203.0.113.1:5140` へ送れるようにする: 送り元が EC2 の IP のままなので、どのみち NLB の SG を開ける必要がある。SG を直せば NLB の IP へ直接送れるので要らない。
