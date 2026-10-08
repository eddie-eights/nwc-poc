# 設計の経緯（014）

## Round 0

PM（SendMessage）との要件のやりとりの要約。割り当ては BACKLOG の 71〜75・78。PM の最初の決定は次のとおり。

- 72: Gradio の設定（`aws s3 sync` まで）を Docker より前に。Docker と Kafbat UI の節は落ちても止めず journald に 1 行出して進む。tests/test_stream.py の user_data の検査（chmod / shebang / set -e の変異検出）を壊さない
- 75: exit 75（stream が無い）は `RestartPreventExitStatus` で再試行を止め、stream を上げたあとの `sudo systemctl start <prefix>-kafka-ui` を docs/deploy.md に書く。AccessDenied やエンドポイント不達は別の終了コードと別の文言。ops/up.sh は触らない
- 78: 注記だけ。本文は書き換えない

### 質問 1: 75 で止めると、通常のデプロイで Kafbat UI が起きなくなる

調べて分かったこと: `ops/up.sh` の流れでは、手順 4-4 で Web の EC2 を reboot するとき stream はまだ無い（stream は手順 7）。いまは 30 秒ごとの再試行で手順 7 のあとに勝手に起きているが、75 で止めると、`SKIP_STREAM` を付けない普通のデプロイでも Kafbat UI が止まったままになり、毎回 `sudo systemctl start` が要る（退行）。OSS 版（`oss/ops/up.sh` の 4-4 と 7-5）も同じ。

出した案:

- (a) 75 で止める。毎回の手作業は docs に書く — 通常のデプロイで手作業が増える
- (b) up.sh の stream のあとに `systemctl start <prefix>-kafka-ui` を足す — up.sh を触らない指示に反する
- (c) 止めずに RestartSec を伸ばす（例: 300 秒） — 「止める」にならない
- (d) Web のユニットに `Wants=<prefix>-kafka-ui.service` を足し、up.sh の手順 8-3（OSS 版は 7-5）の `systemctl restart <prefix>-web` で Kafbat UI を起こす — up.sh を触らずに済む（推奨）

PM の回答: **(d) を採用。** 行 75 の「止める」の意図は「SKIP_STREAM=1 のとき 30 秒ごとに回り続けるのを止める」で、通常のデプロイで手作業が増える退行は意図していない。条件 2 つ。

- 手元の systemd のコンテナで「exit 75 で failed になった kafka-ui が、web の `systemctl restart` で起きること」と「kafka-ui が落ちても web が巻き込まれないこと」の両方を実測し、コマンドと出力を build.md に貼る（restart ジョブが Wants= の先に start ジョブを積むかは実測が根拠）
- design.md に「up.sh の 8-3 と OSS 版の 7-5 の restart が Kafbat UI を起こす」ことを書き、docs/deploy.md の `sudo systemctl start` は SKIP_STREAM のあとに stream だけ上げた場合用として残す

### 質問 2: 終了コードの分け方

手元の AWS CLI で ParameterNotFound と AccessDenied はどちらも 254 だったので、終了コードでは分けられない。標準エラーの `(ParameterNotFound)` で分け、75 = ParameterNotFound（止める）、69 = それ以外（再試行を続ける）を提案。`get-parameters`（InvalidParameters で無いものを返す）は Web のロールに権限が無いので使わない。

PM の回答: そのとおり。文言に AWS CLI のエラー文を添えるのも良い。get-parameters に変えないのも了解。

### 質問 3: user_data の並び

`aws s3 sync` の直後、web/ が無いときの `exit 0` より前に、関数にした Docker と Kafbat UI の節を置く。落ちても 1 行出して進む。

PM の回答: そのとおり。

### 設計で PM の決定から変えたこと

- 「logger で journald に 1 行」は、標準エラーへの 1 行に変えた。AL2023 のイメージに `logger`（util-linux）が無く（手元で確認）、無いと `kafka_ui_setup || logger …` の右側が落ちて `set -e` で user_data が止まる。user_data の標準エラーは cloud-init-output.log と cloud-final.service の journald に入る（EC2 では未確認）。build の報告で PM に伝える
