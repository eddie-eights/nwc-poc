## Round 1

レビューモデル: opus-5.5 / effort: xhigh（cold reviewer に依頼。初回ビルド直後）。確認は PM(fable-5-1)。2026-10-10。

対象: ブランチ `feat/038-nlb-ingress-docs`、`df18d07..446da28`（821cf54 docs、446da28 セルフレビュー）。10 ファイル、+139 / -4。

## サマリ

「NLB の受信の説明を 037 に合わせる（038）」の Round 1 をレビューした。対象は `git diff df18d07..HEAD`（2 commit、10 ファイル、+139 / -4）。build.md の 122 行を除くと、`.tf` のコメント 3 か所と docs 6 ファイルで 17 行ほどの追記になる。

全体の評価:

- design.md の変更対象 7 項目（設計方針 2〜6）は全部入っていて、設計に無い変更は混じっていない。
- `.tf` の差分はコメント行だけで、`sg_flows` と `tests/test_analytics.py` の `EXPECTED_FLOWS` には触れていない。
- 書いた事実（lab の SG から NLB への 5140 / 2055 / 6343 は両側、162 は `only = "egress"`）は `security_groups.tf:118-126` と、`:162` / `:175` の `for_each` の条件に合っている。
- 設計方針 6 の文面からの逸脱（trap は落ちる、syslog は `tags.source` が変わる、sysName は HOST から取る）は build.md に理由付きで書いてある。`syslog-ng.conf.in:14` / `:56` / `:60` と `snmp_sinks.py:461-469` を読んで、逸脱した側が正しいことを確かめた。
- Must fix は無い。Should fix は 1 件で、新しく足した 3 か所が、AWS でまだ確かめていない送り方を、確かめていないと断らずに「試せる」と書いている点。

### 見た観点 / 見ていない観点

見た観点:

- design.md との整合性: 設計方針 1〜7、変更対象ファイルの表、未確定事項 2・3 と、実際の差分を 1 つずつ突き合わせた。design.md の検証 2〜6 も自分で打った。
  - 検証 2: `git diff -U0 df18d07 -- '*.tf' | grep '^[+-][^+-]' | grep -v '^[+-][[:space:]]*#' | wc -l` → `0`
  - 検証 3: パターンを `管理ネットワークの CIDR から` に広げて打つと 3 件出た。3 件とも、同じ行か直後の行で lab の SG からの 5140 / 2055 / 6343 に触れている。
  - 検証 4: core.md:111 と vpc-perimeter.md:130 に 1 行ずつ出た。
  - 検証 5: 3 ファイルとも 1 件ずつ出た。
  - 検証 6: troubleshooting.md:140 に 1 件出た。
- correctness（書いた事実が実装と合っているか）: 次のものを読んで確かめた。
  - `security_groups.tf:115-126` の `sg_flows` と、`:162`（egress）/ `:175`（ingress）の `for_each` の条件
  - `oss.tf:75-83`（OSS 版も lab と NLB の行を外さない）
  - `app/containerlab/lab.sh:173` / `:333-334` / `:340`（行番号は design.md のとおりだった）
  - `app/syslog-ng/syslog-ng.conf.in:14` / `:56` / `:60`
  - `app/spark/snmp_sinks.py:461-469`
  - `app/splunk/nwc_alerts/default/savedsearches.conf:67` と `bin/nwc_sns.py:70-76`
  - troubleshooting.md の「syslog の試験行が logs に入らない」（370〜466 行）
- リンク先: 足したリンク（`troubleshooting.md`、`pipeline.md`）は同じディレクトリにあり、指している見出し「syslog の試験行が logs に入らない」（370 行）と「正しい送り方」（439 行）もある。
- OSS 版: `IaC/terraform/oss/` の 3 ファイルがシンボリックリンクであることを `ls -la` で確かめた。
- 自分で打った検査:
  - `terraform fmt -check -recursive IaC/terraform/aws-managed` と同じく `IaC/terraform/oss` → どちらも差分なし
  - `git diff --check df18d07..HEAD` → 出力なし
  - 変更したファイルを読むテスト 6 本（`uv run --group dev --group web python tests/<名前>`）:
    - `test_stream.py` → `通過 114 / 失敗 0`
    - `test_sync.py` → `通過 104 / 失敗 0`
    - `test_analytics.py` → `通過 549 / 失敗 0`
    - `test_oss.py` → `通過 177 / 失敗 0`
    - `test_workflow.py` → `通過 355 / 失敗 0`
    - `test_nautobot.py` → `68 項目すべて通過`
- security / data loss / API compatibility / type safety: コメントと docs だけの変更なので、該当するものは無い。SG の `description`（`why`）も変わっていない（検証 2）。

見ていない観点:

- `./ops/check.sh` の全体は打っていない。`terraform init` が追跡中の `.terraform.lock.hcl` を書き換えるおそれがあり、「既存ファイルを変更しない」に反するため。
  - そのため、20 ルートの `validate`、`.sh` の `bash -n`、上の 6 本以外のテスト 10 本、旧名の検査は自分では確かめていない。結果は build.md の検証 1 の記録だけが根拠。
- AWS の上での動き（MASQUERADE された送り元が NLB の SG の `lab` 参照で通るか、client IP preservation と SG の組み合わせ）は確かめていない。design.md の方針 7 のとおり、このサイクルの対象外。
- SR Linux が実際にどの形式の syslog を出すかは実機で見ていない。`LAB_SYSLOG_STANDARD=RFC5424` と、docs にある手元の docker での再現結果を前提にした。

## Must fix

None

## Should fix

- [correctness（docs の確からしさ）] 新しく足した 3 か所が、AWS でまだ確かめていない送り方を、確かめていないと断らずに「試せる」と書いている。
  - 対象:
    - `docs/pipeline.md:107`（「syslog も lab の EC2 から `logger -n <NLB の IP> …` で試せる」）
    - `docs/collection.md:33`（「lab の EC2 から `logger` で NLB の 5140 へ送って試せる」）
    - `docs/deploy.md:96`（「syslog は lab の EC2 から `logger` で NLB へ送っても試せる」）
  - 根拠:
    - 3 か所が指す「lab の EC2 のホストから NLB の IP へ直接送る」形は、リンク先の `docs/troubleshooting.md:454` でも、037 が書いた `docs/architecture/resources/lab-ec2.md:44` でも「AWS では未確認」と書いてある。
    - 2026-10-09 の AWS ではこの送り方の行が `logs` に 1 件も入らなかった（`troubleshooting.md:372`）。直したのは 037 だが、AWS で確かめるのは QUEUE の動作確認のとき。
    - 直上の行は NetFlow を「確かめる」と書いているが、NetFlow は 2026-10-09 に `flows` に 2 件入ったのを確かめている（`troubleshooting.md:419`）。並べると、syslog も同じように確かめ済みと読める。
    - 同じファイルの 2 行上（`docs/deploy.md:94`）も「手元の docker で確かめた。ECS では未確認」と断っている。docs の書き方とも揃っていない。
  - 困る場面: QUEUE の「残った修正をまとめて AWS で動作確認して直す」でこの 3 か所を読んだ人が、`logger` の行が入らなかったとき、まだ確かめていない前提が外れたのではなく、壊れた（回帰した）と受け取る。
  - Should にした理由: リンク先には「未確認」とあり、動作もコードも壊れない。それでも、確からしさを揃えるための docs のサイクルで、確かめていない主張を 3 か所に増やすことになるので、今回のうちに直すのが望ましい（例: 各行の括弧に「AWS では未確認」を足す）。

## Nit

- [design.md 整合性] 「162 は受けない」の言い方が 4 通りに分かれていて、設計の未確定事項 2（`core.md:84` の表の言い方に揃える）と食い違う。build.md の「設計からの逸脱」にも書いていない。
  - 4 か所の言い方:
    - `security_groups.tf:117`: 「lab の SG は 162/udp も NLB へ送るが、NLB は trap を lab の SG から受けない」（表と同じ）
    - `stream/telegraf.tf:21`: 「trap の 162 は lab の SG から受けない」
    - `lab/telegraf.tf:17`: 「162 は受けない」
    - `core.md:93`: 「162/udp の trap は lab の SG から受けない」
  - 意味はどれも同じで、列を揃えたコメントの中では短くしたほうが読みやすい。そのため Nit にした。
- [docs] `docs/deploy.md:96` は `#### SKIP_LAB` の節（91 行目〜）の中にある。lab を作らないときの注意の中で、「lab の EC2 から `logger` で試せる」と案内している。
  - `SKIP_LAB` では lab の EC2 が無い。NLB の SG は `lab_mgmt` の CIDR と `lab` の SG からしか 5140 / 2055 / 6343 を受けないので、ほかの EC2 からは送れない。
  - Nit にした理由: 文は「lab の EC2 から」と前提を書いている。直上の NetFlow の行（95 行目、元からある）にも同じ曖昧さがある。読み違えても、lab の EC2 が無いことにすぐ気づく。直すなら、「lab を作ったときは」と前提を明記するか、`SKIP_LAB` では送り手が無いと書く。
- [docs] `docs/troubleshooting.md:140` の参照「[pipeline.md](pipeline.md) の `forward-status` の行」の行き先が 1 つに決まらない。
  - `docs/pipeline.md` には `forward-status` が 3 か所ある（251 行目の表の行、347 行目の箇条、387 行目のデバッグ用 EC2 の表の行）。詳しく書いたのは 347〜350 行目だが、「行」という言い方だと 251 行目の表の行に読める。
  - Nit にした理由: 3 か所とも同じファイルなので探せばたどり着く。直すなら、節の名前（「gnmic と Telegraf に入る」）を添えるか、見出しへのアンカーにする。
- [correctness（言い回し）] `docs/pipeline.md:350` の「機器の名前ではなく EC2 の IP として入る」は、比べる元の状態が正しくない。
  - 既定の RFC3164 の受け口で SR Linux（RFC5424）を受けているとき、`RETURN` が正しくても `tags.sysName` は機器の管理 IP（例 203.0.113.11）で、機器の名前ではない（`troubleshooting.md:408`、`collection.md:23` の「崩れる」）。
  - Spark は `with_sysname` が sysName を上書きしないので、管理 IP のまま入る（`snmp_sinks.py:465-466`）。Splunk の SNS の通知だけは `nwc_sns.py:70-76` が `DEVICE_MAP` で IP から機器の名前に引く。
  - 壊れたときにいちばん効くのは、lab の全部の機器の syslog が、1 つの EC2 の IP にまとまって区別できなくなること。
  - Nit にした理由: 結論（EC2 の IP として入る）は正しく、比べる元が見る側（Spark か Splunk か）で変わるので、誤りとまでは言えない。直すなら「機器ごとの区別が消え、全部が lab の EC2 の 1 つの IP として入る」。
- [docs] 差分の外に、同じ種類の書き足りない記述が残っている。設計の変更対象の外で、build.md のセルフレビューの S3 / S4 / S5 で PM に回してある。QUEUE の候補。
  - `docs/architecture/resources/vpc-perimeter.md:62`: 表の行が「lab の管理ネットワーク（203.0.113.0/24）、lab → telegraf_dialout_nlb：162/udp、5140/udp、2055/udp、6343/udp」と 1 行にまとめてある。lab の SG から 162 も受けると読める。正本の `core.md:83-84` は 2 行に分けて「162 は受けない」と書いている。
  - `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf:16`: 試し方が NetFlow の `ops/netflow_send.py` だけで、syslog の `logger` が無い。
  - `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf:3`: 「次の 3 つで届ける」が gNMI・trap・syslog だけで、NetFlow / sFlow の DNAT（`lab.sh:328-332`）が無い。
- [correctness（差分の外）] 038 がリンクを 3 か所足した先の `docs/troubleshooting.md:452`（037 で入った行）に誤りがある。build.md のセルフレビュー S6 と同じ内容で、自分でも確かめた。
  - 誤りの文: 「Spark の `DEVICE_MAP` は 203.0.113.101 を `dc1-trex-01` に引く」
  - 理由:
    - `logger --rfc3164` は HOST に送り手のホスト名を入れ、`keep-hostname(yes)`（`syslog-ng.conf.in:14`）のまま `tags.sysName` になる。
    - `with_sysname` は sysName があればそのまま返す（`snmp_sinks.py:465-466`）ので、`DEVICE_MAP` は引かれない。
    - Splunk も `coalesce('tags.sysName', …)`（`savedsearches.conf:67`）で EC2 のホスト名を取り、`DEVICE_MAP` には当たらない。
  - Nit にした理由: 038 の差分の外の行なので、このサイクルで直さなくても 038 の変更は壊れない。ただ、038 でこの節へ送る入口が 3 つ増えたので、QUEUE で早めに直すのが望ましい。

## 良かった点

- 設計方針 6 の文面（「syslog / NetFlow / sFlow は送り元が EC2 の IP で届き、別の機器として入る」）をそのまま書かなかった。SG の `only = "egress"` と syslog-ng の HOST の取り方を読み直して、trap は落ちる、syslog は `tags.source` が変わる、sysName が変わるのは RFC3164 の受け口のときだけ、と実装に合う形に直し、根拠の行番号付きで build.md に逸脱として書いた。
- 設計の未確定事項 3 に従い、表のセルは 1 文と参照にとどめ、詳しい壊れ方は `pipeline.md` の `forward-status` の箇条（347 行目）の下に置いた。表が膨らんでいない。
- `.tf` の差分がコメント行だけであることを確かめる grep が本当に効くかを、わざと 1 行混ぜて拾えることで確かめている（build.md の検証 2）。
- 行番号のずれ（106 → 115）、design.md の検証 3 の grep が `stream/telegraf.tf` を拾わない点、037 の `logger` の手順が `pipeline.md` ではなく `troubleshooting.md` にある点を、黙って読み替えずに build.md に書いている。
- `lab/telegraf.tf` で、「trap / syslog」の見出しの下に 2055 / 6343 の受信だけを書くと送信側が抜けて読める点に気づき、「NetFlow / sFlow も同じ形」を足して直した。

## ユーザーへの質問

None

### PM の確認（2026-10-10、fable-5-1）

- Should fix（`docs/pipeline.md:107`、`docs/collection.md:33`、`docs/deploy.md:96` が「試せる」とだけ書く）: 再現した。3 行を読むと「AWS では未確認」が無く、リンク先の `docs/troubleshooting.md:454` と `docs/architecture/resources/lab-ec2.md:44` は「AWS では未確認」と書いている。docs の確からしさ（correctness）なので PM が 3 行の括弧に「AWS では未確認。」を足した。直した後の `grep -n 'AWS では未確認' docs/pipeline.md docs/collection.md docs/deploy.md` に 107 / 33 / 96 行目が出る
- Nit 6 件は直さない。差分の外の 2 件（`troubleshooting.md:452` の `DEVICE_MAP` の誤り、S3〜S5 の書き足りない記述）と差分の中の 4 件（162 の言い方、`deploy.md:96` が `SKIP_LAB` の節、`troubleshooting.md:140` の参照先、`pipeline.md:350` の比べる元）を QUEUE に 2 行で足した
- 全テスト（直した後）: `./ops/check.sh` → fmt 差分なし、validate OK、テスト 15 本 `通過 2330 / 失敗 0`、`すべて通過`（exit 0）
- design.md の検証 2（直した後）: `git diff -U0 df18d07 -- '*.tf' | grep '^[+-][^+-]' | grep -v '^[+-][[:space:]]*#' | wc -l` → `0`
- 2 回目の cold review は呼ばない。Round 1 のあと実装ファイル（`.tf` 3 本）は 1 バイトも変えていない（直したのは docs 3 行と QUEUE だけ）
- 見た観点: design 整合性 / correctness（cold reviewer と同じ）。見ていない観点: AWS での動作（QUEUE の「残った修正をまとめて AWS で動作確認して直す」で見る）

結論: Must fix 0 / Should fix 0（1 件を直した）/ Nit 6（QUEUE に 2 行）。サイクル完了。
