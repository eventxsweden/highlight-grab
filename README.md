# Highlight Grab

Highlight Grab är ett tangentbordsdrivet skrivbordsverktyg för Windows för att granska videofiler och markera in/ut-punkter för att extrahera de bästa segmenten i originalkvalitet via FFmpeg stream copy.

## Krav

- Python 3.10+
- VLC installerat ([videolan.org/vlc](https://www.videolan.org/vlc/))
- FFmpeg i PATH eller i samma mapp som `highlight_grab.py` ([ffmpeg.org](https://ffmpeg.org/download.html))

## Installation

```bash
pip install -r requirements.txt
```

## Starta

```bash
python highlight_grab.py
```

Eller dubbelklicka på `HighlightGrab.bat`.

## Tangentbordsgenvägar

| Tangent         | Funktion                        |
|----------------|---------------------------------|
| `Space`        | Spela / Pausa                   |
| `I`            | Sätt in-punkt                   |
| `O`            | Sätt ut-punkt                   |
| `M` / `Enter`  | Spara segment                   |
| `E`            | Exportera alla segment          |
| `→`            | +5 sekunder                     |
| `←`            | −5 sekunder                     |
| `Shift+→`      | +1 sekund                       |
| `Shift+←`      | −1 sekund                       |
| `N`            | Nästa fil                       |
| `P`            | Föregående fil                  |
| `Delete`       | Ta bort senaste segment         |
| `L`            | Öka uppspelningshastighet       |
| `J`            | Minska uppspelningshastighet    |
| `?`            | Visa tangentbordshjälp          |

## Export

Export sker via FFmpeg stream copy — ingen omkodning. Det betyder:

- **Originalqualitet bevaras**
- **Blixtsnabb export** (kopierar bara bytes)
- Utdatafiler sparas i `highlight_grab_export/` bredvid källfilen
- Filnamnsformat: `[originalnamn]_[index]_[in]s-[out]s.mp4`

## Stödda format

`.mp4` `.mov` `.avi` `.mkv` `.mts` `.m4v`
