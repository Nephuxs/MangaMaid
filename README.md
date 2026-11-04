# MangaMaid (Portable) — All-in-One Manga / Doujin Downloader & CBZ Packager

![MangaMaid (Portable) Screenshot](assets/preview.jpg)

**MangaMaid (Portable)** (`Beta v0.5.0`) is a standalone, 100% self-contained **Windows** and **macOS** desktop application for downloading manga chapters, webtoons, doujinshi, and galleries from 280+ supported sites and automatically packaging them into clean `.cbz` archives.

---

## Features

* **Automatic `.cbz` Packaging & Naming**: Downloads chapters or full galleries and packs each directly into `.cbz` archives (`{manga}/{volume:?v/ />02}c{chapter:>03}{chapter_minor:?//}{title:? - //}.cbz`), automatically cleaning up loose image files when each archive completes.
* **All-Inclusive Manga, Webtoon, Doujin & Hentai Support (280+ Sites)**:
  * **Manga & Webtoons**: Built-in support for **MangaDex**, **MangaFire**, **comix.to**, **WeebCentral**, **MangaPark**, **MangaSee**, **MangaReader**, **Dynasty Scans**, **Webtoons**, **TCB Scans**, and more. Automatically resolves `comix.to` URLs, MangaDex links missing English chapters, or plain-text manga titles directly to working sources (defaulting to English chapters first).
  * **Doujin & Hentai Galleries**: Dedicated multi-threaded support for **NHentai** (`nhentai.net`, `nhentai.xxx`, and direct 6-digit codes), **HentaiVox**, **HentaiPaw**, **RokuHentai**, **IMHentai**, **HentaiEra**, **HentaiFox**, **HentaiEnvy**, **HentaiRox**, **HentaiZap**, **AsmHentai**, **3Hentai**, **E-Hentai / ExHentai**, **Hitomi.la**, **Hentai2Read**, **HentaiHere**, **Pixiv**, and more.
  * Includes a searchable **Supported Sites** popup menu inside the app organized by category.
* **Interactive Manga Maid UI (16:9 Widescreen `1280×720`)**:
  * Click the **Manga Maid Mascot** (or the **"Touch Me to Get Started!"** button) to begin downloading.
  * Real-time monotonic **Overall Job Progress Bar** (`0%` → `100%`) tracking total chapters/galleries, packed `.cbz` archives, and downloaded pages.
  * Built-in **Error & Failure Popup Dialog** with technical log capture if a link is unsupported or unavailable.
* **Maid Cafe Chiptune BGM & Custom Voice SFX**:
  * Includes an original looping **Maid Cafe Pixel / Chiptune BGM** track plus custom voice/sound effects for button presses and task completion.
  * Independent **BGM: ON/OFF** and **SFX: ON/OFF** toggle switches in the top-left corner.
* **100% Portable (Windows & macOS)**:
  * Packaged as a standalone Windows executable (`MangaMaid (Portable).exe`) and a Universal macOS application bundle (`MangaMaid (Portable).app` inside `MangaMaid (Portable) - macOS.zip` for both Apple Silicon M1/M2/M3/M4 and Intel Macs)—no Python installation or external dependencies required.
  * Automatically defaults to the current user's `Downloads` folder on any computer.

---

## Download & Run (No Installation Needed)

### Windows
1. Download **`MangaMaid (Portable).exe`** from the [**Latest Release**](../../releases/latest).
2. Double-click **`MangaMaid (Portable).exe`** to launch.
3. Paste any manga/doujin URL, manga title, or 6-digit NHentai code into **MANGA URL**, optionally choose a **SAVE FOLDER**, and click the **Manga Maid**!

### macOS (Apple Silicon & Intel)
1. Download **`MangaMaid (Portable) - macOS.zip`** from the [**Latest Release**](../../releases/latest).
2. Double-click **`MangaMaid (Portable) - macOS.zip`** to extract **`MangaMaid (Portable).app`**, then launch it directly or drag it into your `Applications` folder.
   * *Note for macOS Gatekeeper on first launch*: Because this is a portable open-source build without an Apple Developer certificate, if macOS displays an unverified developer prompt on first launch, **Right-Click (or Control-Click)** `MangaMaid (Portable).app` and select **Open**, or run `xattr -cr "MangaMaid (Portable).app"` in Terminal.
