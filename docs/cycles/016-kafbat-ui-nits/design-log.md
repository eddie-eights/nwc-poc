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
