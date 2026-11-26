# SMI Bot – spółki z WIG → Telegram

Bot działa na GitHub Actions. Co 5 minut w czasie sesji GPW (pon–pt) sprawdza wszystkie polskie spółki z indeksu WIG i liczy SMI (Length 14, R 5, Smooth K 3, wzory jak w strategii Pine) na zamkniętych świecach M5, M30 i H1. Dane pochodzą z TradingView (bez logowania).

| Sygnał | Warunek |
|---|---|
| 🟢 LONG | SMI < **-70** jednocześnie na M5, M30 i H1 |
| 🔴 SHORT | domyślnie wyłączony (zmienna `SHORT_ENABLED=true`, próg `SHORT_LEVEL`) |

Po sygnale ta sama spółka nie da kolejnego, dopóki SMI na którymkolwiek interwale nie wyjdzie ze strefy o co najmniej `REARM_MARGIN` (5 pkt). Jeśli kilka spółek da sygnał naraz, przychodzi jedna zbiorcza wiadomość.

## Spółki

- `wig_isin.csv`: skład WIG po rewizji z 19.06.2026 (321 spółek). Pominięte zostały zagraniczne spółki notowane głównie za granicą: MOL, UniCredit, Santander, ČEZ, Krka, Sopharma, MBWS, Warimpex, CPI Europe.
- `wig_symbols.csv`: te same spółki z symbolami TradingView (`GPW:PKO` itd.). Plik tworzy workflow **Odśwież listę WIG**.
- **Po rewizji indeksu** (marzec, czerwiec, wrzesień, grudzień): zaktualizuj `wig_isin.csv`. Workflow sam odświeży symbole.

## Uruchamianie ręczne

**Actions → SMI Telegram Bot → Run workflow**:
- *test*: krótka wiadomość „bot działa”,
- *replay_day*: `wczoraj` albo data `2026-11-25` wysyła sygnały z tego dnia (test historyczny).

## Ustawienia (Settings → Secrets and variables → Actions)

- **Secrets:** `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.
- **Variables** (opcjonalnie):
  - `LONG_LEVEL` (-70),
  - `SHORT_ENABLED` (false),
  - `SHORT_LEVEL` (75),
  - `REARM_MARGIN` (5),
  - `SYMBOLS`: własna lista zamiast WIG, np. `GPW:PKO,GPW:CDR`.

## Warto wiedzieć

- **Nieoficjalny interfejs.** Dane pobierane są przez nieoficjalne połączenie TradingView. Bez logowania notowania GPW mogą być opóźnione, zwykle o 15 minut.
- **M30 i H1 z M5.** Świece M30 i H1 są składane ze świec M5, więc jedno zapytanie przypada na spółkę. Wartości mogą minimalnie różnić się od wykresu TradingView.
- **Repozytorium publiczne.** Repo musi być publiczne, żeby minuty Actions były darmowe. Keepalive co 40 dni chroni przed automatycznym wyłączeniem harmonogramu.
