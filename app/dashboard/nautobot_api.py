"""「トポロジ」タブの編集の書き先（Nautobot があるとき）。Nautobot の REST API にケーブルを作る・消す。

Nautobot が機器と回線の正なので、Web からは Nautobot にだけ書く。Neptune の物理層と Telegraf の機器の一覧には、
Nautobot の JobHook（netops-sync）が呼ぶ Job（app/nautobot/jobs/netops_jobs.py）が反映する（数秒〜十数秒あと）。
URL は SSM の <PARAM_PREFIX>/nautobot/url（IaC/terraform/aws-managed/pipeline/nautobot）、API トークンは同じく nautobot/api-token
（ops/up.sh が作る SecureString。app/nautobot/netops/bootstrap.py が同じ値でユーザー netops-web のトークンを作る）。
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import toolkit

URL = toolkit.Param("NAUTOBOT_URL", "nautobot/url")
TOKEN = toolkit.Param("NAUTOBOT_API_TOKEN", "nautobot/api-token", decrypt=True)
TIMEOUT = 15
SYNC_NOTE = "Nautobot の Job が Neptune に反映するまで数秒〜十数秒かかる（「再読み込み」で確かめる）"


class NautobotError(Exception):
    """Nautobot に書けなかった理由（画面にそのまま出す文）"""


def configured() -> bool:
    return bool(URL.value())


def _call(method: str, path: str, query: dict | None = None, body: dict | None = None):
    token = TOKEN.value()
    if not token:
        raise NautobotError("Nautobot の API トークン（SSM の nautobot/api-token）が読めない。ops/up.sh を打ち直す")
    url = f"{URL.value().rstrip('/')}/api/{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Token {token}", "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - URL は SSM のパラメータ（VPC の中の Nautobot）
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise NautobotError(f"Nautobot が {e.code} を返した: {e.read().decode(errors='replace')[:300]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NautobotError(f"Nautobot に届かない: {str(e)[:200]}") from e
    return json.loads(raw) if raw else {}


def _interface(device: str, name: str, depth: int = 0) -> dict | None:
    """機器 device のインタフェース name。無ければ None（機器が無ければ Nautobot が 400 を返す）"""
    rows = _call("GET", "dcim/interfaces/", {"device": device, "name": name, "depth": depth}).get("results") or []
    return rows[0] if rows else None


def _interface_type(name: str) -> str:
    """app/nautobot/netops/nb_map.py の interface_type と同じ（SR Linux の ethernet-* は 25G、それ以外は 1G）"""
    return "25gbase-x-sfp28" if name.startswith("ethernet-") else "1000base-t"


def _ensure_interface(device: str, name: str) -> tuple[dict, bool]:
    """(インタフェース, 新しく作ったか)。無ければ作る"""
    found = _interface(device, name)
    if found:
        return found, False
    return _call("POST", "dcim/interfaces/", body={"device": {"name": device}, "name": name, "type": _interface_type(name), "status": "Active"}), True


def add_link(a: str, a_if: str, b: str, b_if: str, role: str = "", bandwidth_mbps: int | None = None) -> dict:
    """a の a_if と b の b_if のあいだにケーブルを作る。インタフェースが無ければ作る。種別（fabric / lag / l2）は Nautobot の Job が両端の役割と LAG から決める"""
    ends, created = [], []
    for device, name in ((a, a_if), (b, b_if)):
        interface, new = _ensure_interface(device, name)
        if interface.get("cable"):
            raise NautobotError(f"{device} の {name} にはもうケーブルがある（先にそのリンクを削除する）")
        ends.append(interface["id"])
        if new:
            created.append(f"{device}:{name}")
    fields = {k: v for k, v in (("link_role", role), ("bandwidth_mbps", bandwidth_mbps)) if v}
    cable = _call("POST", "dcim/cables/", body={
        "termination_a_type": "dcim.interface", "termination_a_id": ends[0],
        "termination_b_type": "dcim.interface", "termination_b_id": ends[1],
        "status": "Connected", "custom_fields": fields})
    out = {"Nautobot に追加": f"{a}:{a_if} - {b}:{b_if}", "cable": cable.get("id", "")}
    if created:
        out["作ったインタフェース"] = " / ".join(created)
    return out


def remove_link(a: str, a_if: str, b: str) -> dict:
    """a の a_if から b へのケーブルを消す（インタフェースは残す）"""
    interface = _interface(a, a_if, depth=2)
    if not interface or not interface.get("cable"):
        raise NautobotError(f"Nautobot の {a} の {a_if} にケーブルが無い（もう消えているなら「再読み込み」）")
    peer = ((interface.get("cable_peer") or {}).get("device") or {}).get("name")
    if peer != b:
        raise NautobotError(f"Nautobot では {a} の {a_if} の先は {peer or '不明'}（{b} ではない）。「再読み込み」してから選び直す")
    _call("DELETE", f"dcim/cables/{interface['cable']['id']}/")
    return {"Nautobot から削除": f"{a}:{a_if} - {b}"}
