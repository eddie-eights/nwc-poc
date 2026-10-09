# 手元の docker compose で動く構成を作る（006）レビュー Round 1

- 対象: worktree `docs/cycle-006-design`。基準は `4665ffa` との差分
- 照合先: `docs/cycles/006-local-compose/design.md`
- レビュアーは実装に手を入れていない（このファイルだけを新規作成した）

## サマリ

design.md の決定・ルール・実装ステップに対して、実装に食い違いは見つからなかった。主な確認点は次のとおり。

- compose の全ポートが 127.0.0.1 に限られている
- `name: nwc-local` が入っている
- lab.sh の 3 箇所（pull / `local_telegraf` / 実行ビット）
- `.env.example` の 7 キー
- ops/check.sh の `find` に `local` が入っている

Must fix は無い。Should fix は 3 件ある。どれも BACKLOG.md に「次に回す」として既に載っており、緩和策もある。

レビュアーが自分で走らせた結果（このラウンドの worktree で実行）は次のとおり。

- `uv run --group dev python tests/test_local_compose.py` → `通過 75 / 失敗 0`
- `tests/test_lab_debug.py` → `通過 82 / 失敗 0`
- `tests/test_oss.py` → `通過 164 / 失敗 0`
- `tests/test_oss_ops.py` → `通過 139 / 失敗 0`
- `docker compose -f local/compose/compose.yaml --env-file local/compose/.env.example config` → rc=0（Mac の Docker Compose v5.1.3）

### 見た観点 / 見ていない観点

見た観点:

- [design-conformance] design.md の決定表、ルール、実装ステップ、検証一覧と、実装（compose.yaml / up.sh / down.sh / check.sh / lab.sh ラッパー / lab/lab.sh / ops/check.sh / docs / BACKLOG）の照合
- [correctness] Kafka の EXTERNAL リスナーと advertised の組み合わせ、Spark 2 系統の引数、Grafana の datasource とアラートの出し分け、snmp_sinks の 4xx / 5xx の扱い、lab.sh の分岐
- [security] ポートの bind 先、`network_mode: host` の Telegraf、check.sh のパスワードの渡し方（`-K -` で stdin）、`sudo env` で渡す環境変数の範囲
- [api-compat] lab/lab.sh の既存の AWS 経路（`TELEGRAF_IMAGE` と `REGISTRY` あり）が変わらないこと、test_lab_debug.py の正規表現の更新
- [missing-tests] tests/test_local_compose.py の網羅範囲（版の同期、Kafka env の差分、telegraf.sh render、parse_args、偽の docker / sudo / curl / free での各スクリプトの実行）
- [data-loss] down.sh が `-v` を付けず volume を残すこと、project 名で他の compose と volume が衝突しないこと

見ていない観点:

- 実機での end-to-end（`docker compose up` / `docker compose build`、WSL2 上の containerlab、Telegraf から Kafka 経由で各シンクまでの疎通）。レビュアーの環境は Mac で、lab は Linux（WSL2）前提のため
- `bash ops/check.sh` の通し実行（terraform が要るため走らせていない）
- 性能・メモリの実測（check.sh の free -m の閾値 19456 が妥当かどうか）
- `.env`（実ファイル）の中身。決まりにより読んでいない。`.env.example` だけを見た

## Must fix

None

## Should fix

- [design-conformance] ops/check.sh 45 行目の `bash -n ops/up.sh ... local/compose/*.sh` は、`bash -n` が先頭の 1 ファイルだけを構文検査して残りを位置引数として扱うため、design.md の「ops/check.sh の `bash -n` に `local/compose/*.sh` を足す」は形だけで、効果がない（既存の列も同じ）。
  - 分類理由: 設計の検証手段が実際には働いていない。ただし tests/test_local_compose.py がファイルごとに `bash -n` を走らせて緩和している。BACKLOG.md にも載っているので、Must ではなく Should とした。
- [security] compose.yaml 63 行目の Telegraf は `network_mode: host` で、1162 / 5140 / 57000 / 8080 を全インターフェースで待ち受ける。一方、ルールは「compose のポートは 127.0.0.1 に限る」としている。WSL2 の mirrored ネットワークや LAN に面した Linux では、ホスト外から trap / syslog / gNMI dial-out を投げ込める。
  - 分類理由: 手元の試験用でデータも試験値だけなので Must ではない。とはいえ設計の「127.0.0.1 に限る」の意図から外れる経路が 1 本ある。BACKLOG.md に載っているので Should とした。
- [correctness] local/compose/check.sh の判定は 0 件と取得失敗を区別しない。38 / 41 / 44 行目の judge は、件数が 0 なら一律に `0 件` と出す。たとえば Splunk が 401 を返し `result` が無いとき、`.get('count', 0)` で 0 になる。Kafka の確認もトピックがあるかどうかの深さまでしか見ていない。
  - 分類理由: 認証の設定ミスが「データがまだ来ていない」と同じ表示になり、切り分けを誤らせる。ただし NG は NG として出る（誤って ok にはならない）ので Should とした。BACKLOG.md に載っている。

## Nit

- [correctness] compose.yaml の `spark-splunk` と `spark-http` は、どちらも `build: ../../spark` と `image: nwc-local-spark`（44 行目）を持つ。そのため `up.sh` の `docker compose up -d --build` で同じイメージを 2 回ビルドする。
  - 分類理由: 2 回目はキャッシュが効き、結果のイメージも同じなので動作には影響しない。ビルド時間とログの重複だけ。
- [correctness] compose.yaml 125 行目で `spark-splunk` にも `--device-map` を渡している。OSS 版の spark.tf は prometheus / opensearch の job にだけ渡す。
  - 分類理由: splunk 側でも引数として受理され、害は確認できなかった。OSS 版との対称性の差だけ。
- [correctness] Kafka の EXTERNAL は `localhost:909N` を advertise する（88 / 97 / 106 行目）が、publish は 127.0.0.1（IPv4）だけ。`localhost` が先に `::1` に解決される環境では、最初の接続が 1 回失敗してから IPv4 へ落ちる。
  - 分類理由: Telegraf（Go）は両方の宛先を試すので、つながらなくなることはない。根拠は挙動の一般論で、実測していない。
- [security] Docker 28 未満では、127.0.0.1 に bind した publish ポートでも、同じ L2 セグメントの他ホストから届くことがある（Docker 28 で塞がれた既知の問題）。
  - 分類理由: 手元の Docker の版に依存する話で、このリポジトリのコードの欠陥ではない。setup 手順に最低版の注記があると親切、という程度。
- [docs] lab/lab.sh 111 行目の render のメッセージ `（イメージは ${REGISTRY:-?}）` が、ローカル経路では `?` を出す。
  - 分類理由: 表示だけの問題で、BACKLOG.md に載っている。

## 良かった点

- tests/test_local_compose.py は、実物の `telegraf.sh render` と `snmp_sinks.parse_args` をそのまま呼ぶ契約テストになっている。版の同期（ops/lab-common.sh、oss/compose との Kafka env の差分）も機械で検査しているので、写しがずれれば落ちる。
- check.sh はパスワードを `curl -K -` で stdin から渡しており、プロセス一覧や引数に出ない。テストもその点を検査している。
- lab.sh ラッパーは `sudo env` で必要な変数（`SRLINUX_IMAGE` / `MULTITOOL_IMAGE` / `TELEGRAF_LOCAL=1`）だけを渡す。利用者の環境を丸ごと root に持ち込まない。
- `name: nwc-local` で project 名を固定し、oss/compose の volume（`nwc-oss_*` 相当）と衝突しないようにしている。down.sh は `-v` を付けずデータを残す。
- lab/lab.sh の変更は `local_telegraf` 1 つに寄せてあり、既存の AWS 経路（`TELEGRAF_IMAGE` と `REGISTRY` あり）の分岐は変わっていない。test_lab_debug.py（82 件）も通る。
- Telegraf を除く compose の全ポートが 127.0.0.1 に bind されている（Splunk の 8000 / 8089 も含む）。
- 範囲外で見つけたもの（12 件）が BACKLOG.md に動詞のタイトルで残されている。

## ユーザーへの質問

- Telegraf の `network_mode: host` で全インターフェースを待ち受けている件（Should fix 2 件目）は、このサイクルの範囲で 127.0.0.1 に絞りますか。それとも BACKLOG のまま次のサイクルに回しますか。WSL2 を mirrored ネットワークで使う予定があるなら、早めに絞るほうが安全です。

## Round 1（PM の確認）

- 確認モデル: claude-fable-5-1 / effort: high。cold reviewer は呼んだ（general-purpose、opus。上の「レビュー Round 1」がその全文。`git status` の新規は review-r01.md の 1 本だけだった）
- 実装モデル（build.md Round 1）: claude-opus-5-5 / xhigh。レビューは実装以上のモデルで行った

### 分類の確定

| 指摘 | 再現 | 確定 | 扱い |
| :--- | :--- | :--- | :--- |
| Should 1 `bash -n` が先頭だけ | 再現した（下） | Should fix（検証手段が働いていない = missing-tests） | Round 2 で直す |
| Should 2 Telegraf が全インターフェースで待つ | 構造上そのとおり（host ネットワーク） | Should fix（security） | **据え置き**（理由は下） |
| Should 3 Splunk の 401 が `0 件` | 再現した（下） | Should fix（correctness） | Round 2 で直す |
| Nit 5 件 | 読んだだけ | Nit | 直さない。最終報告に載せる |

再現 1（`bash -n` は 2 つ目以降を見ない）:

```
$ printf 'echo ok\n' > good.sh; printf 'if then fi (\n' > bad.sh
$ bash -n good.sh bad.sh; echo "rc=$?"
rc=0
$ bash -n bad.sh; echo "rc=$?"
bad.sh: line 1: syntax error near unexpected token `then'
rc=2
```

再現 3（check.sh:44 の式に 401 の本文を食わせる）:

```
$ printf '{"messages":[{"type":"FATAL","text":"Unauthorized"}]}\n' | python3 -c '<check.sh:44 の式>'
0 件
```

Should 2 を据え置く理由（格下げではない。Should fix のまま BACKLOG 40 行目に残す）:

- design.md の「host へ出すポート」が、Telegraf だけは host ネットワークで全インターフェースになることを明示して BACKLOG 送りにしている（正本と実装は一致している）
- 127.0.0.1 に縛ると lab のコンテナ（containerlab の管理ブリッジ側）から trap / syslog / gNMI dial-out が届かなくなる。縛り先は管理ブリッジの gateway の IP になるが、その IP は lab を deploy するまで存在せず、Telegraf の起動順と `telegraf.conf.in`（AWS 経路と共用）の両方に手が入る。Mac では WSL の検証ができず、ユーザーの WSL 検証（設計の検証方法 7）の直前に検証経路を変えない
- 受ける側のデータは lab の試験値だけ、WSL は 1 人で使う前提（README に警告済み）
- cold reviewer の「ユーザーへの質問」はこの件。最終報告でユーザーに示し、絞るなら Kafbat のサイクル以降で行う

### Round 2 への入力

- Should 1 と 3 をエンジニア1 に依頼（枝は docs/cycle-006-design @ 7c7310a から）。design.md は変えない（設計の前提の誤りではなく、検証スクリプトの実装の穴）

## Round 2（PM の確認）

- 確認モデル: claude-fable-5-1 / effort: high。cold reviewer は呼んでいない（中間ラウンド。実装は `fix/local-compose-r2` @ 743ee7c、マージは 1ef8853）。レビューは build.md Round 2 の `### セルフレビュー`（Must 0 / Should 0、退行注入 8 件）に依った
- 実装モデル（build.md Round 2）: claude-opus-5-5 / xhigh

### Round 1 の Should 1 / 3 の解消確認（マージ後の木で実行）

再現 1（`ops/check.sh:45` と同じ `for … do bash -n "$f"; done` の形で good.sh bad.sh を通す）:

```
$ bash scratchpad/bashn_loop.sh; echo "rc=$?"
…/bad.sh: line 1: syntax error near unexpected token `then'
…/bad.sh: line 1: `if then fi ('
rc=2
```

2 つ目のファイルの構文エラーで止まる（Round 1 は rc=0 で素通りしていた）。解消。

再現 3（`local/compose/check.sh:45-48` の式に本文を食わせる。`scratchpad/splunk_judge_r2.py` で check.sh から式を抜いて eval）:

```
$ python3 scratchpad/splunk_judge_r2.py
401 -> FATAL Unauthorized        # {"messages":[{"type":"FATAL","text":"Unauthorized"}]}
count0 -> 0 件                   # {"result":{"count":"0"}}
count3 -> ok                     # {"result":{"count":"3"}}
empty -> raises JSONDecodeError  # 空文字。check.sh では judge の try/except が受けて「読めない応答: 空」になる（check.sh:23-24）
```

401 が `0 件` ではなく `FATAL Unauthorized` と出る。解消。本物の 401 の本文は未確認のまま（BACKLOG 50 行目）。

テスト（マージ後の木）:

```
$ uv run --group dev python tests/test_local_compose.py → 通過 77 / 失敗 0
$ uv run --group dev python tests/test_oss.py           → 通過 164 / 失敗 0
$ bash ops/check.sh                                     → 通過 325 / 失敗 0、すべて通過（rc=0）
```

### 判断

- エンジニア1 の自己レビューの N1（テストが `set -e` に依る）と N5（`.sh` 8 本が `bash -n` の対象外）は BACKLOG 48 行目に 1 件で載せた。N4（理由の重複・長さ）は 49 行目、U1/U2（本物の応答）は 50 行目。このサイクルでは直さない（検証スクリプトの改善で、設計の合格条件の外）
- Should 2（Telegraf の全インターフェース）は Round 1 のまま据え置き
- Must fix が消えたと判断したので、次に cold reviewer の 2 回目を呼ぶ
# 手元の docker compose で動く構成を作る（006）Round 2 cold review

## サマリ

- 基準は `4665ffa`、対象は `HEAD`（`7459e5c`）。design.md と、変わったファイル全部（下の「見た観点」）を突き合わせた。
- Round 1 の Should 1（`ops/check.sh` の `bash -n` を 1 本ずつにする）と Should 3（Splunk の認証の失敗を 0 件と分ける）は、マージ後の木で直っていた。
  - Should 1 は `ops/check.sh:45` の `for f in ...; do bash -n "$f"; done` で直った。`tests/test_local_compose.py` に回帰の検査もある。
  - Should 3 は `local/compose/check.sh` で直った。Splunk の export が FATAL や ERROR を返したときと、「result が無い」ときを分けて NG にしている。
- 前の Round で直っていない Must fix は無い。今回も Must fix は見つからなかった。
- 合格の基準は、design.md の WSL での手順 4〜6（ユーザーが打つ）。このレビューでは打っていない。

### 見た観点 / 見ていない観点

見た観点:

- 設計との一致
  - design.md の構成（`name: nwc-local`、11 サービス、`network_mode: host` の Telegraf、`127.0.0.1` に出すポート、Splunk の `platform: linux/amd64`、volume 10 個、`.env.example` のキー 7 つ）を、`local/compose/compose.yaml` と `.env.example` に照らした。
  - `lab/lab.sh` を変えてよい 3 か所（`local_telegraf` での `hint` と `forward`、`pull` の `REGISTRY` の分岐）を確かめた。
  - docs の差分（README、`docs/architecture/README.md`、`docs/setup.md`、`docs/cycles/BACKLOG.md`）を見た。
- 正しさと実行時のバグ
  - `local/compose/up.sh`、`lab.sh`、`check.sh`、`down.sh` を読んだ。
  - 呼び先との約束を確かめた。`telegraf/telegraf.sh`（`AWS_REGION` が要る、`SNMP_AGENTS` と `GNMI_TARGETS` の形）、`grafana/start.sh`（`ALERTS_TOPIC_ARN` が無ければアラートを入れない）、Splunk の entrypoint、`spark/Dockerfile`（`USER spark`、`snmp_sinks.py` の置き場）、`snmp_sinks` の `metric_name` を見た。
- セキュリティ
  - パスワードの渡し方（`curl -K -` に stdin で渡す）と、ポートを出すインターフェースを見た。
- テスト
  - 下のコマンドを自分で打って、出力を見た。
    - `uv run --group dev python tests/test_local_compose.py` → 通過 77 / 失敗 0
    - `uv run --group dev python tests/test_lab_debug.py` → 通過 82
    - `uv run --group dev python tests/test_oss.py` → 通過 164
    - `docker compose -f local/compose/compose.yaml --env-file local/compose/.env.example config -q` → 終了コード 0
    - `git status --short` → 何も出ない（clean）

見ていない観点:

- `bash ops/check.sh` は打っていない（terraform の検査も含むため）。
- WSL2 で通して打つ手順（design.md の手順 4〜6）は打っていない。
  - コンテナを本当に起動して確かめてはいない。
  - containerlab での lab の deploy と、`iptables` の REDIRECT を本物で確かめてはいない。
- 本物の Splunk が 401 のときに返す body の形と、OpenSearch を single-node・TLS 無しで起動したときの挙動は、実物で確かめていない。
- `.env` は読んでいない（`.env.example` だけ見た）。

## Must fix

None

## Should fix

None

## Nit

- [設計との一致] `lab/lab.sh:90`, `lab/lab.sh:94`, `lab/lab.sh:221`, `lab/lab.sh:231`
  - design.md:141 の検証の項目は「`lab/lab.sh` に `TELEGRAF_LOCAL` が `forward` と `hint` の両方にある」と書いている。
  - 実装では、`TELEGRAF_LOCAL` という文字は `local_telegraf()` の定義（:90）にしか無い。`hint`（:94）と `forward`/`failover`（:221, :231）は `local_telegraf` を呼ぶ。
  - 挙動は design と同じ。ただ、design の文の通りに `grep TELEGRAF_LOCAL` で確かめる人は、hint と forward に見つけられない。
  - 機能は満たしていて、design の書き方と実装の言葉がずれているだけなので Nit とした。

- [実行時のバグ] `local/compose/compose.yaml:116`
  - kafka-ui は `127.0.0.1:18080` に出す。`oss/compose/compose.yaml:90` も同じ `127.0.0.1:18080` を使う。
  - 同じ機械で OSS 版の compose と手元の compose を同時に上げると、後から上げた方の kafka-ui が bind に失敗して起動しない。
  - 2 つを並べて動かすことは design の範囲外で、ぶつかればエラーが出るので Nit とした。README の「ぶつかりやすいポート」にも書かれていない。

- [実行時のバグ] `local/compose/compose.yaml:46`, `local/compose/compose.yaml:65`
  - telegraf と spark は `restart: on-failure` で、再起動の回数に上限が無い。
  - 例えば `docker compose` を直に打って `SNMP_AGENTS` が空のとき、Telegraf は起動の検査で止まって再起動を繰り返す。
  - ログを見れば原因は分かり、データは壊れないので Nit とした。README:46 は `up.sh` を使うよう書いている。

- [テストの不足] `tests/test_local_compose.py` / `tests/test_lab_debug.py`
  - 次の経路は、正規表現で文字があるかを見るだけで、実行しては確かめていない。
    - `lab/lab.sh` の `hint` で `TELEGRAF_LOCAL=1` のときに出る文言
    - `failover` の分岐（:221）
  - `forward` は偽の `iptables` と `sudo` で実行の検査がある。
  - どちらも表示や手で打つ経路で、壊れても Grafana と Splunk のデータには影響しないので Nit とした。

既に BACKLOG にあるため、新しい指摘には数えないもの:

- `env_get` は CRLF や `export ` や行末のコメントを扱わない（`local/compose/check.sh:7`, `local/compose/lab.sh:8`）。
- Telegraf の 4 つのポートは、host の全部のインターフェースで待つ（BACKLOG:40）。
- `fail-main` の案内がまだ `lab failover` を指している。
- `render` は `${REGISTRY:-?}` と表示する。
- splunk-etc の volume の扱い（U2）。
- Prometheus の `out_of_order_time_window`（BACKLOG:42）。
- `lab/clab-*/` が `.gitignore` に入っていない（BACKLOG:39）。
- 手元の `check.sh` は、Kafka のトピックがあるかだけを見て、メッセージ数は見ない（BACKLOG:41）。

## 良かった点

- `ops/check.sh:45` を 1 本ずつの `bash -n` にした。今回の `local/compose/*.sh` だけでなく、それより前から黙って見落とされていた `ops/` と `oss/ops/` の 2 本目以降の構文検査も効くようになった。`tests/test_oss.py` の正規表現も合わせて直してある。
- `local/compose/check.sh` はパスワードを `curl -K -` に stdin で渡し、`"` と `\` をエスケープしている。そのため、パスワードがプロセスの引数（`ps` で見える）に出ない。
- Splunk の判定で、認証の失敗や検索のエラーを「0 件」と分けた。NG の理由を取り違えにくくなった。
- `local/compose/lab.sh` は `sudo env` で 3 つの変数だけを渡す。シェルにある `REGISTRY` や `AWS_REGION` が漏れて、ECR や SSM へ行くことを構造で防いでいる。
- ポートは Telegraf 以外すべて `127.0.0.1` に出している。例外の Telegraf は、README とBACKLOG に理由と残りの作業が書いてある。
- 偽の docker、iptables、sudo、curl を使って、`up.sh`、`lab.sh`、`check.sh` の分岐（`.env` が無い、メモリが足りない、Splunk の FATAL と 0 件）を実行で確かめるテストがある。

## ユーザーへの質問

- design.md:141 の検証の項目「`TELEGRAF_LOCAL` が `forward` と `hint` の両方にある」は、どちらに揃えるか。
  - design.md の文を「`local_telegraf`（`TELEGRAF_IMAGE` か `TELEGRAF_LOCAL=1`）を `forward` と `hint` が使う」に直す。
  - または実装をこの文に合わせる。
  - 今の実装で挙動に問題は無い。
- OSS 版の compose と手元の compose を、同じ機械で同時に上げることを想定するか。
  - 想定するなら、kafka-ui の 18080 をどちらかで変える必要がある。

## Round 3（PM の確認・完了判定）

- 確認モデル: claude-fable-5-1 / effort high（実装は opus 5.5 / xhigh。同等以上）
- cold reviewer: **2 回目を依頼した**（opus / high。上の「Round 2 cold review」。`git status` で増えた新規ファイルは `review-r02.md` の 1 本だけだったのを確かめてから連結した）
- 結果: Must 0 / Should 0 / Nit 4

### Nit の扱い

| 番号 | 指摘 | 扱い |
| :--- | :--- | :--- |
| Nit 1 | design.md:141 の文と `lab/lab.sh` の言葉がずれている | design.md:141 を「`local_telegraf()`（`TELEGRAF_IMAGE` か `TELEGRAF_LOCAL=1`）があり、`forward` と `hint` がそれを呼ぶ」に直した（実装は変えない） |
| Nit 2 | `oss/compose` と `local/compose` の kafka-ui が同じ `127.0.0.1:18080` | 並べて上げることは想定しない。`oss/compose` は OSS 版の検証用の使い捨てで、残すか消すかは 007（並べ直し）で決める。BACKLOG に行を足した |
| Nit 3 | telegraf / spark の `restart: on-failure` に回数の上限が無い | 直さない。BACKLOG に行を足した |
| Nit 4 | `hint` と `failover` の案内は正規表現でしか見ていない | 直さない。BACKLOG に行を足した |

### 完了前の取り直し（HEAD `7459e5c`、この Round で打った）

- `bash ops/check.sh` → 終了コード 0、「すべて通過」（通過 325 / 失敗 0）
- `uv run --group dev python tests/test_local_compose.py` → 通過 77 / 失敗 0
- `uv run --group dev python tests/test_lab_debug.py` → 通過 82 / 失敗 0
- `uv run --group dev python tests/test_oss.py` → 通過 164 / 失敗 0
- `uv run --group dev python tests/test_oss_ops.py` → 通過 139 / 失敗 0
- `docker compose -f local/compose/compose.yaml --env-file local/compose/.env.example config -q` → 終了コード 0
- Round 1 の Should 1 の再現（`scratchpad/bashn_loop.sh`。構文エラーのファイルを 2 番目に置く）→ 終了コード 2（落ちる。直前は 0 で素通りしていた）
- Round 1 の Should 3 の再現（`scratchpad/splunk_judge_r2.py`）→ `401 -> FATAL Unauthorized / count0 -> 0 件 / count3 -> ok`

### 判断

- Must fix / Should fix は無い。Round 1 の Should 2（Telegraf の 4 ポートが host の全インターフェース）は据え置きのまま BACKLOG:40 にある（理由は Round 1 の PM の確認）。
- design.md の合格条件のうち、WSL2 で通して打つ手順 4〜6（`lab up` → `check.sh` が「すべて ok」 → `fail-main` のあとに Grafana と Splunk で見える）は**このレビューでも打っていない**。ユーザーが WSL で打つ。
- サイクルを完了にし、BACKLOG の行を `[x]` にした。

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261008-cycle-006-local-compose-review.html -->
