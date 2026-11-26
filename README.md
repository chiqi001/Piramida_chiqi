# SMI Telegram Bot (M5 + M30 + H1, dane z TradingView)

Bot działa na GitHub Actions. Co 5 minut pobiera świece z TradingView (tryb anonimowy, bez konta i bez hasła) i liczy SMI (Length 14, R 5, Smooth K 3, te same wzory co strategia w Pine) na zamkniętych świecach M5, M30 i H1.

| Sygnał | Warunek (jednocześnie na M5, M30, H1) |
|---|---|
| 🟢 LONG | SMI < **-70** |
| 🔴 SHORT | SMI > **75** |

Po wysłaniu sygnału bot nie powtarza go, dopóki SMI na którymkolwiek interwale nie wyjdzie ze strefy o co najmniej `REARM_MARGIN` (domyślnie 5 punktów).

## Instrumenty (domyślne)

| Nazwa w alercie | Symbol TradingView |
|---|---|
| US100 | `OANDA:NAS100USD` |
| DE40 | `OANDA:DE30EUR` |
| XAUUSD | `OANDA:XAUUSD` |
| WTI | `OANDA:WTICOUSD` |
| USDJPY | `OANDA:USDJPY` |
| USDCHF | `OANDA:USDCHF` |

Notowania różnych brokerów różnią się nieco, więc najlepiej ustawić dokładnie te symbole, których używasz na wykresie. Prefiks giełdy widać w oknie wyszukiwania symbolu w TradingView. Ustawia się je zmienną `SYMBOLS` w formacie `NAZWA=GIEŁDA:TICKER`, oddzielając pozycje przecinkami:

```
US100=CAPITALCOM:US100,DE40=CAPITALCOM:DE40,XAUUSD=OANDA:XAUUSD,WTI=TVC:USOIL,USDJPY=FX:USDJPY,USDCHF=FX:USDCHF
```

## Instalacja

1. Utwórz repozytorium na GitHubie, najlepiej **publiczne** (wyjaśnienie niżej). Wrzuć do niego wszystkie pliki, łącznie z folderem `.github/workflows`.
2. **Settings → Secrets and variables → Actions → Secrets** dodaj:
   - `TELEGRAM_TOKEN`: token bota od @BotFather,
   - `TELEGRAM_CHAT_ID`: ID czatu (kilka ID oddziel przecinkami).
3. Opcjonalnie w zakładce **Variables**: `SYMBOLS`, `LONG_LEVEL` (-70), `SHORT_LEVEL` (75), `REARM_MARGIN` (5), `SMI_LENGTH`, `SMI_R`, `SMI_SMOOTH`.
4. **Actions → SMI Telegram Bot → Run workflow**, zaznacz *test*. Dla każdego instrumentu powinna przyjść wiadomość 🧪 TEST z aktualnymi wartościami SMI. Porównaj je ze wskaźnikiem na swoim wykresie.

## Warto wiedzieć

- **Nieoficjalny interfejs.** Dane pochodzą z interfejsu, z którego korzysta sama strona TradingView. Nie jest to oficjalne API, więc TradingView może go zmienić albo blokować serwery GitHuba. Wtedy w logach Actions pojawi się `BŁĄD danych`.
- **Dane bez logowania.** W trybie bez logowania część symboli może być niedostępna lub opóźniona, np. kontrakty futures z CME. Symbole CFD brokerów (OANDA, FX, CAPITALCOM, PEPPERSTONE) zwykle działają w czasie rzeczywistym.
- **Darmowy plan TradingView.** Na tym planie TradingView nie wysyła alertów ze wskaźników ani webhooków, dlatego bot liczy SMI sam.
- **Repozytorium MUSI być publiczne.** Wtedy GitHub Actions na standardowym runnerze jest darmowy i bez limitu minut. W prywatnym darmowe 2000 minut skończy się po około tygodniu. Token i chat ID w sekretach są niewidoczne także w repo publicznym. Nigdy nie wpisuj ich w kod.
- **Wbudowany keepalive.** GitHub wyłącza harmonogram po 60 dniach bez aktywności w repo. Workflow co 40 dni robi pusty commit „keepalive” i ponownie włącza się przez API, więc nic nie trzeba robić ręcznie.
- **Opóźnienia GitHuba.** Harmonogram GitHuba bywa opóźniony o kilka minut.
