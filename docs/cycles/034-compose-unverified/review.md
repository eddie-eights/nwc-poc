# 手元の compose の未確認を確かめる（034）のレビュー

## Round 1

実行モデル: cold reviewer は opus（初回ビルド直後に依頼した。Must fix 0 なので完了判定のラウンドも兼ね、2 回目は呼ばない）。確認は PM（fable-5-1）。実装は opus（`build.md`）。

### cold reviewer の結果

## サマリ

「手元の compose の未確認を確かめる（034）」の Round 1 を、`design.md` と `git diff 4d3d071 HEAD` で読んだ。対象は 6 ファイル（`docker/compose/README.md` +52、`check.sh` +3/-2（コメントだけ）、`compose.yaml` +2/-2（コメントだけ）、`prometheus.yml` +6、`docs/troubleshooting.md` +1/-1、`tests/test_local_compose.py` +9）。`build.md` は参考として読んだ。

実際に効く変更は `prometheus.yml` の `storage.tsdb.out_of_order_time_window: 1h` の 1 つで、ほかは docs・コメント・テストの check。A の挙動は手元で再現でき、設定の値と docs の主張（1 時間より古いものは今までどおり捨てる）も実測と合った。設計との差（window を `compose.yaml` の引数でなく `prometheus.yml` に書いた、docs を `docker/compose/README.md` に書いた）はどちらも理由が実物で裏付けられている。Must fix は無い。

### 見た観点 / 見ていない観点

- **design.md との整合性（見た）**
  - A: 設計は `compose.yaml` の command に `--storage.tsdb.out_of_order_time_window=1h`。実装は `prometheus.yml` の `storage.tsdb`。`docker run --rm prom/prometheus:v3.15.0 --help` を grep して `out_of_order` を含む行が無い（exit 1）ことを確かめた。引数が無いので設計どおりには書けず、設定ファイルに書くのが唯一の手段。設計の意図（逆順サンプルを 1 時間まで受ける）は満たす。
  - B / C: Mac で Splunk が立ったので「未確認」でなく結果を書いている。C は判定が本物と合ったので `check.sh` の式は変えず（設計の「合わなければ直す」どおり）、コメントだけ本物の形に直した。
  - 「やらないこと」（Splunk の版を上げない、compose.yaml に Mac 用の分岐を足さない、A のスクリプトを repo に入れない）は守られている（`compose.yaml` はコメント 2 行だけ、scratch のスクリプトは diff に無い）。
  - 変更対象の表に無い `compose.yaml` のコメントと `docs/troubleshooting.md:380` を触っているが、どちらも 034 で確かめた事実と食い違う「未確認」の文を直しただけで、`build.md` の「設計との差 5」に書かれている。
- **correctness（見た。A は動的に確かめた）**
  - `prom/prometheus:v3.15.0` を 2 つ立て（既定の設定と、repo の `docker/compose/prometheus.yml` を ro で mount したもの。どちらも `--web.enable-remote-write-receiver`）、`app/spark/snmp_sinks.py` の `prometheus_series` / `encode_write_request` / `snappy_compress` / `http_post` で同じ系列に now → now-60s → now-7200s の順に送った。
    - 既定: `(204, b'')` → `(400, b'out of order sample\n')` → `(400, b'out of bounds\n')`
    - repo の設定: `(204, b'')` → `(204, b'')` → `(400, b'too old sample\n')`
    - README の「最新の時刻より 1 時間以上古いサンプルは、これまでどおり 400 で捨てる」と合う。window 0 でも in-order の受け入れは最新から約 1 時間（chunk range の半分）までなので、1h の window で受け入れの下限が狭まることは無い。
  - `promtool check config` を repo の `prometheus.yml` に当てて `SUCCESS`（`global:` が空でも可）。
  - `check.sh` の Splunk の判定式は変わっていない（diff はコメントだけ）。新しい check の 5 つの本文を式に照らして読み、期待どおりになる。`count` が文字列（`"0"` / `"1"`）でも `int()` で数にしている。空の本文は here-string で `"\n"` になり、テストの `" "`（`printf '%s\n'` で `" \n"`）と同じ経路（JSON として読めず `読めない応答: 空`）を通る。FAKE_SPLUNK が空なら既定の応答になる（`tests/test_local_compose.py:484` の `-n`）ので、空白 1 つで代える理由も正しい。
- **security（見た）**: パスワードは README・コメント・テストのどこにも出ていない。`check.sh` の認証の渡し方（`curl -K -`）は変わっていない。401 の本文にも資格情報は入らない。
- **runtime bugs（見た、一部は静的のみ）**: 設定ファイルの読み込みは上の実測で確かめた。README の `docker compose ... restart prometheus` で bind mount の設定が読み直されることは、私も打っていない（`build.md` も打っていないと書いている）。
- **data loss（見た）**: README の Splunk だけを直す手順（`rm -s -f splunk` → `docker volume rm nwc-local_splunk-etc` → `up.sh`）は、消えるもの（Web で作ったサーチ、`local/`）と残るもの（`splunk-var`）を書いている。volume 名は `compose.yaml:16` の `name: nwc-local` と `:319` の `splunk-etc` から合っている。`nwc-local` での実行は誰も打っていない（README に明記）。
- **API compatibility（見た）**: 外から呼ばれるものの形は変わっていない（`check.sh` の出力行、compose のサービス・ポート・volume は同じ）。
- **type safety**: 該当なし（Python の型の変更は無い）。
- **missing tests（見た）**: `uv run --frozen python3 tests/test_local_compose.py` を打って `通過 144 / 失敗 0`。window の値は check 1 つで縛られている。
- **見ていない観点**
  - Splunk（B と C）は立てていない。B の「volume に写らない」と C の本物の応答の本文は `build.md` の記録を根拠にしていて、私は再現していない。上流の `/sbin/updateetc.sh` の中身も読んでいない。
  - Spark の追い付きで実際に逆順のサンプルが生じるか（パイプラインの中での再現）は見ていない（実装側も再現していないと書いている）。
  - AWS（AMP）での逆順の扱いは範囲外。
  - ほかの `tests/test_*.py` は打っていない。

## Must fix

None

## Should fix

None

## Nit

- [missing tests / runtime] `tests/test_local_compose.py:320-321`: `(yaml.safe_load(...) or {}).get("storage", {}).get("tsdb", {})` は、`storage:` か `tsdb:` が空（YAML の null）に書き換えられたとき `AttributeError` でスクリプトごと落ち、どの check が落ちたかの表示にならない。`or {}` を各段に付けると FAIL の 1 行で止まる。誰かが設定を消し間違えたときに読みやすさが落ちるだけで、退行自体は拾えるので Nit。
- [design.md との整合性] `design.md` の「変更対象ファイル」の表と設計方針 A の 3 は、window を `compose.yaml` の command に足すと書いたまま。v3.15.0 に引数が無いことは実測で確かめたので、PM が `design.md` に追記するか、`review.md` に差として残すとよい。実装は意図を満たしていて壊れるものは無いので Nit。
- [docs] `docker/compose/prometheus.yml:5` と `README.md:249` の「3.15.0 にこれを変える起動の引数は無い」は版を名指ししていて、`compose.yaml:245` の image の版とはテストで結び付いていない。版を上げたときにコメントだけが古くなる。今は正しいので Nit。
- [docs] `docker/compose/README.md:263-274`: 「Splunk だけなら次の 2 つのあとに `up.sh`」の「2 つ」（`rm` と `volume rm` のコードブロック）が、注意の箇条書き 3 つを挟んだ後ろに来る。読む人が手順と注意を対応させにくい。コードブロックを導入の行の直後に置くと読みやすい。体裁の話なので Nit。
- [runtime] `docker/compose/README.md:250-252`: `restart prometheus` で設定が読み直されることは打たずに書いている（README 自身がそう明記している）。コンテナの start のたびに bind mount は貼り直されるので動くはずだが、確かめていない手順なので、WSL で通しを打つときに 1 回確かめるとよい。README が正直に書いているので Nit。

## 良かった点

- 設計どおりに書けない箇所（引数が無い）を、`--help` と `promtool check config` で実物から確かめてから設定ファイルに移していて、根拠が `build.md` に残っている。
- `prometheus.yml` のコメントが「なぜ 1h か」「なぜ設定ファイルか」「何で確かめたか」を書いていて、次に読む人が判断し直せる。
- README が「確かめたもの」と「打っていないもの」（`restart` そのもの、`up.sh` 経由、`nwc-local` での `rm`、result と ERROR の混在）を分けて書いていて、確かめた範囲より広く言っていない。
- C で判定式を変えずに、本物の本文をそのまま check に写したので、式が本物の形から外れたら落ちる。
- `check.sh` のコメントが「本物の 401 の本文は未確認」から本物の形に更新されていて、古い「未確認」が残っていない（`compose.yaml` と `troubleshooting.md` も同様）。

## ユーザーへの質問

None

### PM の確認

- Must fix 0 / Should fix 0 なので、この Round 1 が完了判定を兼ねる（cold reviewer の 2 回目は呼ばない）。
- `git diff 4d3d071 f57c428`（7 ファイル）を読んだ。`uv run --frozen python3 tests/test_local_compose.py` → `通過 144 / 失敗 0`、`bash -n docker/compose/check.sh` → エラー無し（PM が取り直した）。
- A の実測（既定で `400 out of order sample`、`1h` で `204`、2 時間前は `400 too old sample`）は cold reviewer が本物の v3.15.0 で再現したもの。PM は再現していない（読んだだけ）。B / C は実装の `build.md` だけが根拠（PM も cold reviewer も Splunk は立てていない）。
- Nit の扱い: 2 つ目（`design.md` が compose.yaml の command のまま）は設計を正本にするため PM が `design.md` の A.3 と変更対象の表を `prometheus.yml` に書き換えた。ほかの 4 つ（テストの `or {}`、版の名指し、README の並び、`restart` 未確認）とエンジニアが直さなかった Nit（「Kafka の追い付き」を事実として書く表現、README の `.env` の節から 034 の節への参照が無い）は直さず、最終報告の一覧に載せる。
- 未確認のまま残るもの: `restart prometheus` で設定が読み直されること、`up.sh` 経由の Splunk の作り直し、全部同時起動の Mac の起動時間、AWS の AMP での逆順の扱い（AMP は out-of-order を受けるかを 1 回の AWS 検証では見ない。範囲外）。
