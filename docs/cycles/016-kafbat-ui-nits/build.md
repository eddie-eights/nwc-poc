# Cycle 016 kafbat-ui-nits 実装の記録

## Round 1

実装モデル: claude-opus-5-5 / effort: セッションの既定のまま（`set_session_effort` が使えず、`high` への切り替えを確かめられていない）

エンジニア2。ブランチ `fix/kafbat-ui-nits`（`docs/cycle-006-design` の c4c378a から）。AWS は使っていない（docs・コメント・tests だけ）。

### commit

| commit | 中身 |
|---|---|
| aa6efad | tests: `tests/test_stream.py` の 8-3 / 7-5 の検査を `ValueError` で止まらない形に、描いた user_data の Web のユニットの `Wants=` の check を 1 本 |
| 309c216 | docs とコメント: `docs/deploy.md`、`docs/troubleshooting.md`、`docs/pipeline.md`、`docs/development.md`、`ops/up.sh:506` のコメント。セルフレビューで直した `tests/test_stream.py`（7-5 の `re.fullmatch` とコメント）。`design.md` / `design-log.md` の上書き、この build.md |
| （5be0288 のマージ。この節を含む commit。ハッシュは PM への報告に書く） | 5be0288（012 のマージ後の `docs/cycle-006-design`）を取り込み、衝突 3 本を解いた。`docs/development.md` の `test_stream` を 96 に。design.md / design-log.md の数の上書きと、この build.md の「012 のマージ」 |

### 変更ファイル

| ファイル | 変更 |
|---|---|
| `tests/test_stream.py` | ヘルパー `_between(text, start, end)`（印が無ければ `""`）。`_s83` / `_s75` を `_between` で切り出す。7-5 は `re.fullmatch` で、`log "7-5. …"` の行と `run_on_instance … systemctl restart $PREFIX-web.service; $WEB_ACTIVE` の行のあいだと後ろに、空行とコメント行しか許さない（セルフレビューの OC1）。`_krendered` の直後に、描いた Web のユニット（`cat > /etc/systemd/system/x-nwc-poc-web.service <<__UNIT__` 〜 `__UNIT__`）の `Wants=x-nwc-poc-kafka-ui.service` と、`kafka-ui` を含む行がその 1 行だけ、の check を 1 本 |
| `docs/deploy.md` | :26 `SKIP_STREAM` の括弧（`SKIP_STREAM` を外して `ops/up.sh` を打ち直す。手順 7 で `admin-password` を作り、8-3 で起こす。terraform だけで上げると 75）。:102 手順 10 の表の下に注記（手順 10 が開くのは Web の 8080 だけ、Kafbat UI はまだ上がっていないことがある、合図は `Started KafkaUiApplication`）。:103 を 010 だけから「user_data を変えたサイクル（010、014）」に広げる。:276 の同じ案内も :26 と同じに |
| `docs/pipeline.md` | :156 :26 と同じ訂正（terraform だけで上げると `admin-password` が無い）と `unmask --runtime` |
| `docs/troubleshooting.md` | 表の Kafbat UI の行を案内の 1 行（:94）に。`## パイプラインと WORKFLOW` の表の下に `### Kafbat UI`（:100、10 項目） |
| `ops/up.sh` | :506 のコメントだけ（Kafbat UI も ecr.api / ecr.dkr で pull する） |
| `docs/development.md` | :37 の `test_stream` 88 → 89 |
| `design.md` / `design-log.md` | 実装とセルフレビューで分かった事実で design.md を上書き（PM の指示「直すなら先に design.md に入れる」）。中身は design-log.md の Round 1 |

### 設計から逸脱した点

design.md はこの節の 1〜10 と OC1・OC2 の分を上書き済み（design-log.md の Round 1）。いまの design.md と実装のずれは無い。

1. `check()` は `assert` なので、直したあとも失敗した check のところで止まる（`AssertionError`）。最初の design.md の「`check()` の中で例外が出るので失敗数に数えない」「`通過 88 / 失敗 1`」は、そのままでは出ない。変異は `assert` のままの版と、scratch の写しで `check()` だけを数える形に替えた版（count）の両方で打った。`通過 88 / 失敗 1` は count の数字。直したのは `ValueError: substring not found` で止まる点（どの check が落ちたか名前で分かるようになった）。
2. 変異 (a)（7-5 の `run_on_instance` 行の前に空行）は、直したあとは **通る**（89 / 0）。空行は退行ではない。代わりに、7-5 の印を変える (a2) と、7-5 の `run_on_instance` 行を消す (a3) を足した。最初の実装（aa6efad）は `_s75` に行が含まれるかだけを見ていて、行を条件・関数・ループ・ヒアドキュメント・継続で包む退行 (o2)〜(o5)・(o7) を通した（c4c378a の tests は落とす）。セルフレビューの OC1 で `re.fullmatch` に直し、c4c378a と同じ範囲を縛る形に戻した（検証 5）。
3. `_krendered` の `Wants=` の check は、最初の design.md の「`:471-476` の隣」ではなく `_krendered` を作った直後に置いた（`_krendered` はそこより後ろで作る）。
4. `### Kafbat UI` は「画面に入れない」ではなく、もとの行があった `## パイプラインと WORKFLOW` の表の下に置いた。表の案内の「下の」がそのまま当たる。
5. `admin-password` を作るのは手順 8 ではなく手順 7（`ops/up.sh:919` の `ensure_secret` は手順 7 の中、`oss/ops/up.sh:324`）。docs は手順 7 と書いた。
6. コンテナが 75 で終わったときの見る先は `docker logs` ではなく `journalctl -u <prefix>-kafka-ui`（`docker run --rm` なので、止まったときにはコンテナが消えている）。
7. `docs/deploy.md:276` と `docs/pipeline.md:156` にも「stream だけ上げたら `sudo systemctl start`」の同じ案内があったので直した（最初の design.md は deploy.md:26 だけ）。pipeline.md は変更対象に無かったファイルで、PM が範囲拡張を承認した（2026-10-09。PM の行番号は 5be0288 の :157 / :278）。
8. 最初の design.md の「`journalctl -u <prefix>-kafka-ui` で `Started` を待ってもよい」は、systemd の `Started <prefix>-kafka-ui.service`（スクリプトが動き出した時点）と取り違える。上がった合図は Kafbat UI 自身の `Started KafkaUiApplication`。手元の Docker で確かめた（下の「手元で確かめたこと」）。troubleshooting にも「ユニットは動いているのに 8082 につながらない」を 1 項目足した。
9. 既存の「戻すのは `unmask`」は誤り。`mask --runtime` は `/run/systemd/system` に置くので、`--runtime` の無い `unmask` は永続側（`/etc`）しか見ず外れない。`unmask --runtime` に直した。根拠は systemd のソース（v252 と main の `unit_file_unmask`: `config_path = (flags & UNIT_FILE_RUNTIME) ? lp.runtime_config : lp.persistent_config;`）。AL2023 では確かめていない。
10. 「1〜2 分」は設計どおり見込みとして残し、「AWS では未計測」と書いた。最初の design.md の「イメージ 640 MB」は展開後の大きさ（`docker image inspect` の `.Size` は 222684549、`docker images` は 637MB）で pull の量ではないので、docs には書いていない。

### 手元で確かめたこと

Kafbat UI が上がった合図（逸脱 8）。`ghcr.io/kafbat/kafka-ui:v1.5.0`（pull 済み）を 127.0.0.1:18082 で起こし、HTTP が返るまでと journald に当たるコンテナの出力を見た。コンテナは消した（最後の `0`）。

```
$ N=k016-probe; docker rm -f $N >/dev/null 2>&1; T0=$(date +%s); docker run -d --rm --name $N -e KAFKA_CLUSTERS_0_NAME=x -e KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS=127.0.0.1:9 -e AUTH_TYPE=LOGIN_FORM -e SPRING_SECURITY_USER_NAME=admin -e SPRING_SECURITY_USER_PASSWORD=probe016 -e GITHUB_RELEASE_INFO_ENABLED=false -e JAVA_OPTS=-XX:MaxRAMPercentage=50 -p 127.0.0.1:18082:8080 ghcr.io/kafbat/kafka-ui:v1.5.0 >/dev/null; for i in $(seq 1 60); do c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18082/ 2>/dev/null); if [ "$c" != 000 ]; then echo "t=$(( $(date +%s)-T0 ))s http_code=$c"; break; fi; sleep 1; done; docker logs $N 2>&1 | grep -n 'Started\|Netty started\|Tomcat started' | cut -c1-220; docker rm -f $N >/dev/null 2>&1; docker ps -a --format '{{.Names}}' | grep -c $N
t=7s http_code=302
24:2026-10-08T16:55:32.354Z  INFO 1 --- [           main] o.s.b.web.embedded.netty.NettyWebServer  : Netty started on port 8080 (http)
25:2026-10-08T16:55:32.374Z  INFO 1 --- [           main] io.kafbat.ui.KafkaUiApplication          : Started KafkaUiApplication in 4.98 seconds (process running for 6.669)
0
```

設計の「未確定事項とリスク」の 4 つ目（tests に表の行の文言を見る検査があるか）:

```
$ grep -rn 'Kafbat UI のポートフォワード\|mask --runtime' tests/ ; echo "rc=$?"
rc=1
```

### 検証 1: test_stream

最後の tests の編集（02:26）のあとに取った。test_stream は docs を読まないので、そのあとの docs の編集は結果に関係しない。

```
$ uv run --group dev --group web python tests/test_stream.py > v1c.log 2>&1; echo "rc=$?" >> v1c.log
$ grep -c '^ok ' v1c.log; grep -v '^ok ' v1c.log
89
通過 89 / 失敗 0
rc=0
```

### 検証 2〜5: 変異

scratch の写し（`pre/` = c4c378a の tests、`final/` = 最後の tests の編集のあとのツリー、`aa6efad` = 最初の tests の commit）に変異を入れて `tests/test_stream.py` を打った。`assert` は元のまま、`count` は写しの `check()` だけを数える形に替えたもの。変異は写しの中だけで、ワークツリーは触っていない。

変異:

- (a) OSS 版の 7-5 の `run_on_instance` 行の前に空行
- (a2) `log "7-5.` を `7-6.` に（`_s75` の開始の印が外れる）
- (a3) 7-5 の `run_on_instance` 行を消す
- (b) `# ---- 8-3. Web ` を `# ---- 8-3. web ` に
- (c) テンプレートの `Wants=${name_prefix}-kafka-ui.service` を消す
- (o1)〜(o7) 7-5 の `run_on_instance` 行を条件・関数・`exit`・ループ・ヒアドキュメント・行の継続で殺す（(o6) は空行とコメント行を足すだけで退行ではない）
- (p1) 8-3 の `run_on_instance` 行を呼ばない関数で包む（OC7 の確認）

直す前（`pre/`）。`ValueError` は (a2) と (b)。

```
[変異なし] assert: rc=0 | 最後の出力行: 通過 88 / 失敗 0 | 
[変異なし] count: rc=0 | 最後の出力行: 通過 88 / 失敗 0 | 
[a] 7-5 の run_on_instance 行の前に空行 / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[a] 7-5 の run_on_instance 行の前に空行 / count: rc=0 | 最後の出力行: 通過 87 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[a2] 7-5 の log の見出しを 7-6 に変える（_s75 の開始マーカーが外れる） / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: ValueError: substring not found
[a2] 7-5 の log の見出しを 7-6 に変える（_s75 の開始マーカーが外れる） / count: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: ValueError: substring not found
[a3] 7-5 の run_on_instance 行を消す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[a3] 7-5 の run_on_instance 行を消す / count: rc=0 | 最後の出力行: 通過 87 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[b] _s83 の開始マーカー # ---- 8-3. Web を # ---- 8-3. web に / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: ValueError: substring not found
[b] _s83 の開始マーカー # ---- 8-3. Web を # ---- 8-3. web に / count: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: ValueError: substring not found
[c] テンプレートの Wants=${name_prefix}-kafka-ui.service を消す / assert: rc=1 | 最後の出力行: ok Kafbat UI は Web の EC2 の systemd のユニットが Docker のコンテナを 127. | stderr: AssertionError: 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8-3（OSS 版は 7-5）の systemctl restart <接頭辞>-web が起こす。弱い依存だけにして、Kafbat UI が落ちても Web を止めない（Requires / BindsTo / PartOf / Requisite にしな
[c] テンプレートの Wants=${name_prefix}-kafka-ui.service を消す / count: rc=0 | 最後の出力行: 通過 87 / 失敗 1 | NG 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8-3（OSS 版は 7-5）の systemctl restart <接頭辞>-web が起こす。弱い依存だけにして、Ka | 
```

直す前の `ValueError` の traceback（`assert` の版。`ok` の行はどれも 72 で、そこから先は 1 本も走っていない）:

```
---- [a] rc=1 標準出力の行数=72（ok の数）
  File "…/scratchpad/016/pre/tests/test_stream.py", line 12, in check
    assert cond, name
           ^^^^
AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
---- [a2] rc=1 標準出力の行数=72（ok の数）
  File "…/scratchpad/016/pre/tests/test_stream.py", line 480, in <module>
    _s75 = _oss_up_sh[_oss_up_sh.index('\nlog "7-5. '):_oss_up_sh.index("\n# ---- 8. workflow ")]
                      ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^
ValueError: substring not found
---- [b] rc=1 標準出力の行数=72（ok の数）
  File "…/scratchpad/016/pre/tests/test_stream.py", line 479, in <module>
    _s83 = _up_sh[_up_sh.index("\n# ---- 8-3. Web "):_up_sh.index("\n# ---- 8-5. ")]
                  ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
ValueError: substring not found
```

OC1 の再現（c4c378a と aa6efad の tests、`assert` の版）。aa6efad は (o2)〜(o5)・(o7) を通していた。(p1) は c4c378a でも通る（OC7、014 からの弱さ）。最初の回の (p1) の「置換できない」は変異の当て先の誤りで、当て直して 2 回目に打った。

```
## 直す前のテスト（c4c378a）
[o1] 7-5 の run_on_instance 行の頭に [ -n "${X:-}" ] && を足す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o2] 7-5 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o3] 7-5 の run_on_instance 行の前に [ -z "${X:-}" ] || exit 0 / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o4] 7-5 の run_on_instance 行を while false; do … done で包む / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o5] 7-5 の run_on_instance 行を : <<'__X__' … __X__ で殺す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o6] 7-5 の run_on_instance 行の後ろに空行とコメント行（退行ではない） / assert: rc=0 | 最後の出力行: 通過 88 / 失敗 0 | 
[p1] 8-3 の run_on_instance 行を関数 _x() { … } で包む（呼ばない）: 置換できない
## いまのテスト（aa6efad）
[変異なし] assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[o1] 7-5 の run_on_instance 行の頭に [ -n "${X:-}" ] && を足す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o2] 7-5 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[o3] 7-5 の run_on_instance 行の前に [ -z "${X:-}" ] || exit 0 / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[o4] 7-5 の run_on_instance 行を while false; do … done で包む / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[o5] 7-5 の run_on_instance 行を : <<'__X__' … __X__ で殺す / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[o6] 7-5 の run_on_instance 行の後ろに空行とコメント行（退行ではない） / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[p1] 8-3 の run_on_instance 行を関数 _x() { … } で包む（呼ばない）: 置換できない
## 直す前のテスト（c4c378a）
[o7] 7-5 の run_on_instance 行の前の行に false &&（行の継続） / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[p1] 8-3 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / assert: rc=0 | 最後の出力行: 通過 88 / 失敗 0 | 
## いまのテスト（aa6efad）
[o7] 7-5 の run_on_instance 行の前の行に false &&（行の継続） / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[p1] 8-3 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
```

直したあと（`final/` = OC1 と OC4 を直した最後の tests の写し）。`ValueError` は無い。(a) と (o6) は通る。(a2)(a3)(b) と (o1)〜(o5)・(o7) は同じ check の `AssertionError`、count で失敗 1。(c) は失敗 2。(p1) は通る（OC7。直していない）。

```
[変異なし] assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[変異なし] count: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[a] 7-5 の run_on_instance 行の前に空行 / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[a] 7-5 の run_on_instance 行の前に空行 / count: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[a2] 7-5 の log の見出しを 7-6 に変える（_s75 の開始マーカーが外れる） / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[a2] 7-5 の log の見出しを 7-6 に変える（_s75 の開始マーカーが外れる） / count: rc=0 | 最後の出力行: 通過 88 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[a3] 7-5 の run_on_instance 行を消す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[a3] 7-5 の run_on_instance 行を消す / count: rc=0 | 最後の出力行: 通過 88 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[b] _s83 の開始マーカー # ---- 8-3. Web を # ---- 8-3. web に / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[b] _s83 の開始マーカー # ---- 8-3. Web を # ---- 8-3. web に / count: rc=0 | 最後の出力行: 通過 88 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[c] テンプレートの Wants=${name_prefix}-kafka-ui.service を消す / assert: rc=1 | 最後の出力行: ok Kafbat UI は Web の EC2 の systemd のユニットが Docker のコンテナを 127. | stderr: AssertionError: 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8-3（OSS 版は 7-5）の systemctl restart <接頭辞>-web が起こす。弱い依存だけにして、Kafbat UI が落ちても Web を止めない（Requires / BindsTo / PartOf / Requisite にしな
[c] テンプレートの Wants=${name_prefix}-kafka-ui.service を消す / count: rc=0 | 最後の出力行: 通過 87 / 失敗 2 | NG 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8-3（OSS 版は 7-5）の systemctl restart <接頭辞>-web が起こす。弱い依存だけにして、Ka / NG 描いた user_data の Web のユニットにも Wants=x-nwc-poc-kafka-ui.service があり、kafka-ui を含む行はその 1 行だけ（cycle 016） | 
[o1] 7-5 の run_on_instance 行の頭に [ -n "${X:-}" ] && を足す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o1] 7-5 の run_on_instance 行の頭に [ -n "${X:-}" ] && を足す / count: rc=0 | 最後の出力行: 通過 88 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o2] 7-5 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o2] 7-5 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / count: rc=0 | 最後の出力行: 通過 88 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o3] 7-5 の run_on_instance 行の前に [ -z "${X:-}" ] || exit 0 / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o3] 7-5 の run_on_instance 行の前に [ -z "${X:-}" ] || exit 0 / count: rc=0 | 最後の出力行: 通過 88 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o4] 7-5 の run_on_instance 行を while false; do … done で包む / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o4] 7-5 の run_on_instance 行を while false; do … done で包む / count: rc=0 | 最後の出力行: 通過 88 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o5] 7-5 の run_on_instance 行を : <<'__X__' … __X__ で殺す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o5] 7-5 の run_on_instance 行を : <<'__X__' … __X__ で殺す / count: rc=0 | 最後の出力行: 通過 88 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o6] 7-5 の run_on_instance 行の後ろに空行とコメント行（退行ではない） / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[o6] 7-5 の run_on_instance 行の後ろに空行とコメント行（退行ではない） / count: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[o7] 7-5 の run_on_instance 行の前の行に false &&（行の継続） / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o7] 7-5 の run_on_instance 行の前の行に false &&（行の継続） / count: rc=0 | 最後の出力行: 通過 88 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[p1] 8-3 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / assert: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
[p1] 8-3 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / count: rc=0 | 最後の出力行: 通過 89 / 失敗 0 | 
```

### 検証 6: check.sh

012 のマージの前。最後の docs の編集（`docs/pipeline.md`、02:35:24）のあとに打った（02:35〜02:38）。各段の結果の行と末尾だけを抜いた（全文は 2701 行、scratch の `check2.log`）。

```
$ bash ops/check.sh > check2.log 2>&1; echo "rc=$?" >> check2.log
$ grep -n '^== \|差分なし\|  OK$\|bash -n\|構文エラー\|すべて通過\|^通過\|^rc=' check2.log
2:== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
3:差分なし
5:== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
6:IaC/terraform/aws-managed/base/ecr  OK
7:IaC/terraform/aws-managed/base/core  OK
8:IaC/terraform/aws-managed/agent  OK
9:IaC/terraform/aws-managed/pipeline/lab  OK
10:IaC/terraform/aws-managed/pipeline/stream  OK
11:IaC/terraform/aws-managed/pipeline/analytics  OK
12:IaC/terraform/aws-managed/pipeline/graph  OK
13:IaC/terraform/aws-managed/pipeline/nautobot  OK
14:IaC/terraform/aws-managed/workflow  OK
15:IaC/terraform/oss/base/ecr  OK
16:IaC/terraform/oss/base/core  OK
17:IaC/terraform/oss/agent  OK
18:IaC/terraform/oss/pipeline/lab  OK
19:IaC/terraform/oss/pipeline/stream  OK
20:IaC/terraform/oss/pipeline/analytics  OK
21:IaC/terraform/oss/pipeline/graph  OK
22:IaC/terraform/oss/pipeline/nautobot  OK
23:IaC/terraform/oss/workflow  OK
25:== 3. スクリプトの構文
26:bash -n: 26 本
27:構文エラーなし
29:== 4. 模擬テスト
188:通過 158 / 失敗 0
679:通過 489 / 失敗 0
1254:通過 161 / 失敗 0
1258:通過 3 / 失敗 0
1338:通過 78 / 失敗 0
1348:通過 7 / 失敗 0
1453:通過 104 / 失敗 0
1576:通過 122 / 失敗 0
1648:69 項目すべて通過
1820:通過 171 / 失敗 0
1977:通過 156 / 失敗 0
2044:通過 66 / 失敗 0
2134:通過 89 / 失敗 0
2253:通過 103 / 失敗 0
2698:通過 327 / 失敗 0
2700:すべて通過
2701:rc=0
```

`test_stream` は 2134 行目の 89 で、`docs/development.md:37` の 89 と同じ。

### 検証 7〜9: grep

012 のマージの前。最後の docs の編集のあと（02:42）に取った。

```
$ grep -c 'Kafbat UI' docs/troubleshooting.md
5
$ grep -n '^### Kafbat UI' docs/troubleshooting.md
100:### Kafbat UI
$ grep -n 'Kafbat UI のポートフォワード' docs/troubleshooting.md
94:| Kafbat UI のポートフォワードがつながらない、画面が開かない | 下の「Kafbat UI」を見る |
$ grep -n 'admin-password' docs/deploy.md docs/troubleshooting.md docs/pipeline.md | cut -d: -f1,2
docs/deploy.md:26
docs/deploy.md:276
docs/deploy.md:278
docs/troubleshooting.md:107
docs/troubleshooting.md:109
docs/pipeline.md:40
docs/pipeline.md:156
docs/pipeline.md:189
docs/pipeline.md:385
$ grep -o '[^。]*unmask[^。]*' docs/troubleshooting.md docs/pipeline.md
docs/troubleshooting.md:戻すのは `sudo systemctl unmask --runtime <prefix>-kafka-ui`（`--runtime` を付けないと `/run` の mask は外れない）
docs/pipeline.md:戻すのは `sudo systemctl unmask --runtime <prefix>-kafka-ui`
$ grep -c 'unmask <\|unmask`' docs/troubleshooting.md docs/pipeline.md
docs/troubleshooting.md:0
docs/pipeline.md:0
$ sed -n '506p' ops/up.sh
    # Telegraf（ECS）: イメージを ECR から引き、ログを CloudWatch に書く。MSK は VPC の中。Web の EC2 の Kafbat UI（Docker）も同じ ecr.api / ecr.dkr で ECR から pull する
$ git diff --stat c4c378a -- ops/up.sh
 ops/up.sh | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

`grep -c 'Kafbat UI'` の 5 は :94（表の案内）、:100（見出し）、:102（小節の頭の文）、:112（コンテナの 75）、:124（パスワードかイメージを変えた）。`admin-password` の deploy.md:278 と pipeline.md の :40 / :189 / :385 は前からある行（ポリシーの Resource、パスワードの取り出し方など）。

### セルフレビュー

- 自分: claude-opus-5-5 / effort はセッションの既定のまま（`xhigh` への切り替えは `set_session_effort` が使えず、確かめられていない）。入力は design.md と実装したファイルだけ
- 反対弁護人: `Agent`（general-purpose、model `opus`）を 1 回。読み取り専用。文脈（設計の意図、実装の方針、迷った点）を渡した。返ってきたあと `git status --porcelain -uall` は呼ぶ前と同じ（HEAD aa6efad）。変異は scratch の `016-oc/` の写しだけ

件数: **Must fix 0 / Should fix 5 / Nit 7**。Should fix は自分の 2（SR1、SR3）と反対弁護人の 3（OC1、OC2、OC3）で、全部直した。Nit は SR2 と OC4〜OC9。OC5 は一部だけ直し、OC7 は直していない（理由は各項目）。

#### SR1: Should fix（直した）

[correctness / docs] `docs/deploy.md:102`

- 破綻シナリオ: 最初の注記は、手順 10 が Kafbat UI のポートフォワードも開くように読めた。読み手は手順 10 の直後に `http://localhost:8082/` を開いてつながらず、Kafbat UI が落ちたと思う。
- 確かめたこと: 手順 10 が開くのは 8080 だけ。

  ```
  $ sed -n 1313,1319p ops/up.sh
  if [ -n "$NO_DASHBOARD_PORTFORWARD" ]; then exit 0; fi
  log "10. ポートフォワーディング（http://localhost:$LOCAL_PORT/ 。Ctrl+C で閉じる）"
  trap - EXIT
  if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi
  exec aws ssm start-session --region "$REGION" --target "$INSTANCE_ID" \
    --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"8080\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
  ```

- 片付け: 「手順 10 が自分で開くのは Web（EC2 の 8080）のポートフォワードだけで、Kafbat UI（EC2 の 8082）は表示されたコマンド（`kafka_ui_port_forward_command`）を別のターミナルで打って開く」に直した。

#### SR2: Nit（直した）

[docs] `docs/troubleshooting.md:107`

- 破綻シナリオ: 「行の頭の名前が最初に無かったパラメータ」と書いていたが、行の頭はパラメータの名前ではない（`… does not exist` の形）。
- 片付け: 「`does not exist` の前の名前が、最初に無かったパラメータ」に直した。

#### SR3: Should fix（直した）

[correctness / docs] `docs/troubleshooting.md:115`

- 破綻シナリオ: 「ユニットが無い」の項目は「ユニットを書く前に落ちた」と言い切っていた。下の `daemon-reload` / `enable --now` の項目（ユニットは書けた）と食い違い、読み手は `could not be found` を見てユニットのファイルを探しに行かない。
- 片付け: 「ユニットを書く前に落ちたときはこれ。下の `daemon-reload` / `enable --now` で落ちたときもこれになることがある」に直した。

#### OC1: Should fix（直した）

[テストの検出力・退行] `tests/test_stream.py:484-492`

- 反対弁護人の指摘: aa6efad の 7-5 の check は、`_s75` に `run_on_instance` 行が含まれるかだけを見ていて、行を条件・関数・ループ・ヒアドキュメント・継続で殺す退行を通す。拾うのは 11 件中 2 件、c4c378a は 9 件。
- 再現: (o1)〜(o7) を c4c378a と aa6efad の tests に当てた（検証 2〜5 の「OC1 の再現」）。aa6efad は (o2)(o3)(o4)(o5)(o7) を通す。c4c378a はどれも落とす。
- 片付け: 7-5 を `re.fullmatch` に直し、`log "7-5. …"` の行と `run_on_instance` 行のあいだと後ろに、空行とコメント行しか許さない形にした。直したあとは (o1)〜(o5)・(o7) を落とし、(a)(o6) は通す（検証 2〜5 の「直したあと」）。design.md の検証方法に (o1)〜(o7) を足した。7-5 の節の末尾に行を足すと落ちるようになるが、c4c378a の `split("\n")[2]` と同じく保守的な側に倒した。

#### OC2: Should fix（直した。PM が範囲拡張を承認）

[docs の食い違い] `docs/pipeline.md:156`

- 破綻シナリオ: Kafbat UI の本節に、このサイクルが誤りとした 2 文が残っていた。1 つは「stream だけ上げたら `sudo systemctl start`」で、従うと `admin-password` が無く 75 で止まる。もう 1 つは「戻すのは `unmask`」で、`/run` の mask が外れない。
- 再現:

  ```
  $ git show c4c378a:docs/pipeline.md | grep -o "[^。]*\(systemctl start\|unmask\)[^。]*"
  戻すのは `unmask`）
  `ops/up.sh` を通さずに stream だけを上げたら、Web の EC2 で `sudo systemctl start <prefix>-kafka-ui`
  ```

- 片付け: PM に範囲拡張を確かめ、承認（2026-10-09）を得てから deploy.md:26 と同じ訂正と `unmask --runtime` を入れた。design.md の変更対象に pipeline.md を足し、design-log.md に承認を 1 行残した。

#### OC3: Should fix（直した）

[移したときの意味の変化] `docs/troubleshooting.md:124`

- 破綻シナリオ: 表の行の「同じ回では入れ替わらない」を、移したときに「`ops/up.sh` の中の再起動では入れ替わらない」と書いた。実際は次に打ち直した回の手順 4-4 の再起動で入れ替わるので、逆のことを言っていた。
- 再現:

  ```
  $ sed -n 823,827p ops/up.sh

  log "4-4. EC2 を再起動して Web を立てる（初回の apply 時点では app/dashboard/ が無いため）"
  wait_ssm_online "$INSTANCE_ID"
  run_on_instance "$INSTANCE_ID" "true"          # 初回の user_data が終わるのを待ってから再起動する
  aws ec2 reboot-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
  ```

- 片付け: 「同じ回の `ops/up.sh` の中では入れ替わらない（手順 4-4 の EC2 の再起動は stream の apply（手順 7）より前で、手順 8-3 の Web の再起動は動いている Kafbat UI に触らない）。次に打ち直した回の手順 4-4 の再起動で新しい値を読む」に直した。

#### OC4: Nit（直した）

[コメントの誤り] `tests/test_stream.py:479`

- 破綻シナリオ: `_between` のコメントの「`.index()` の ValueError で残りの検査を止めない」は誤り。`check()` は `assert` なので、そこから先は走らない。
- 片付け: 「`.index()` の ValueError ではなく、それを使う check の名前で落ちる」に直した。

#### OC5: Nit（一部だけ直した）

[docs の正確さ・未確認] `docs/troubleshooting.md:115` と `:117`

- 指摘: `daemon-reload` / `enable --now` で落ちた場合はユニットのファイルが書けているので、AL2023 の systemd 252 では `could not be found` ではなく `disabled` になると読める。もう 1 つ、`enable` は通り `start` だけ落ちた形が無い。
- 片付け: `setup failed` の項目（:117）を足し、`status` は「`could not be found` か `disabled` になる（どちらになるかは未確認）」と書いた。systemd 252 で確かめていない（反対弁護人も推論）。
- 直さなかったもの: 「`enable` は通り `start` だけ落ちた」は別の項目にしない。`systemctl status` が `enabled` のまま `failed` になり、journald の行で 75 / 69 / docker の項目に分けられるので、項目を足すと重複する。

#### OC6: Nit（直した）

[OSS 版の読者] `docs/troubleshooting.md:108-109`

- 破綻シナリオ: 「`SKIP_STREAM` を外して `ops/up.sh` を打ち直す」は OSS 版には当たらない（`oss/ops/up.sh` は `SKIP_STREAM` を読まない）。
- 片付け: OSS 版は `oss/ops/up.sh` が `SKIP_STREAM` を読まず手順 7-5 で起こすこと、`admin-password` は `oss/ops/up.sh` の手順 7 が作ることを足した。

#### OC7: Nit（直していない。次の候補）

[テストの検出力・既存] `tests/test_stream.py:487-489`（8-3 の側）

- 指摘: 8-3 の側は、if の中で `&&` で続ける形、字下げした `if` で包む形、呼ばない関数で包む形を通す。
- 再現: (p1) は c4c378a でも aa6efad でもいまの tests でも通る（検証 2〜5）。014 からある弱さで、このサイクルで弱くしたものではない。
- 直さない理由: このサイクルの tests の範囲は「014 の 2 本を `ValueError` で止まらない形に」で、8-3 の検出力を上げるのはその外。7-5 の OC1 は、このサイクルの変更で弱くした分を戻したもの。次の候補として PM に渡す（BACKLOG は PM が書く）。

#### OC8: Nit（直した）

[docs] `docs/deploy.md:102`

- 破綻シナリオ: 「Web（8080）」は手元のポートと読める。手元のポートは `LOCAL_PORT`（既定 8080）。
- 片付け: 「Web（EC2 の 8080）」に直した。

#### OC9: Nit（直した）

[記録] `docs/cycles/016-kafbat-ui-nits/build.md`

- 破綻シナリオ: 検証 5 が「（check.sh の結果を貼る）」のままで、セルフレビューの節が無かった。
- 片付け: この build.md で埋めた。

#### 反対弁護人が反証を試みて成立しなかった点

- 「75 なのに `does not exist` が無い」の項目。`exec docker run --rm` の終了コードはコンテナの終了コードで、docker 自身の失敗（125/126/127）や `set -e` の 1 は 75 にならない
- 読む順（image → bootstrap-servers → security-protocol → admin-password）と手順の番号（admin-password は手順 7、`ops/up.sh:919`、`oss/ops/up.sh:324`）
- 「010 と 014」の一覧（テンプレートの `git log` は 3c3e6c0 と eceb91b）
- `unmask --runtime`（systemd の `unit_file_unmask`）
- `Started KafkaUiApplication`（上の「手元で確かめたこと」）
- 範囲（変更は docs と `ops/up.sh` のコメント 1 行とテストと build.md だけ）
- 表の行を節に移しても中身は落ちていない（OC3 の 1 か所を除く）
- テストの数（89）と count の写しの数
- `_kwunit` の check（変異 (c) で `_wunit` の check と両方が落ちる）

#### 問題なしとした観点と根拠

- 範囲: 触らないファイルに差分が無い（実行した）

  ```
  $ git diff --stat c4c378a -- IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl IaC/terraform/aws-managed/base/core/web.tf IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf oss/ops/up.sh docs/architecture docs/cycles/BACKLOG.md | wc -l
         0
  ```

  `ops/up.sh` はコメント 1 行だけ（検証 9 の `git diff --stat`）。
- tests の検出力: 変異 (a)〜(c)・(o1)〜(o7)・(p1) を実際に当てた（検証 2〜5）。8-3 の側だけ OC7 の弱さが残る。
- docs の事実: 手順の番号、ポート、再起動の位置は `ops/up.sh` の該当行を `sed` で読んで合わせた（SR1、OC3）。`Started KafkaUiApplication` と起動時間は手元の Docker で測った（AWS では未計測）。`unmask --runtime`、`could not be found` / `disabled`、コンテナが 75 で終わる場面は AL2023 で確かめていない（読んだだけ）。
- 全体の退行: `bash ops/check.sh` が `すべて通過`、rc=0（検証 6）。

### 012 のマージ

PM の指示で、`docs/cycle-006-design` の 5be0288（「MSK に SASL/SCRAM を足し、syslog-ng と GoFlow2 を立てる（012）」のマージ。c4c378a から 10 commit、74 ファイル）を `fix/kafbat-ui-nits` にマージした。親は 309c216 と 5be0288。

#### 衝突と解き方

衝突は 3 本。どれも 1 か所で、`scratch` の `resolve.py` が「衝突が 1 か所だけ・行の頭が想定どおり」を assert してから書き換えた。

| ファイル | 衝突した所 | 解き方 |
|---|---|---|
| `docs/deploy.md` | 引数の表の `SKIP_LAB` / `SKIP_STREAM` / `SKIP_ANALYTICS` の 3 行 | `SKIP_LAB` と `SKIP_ANALYTICS` は 012 の行。`SKIP_STREAM` は 016 の行（打ち直しの案内）に、012 の中身「MSK、Telegraf・syslog-ng・GoFlow2 の ECS、MSK の SCRAM の secret と KMS の鍵」と `-$1.82/h` を入れた |
| `docs/development.md` | :37 の `tests/test_*.py` の数の一覧 | 012 の行を取り、`test_stream` を下の実測の 96 に直した（012 の行は 95、016 の行は 89） |
| `ops/up.sh` | :505 の `pipeline/stream)` のエンドポイントのコメント | 012 のコメント 2 行と `add_endpoints ecr.api ecr.dkr logs secretsmanager` を取り、1 行目の末尾に 016 の文「Web の EC2 の Kafbat UI（Docker）も同じ ecr.api / ecr.dkr で ECR から pull する」を足した |

`docs/pipeline.md`、`docs/troubleshooting.md`、`tests/test_stream.py` ほか 012 のファイルは自動でマージされた。

#### 行の番号の移動

上の「変更ファイル」と検証 7〜9 の行の番号はマージ前のもの。マージ後はこう動いた。

| 場所 | マージ前 | マージ後 |
|---|---|---|
| `ops/up.sh` の Kafbat UI の ECR のコメント | :506 | :505 |
| `ops/up.sh` の `ensure_secret "/$PREFIX/kafka-ui/admin-password"`（手順 7） | :919 | :931 |
| `oss/ops/up.sh` の同じ行（手順 7） | :324 | :334 |
| `ops/up.sh` の `log "10.` | :1313 | :1337 |
| `docs/deploy.md` の手順 10 の下の注記 2 本 | :102-103 | :103-104 |
| `docs/deploy.md` の 2 つ目の打ち直しの案内 | :276 | :279 |
| `docs/pipeline.md` の Kafbat UI の訂正 | :156 | :157 |
| `docs/troubleshooting.md` の表の案内 | :94 | :96 |
| `docs/troubleshooting.md` の `### Kafbat UI` | :100 | :102 |

#### 範囲

5be0288 との差は 016 のファイルだけ。触らないファイルには差が無い。

```
$ git diff --stat 5be0288 | tail -12
 docs/cycles/016-kafbat-ui-nits/build.md      | 425 +++++++++++++++++++++++++++
 docs/cycles/016-kafbat-ui-nits/design-log.md |  34 +++
 docs/cycles/016-kafbat-ui-nits/design.md     | 108 ++++---
 docs/deploy.md                               |   7 +-
 docs/development.md                          |   2 +-
 docs/pipeline.md                             |   2 +-
 docs/troubleshooting.md                      |  30 +-
 ops/up.sh                                    |   2 +-
 tests/test_stream.py                         |  20 +-
 9 files changed, 575 insertions(+), 55 deletions(-)
$ git diff --stat 5be0288 -- IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl IaC/terraform/aws-managed/base/core/web.tf IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf oss/ops/up.sh docs/architecture docs/cycles/BACKLOG.md | wc -l
       0
```

（`build.md` の 425 行はこの節を書く前の数。）

#### 検証 1: test_stream

```
$ uv run --group dev --group web python tests/test_stream.py > m1.log 2>&1; echo "rc=$?" >> m1.log
$ grep -c '^ok ' m1.log; grep -v '^ok ' m1.log
96
通過 96 / 失敗 0
rc=0
```

012 が `test_stream` に 7 本足したので 89 → 96。

#### 検証 2〜5: 変異

マージ後のツリーの写し（scratch の `merged/`）に `mut16.py` で同じ変異を当てた。生ログ（`merged-mut.log`）:

```
[変異なし] assert: rc=0 | 最後の出力行: 通過 96 / 失敗 0 | 
[変異なし] count: rc=0 | 最後の出力行: 通過 96 / 失敗 0 | 
[a] 7-5 の run_on_instance 行の前に空行 / assert: rc=0 | 最後の出力行: 通過 96 / 失敗 0 | 
[a] 7-5 の run_on_instance 行の前に空行 / count: rc=0 | 最後の出力行: 通過 96 / 失敗 0 | 
[a2] 7-5 の log の見出しを 7-6 に変える（_s75 の開始マーカーが外れる） / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[a2] 7-5 の log の見出しを 7-6 に変える（_s75 の開始マーカーが外れる） / count: rc=0 | 最後の出力行: 通過 95 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[a3] 7-5 の run_on_instance 行を消す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[a3] 7-5 の run_on_instance 行を消す / count: rc=0 | 最後の出力行: 通過 95 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[b] _s83 の開始マーカー # ---- 8-3. Web を # ---- 8-3. web に / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[b] _s83 の開始マーカー # ---- 8-3. Web を # ---- 8-3. web に / count: rc=0 | 最後の出力行: 通過 95 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[c] テンプレートの Wants=${name_prefix}-kafka-ui.service を消す / assert: rc=1 | 最後の出力行: ok Kafbat UI は Web の EC2 の systemd のユニットが Docker のコンテナを 127. | stderr: AssertionError: 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8-3（OSS 版は 7-5）の systemctl restart <接頭辞>-web が起こす。弱い依存だけにして、Kafbat UI が落ちても Web を止めない（Requires / BindsTo / PartOf / Requisite にしな
[c] テンプレートの Wants=${name_prefix}-kafka-ui.service を消す / count: rc=0 | 最後の出力行: 通過 94 / 失敗 2 | NG 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8-3（OSS 版は 7-5）の systemctl restart <接頭辞>-web が起こす。弱い依存だけにして、Ka / NG 描いた user_data の Web のユニットにも Wants=x-nwc-poc-kafka-ui.service があり、kafka-ui を含む行はその 1 行だけ（cycle 016） | 
[o1] 7-5 の run_on_instance 行の頭に [ -n "${X:-}" ] && を足す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o1] 7-5 の run_on_instance 行の頭に [ -n "${X:-}" ] && を足す / count: rc=0 | 最後の出力行: 通過 95 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o2] 7-5 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o2] 7-5 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / count: rc=0 | 最後の出力行: 通過 95 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o3] 7-5 の run_on_instance 行の前に [ -z "${X:-}" ] || exit 0 / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o3] 7-5 の run_on_instance 行の前に [ -z "${X:-}" ] || exit 0 / count: rc=0 | 最後の出力行: 通過 95 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o4] 7-5 の run_on_instance 行を while false; do … done で包む / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o4] 7-5 の run_on_instance 行を while false; do … done で包む / count: rc=0 | 最後の出力行: 通過 95 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o5] 7-5 の run_on_instance 行を : <<'__X__' … __X__ で殺す / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o5] 7-5 の run_on_instance 行を : <<'__X__' … __X__ で殺す / count: rc=0 | 最後の出力行: 通過 95 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[o6] 7-5 の run_on_instance 行の後ろに空行とコメント行（退行ではない） / assert: rc=0 | 最後の出力行: 通過 96 / 失敗 0 | 
[o6] 7-5 の run_on_instance 行の後ろに空行とコメント行（退行ではない） / count: rc=0 | 最後の出力行: 通過 96 / 失敗 0 | 
[o7] 7-5 の run_on_instance 行の前の行に false &&（行の継続） / assert: rc=1 | 最後の出力行: ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8- | stderr: AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
[o7] 7-5 の run_on_instance 行の前の行に false &&（行の継続） / count: rc=0 | 最後の出力行: 通過 95 / 失敗 1 | NG Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5  | 
[p1] 8-3 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / assert: rc=0 | 最後の出力行: 通過 96 / 失敗 0 | 
[p1] 8-3 の run_on_instance 行を関数 _x() { … } で包む（呼ばない） / count: rc=0 | 最後の出力行: 通過 96 / 失敗 0 | 
```

マージ前の同じ表（`final-mut.log`）と、「通過 N / 失敗 M」の N だけを伏せて比べると全行が同じ（どの変異がどの check で落ちるかは変わらない。(p1) は OC7 のとおり通るまま）。

```
$ python3 - <<'EOF'   # 通過 \d+ を 通過 N にして final-mut.log と merged-mut.log を比べる
…
EOF
28 28 SAME-SHAPE
```

#### 検証 6: check.sh

```
$ bash ops/check.sh; echo rc=$?
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし

== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK

== 3. スクリプトの構文
bash -n: 29 本
構文エラーなし
…（各テストの最後の行。check3.log の行番号付き）
188:通過 158 / 失敗 0
685:通過 495 / 失敗 0
1260:通過 161 / 失敗 0
1339:通過 78 / 失敗 0
1343:通過 3 / 失敗 0
1423:通過 78 / 失敗 0
1433:通過 7 / 失敗 0
1538:通過 104 / 失敗 0
1671:通過 132 / 失敗 0
1743:69 項目すべて通過
1916:通過 172 / 失敗 0
2098:通過 181 / 失敗 0
2165:通過 66 / 失敗 0
2262:通過 96 / 失敗 0
2381:通過 103 / 失敗 0
2826:通過 327 / 失敗 0
…
ok Temporal UI（8233）は土台の通信の表の web → workflow の 1 行で、workflow にはルールも Web の SG の参照も無く、7233 の行は無い
ok 修復案の status に obsolete がある（tools.json の説明も）
通過 327 / 失敗 0

すべて通過
rc=0
```

16 本の数は `docs/development.md:37` の一覧と全部同じ。

#### 検証 7〜9: grep

```
$ grep -c 'Kafbat UI' docs/troubleshooting.md
5
$ grep -n '^### Kafbat UI' docs/troubleshooting.md
102:### Kafbat UI
$ grep -n 'Kafbat UI のポートフォワード' docs/troubleshooting.md
96:| Kafbat UI のポートフォワードがつながらない、画面が開かない | 下の「Kafbat UI」を見る |
$ grep -n 'kafka-ui/admin-password' docs/deploy.md docs/troubleshooting.md docs/pipeline.md | cut -d: -f1,2
docs/deploy.md:26
docs/deploy.md:279
docs/troubleshooting.md:111
docs/pipeline.md:157
$ grep -no 'unmask[^`]*' docs/troubleshooting.md docs/pipeline.md
docs/troubleshooting.md:128:unmask --runtime <prefix>-kafka-ui
docs/pipeline.md:157:unmask --runtime <prefix>-kafka-ui
$ sed -n '505p' ops/up.sh
    # Telegraf・syslog-ng・GoFlow2（ECS）: イメージを ECR から引き、ログを CloudWatch に書く。MSK は VPC の中。Web の EC2 の Kafbat UI（Docker）も同じ ecr.api / ecr.dkr で ECR から pull する
$ git diff --stat 5be0288 -- ops/up.sh
 ops/up.sh | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

#### design.md の上書き

検証方法の数を実測に合わせた（89 / 88 / 87 → 96 / 95 / 94）。9 の行（:506 → :505、差の基準を 5be0288 に）、変更対象の `docs/development.md` の数（95 → 96）、未確定事項の 012 の項目（マージの結果）も直した。方針と範囲は変えていない。design-log.md に 1 行。
