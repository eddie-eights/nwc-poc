# Kafbat UI の残りの Nit を片付ける（016）— design-log

## Round 0（2026-10-09、PM）

ユーザーへの質問は無し。014 の Nit の後始末で、決めることは「何を入れ、何を外すか」だけなので PM が決めた（「判断は一旦君に任せる」）。

### 入れたもの（BACKLOG の行）

- 77: `ops/up.sh` の ECR エンドポイントのコメントに Kafbat UI の pull を足す（010 の Nit 10）
- 84: 初回の 8-3 の直後は Kafbat UI がまだ上がっていないことがある（014 のセルフレビューの Nit 4）
- 86: terraform だけで stream を上げたとき `admin-password` が無い（014 のセルフレビューの Nit 6）。**docs で扱う。** `admin-password` を Terraform で作る案は、リポジトリの決まり（シークレットは `ops/up.sh` が SSM の SecureString として作る）に反するので採らない
- 88: `tests/test_stream.py` の 8-3 / 7-5 の検査の `ValueError`（014 のセルフレビューの Nit 9、cold review の Nit 5）
- 89: `docs/troubleshooting.md` の Kafbat UI の行を小節に分け、`daemon-reload` / `enable` の失敗の形を足す（014 のセルフレビューの Nit 10、cold review の Nit 6）
- 90: `_krendered` の `Wants=` の検査（014 の cold review の Nit 1）
- 92: `docs/deploy.md` の user_data の作り直しの注意（014 の cold review の Nit 3）
- 93: `RestartPreventExitStatus=75` がコンテナの 75 にも効く（014 の cold review の Nit 4）。**docs で扱う。** 設定は変えない

### 外したもの

- 85（`SuccessExitStatus=75` で degraded が消えるか）と 87（reboot のときの 69 の待ち）: AWS で測らないと決められない。PM の AWS 検証のときに見る
- 91（014 の design.md の変更対象に web-ec2.md と ssm-parameter-store.md を足す）: PM が 9667bcc で直した
- 64 / 65 / 76（`lab.sh` の `${VAR}`、failover の `route()`、`lab graph` の案内）: `app/containerlab/lab.sh` は 013 が触るので、衝突を避けて 013 のあとに回す
- 66（flows のダッシュボード）: 012 のあと
- 69 / 70（手元の `check.sh`）: Kafbat UI と関係が無い。別のサイクル

### 置いた前提

- user_data / ユニット / Terraform / `ops/up.sh` の動きは変えない。AWS で確かめ直す項目を増やさないため
- `tests/test_stream.py` で直すのは 014 で足した 2 本（8-3 / 7-5）だけ。ほかの `check` の `.index()` は触らない（範囲を広げない）
- Kafbat UI が上がるまでの時間は未計測。docs には「1〜2 分（未計測）」と書く

## Round 1（2026-10-09、エンジニア2 / opus-5.5）

PM の指示（2026-10-09）「レビューの指摘で直すのは、実装が design.md からずれているものと、design.md が事実と食い違っていると分かったものだけ。後者は design.md を上書きしてから実装を直す。設計に無いことを採るなら design.md に入れる」に従い、実装とセルフレビューで分かったことで design.md を上書きした。セルフレビューの指摘を直すかどうかは実装者が決める（同日の PM の指示）。

### 事実と食い違っていた所

- 読むパラメータに `security-protocol` が抜けていた（`web_user_data.sh.tftpl:54-57` は 4 つ）
- `ValueError` が出るのは `check()` の中ではなく、`_s83` / `_s75` を切り出すモジュールの最上位（`check()` は `assert` なので、直したあとも最初の失敗で止まる。変わるのは止まり方）
- `admin-password` を作るのは手順 8 ではなく手順 7（`ops/up.sh:919`、`oss/ops/up.sh:324`）
- 上がった合図は systemd の `Started` ではなく Kafbat UI の `Started KafkaUiApplication`（手元の Docker で 7 秒）
- コンテナは `--rm` なので、出力は `docker logs` ではなく `journalctl -u`
- 「戻すのは `unmask`」は誤りで `unmask --runtime`（systemd の `unit_file_unmask`。AL2023 では未確認）。`docs/pipeline.md:156` にも同じ誤りと `admin-password` の穴があった
- 小節は「画面に入れない」ではなく、表の行がある `## パイプラインと WORKFLOW` の直後に置いた（「下の」が当たる位置）
- `Wants=` の新しい `check` は `_krendered` を作った後ろ（`:545` 以降）にしか置けない
- 変異 (a)（空行）は退行ではないので、直したあとは通るのが正しい。開始の印が外れる (a2)、行が消える (a3) を足した
- 637MB は展開後の大きさで、pull の量ではない
- 012 のマージは PM ではなくエンジニア2 が 5be0288 を作業ブランチへマージして解く

### 採った変更（design.md に入れたもの）

- `deploy.md:276` と `pipeline.md:156` も直す（同じ穴を案内していた。セルフレビューの SR / OC2）
- 手順の表の下の注記（Kafbat UI がまだ上がっていない。設計方針 1 の 3 つ目。OC8 で場所を決めた）
- 7-5 を `split("\n")[2]` から `re.fullmatch` に（OC1）。最初の実装（aa6efad）は `_s75` に `run_on_instance` 行が含まれるかだけを見ていて、行を条件・関数・ループ・ヒアドキュメント・継続で包む変異 (o2)〜(o5)・(o7) を通してしまい、c4c378a の tests より弱くなっていた。空行とコメント行の出し入れだけを許す形にして、c4c378a と同じ範囲を縛る
- 検証方法に (o1)〜(o7) と assert / count の 2 つの打ち方を足した
- troubleshooting の 10 項目に「ユニットは動いているのに 8082」と、`status=75` で `does not exist` が無い形を明示した

- PM が範囲拡張を承認（2026-10-09）: `docs/pipeline.md:156`（変更対象に無かったファイル）と `docs/deploy.md:276`（「3 か所」を 4 か所に）。どちらも deploy.md:26 と同じ文の繰り返しで、016 の目的の中の事実訂正（PM の行番号は 5be0288 の :157 / :278）
- 5be0288（012 のマージ後）を取り込んだあとの実測に合わせて、検証方法の数（89 / 88 / 87 → 96 / 95 / 94。012 が `test_stream` に 7 本足した）と 9 の行（:506 → :505、差分の基準を 5be0288 に）、変更対象の `docs/development.md` の数（95 → 96）、未確定事項の 012 の項目（マージの結果）を直した（2026-10-09、エンジニア2。検証項目の更新だけで、方針・範囲は変えない）

### 採らなかったもの

- 「`enable` は通り `start` だけ落ちた」形を小節の項目にする（OC5）: `systemctl status` が `enabled` で `failed` になり、ほかの項目（75 / 69 / docker）の行で原因が分かるので、別項目にすると重複する
- 8-3 も 7-5 と同じく行を包む変異で落とす（OC7）: c4c378a の tests も同じく通す既存の弱さで、014 の 2 本を `ValueError` で止まらない形にするこのサイクルの範囲を超える。次の候補（BACKLOG は PM が書く）
