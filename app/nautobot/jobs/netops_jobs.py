"""Nautobot の Job（JOBS_ROOT = /opt/nautobot/jobs）。Nautobot を機器の一覧とトポロジの正にして、gnmic の購読先とグラフ DB（Neptune）に流す。
OSS 版（cycle 005）は同じ Job が Neo4j に書く（app/agentcore/graph.py が GRAPH_BACKEND=neo4j で切り替える）。Job の名前は両方の版で同じ「グラフ DB」で、
説明だけが書き先の名前（nb_sync.GRAPH_NAME）になる。JobHook と bootstrap.py は Job をクラスの場所（netops_jobs.SyncOnChange）で引くので、名前を変えても外れない。
中身は /opt/nautobot/netops/nb_sync.py。Job の行と JobHook は起動時の bootstrap.py が作って有効にする。

同期の最後に、Nautobot の変更履歴（ObjectChange）の新しい 50 件をグラフ DB の頂点 change に写す（エージェントの recent_changes が読む）。

  SyncTopology   画面から手で打つ同期。IP をインタフェースに付け替えただけのような、JobHook が出ない変更のあとに使う
  SyncOnChange   JobHook が呼ぶ。Device / Interface / Cable / IPAddress / Service / Location / Role の作成・変更・削除のたびに同じ同期をする
"""
from nautobot.apps.jobs import BooleanVar, Job, JobHookReceiver, register_jobs

import nb_sync

name = "NetOps"


class SyncTopology(Job):
    redeploy = BooleanVar(default=False, label="gnmic を作り直す",
                          description="機器の一覧が変わっていなくても gnmic のサービスを作り直す（前回の作り直しが失敗したとき）")

    class Meta:
        name = "gnmic とグラフ DB に同期"
        description = f"Service gnmi を持つ機器を gnmic の購読先の一覧に、機器・インタフェース・ケーブルを {nb_sync.GRAPH_NAME} の物理層に合わせる"
        has_sensitive_variables = False

    def run(self, redeploy=False):
        return nb_sync.sync(self.logger, force_redeploy=redeploy)


class SyncOnChange(JobHookReceiver):
    class Meta:
        name = "変更のたびに gnmic とグラフ DB に同期"
        description = f"JobHook（netops-sync）が呼ぶ。中身は「{SyncTopology.Meta.name}」と同じ（グラフ DB は {nb_sync.GRAPH_NAME}）"
        has_sensitive_variables = False

    def receive_job_hook(self, change, action, changed_object):
        self.logger.info("%s（%s）を受けて同期する", change.changed_object_type, action)
        return nb_sync.sync(self.logger)


register_jobs(SyncTopology, SyncOnChange)
