# MvC2 Steam Palette Randomizer

Randomizes character palettes in the Steam version of **Marvel vs. Capcom 2** (from the Fighting Collection). Each game launch gets a fresh set of random color schemes across all 56 characters and 6 button slots.

Uses curated skin PNGs from the [MvC2 Skins Gallery](https://github.com/karttoon/mvc2-skins). Includes a web-based gallery viewer for reviewing and curating your skin collection.

## What This Changes

This tool randomizes **character palettes** across all 56 playable characters and 6 button colors. It handles:

- **Body palettes** — the main character sprite color scheme
- **Accessory palettes** — multi-row characters like Sentinel (drones/rockets), Strider (Ouroboros/tiger/eagle), Tron Bonne (Servbot helpers), SonSon (staff/monkey), and others
- **Super trail / afterimage slots** — so supers and dashes match the new colors
- **Animation frame palettes** — intro animations, shine frames, stance changes, power-up effects, counterflash, teleport, and other special move visuals for 20+ characters
- **Shared effect palettes** — fly-mode flames (Sentinel), launcher flames, and similar shared entries

It does **not** modify:

- Projectile/effect colors (fireballs, beams, lightning, etc.)
- Status effect palettes (burn, shock, kinetic charge overlays)
- HUD elements or portraits

## Requirements

- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/) (`pip install Pillow`)
- Steam version of MARVEL vs. CAPCOM Fighting Collection

## Quick Start

```bash
# 1. Clone this repo
git clone https://github.com/karttoon/mvc2-randomizer.git
cd mvc2-randomizer

# 2. Install dependencies
pip install Pillow

# 3. Download the skins gallery
python mvc2_randomizer.py --gallery-download --skins ./skins

# 4. Randomize!
python mvc2_randomizer.py --skins ./skins
```

The game install path is auto-detected from the default Steam location. Use `--game` to override if your install is elsewhere.

## Steam Setup (Auto-Randomize on Launch)

A `launch.bat` script is included that randomizes palettes and then lets Steam launch the game. This makes it so every time you hit Play in Steam, you get fresh random palettes.

### Setup Steps

1. **Edit `launch.bat`** — open it in a text editor and update the `PYTHON` path at the top:

   ```bat
   REM Path to Python (find yours with: where python)
   set PYTHON=C:\Users\YOU\AppData\Local\Programs\Python\Python312\python.exe
   ```

   The `RANDOMIZER` and `SKINS` paths default to the script's own directory, so they work automatically if you launch from the repo folder. To find your Python path, open a terminal and run `where python`.

2. **Set as Steam launch option:**
   - Open Steam and find **MARVEL vs. CAPCOM Fighting Collection** in your library
   - Right-click → **Properties**
   - Under **General**, find **Launch Options**
   - Paste this (adjust the path to where you put `launch.bat`):

   ```
   "C:\path\to\mvc2-randomizer\launch.bat" && %command%
   ```

3. **Launch the game normally from Steam** — palettes will randomize before the game starts.

> **Note:** The `&& %command%` part is required. It tells Steam to launch the actual game after the batch script finishes. Without it, the game won't start.

### Troubleshooting

- **Game doesn't launch:** Make sure `&& %command%` is at the end of the launch option
- **"python is not recognized":** The `PYTHON` path in `launch.bat` is wrong — run `where python` to find the correct path
- **Randomizer errors but game still launches:** The launcher is designed to continue to game launch even if randomization fails — check the console output for error details
- **Want to stop randomizing:** Remove the launch option from Steam, then run `python mvc2_randomizer.py --restore` to go back to default palettes

## CLI Reference

```
python mvc2_randomizer.py [options]
```

| Flag | Description |
|------|-------------|
| `--skins PATH` | Path to skins collection folder |
| `--game PATH` | Game install directory (auto-detected if omitted) |
| `--config PATH` | Path to config JSON (default: `randomizer_config.json`) |
| `--character NAME` | Only randomize a specific character |
| `--seed N` | Fixed random seed for reproducible results |
| `--dry-run` | Show what would be assigned without modifying files |
| `--restore` | Restore `game_50.arc` from backup |
| `--gallery-download` | Download/update skins from the gallery (merges new, keeps existing) |
| `--force-backup` | Recreate backup from current game file |
| `--list-characters` | List all valid character names |

### Examples

```bash
# Randomize all characters
python mvc2_randomizer.py --skins ./skins

# Randomize only Storm
python mvc2_randomizer.py --skins ./skins --character Storm

# Reproducible results with a seed
python mvc2_randomizer.py --skins ./skins --seed 42

# Preview assignments without changing anything
python mvc2_randomizer.py --skins ./skins --dry-run

# Restore original game palettes
python mvc2_randomizer.py --restore

# Download gallery skins (safe to re-run — only adds new ones)
python mvc2_randomizer.py --gallery-download --skins ./skins
```

## Configuration

On first run, config files are auto-generated next to the script:

### randomizer_config.json

Stores paths and global settings. CLI flags override these values.

```json
{
    "skins_path": "C:/path/to/skins",
    "game_path": null,
    "seed": null
}
```

| Key | Type | Description |
|-----|------|-------------|
| `skins_path` | string/null | Path to skins folder. `null` uses `./skins` next to script. |
| `game_path` | string/null | Game install directory. `null` uses default Steam path. |
| `seed` | int/null | Fixed seed for reproducible randomization. `null` = random each run. |

### skin_locks.txt

Controls per-character, per-button skin assignments. Every character and button slot is listed. Set a filename to **lock** that skin to that slot, or leave as `null` to **randomize** it each run.

```
# Each line is: Character_Name BUTTON=filename.png
# Set a filename to lock that skin to that button slot.
# Leave as "null" to randomize that slot each run.

Akuma LP=Akuma_9bc20b73_AccurateMix.png
Akuma LK=null
Akuma HP=Akuma_976f5700_Dragonball.png
Akuma HK=null
Akuma A1=null
Akuma A2=null

Storm LP=null
Storm LK=null
Storm HP=null
Storm HK=null
Storm A1=null
Storm A2=null
```

In this example:
- Akuma's LP always uses the AccurateMix skin, HP always uses the Dragonball skin
- Akuma's other 4 buttons get randomized each run
- All of Storm's buttons get randomized each run

**Filenames are case-insensitive** — `akuma_9bc20b73_accuratemix.png` and `Akuma_9bc20b73_AccurateMix.PNG` both work. The filename must match a file in that character's skins folder.

## Skins Folder Structure

Skins are organized in folders by character name (underscores for spaces, hyphens preserved where canonical). The `--gallery-download` command creates these folders automatically. If adding your own skins, use `--list-characters` to see all valid names, or match an existing folder name.

Two special **combined folders** contain palettes shared between related characters:

- `Iron_Men` — shared between Iron Man and War Machine
- `Wolverines` — shared between both Wolverine variants

These exist alongside the individual character folders (`Iron_Man`, `War_Machine`, `Wolverine`, `Wolverine_Bone_Claw`).

## Gallery Download & Custom Skins

`--gallery-download` fetches the latest skins from the [MvC2 Skins Gallery](https://github.com/karttoon/mvc2-skins) and merges them into your skins folder. It's safe to run repeatedly:

- **New skins** from the gallery are added
- **Existing skins** (including your own custom palettes) are never overwritten
- **Previously rejected skins** (marked for deletion in the gallery reviewer) are skipped

### Adding Your Own Skins

Place indexed PNG sprite sheets in the appropriate character folder. Skin PNGs must be **indexed color** (palette mode) with 16 colors per palette row. The first 16 palette entries are the body colors. Multi-row characters (like Spiral or Sentinel) use additional palette rows for accessories.

## Gallery Reviewer

A built-in web-based tool for reviewing and curating your skin collection. Lets you page through every skin, keep the ones you like, and batch-delete the rest.

```bash
python gallery.py [skins_path] [--port PORT]
```

Opens a browser with a full-screen viewer where you can rate each skin:

- **Y / Up Arrow** — keep the skin
- **N / Down Arrow** — mark for deletion
- **Left / Right Arrow** (or A / D) — navigate between skins
- **Delete All Marked** — batch-delete all skins marked for deletion from disk

The sidebar shows every character with a progress bar so you can see how many skins are reviewed vs. remaining. Click any character to jump to its first unreviewed skin.

Verdicts are saved to `gallery_verdicts.json` and persist across sessions. Future `--gallery-download` runs will skip any skins you previously rejected, so they won't reappear.

## Run Log

After each randomization, `last_run.txt` is saved with every skin assignment. If you spot a palette you like in-game and want to know which skin file it was, check this file. You can then copy the filename into `skin_locks.txt` to lock it in for future runs.

## Backup & Restore

The tool automatically backs up `game_50.arc` before the first modification. The backup is stored next to the game file as `game_50.arc.bak`.

- **`--restore`** copies the backup over the game file, reverting to default palettes
- **`--force-backup`** recreates the backup from the current game file (use after a game reinstall/update)
- The tool detects game reinstalls automatically (if the game file is newer than the backup, it refreshes the backup)

To fully reset without the tool: use Steam's "Verify integrity of game files" to re-download the original `game_50.arc`.

## How It Works

1. Reads and decompresses `game_50.arc` (MT Framework ARC archive with zlib-compressed ROM)
2. For each of 56 characters, checks `skin_locks.txt` — locked slots use the specified skin, unlocked slots get a random skin from the folder
3. Extracts the indexed color palette from each skin PNG (handles single-row and multi-row sprites)
4. Writes palette data to the correct ROM offsets in ARGB4444 format (same as PalMod):
   - Body palette + accessory rows mapped to their per-button slots
   - Super trail / afterimage slots (6-7) filled with the body palette
   - Animation frame extras (intro, shine, stance, counterflash, etc.) derived from body palette with per-frame luminance adjustments where applicable
   - Shared effect palettes (e.g. Sentinel fly-mode flames) composited from the appropriate source palettes
5. Recompresses and writes the modified ARC back

Palette data and character offsets are derived from [PalMod](https://github.com/Preppy/PalMod)'s MVC2 definitions.

## License

MIT
