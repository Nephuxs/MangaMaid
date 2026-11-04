# MangaMaid (Portable) — All-in-One Manga Downloader & CBZ Packager

![MangaMaid (Portable) Screenshot](assets/preview.jpg)

**MangaMaid (Portable)** (`Beta v0.4.0`) is a standalone, 100% self-contained Windows desktop application for downloading English manga chapters from 270+ supported manga, webtoon, and comic sites and automatically packaging them into clean `.cbz` archives.

---

## Features

* **Automatic `.cbz` Packaging & Naming**: Downloads English chapters and packs each chapter directly into `.cbz` archives (`{manga}/{volume:?v/ />02}c{chapter:>03}{chapter_minor:?//}{title:? - //}.cbz`), automatically cleaning up loose image files when each chapter completes.
* **270+ Supported Sites + Smart Cross-Site Resolution**:
  * Built-in support for **MangaDex**, **MangaFire**, **comix.to**, **WeebCentral**, **MangaPark**, **MangaSee**, **MangaReader**, **Dynasty Scans**, **Webtoons**, **TCB Scans**, and 270+ extractors powered by `gallery-dl`.
  * Automatically resolves `comix.to` URLs, MangaDex links missing English chapters, or plain-text manga titles directly to working sources.
  * Includes a searchable **Supported Sites** popup menu inside the app.
* **Interactive Manga Maid UI (16:9 Widescreen `1280×720`)**:
  * Click the **Manga Maid Mascot** (or the **"Touch Me to Get Started!"** button) to begin downloading.
  * Real-time monotonic **Overall Job Progress Bar** (`0%` → `100%`) tracking total chapters, packed `.cbz` archives, and downloaded pages.
  * Built-in **Error & Failure Popup Dialog** with technical log capture if a link is unsupported or unavailable.
* **Maid Cafe Chiptune BGM & Custom Voice SFX**:
  * Includes an original looping **Maid Cafe Pixel / Chiptune BGM** track plus custom voice/sound effects for button presses and task completion.
  * Independent **BGM: ON/OFF** and **SFX: ON/OFF** toggle switches in the top-left corner.
* **100% Portable**:
  * Packaged as a single standalone `MangaMaid (Portable).exe`—no Python installation or external dependencies required.
  * Automatically defaults to the current Windows user's `Downloads` folder on any computer.

---

## Download & Run (No Installation Needed)

1. Download **`MangaMaid (Portable).exe`** from the [**Latest Release**](../../releases/latest).
2. Double-click **`MangaMaid (Portable).exe`** to launch.
3. Paste any manga URL (or manga title) into **MANGA URL**, optionally choose a **SAVE FOLDER**, and click the **Manga Maid**!
