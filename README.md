# Grim Dawn Build Check

Reads your Grim Dawn character saves on your PC and shows what holds the build back, in the order to fix it.

**[Download for Windows](https://github.com/slovi21/grim-dawn-build-check/releases/latest/download/GrimDawnBuildCheck-Setup.exe)** · Windows 10/11, 64-bit. Free and open source. Beta.

Website: <https://slovi21.github.io/grim-dawn-build-check/> · [Example report](https://slovi21.github.io/grim-dawn-build-check/example.html)

No install? [Check a character in your browser](https://slovi21.github.io/grim-dawn-build-check/check.html): your save never leaves your browser. It needs Chrome or Edge 112, Firefox 114 or Safari 16.4 (or newer): WebAssembly and module workers.

## What the report shows

- **Where you stand:** your stage (early, mid, endgame), a score from 0 to 100, the endgame gates that are still open and the next steps, fastest first.
- **Areas:** health, survival, resistances, bosses, damage, skills, devotions and more, each number next to its target.
- **Improvements:** concrete fixes by area, each with what it gains and what it costs, and where to get it.
- **How this is rated:** the targets, terms and data behind every number, one click away from each "Why".

## Install

1. Download and run the installer. Windows may warn because the installer is not signed yet: More info → Run anyway.
2. Start "Grim Dawn Build Check" from the Start menu. The first start builds the game data from your own Grim Dawn installation. This takes a few minutes.
3. Pick a character. The report opens in your browser.

The installer needs no administrator rights. It installs for the current user into `%LOCALAPPDATA%\Programs\Grim Dawn Build Check` and brings its own Python, so nothing else has to be installed.

## Privacy

- Your saves are read on your PC. Nothing is uploaded.
- The browser check reads your save in memory inside the page: it never leaves your browser, and nothing is stored.
- The source code is open, so you can check that.
- Every release lists the SHA256 checksum of the installer in `SHA256SUMS.txt`. To compare it, run `certutil -hashfile GrimDawnBuildCheck-Setup.exe SHA256` in the folder with the download.

## Where your data is

Everything the program creates lives in `%LOCALAPPDATA%\GrimDawnBuildCheck`: the game data built from your installation, your settings and the reports (in the `out` folder).
## Uninstall

Use "Apps > Installed apps" in Windows settings and remove "Grim Dawn Build Check". This also deletes `%LOCALAPPDATA%\GrimDawnBuildCheck`. Copy your reports out of its `out` folder first if you want to keep them.

## Problems and questions

Please open an issue: <https://github.com/slovi21/grim-dawn-build-check/issues>. A report that looks wrong is easiest to check if you attach the character's `player.gdc`.

## License

[MIT](LICENSE).

## Disclaimer

Fan-made tool. Not affiliated with Crate Entertainment. Grim Dawn is a trademark of Crate Entertainment.
