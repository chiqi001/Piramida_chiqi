#!/usr/bin/env python3
"""
Zamienia listę spółek z WIG (wig_isin.csv: ISIN;nazwa) na symbole TradingView
(wig_symbols.csv: nazwa;isin;tv). Uruchamiane z workflow "Odśwież listę WIG".
Wyszukiwarka symboli TradingView przyjmuje kod ISIN.
"""
import csv
import re
import sys
import time

import requests

URL = "https://symbol-search.tradingview.com/symbol_search/v3/"
HEADERS = {
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
}


def clean(s) -> str:
    return re.sub(r"</?em>", "", str(s or "")).strip()


def search(text: str) -> list:
    for attempt in range(3):
        try:
            r = requests.get(URL, headers=HEADERS, timeout=20, params={
                "text": text, "hl": "0", "exchange": "GPW", "lang": "pl",
                "search_type": "stock", "domain": "production",
            })
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            return data.get("symbols", []) if isinstance(data, dict) else data
        except Exception as e:  # noqa: BLE001
            print(f"  błąd wyszukiwania {text}: {e}")
            time.sleep(2)
    return []


def pick(cands: list, isin: str):
    gpw = [c for c in cands if clean(c.get("exchange")).upper() == "GPW"
           or clean(c.get("source_id")).upper() == "GPW"]
    exact = [c for c in gpw if clean(c.get("isin")).upper() == isin.upper()]
    return (exact or gpw or [None])[0]


def main() -> int:
    rows = list(csv.DictReader(open("wig_isin.csv", encoding="utf-8"), delimiter=";"))
    out, missing = [], []
    for i, row in enumerate(rows, 1):
        isin, name = row["isin"], row["nazwa"]
        c = pick(search(isin), isin) or pick(search(name), isin)
        if c:
            tv = f"GPW:{clean(c.get('symbol'))}"
            out.append((name, isin, tv))
            print(f"{i:3} {name:12} {isin} -> {tv}  ({clean(c.get('description'))})")
        else:
            missing.append(name)
            print(f"{i:3} {name:12} {isin} -> NIE ZNALEZIONO")
        time.sleep(0.3)

    if len(out) < len(rows) * 0.8:
        print(f"Za mało dopasowań ({len(out)}/{len(rows)}) – nie zapisuję pliku.")
        return 1
    with open("wig_symbols.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["nazwa", "isin", "tv"])
        w.writerows(out)
    print(f"\nZapisano {len(out)}/{len(rows)} spółek. Nie znaleziono: {', '.join(missing) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
