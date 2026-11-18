"""python -m publisher.robot doctor —— 现场自检：
1) 当前微信界面的 surface 识别结果与快照摘要
2) 能力清单（给 planner 看）
3) surface 闸门是否真的挡住错误操作
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    from . import OPS, describe, snapshot_desktop, detect_desktop
    from publisher.agent.desktop import find_sns_window, find_window

    out = {"ops": describe(), "surfaces": []}
    win = find_window()
    if win is not None:
        snap = snapshot_desktop(win)
        out["surfaces"].append({"window": "wechat", "surface": snap.surface,
                                "count": snap.count, "digest": snap.digest,
                                "anchors": {k: (v if not isinstance(v, list) else v[:3])
                                            for k, v in snap.anchors.items()}})
    sns = find_sns_window()
    if sns is not None:
        snap = snapshot_desktop(sns)
        out["surfaces"].append({"window": "moments", "surface": snap.surface,
                                "count": snap.count, "digest": snap.digest})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
