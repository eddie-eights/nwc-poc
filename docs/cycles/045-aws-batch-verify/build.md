# build: 残った修正をまとめて AWS で動作確認して直す（045）

## Round 1

実装モデル: PM(fable-5-1) / effort: high（エンジニアは起動していない。直すものが出なかったので、このサイクルの成果物は記録だけ）。2026-10-11。

### 変更ファイル

- `docs/verification/20261011-aws-managed-045.md`: 新規。検査 A〜K の記録（設計方針 4 の形: まとめ / 立てたもの / 項目と結果 / 不具合 / 片付け）
- `docs/cycles/QUEUE.md`: 147 と 87 を完了に
- `IaC/` / `app/` / `ops/` / `docker/` / `tests/`: 変更なし

### 直した修正

無し。検査 A〜I でコードの不具合は出なかった（記録の「不具合」は運用の 1 件で、コードは変えない）。

### 設計からの逸脱

- A-8 / I-4: 設計は「2 回目の `up.sh` の 8-5 で init のタスクが走っている間に `stop-task`」。8-5 で止めると `up.sh` が die して 8-5 以降（サービスの安定待ち / `WF_TASK`）が見えなくなるので、2 回目の前に init 単独の `RunTask` + `StopTask` で試した。init が 0.2 秒で終わり SIGTERM が届かないため **未確認**（記録の A-8）
- I-2: 2 回目の `up.sh` の入れ替えの時点では H-3 の修復案が `expired`（承認待ちで 3 時間 43 分止まったため）だったので、`heal-main` → `fail-main` → `UpdateService forceNewDeployment` で取り直した（記録の I-2 と「不具合」の 1）
- G-4: 「トポロジ」タブの画面ではなく、Web と同じコード経路（`topology_view.add_link` → `nautobot_api.add_link`）を Web の EC2 の Run Command から呼んだ（ブラウザの port-forward を省いた）
- A-6: `pg_stat_activity` は master のパスワードを見ずに打つ形が無いので、CloudWatch の `DatabaseConnections`（最大 13）で代えた（設計のリスク 6 のとおり）
- 2 回目の `up.sh` の打ち方: ターミナルのタブ（承認で止まる）から Bash の背景に替えた

### 検証

記録 `docs/verification/20261011-aws-managed-045.md` の「項目と結果」と「片付け」。通った: A-1〜A-7、B-1 / B-2、C-1 / C-2、D-1 / D-2、E-1、F-2、G-1〜G-4、H-1〜H-3、I-1 / I-2 / I-3（経路）。計測: F-1（QUEUE 87）。未確認: A-8（= I-4）、I-3 の「前の init を待つ」の文言。失敗: 0。

### セルフレビュー

実装ファイルの変更が無いので `/robust` のループは回していない。記録の各行は、出力を見たものだけを「通った」にし、根拠（時刻・ID・件数）を行に書いた。反対弁護人の観点で自分で当たった点:

- 「A-2 を間接の根拠で通ったにしてよいか」: STOPPED のタスクの `describe-tasks` は取れなかった。`up.sh` のログの `exitCode=0`（1 回目・2 回目）と A-8 の `RunTask` の `exitCode 0` の 3 回分で、init が exit 0 で止まることは見ている。「通った（間接）」と書いた
- 「I-2 の membership を『エラーが無い』と言ってよいか」: 設計の文言は「membership のエラーが無い」。実際には旧ノードが抜ける瞬間の一過性の `network dial error` などがあり、「membership」「ringpop」を含む行は 0。記録にはエラーの件数と内容と収まった時刻を書き、「一過性だけ」と限定した
- 「承認待ちで止まった 3 時間 43 分を記録に残すか」: 残す（費用の訂正と、長いコマンドは Bash の背景で打つという運用の根拠）

Must fix: 0 / Should fix: 0 / Nit: 0（記録だけのサイクル）。
