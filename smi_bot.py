#!/usr/bin/env python3
"""
SMI bot dla spółek z indeksu WIG (GPW) -> Telegram.

Liczy Stochastic Momentum Index (wzory jak strategia Pine
"SMI Strategy – surjithctly version") na interwałach z TIMEFRAMES (domyślnie D1)
i wysyła sygnał po zamknięciu świecy, gdy:
  * LONG  – SMI < LONG_LEVEL (domyślnie -70) na wszystkich interwałach
  * SHORT – (domyślnie WYŁĄCZONY) SMI > SHORT_LEVEL

Dane: TradingView (bez logowania). Pobierane są tylko świece M5; M30 i H1
składane są z M5 – jedno zapytanie na spółkę. Liczone są tylko zamknięte świece.

Tryby:
  python smi_bot.py                     # sprawdzenie bieżące (co 5 min z GitHub Actions)
  REPLAY_DAY=2026-11-25 python smi_bot.py   # wyślij sygnały z danego dnia (test)
  REPLAY_DAY=wczoraj   python smi_bot.py    # j.w. – poprzednia sesja
"""

import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import tv_feed


# ---------------------------------------------------------------- konfiguracja

def env_str(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value if value else default


def env_num(name: str, default, cast=float):
    value = os.getenv(name, "").strip()
    return cast(value) if value else default


SMI_LENGTH = env_num("SMI_LENGTH", 14, int)
SMI_R = env_num("SMI_R", 5, int)
SMI_SMOOTH = env_num("SMI_SMOOTH", 3, int)

LONG_LEVEL = env_num("LONG_LEVEL", -70.0)
SHORT_ENABLED = env_str("SHORT_ENABLED", "false").lower() == "true"
SHORT_LEVEL = env_num("SHORT_LEVEL", 75.0)
REARM_MARGIN = env_num("REARM_MARGIN", 5.0)

TZ = env_str("DISPLAY_TZ", "Europe/Warsaw")
SESSION_LAST_MINUTE = env_str("SESSION_LAST_MINUTE", "16:59")  # pusty = brak sesji (24h)
M5_BARS = env_num("M5_BARS", 1500, int)
WORKERS = env_num("WORKERS", 4, int)
MAX_SIGNAL_AGE_MIN = env_num("MAX_SIGNAL_AGE_MIN", 0, int)  # 0 = automatycznie

SEND_TEST = env_str("SEND_TEST", "false").lower() == "true"
REPLAY_DAY = env_str("REPLAY_DAY", "")
STATE_FILE = Path(env_str("STATE_FILE", "state.json"))
SYMBOLS_FILE = Path(env_str("SYMBOLS_FILE", "wig_symbols.csv"))

TF_MINUTES = {"M5": 5, "M30": 30, "H1": 60, "D1": 1440}
TFS = sorted(((tf, TF_MINUTES[tf]) for tf in
              (x.strip().upper() for x in env_str("TIMEFRAMES", "D1").split(",")) if tf),
             key=lambda x: x[1])
BASE_TF = TFS[0][0]                       # najkrótszy interwał = rytm sprawdzania
TF_LABEL = ", ".join(tf for tf, _ in TFS)
SESSION_CLOSE = env_str("SESSION_CLOSE", "17:05")  # koniec sesji GPW (zamknięcie świecy D1)
D1_BARS = env_num("D1_BARS", 300, int)
MIN_BARS = 40


def load_symbols() -> list:
    raw = env_str("SYMBOLS", "")
    if raw:
        out = []
        for item in raw.replace("\n", ",").split(","):
            item = item.strip()
            if item:
                name, _, tv = item.partition("=")
                out.append((name.strip(), tv.strip()) if tv else (item.split(":")[-1], item))
        return out
    with open(SYMBOLS_FILE, encoding="utf-8") as f:
        return [(f'{r["nazwa"]} ({r["tv"].split(":")[-1]})', r["tv"])
                for r in csv.DictReader(f, delimiter=";") if r.get("tv")]


# ---------------------------------------------------------------- wskaźnik

def pine_ema(series: pd.Series, length: int) -> pd.Series:
    """EMA jak ta.ema w Pine: start od SMA z pierwszych `length` wartości."""
    vals = series.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    alpha = 2.0 / (length + 1)
    prev, run = np.nan, 0
    for i, v in enumerate(vals):
        if np.isnan(v):
            run = 0
            if not np.isnan(prev):
                out[i] = prev
            continue
        run += 1
        if np.isnan(prev):
            if run >= length:
                prev = vals[i - length + 1: i + 1].mean()
                out[i] = prev
        else:
            prev = alpha * v + (1 - alpha) * prev
            out[i] = prev
    return pd.Series(out, index=series.index)


def compute_smi(df: pd.DataFrame) -> pd.Series:
    hh = df["High"].rolling(SMI_LENGTH).max()
    ll = df["Low"].rolling(SMI_LENGTH).min()
    mom = df["Close"] - (hh + ll) / 2
    dmom = hh - ll
    smom = pine_ema(pine_ema(mom, SMI_R), SMI_R)
    sdmom = pine_ema(pine_ema(dmom, SMI_R), SMI_R)
    raw = pd.Series(np.where(sdmom != 0, 100 * (smom / (sdmom / 2)), 0.0), index=df.index)
    raw[sdmom.isna()] = np.nan
    return raw.rolling(SMI_SMOOTH).mean()


# ---------------------------------------------------------------- dane

def resample(df5: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Składa świece M30/H1 z M5 (czas lokalny, wyrównanie do pełnych godzin)."""
    loc = pd.Series(df5.index.tz_convert(TZ), index=df5.index)
    if SESSION_LAST_MINUTE:
        hh, mm = map(int, SESSION_LAST_MINUTE.split(":"))
        cap = loc.dt.normalize() + pd.Timedelta(hours=hh, minutes=mm)
        loc = loc.where(loc <= cap, cap)  # dogrywka po sesji -> ostatnia świeca sesji
    bucket = loc.dt.floor(f"{minutes}min", ambiguous=True, nonexistent="shift_forward")
    g = df5.groupby(bucket)
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                        "Low": g["Low"].min(), "Close": g["Close"].last()})
    out.index = pd.DatetimeIndex(out.index).tz_convert("UTC")
    return out


def drop_open_bar(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    if len(df) and df.index[-1] + pd.Timedelta(minutes=minutes) > now:
        return df.iloc[:-1]
    return df


def fetch_tv(tv_symbol: str, interval: str, n_bars: int) -> pd.DataFrame:
    last = None
    for attempt in range(5):
        try:
            df = tv_feed.get_bars(tv_symbol, interval, n_bars=n_bars, timeout=40)
            return df.dropna(subset=["High", "Low", "Close"])
        except Exception as e:  # noqa: BLE001
            last = e
            if "symbol_error" in str(e):
                break
            # 429 = TradingView ogranicza liczbę połączeń – odczekaj dłużej
            time.sleep((10 if "429" in str(e) else 2) * (attempt + 1))
    msg = str(last)
    raise RuntimeError("429 Too Many Requests" if "429" in msg else msg[:200])


def daily_closed(df: pd.DataFrame) -> pd.DataFrame:
    """Świece D1: indeks = czas zamknięcia sesji (data lokalna + SESSION_CLOSE); bez bieżącej sesji."""
    hh, mm = map(int, SESSION_CLOSE.split(":"))
    days = df.index.tz_convert(TZ).normalize()
    close = (days + pd.Timedelta(hours=hh, minutes=mm)).tz_convert("UTC")
    out = df.copy()
    out["open_time"] = days.tz_convert("UTC")
    out.index = close
    return out[out.index <= pd.Timestamp.now(tz="UTC")]


def history_table(tv_symbol: str) -> pd.DataFrame:
    """Dla każdej zamkniętej świecy najkrótszego interwału: SMI z niej i z ostatnich
    ZAMKNIĘTYCH świec dłuższych interwałów."""
    df5 = None
    base = None
    for name, minutes in TFS:
        if name == "D1":
            df = daily_closed(fetch_tv(tv_symbol, "1D", D1_BARS))
            open_times = df.pop("open_time")
        else:
            if df5 is None:
                df5 = fetch_tv(tv_symbol, "5", M5_BARS)
            df = df5 if minutes == 5 else resample(df5, minutes)
            df = drop_open_bar(df, minutes)
            open_times = pd.Series(df.index, index=df.index)
            df.index = df.index + pd.Timedelta(minutes=minutes)  # czas zamknięcia
            open_times.index = df.index
        if len(df) < MIN_BARS:
            raise RuntimeError(f"za mało świec {name}: {len(df)}")
        part = pd.DataFrame({name: compute_smi(df), f"{name}_close": df["Close"],
                             f"{name}_open": open_times})
        part = part.dropna().reset_index(names="t").sort_values("t")
        part["t"] = part["t"].astype("datetime64[ns, UTC]")
        part[f"{name}_open"] = pd.to_datetime(part[f"{name}_open"], utc=True).astype("datetime64[ns, UTC]")
        base = part if base is None else pd.merge_asof(base, part, on="t", direction="backward")
    return base.dropna().reset_index(drop=True)


# ---------------------------------------------------------------- sygnały

def step(armed: dict, smis: list) -> list:
    """Aktualizuje stan uzbrojenia, zwraca listę nowych sygnałów ('LONG'/'SHORT')."""
    out = []
    if all(x < LONG_LEVEL for x in smis) and armed.get("long", True):
        out.append("LONG")
    elif not armed.get("long", True) and any(x > LONG_LEVEL + REARM_MARGIN for x in smis):
        armed["long"] = True
    if SHORT_ENABLED:
        if all(x > SHORT_LEVEL for x in smis) and armed.get("short", True):
            out.append("SHORT")
        elif not armed.get("short", True) and any(x < SHORT_LEVEL - REARM_MARGIN for x in smis):
            armed["short"] = True
    return out


def row_smis(row) -> list:
    return [getattr(row, tf) for tf, _ in TFS]


def row_info(name: str, row) -> dict:
    return {"name": name, "smi": {tf: getattr(row, tf) for tf, _ in TFS},
            "price": getattr(row, f"{BASE_TF}_close"), "time": getattr(row, f"{BASE_TF}_open")}


# ---------------------------------------------------------------- telegram

def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_ids = [c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    print("-" * 40 + "\n" + text)
    if not token or not chat_ids:
        print("[DRY RUN – brak TELEGRAM_TOKEN / TELEGRAM_CHAT_ID]")
        return False
    for chat_id in chat_ids:
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                             data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"})
        if resp.status_code != 200:
            raise RuntimeError(f"Telegram {resp.status_code}: {resp.text[:200]}")
    return True


def fmt_price(p: float) -> str:
    return f"{p:,.2f}".replace(",", " ") if p >= 10 else f"{p:.3f}"


def fmt_line(s: dict) -> str:
    t = s["time"].tz_convert(TZ).strftime("%d.%m.%Y" if BASE_TF == "D1" else "%d.%m %H:%M")
    smi = " · ".join(f"{tf} {v:.1f}" for tf, v in s["smi"].items())
    return f"{s['name']} | {fmt_price(s['price'])} zł | {t}\n   SMI {smi}"


def header(kind: str) -> str:
    if kind == "LONG":
        return f"🟢 LONG – SMI < {LONG_LEVEL:g} ({TF_LABEL})"
    return f"🔴 SHORT – SMI > {SHORT_LEVEL:g} ({TF_LABEL})"


def send_chunks(title: str, lines: list) -> bool:
    """Telegram ma limit 4096 znaków – dzieli długie listy na kilka wiadomości."""
    ok, chunk = True, title
    for line in lines:
        if len(chunk) + len(line) + 2 > 3800:
            ok &= send_telegram(chunk)
            chunk = title + " (cd.)"
        chunk += "\n\n" + line
    ok &= send_telegram(chunk)
    return ok


# ---------------------------------------------------------------- stan

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("Uszkodzony state.json – zaczynam od nowa")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- tryby

def load_all(symbols: list) -> dict:
    def job(item):
        name, tv = item
        try:
            return name, history_table(tv), None
        except Exception as e:  # noqa: BLE001
            return name, None, e

    tables, errors = {}, []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for name, table, err in ex.map(job, symbols):
            if err is not None:
                errors.append(f"{name}: {err}")
            else:
                tables[name] = table
    print(f"Pobrano {len(tables)}/{len(symbols)} spółek w {time.time() - t0:.0f} s")
    if tables:
        newest = max(t["t"].iloc[-1] for t in tables.values() if not t.empty)
        lag = (pd.Timestamp.now(tz="UTC") - newest).total_seconds() / 60
        print(f"Najnowsza zamknięta świeca {BASE_TF}: {newest.tz_convert(TZ):%Y-%m-%d %H:%M} "
              f"(opóźnienie danych ok. {lag:.0f} min)")
    for e in errors[:30]:
        print("  BŁĄD", e)
    if len(errors) > 30:
        print(f"  ... i {len(errors) - 30} innych błędów")
    return tables


def run_live(symbols: list) -> int:
    tables = load_all(symbols)
    if not tables:
        return 1
    state = load_state()
    before = json.dumps(state, sort_keys=True)
    now = pd.Timestamp.now(tz="UTC")
    max_age = MAX_SIGNAL_AGE_MIN or (23 * 60 if BASE_TF == "D1" else 30)
    new = {"LONG": [], "SHORT": []}

    for name, table in tables.items():
        if table.empty:
            continue
        row = table.iloc[-1]
        if name not in state:  # pierwszy raz: odtwórz stan z historii, bez starych sygnałów
            init = {}
            for r in table.iloc[:-1].itertuples(index=False):
                for k in step(init, row_smis(r)):
                    init["long" if k == "LONG" else "short"] = False
            state[name] = init
        armed = state[name]
        fresh = (now - row["t"]) <= pd.Timedelta(minutes=max_age)
        for kind in step(armed, [row[tf] for tf, _ in TFS]):
            if fresh:
                new[kind].append((name, row_info(name, row)))

    for kind, items in new.items():
        if not items:
            continue
        try:
            sent = send_chunks(header(kind), [fmt_line(s) for _, s in items])
        except Exception as e:  # noqa: BLE001
            print(f"BŁĄD wysyłki: {e}")
            sent = False
        if sent:  # oznacz jako wysłane tylko po udanej wysyłce
            for name, s in items:
                st = state[name]
                st["long" if kind == "LONG" else "short"] = False
                st[f"last_{kind.lower()}"] = s["time"].tz_convert(TZ).strftime("%Y-%m-%d %H:%M")
                st["tf"] = TF_LABEL
    print(f"Nowe sygnały: LONG {len(new['LONG'])}, SHORT {len(new['SHORT'])}")

    if json.dumps(state, sort_keys=True) != before:
        save_state(state)
    return 0


def run_replay(symbols: list, day_text: str) -> int:
    tables = load_all(symbols)
    if not tables:
        return 1
    if day_text.lower() in ("wczoraj", "yesterday", "ostatnia"):
        today = pd.Timestamp.now(tz=TZ).normalize()
        days = sorted({d for t in tables.values()
                       for d in t[f"{BASE_TF}_open"].dt.tz_convert(TZ).dt.normalize() if d < today})
        day = days[-1] if days else today - pd.Timedelta(days=1)
    else:
        day = pd.Timestamp(day_text).tz_localize(TZ)
    print(f"Dzień testowy: {day.date()}")

    events = {"LONG": [], "SHORT": []}
    for name, table in tables.items():
        armed = {}
        for row in table.itertuples(index=False):
            kinds = step(armed, row_smis(row))
            for kind in kinds:
                armed["long" if kind == "LONG" else "short"] = False
                if getattr(row, f"{BASE_TF}_open").tz_convert(TZ).normalize() == day:
                    events[kind].append(row_info(name, row))

    total = sum(len(v) for v in events.values())
    print(f"Sygnałów w dniu {day.date()}: {total}")
    title_prefix = f"📜 TEST – sygnały z {day.strftime('%d.%m.%Y')} (historyczne, nie aktualne)\n"
    if total == 0:
        send_telegram(title_prefix + f"Brak sygnałów (SMI < {LONG_LEVEL:g}, {TF_LABEL}) "
                                     f"wśród {len(tables)} spółek.")
        return 0
    try:
        for kind, items in events.items():
            if items:
                items.sort(key=lambda s: s["time"])
                send_chunks(title_prefix + header(kind) + f" – {len(items)} sygn.",
                            [fmt_line(s) for s in items])
    except Exception as e:  # noqa: BLE001
        print(f"BŁĄD wysyłki: {e}")
        return 1
    return 0


def main() -> int:
    symbols = load_symbols()
    print(f"Spółek: {len(symbols)} | interwały: {TF_LABEL} | LONG < {LONG_LEVEL:g}"
          + (f" | SHORT > {SHORT_LEVEL:g}" if SHORT_ENABLED else " | SHORT wyłączony"))
    if REPLAY_DAY:
        return run_replay(symbols, REPLAY_DAY)
    if SEND_TEST:
        ok = send_telegram(f"🧪 TEST – bot SMI działa. Monitoruję {len(symbols)} spółek z WIG "
                           f"(sygnał: SMI < {LONG_LEVEL:g} na {TF_LABEL}, po zamknięciu świecy).")
        return 0 if ok else 1
    return run_live(symbols)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"BŁĄD: {e}")
        sys.exit(1)
