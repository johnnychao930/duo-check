"""Apple 台灣 iPhone Duo 監控：取貨（台北 101 / 信義 A13）＋配送最早日期，ntfy 推播。
v4：品名改成「容量 顏色」、通知分組縮短、加入配送（多種接口自動嘗試）。
"""
import json
import os
import re
import sys
import time

import requests

TOPIC = os.environ["NTFY_TOPIC"]
LINE_TOKEN = os.environ.get("LINE_TOKEN", "").strip()
LINE_USER_ID = os.environ.get("LINE_USER_ID", "").strip()
FULL_REPORT = os.environ.get("FULL_REPORT", "false").lower() == "true"
PARTS = [p.strip() for p in os.environ.get("PARTS", "").split(",") if p.strip()]
STORE_KEYWORDS = {"101": "101", "A13": "A13"}
LOCATION = "台北市"
STATE_FILE = "state.json"
CLICK = "https://www.apple.com/tw/shop/buy-iphone/iphone-duo"
NAMES = {
    "MK2D4ZP/A": "256GB 星光白", "MK2E4ZP/A": "256GB 夜空色",
    "MK2F4ZP/A": "512GB 星光白", "MK2G4ZP/A": "512GB 夜空色",
    "MK2H4ZP/A": "1TB 星光白", "MK2J4ZP/A": "1TB 夜空色",
    "MK2K4ZP/A": "2TB 星光白", "MK2L4ZP/A": "2TB 夜空色",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": CLICK,
}
BASE = "https://www.apple.com/tw/shop/"
# 配送接口候選：依序嘗試，第一個能解析出 deliveryMessage 的就用
DELIVERY_CANDIDATES = [
    ("delivery-message", lambda p: ("delivery-message", {"parts.0": p, "little": "false"})),
    ("fulfillment-a", lambda p: ("fulfillment-messages", {"fae": "true", "little": "false", "parts.0": p,
                                                          "mts.0": "regular", "mts.1": "compact", "fts": "true"})),
    ("fulfillment-b", lambda p: ("fulfillment-messages", {"pl": "true", "mts.0": "regular", "parts.0": p,
                                                          "location": LOCATION})),
]


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def get_json(path, params):
    r = requests.get(BASE + path, params=params, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        return r.json(), None
    except Exception:  # noqa: BLE001
        return None, f"非JSON {r.text[:60]!r}"


def fetch_pickup(part):
    data, err = get_json("retail/pickup-message", {"pl": "true", "parts.0": part, "location": LOCATION})
    if data is None:
        return None, err
    stores = find_key(data, "stores")
    if stores is None:
        return None, "找不到 stores " + json.dumps(data, ensure_ascii=False)[:200]
    return stores, None


def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(s or ""))).strip()


def fetch_delivery(part, state):
    """回傳 (buyable, 日期文字) 或 (None, 錯誤說明)。"""
    notes = []
    order = sorted(range(len(DELIVERY_CANDIDATES)), key=lambda i: i != state.get("idx", -1))
    for i in order:
        name, build = DELIVERY_CANDIDATES[i]
        path, params = build(part)
        try:
            data, err = get_json(path, params)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{name}: {e}")
            continue
        if data is None:
            notes.append(f"{name}: {err}")
            continue
        dm = find_key(data, "deliveryMessage")
        if not isinstance(dm, dict) or not dm:
            notes.append(f"{name}: 無 deliveryMessage")
            continue
        d = dm.get(part) or next((v for k, v in dm.items() if k.upper() == part.upper()), None) \
            or next(iter(dm.values()))
        if not isinstance(d, dict):
            notes.append(f"{name}: 格式不符")
            continue
        state["idx"] = i
        raw = json.dumps(d, ensure_ascii=False)
        reg = d.get("regular") if isinstance(d.get("regular"), dict) else d
        ship = ""
        msgs = reg.get("deliveryOptionMessages") or []
        if msgs and isinstance(msgs[0], dict):
            ship = clean(msgs[0].get("displayName"))
        transit = ""
        opts = reg.get("deliveryOptions") or []
        if opts and isinstance(opts[0], dict):
            transit = clean(opts[0].get("date"))
        text = ship + (f"（{transit}）" if transit else "")
        if not text:
            text = clean(d.get("quote") or reg.get("quote"))
        if not text:
            m = re.search(r"\d{1,2}\s*月\s*\d{1,2}\s*日", raw)
            text = m.group(0) if m else ""
        text = re.sub(r"\s+", "", text)
        buyable = bool(d.get("isBuyable") or reg.get("isBuyable")) or bool(re.search(r"\d{1,2}月\d{1,2}日", text))
        return buyable, text
    return None, "；".join(notes)


def notify(title, msg, line_msg=None):
    """同時推播到 ntfy 與 LINE（LINE 沒設定就略過；任一邊失敗不影響另一邊）。"""
    try:
        requests.post("https://ntfy.sh/", json={"topic": TOPIC, "title": title, "message": msg,
                                                "priority": 4, "click": CLICK}, timeout=20)
    except Exception as e:  # noqa: BLE001
        print("ntfy 發送失敗:", e)
    if LINE_TOKEN and LINE_USER_ID:
        try:
            r = requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
                json={"to": LINE_USER_ID,
                      "messages": [{"type": "text", "text": f"{title}\n{line_msg if line_msg is not None else msg}\n{CLICK}"[:4900]}]},
                timeout=20,
            )
            print("LINE:", r.status_code, r.text[:200])
        except Exception as e:  # noqa: BLE001
            print("LINE 發送失敗:", e)


def main():
    if not PARTS:
        sys.exit("沒有料號")
    try:
        old = json.load(open(STATE_FILE))
    except Exception:  # noqa: BLE001
        old = {}
    new, errors = {}, []
    report, changes = [], []   # report: 每個規格一行；changes: 新有貨
    dstate = {"idx": old.get("_delivery_idx", -1)}
    delivery_notes = ""

    for part in PARTS:
        name = NAMES.get(part, part)
        cells = []

        stores, err = fetch_pickup(part)
        time.sleep(1)
        if stores is None:
            errors.append(f"{name} 取貨查詢失敗: {err}")
            new.update({k: v for k, v in old.items() if k.startswith(part + "|")})
            cells.append("取貨 ?")
        else:
            for label, kw in STORE_KEYWORDS.items():
                s = next((x for x in stores if kw in x.get("storeName", "")), None)
                if s is None:
                    cells.append(f"{label} ?")
                    continue
                pa = s.get("partsAvailability") or {}
                a = next((v for k, v in pa.items() if k.upper() == part.upper()), {}) or {}
                ok = a.get("pickupDisplay") == "available"
                key = f"{part}|{s['storeName']}"
                new[key] = ok
                quote = clean(a.get("pickupSearchQuote"))
                cells.append(f"{label} " + ("有貨" + (f"({quote})" if quote else "") if ok else "無貨"))
                if ok and not old.get(key):
                    changes.append(f"{name}｜{s['storeName']} 有貨 {quote}".strip())

        buyable, text = fetch_delivery(part, dstate)
        time.sleep(1)
        key = f"{part}|配送"
        if buyable is None:
            delivery_notes = text
            cells.append("配送 ?")
            if key in old:
                new[key] = old[key]
        else:
            new[key] = buyable
            cells.append("配送 " + (text or "有貨") if buyable else "配送 無貨")
            # 配送有貨不推播（只在手動查詢的完整報告中顯示）
        report.append(f"{name}：" + "｜".join(cells))

    new["_delivery_idx"] = dstate["idx"]

    title = msg = None
    if FULL_REPORT:
        title = "iPhone Duo 目前庫存"
        body = report[:]
        if delivery_notes:
            body += ["", "⚠ 配送查詢失敗：" + delivery_notes[:300]]
        if errors:
            body += [""] + errors
        msg = "\n".join(body)
    elif changes:
        title, msg = "iPhone Duo 有貨了！", "\n".join(changes)

    print("\n".join(report))
    if delivery_notes:
        print("配送查詢失敗:", delivery_notes)
    if msg:
        # LINE 版本不含配送：移除每行的「｜配送…」以及配送查詢失敗的提示
        line_msg = "\n".join(
            re.sub(r"｜配送.*$", "", ln) for ln in msg.split("\n") if not ln.startswith("⚠ 配送")
        ).strip()
        notify(title, msg, line_msg)
    json.dump(new, open(STATE_FILE, "w"), ensure_ascii=False, indent=1)
    if len(errors) == len(PARTS):
        sys.exit("全部取貨查詢失敗：\n" + "\n".join(errors))


if __name__ == "__main__":
    main()
