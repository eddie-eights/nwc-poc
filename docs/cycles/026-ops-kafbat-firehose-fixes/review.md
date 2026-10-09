# up.sh の片付けと Kafbat UI と Firehose の直しを片付ける（026）のレビュー

## Round 1

cold reviewer（文脈を持たない別のエージェント）に依頼した。レビューのモデル: fable-5.1。対象は `origin/main`（1dc9645）→ `feat/026-ops-kafbat-firehose-fixes` の 102f350。設計は `docs/cycles/026-ops-kafbat-firehose-fixes/design.md`、実装の記録は同じ場所の `build.md`（Round 1）。以下は reviewer の報告をそのまま写したもの。

## サマリ

対象は `origin/main`（1dc9645）→ HEAD 102f350 の 13 ファイル、+332 / -14（うち `build.md` が +242。コードと docs の実質は約 90 行）。A（`ops/up.sh` の手順 10）・B（テストのみ）・C（systemd ユニット + コメント + docs 3 本）・D（`time_sleep` + provider + lock）の 4 件を `design.md` の方針 1 行ずつと突き合わせ、設計との食い違いは無かった。`build.md` の「設計との違い」3 点（B の在庫から消す前処理、設計の行番号ずれ、`--group web` 付きの実行）はいずれも設計の意図を変えない。

設計の検証方法 1〜3・5 は自分でも走らせて同じ結果を得た（下）。Must fix は無い。Should fix 1 件は実装者のセルフレビューにもある `time_sleep` の `triggers` 無しで、同意見。

### 見た観点 / 見ていない観点

見た観点:

- design.md との整合性: 方針 A〜D の各項目と変更対象ファイルの表を diff と照合。`ops/up-common.sh` が無変更（B.3「コードは直さない」）であることを変更ファイル一覧で確認
- correctness / runtime: `on_exit`（`ops/up.sh:156-165`）を `set -euo pipefail` 下で手順 10 から呼んでも落ちない条件を読んだ（`GRAPH_PID` は `:985` で空、`rm -f` は欠けても 0、`TF_AWS_CONFIG` は `tf()` の `env` でだけ渡し export していないので exec する `aws` に影響しない）。`MSK_SCRAM_INPUT` / `SECRET_INPUT` / `NAUTOBOT_CTX` / `TF_AWS_CONFIG` は `set -u` 下でも初期化済み（`ops/up-common.sh:106, :377`、`ops/up.sh:153`、`ops/common.sh:17`）
- systemd の設定（C）: `SuccessExitStatus=` / `RestartPreventExitStatus=` / `Restart=always` の組み合わせは systemd.service(5) の記述どおり（`RestartPreventExitStatus=` は `Restart=` に優先、成功終了の `Type=simple` は `inactive (dead)`）。読んだだけ
- Terraform（D）: `history.tf` の依存の向き（role → policy → time_sleep → Firehose）、Firehose の属性が `time_sleep` を参照しない（置き換えにならない）こと、`count` の無いこと。lock の time の block は `agent/.terraform.lock.hcl` と `diff` で同一（20 行）
- 動的確認（review-026 のチェックアウトで、`.terraform` と venv は scratchpad に置いて実行。checkout には何も書いていない）:
  - `uv run --frozen --group dev --group web python tests/test_oss_ops.py` → `通過 203 / 失敗 0`
  - `uv run --frozen --group dev --group web python tests/test_stream.py` → `通過 108 / 失敗 0`
  - `uv run --frozen --group dev --group web python tests/test_analytics.py` → `通過 520 / 失敗 0`
  - `TF_DATA_DIR=<scratch> terraform -chdir=IaC/terraform/oss/pipeline/analytics init -backend=false -lockfile=readonly` → `Reusing previous version of hashicorp/time from the dependency lock file` / `Installed hashicorp/time v0.14.2 (signed by HashiCorp)` / `successfully initialized`、`validate` → `Success!`
  - 同じ形で `IaC/terraform/aws-managed/pipeline/analytics` も init（readonly）と `validate` → `Success!`
- security: 秘密の値はテストの `test-value` だけ。`ensure_fixed_secret` は値を環境変数で Python に渡し、コマンドラインに載せない（既存）。新たな外部入力は無い
- docs: `RestartPreventExitStatus` / `SuccessExitStatus` / 「failed のまま」/ `is-system-running` の記述を `docs/` `IaC/` 全体（`docs/cycles` と `docs/verification` を除く）で grep し、矛盾する残りが無いことを確認

見ていない観点:

- **AWS での動作**（C の `inactive (dead)` / `degraded` にならないこと、D の 30 秒で足りること、既存環境での `plan`）。設計もこのサイクルでは AWS に触らない約束なので対象外
- `bash ops/check.sh` の全体（fmt / 9 ルートの validate / 16 本のテスト）。`build.md` の記録はあるが、自分で走らせたのは上の 3 本と 2 ルートの validate だけ
- aws-managed 側の lock を `-lockfile=readonly` 無しで init して差分が出ないこと（設計のリスク 5）。自分は readonly でしか init していない。`build.md` に記録あり
- review-026 の `git status`（自分の環境からは別 worktree への git 操作が禁止されていて打てない）。scratchpad 以外に書いた物は無いが、呼び出し側で確認する

## Must fix

None

## Should fix

- `[correctness]` `IaC/terraform/aws-managed/pipeline/analytics/history.tf:121-125` — `time_sleep.alert_firehose_iam` に `triggers` が無いので、state を残したままロールが作り直される場面（`OWNER` / `PROJECT` を変えて同じ state で apply。state は `ops/up-common.sh:22` のとおりルートごとのローカルファイルで、接頭辞ごとに分かれていない）では `time_sleep` が再作成されず、新しいロール → 新しい Firehose の順に待ち無しで作られて、直したかった `InvalidArgumentException` がそのまま再発しうる。いつもの `down.sh` → `up.sh` では `time_sleep` も消えて作り直すので起きない。Should にした理由: 起きるのは接頭辞を変えて state を使い回したときだけで、壊れても打ち直せば通る（2026-10-09 と同じ）ため。直すなら `triggers = { role = aws_iam_role.alert_firehose.unique_id }` の 1 行（`build.md` のセルフレビューの指摘 1 と同じ）。設計はブロックの形を指定しているので、PM が設計の追記として判断する

## Nit

- `[missing tests]` `tests/test_analytics.py:427` — `create_duration` の正規表現 `"(\d+)s"` は秒の表記しか受けない。設計のリスク 3 のとおり延ばすとき `"1m"` と書くと、条件を満たしていても落ちる。延ばすときに一緒に直せば足りる
- `[runtime bugs]` `ops/up.sh:1352-1353` — 手順 10 の `on_exit` は trap の中ではなく `set -euo pipefail` の下で走るので、`NAUTOBOT_CTX`（`:652` の `mktemp -d` に `app/nautobot/` などを写したもの）に消せない物があると `rm -rf` の失敗で exec の前に止まる。写し元に書けない物が無い（`build.md` の実測）のと、OSS 版も同じ形（`ops/oss/up.sh:656-658`）で設計どおりなので Nit。止まっても資源は立っていて、エラーが見える分、前の黙って残す形より良い
- `[docs]` `docs/troubleshooting.md` — D の `InvalidArgumentException: The security token included in the request is invalid …` は 2026-10-09 に実測した止まり方だが、troubleshooting にも `docs/architecture/resources/firehose.md` にも「`time_sleep` で 30 秒待つ。足りなければ `history.tf` の `create_duration` を延ばす」の案内が無い（grep で `history.tf` のコメントにしか無い）。設計の変更対象ファイルに無いので範囲外。BACKLOG の候補
- `[docs]` `docs/pipeline.md:367` と `docs/troubleshooting.md:281` の「AWS では未確認。cycle 026 で足した形」は、設計 C.3 のとおり AWS で確かめたら外す。忘れないよう PM の AWS 確認の項目に入れておく

## 良かった点

- A〜D が 1 commit ずつで、diff と commit メッセージから何を直したか追える
- B のテストは在庫から `/x-nwc-oss/gnmic/gnmi-password` を消してから打っており（`tests/test_oss_ops.py:920-922`）、`ensure_fixed_secret` が「もうある」で `put-parameter` を打たずに通ってしまう偽陽性を避けている。`build.md` に赤（`_on_exit` 無しで `nwc-secret.` が残る）の実測もある
- C は `RestartPreventExitStatus=75` を残したまま `SuccessExitStatus=75` を足すだけで、69 の扱いと `Wants=` の起こし方を変えていない。テストは `findall` で行の重複も見る
- D の lock は `agent/` の block と byte 単位で同一で、OSS 版の `-lockfile=readonly` の init が実際に通る（自分でも確認）
- `build.md` に退行注入（5 本）と反対弁護人の指摘の採否が書いてあり、Should fix 1 件の判断を PM に回す形になっている

## ユーザーへの質問

None

### PM の判定

- **Must fix 0。**
- **Should fix 1 → 直した**（commit d04562bf86cac3e5f96e9ce41b629ef5954853cb）。
  - `history.tf` の `time_sleep.alert_firehose_iam` に `triggers = { role = aws_iam_role.alert_firehose.unique_id }` を足した。
  - design.md の D の 3 に、`triggers` を持つことと理由（state を残したまま接頭辞を変えてロールが作り直されたときも待つ）を PM の指示として追記した。
  - `tests/test_analytics.py` に `triggers` を見る check を足した（520 → 521）。`triggers` が無い状態で落ちることを見た（赤→緑は `build.md` の Round 2）。
- **Nit 4 → 直さない。**
  - Nit 3（`docs/architecture/resources/firehose.md` と `docs/troubleshooting.md` に IAM の反映待ちの案内が無い）は、BACKLOG の候補として PM に回す。
  - Nit 4（`docs/pipeline.md` と `docs/troubleshooting.md` の「AWS では未確認」の注記）は、PM の AWS 確認のあとで外す。

直したあとのテスト結果（実装者が打った出力。詳細は `build.md` の Round 2）:

```
$ uv run --frozen --group dev --group web python tests/test_analytics.py
通過 521 / 失敗 0
$ terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics validate
Success! The configuration is valid.
$ terraform -chdir=IaC/terraform/oss/pipeline/analytics validate
Success! The configuration is valid.
$ bash ops/check.sh
…
すべて通過
```

AWS に依る項目（C の `inactive (dead)` / `degraded`、D の 30 秒で足りること、既存環境での `plan`）は未確認で、PM の AWS 確認で見る。
