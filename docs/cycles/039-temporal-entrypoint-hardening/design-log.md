# temporal-server の entrypoint の守りを締める（039）設計の経緯

## Round 1（2026-10-10。差し戻し）

Round 1 の design.md（commit df18d07）は、namespace を作る背景のプロセスのゾンビを ECS の `linuxParameters.initProcessEnabled = true` で回収させる設計だった（設計方針 3）。エンジニアのセルフレビューと反対弁護人（opus / xhigh）が手元の docker（`--init`）で再現し、前提が誤りだった:

- init が回収するのは**孤児**（親が死んだプロセス）だけ。背景のプロセスの親は、`exec` で同じ PID を継いだ temporal-server で、生きている。ゾンビの親は temporal-server のままで、init は拾わない
- init を PID 1 にすると、PID 1 の env はコンテナに渡した env そのもの（`unset` の前）になり、`/proc/1/environ` に master のパスワードが戻る。設計方針 1 を打ち消す

却下した案:

- B: 設計方針 3 を取り下げてゾンビ 1 つを受け入れる。サイクルの題が「守りを締める」で、tini なら数行で消えるので却下
- C: 二重 fork（孫にして親を先に抜けさせ、init に拾わせる）。ゾンビは消えるが PID 1 が init になりパスワードが戻る。却下

採った案: A（イメージに tini を入れ、最後の行を `exec tini -- /etc/temporal/entrypoint.sh` にする。`initProcessEnabled` は入れない）。反対弁護人が scratchpad で試し、ゾンビ無し・どのプロセスの env にもパスワード無しを見ている。PM は `temporalio/server:1.32.1`（Alpine 3.24）で `apk add --no-cache tini` が `tini-0.19.0-r3`（community）で入ることを確かめた（2026-10-10）。

Round 1 のセルフレビューの Should fix 2（ECS Exec のシェルはタスク定義の env を引き継ぐので、`unset` してもそこからは master のパスワードが見える）は、Round 2 の design.md で「何を守るか」の書き方に取り込んだ。Nit 10（fork だけのサブシェルは `/proc/<pid>/environ` に親の最初の env を持ち続ける）も Round 2 で、namespace を別の実行ファイルにして解いた。
