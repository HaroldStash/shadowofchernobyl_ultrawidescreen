# S.T.A.L.K.E.R.: Shadow of Chernobyl (1.0006) — Ultrawide UI & Borderless Fullscreen Patcher

Engine-level ultrawide / super-ultrawide (`21:9`, `32:9` / `5120×1440`, etc.) UI fix and native **Borderless Fullscreen** patcher for **S.T.A.L.K.E.R.: Shadow of Chernobyl** (Steam `1.0006`, `xrCore` build `3312`).

Everything is implemented via clean binary hooks in `bin\xrGame.dll` and `bin\XR_3DA.exe` — **zero `gamedata` edits**, so it is **100% compatible with ZRP (Zone Reclamation Project)** and other gameplay/UI mods.

---

## Why the Vanilla Game Breaks on Ultrawide (and Why Hex FOV Patchers Don't Help)

The 3D renderer in X-Ray 1.0006 is already **Hor+** (`CCameraManager::ApplyDevice` fixes vertical FOV and computes `mProject._11 = fASPECT * _22`), so 3D geometry renders natively at `32:9` without stretching.

The problem is the 2D UI coordinate system (`ui_core`), which stretches its `1024×768` virtual canvas across the entire physical width of the display:

- At `5120×1440` (`32:9`), `m_scale_.x = 5.0` while `m_scale_.y = 1.875` — a **2.67× horizontal stretch** (even vanilla `*_16.xml` widescreen layouts stretch **2.0×**).
- HUD elements get slammed to the far left/right periphery, fonts and borders distort, mouse cursor horizontal speed doubles, crosshairs deform, and sniper scopes turn into wide ovals.
- Simply editing `gamedata\config\ui\*.xml` files cannot fix mouse cursor scaling, scissor clipping, frame thickness, or ZRP conflicts.

---

## What This Patcher Does

Zero-dependency Python 3 tool (`soc_ultrawide_patcher.py`) with a built-in 32-bit x86 assembler, PE section builder, automatic `.reloc` neutralization, automatic `*.orig.bak` backup management, and an optional tkinter GUI (`--gui`).

### 1. Centered 16:9 UI Pipeline (`bin\xrGame.dll` → `.uwui` section, 31 callsites)

On any resolution wider than the target UI aspect (default `16:9`), `ui_core` dynamically centers all 2D UI inside a crisp `16:9` canvas (`2560×1440` at `x = [1280..3840]` on `5120×1440`), while the 3D world continues rendering across the full `32:9` monitor. On `≤ 16:9` resolutions, the hooks automatically pass through to vanilla behavior.

- **Dynamic UI scale & offset (`ui_core::OnDeviceReset` & R2 `ui_core::pp_start`)**: Overrides `m_scale_.x` and `m_pp_scale_.x` to `ui_width / 1024` and tracks the active centering offset for both normal HUD rendering and R2 post-process menu rendering.
- **Static textures, HUD, menus, inventory, trade, PDA, movies (`CUIStaticItem::Render`)**: Centers all 2D UI quads.
- **Rotated elements (`CUIStaticItem::Render(angle)`)**: Centers all 4 rotated corners (compass needle, menu gears, PDA arrows).
- **Grids, progress shapes & selection boxes (`CUICellContainer::Draw`, `CUIProgressShape::Draw`, `DrawBox`)**: Keeps inventory grids, radial progress, and boxes aligned pixel-for-pixel.
- **Scissor clipping (`ui_core::PushScissor` / `PopScissor`)**: Shifts all scroll-view clip rectangles so scrollable lists, PDA logs, and dialogs clip cleanly.
- **UI fonts (`CGameFont::Out`, all 9 UI call sites)**: Thunks all UI font draws (including `CUIEditBox`'s cached `EBP` pointer) so text, alignment, wrapping, and input fields match the centered canvas.
- **Mouse cursor (`CUICursor::UpdateCursorPosition`)**: Feeds `ui_width` instead of full monitor width so horizontal and vertical cursor sensitivity match `1:1` and clicks line up exactly with UI hitboxes.
- **Crosshair & center dot (`CHUDCrosshair::OnRender`, `CHUDCursor::OnRender`)**: Keeps the crosshair dead-center at `dwWidth / 2` while sizing lines and dots using `ui_width`.
- **Sniper scopes & binoculars (`CWeapon::RenderUI`)**: Renders vanilla `4:3` scopes as true circles (`1920×1440` centered on `5120×1440`) with solid black `IDirect3DDevice9::Clear` pillarboxes on the wings (configurable via `--scope-aspect 16:9` for widescreen scope packs).
- **Main & Pause menu (`CMainMenu::OnRender` & `OnRenderPPUI_main`)**: Draws solid black pillarboxes behind menus in both R1 and R2 renderers (checks `CMainMenu::IsActive()` first; toggleable with `--no-menu-bars`).

### 2. Centered 4:3 Loading Screen (`bin\XR_3DA.exe` → `.uwld` section, 7 callsites)

- Hooks `CApplication::load_draw_internal` (`0x00412BFB` + 6 X-scale sites) to preserve `4:3` proportions for loading art, the progress bar, and level logos, clearing the backbuffer to solid black so the ultrawide wings have zero visual artifacts.

### 3. Native Borderless Fullscreen + DPI Awareness (`bin\XR_3DA.exe` → `.uwld` + 3 callsites)

Turns X-Ray's windowed mode (`rs_fullscreen off`) into **true Borderless Fullscreen**, while keeping Exclusive Fullscreen (`rs_fullscreen on`) completely intact so you can switch modes anytime in-game under **Options → Video → Full screen**:

- **`CRenderDevice::Initialize` (`0x00484FA0` → `Hook_Borderless_Init`)**: Replaces initial `WS_CAPTION` (`0x00C00000`) with `WS_POPUP` (`0x80000000`) to eliminate startup border flash, and dynamically resolves and invokes `user32.dll!SetProcessDPIAware()` so borderless windows map `1:1` to physical pixels even when Windows display scaling is `> 100%` (125%, 150%, 200%).
- **`CHW::updateWindowProps` (`0x0048B591` & `0x0048B60D`)**: Replaces windowed style `0x10CA0000` (`WS_VISIBLE | WS_BORDER | WS_DLGFRAME | WS_SYSMENU | WS_MINIMIZEBOX`) with `0x90000000` (`WS_POPUP | WS_VISIBLE`) for both `SetWindowLongA` and `AdjustWindowRect`. Combined with X-Ray's existing `SetWindowPos(HWND_TOP, 0, 0, W, H, SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOCOPYBITS)`, this fills the entire primary monitor seamlessly over the taskbar while allowing instant Alt+Tab with zero monitor mode-switching.
- **Multi-monitor mouse lock**: X-Ray's `CInput` natively acquires DirectInput8 mouse with `DISCL_EXCLUSIVE | DISCL_FOREGROUND` even when `rs_fullscreen off`, and automatically calls `Unacquire()` / `Acquire()` on `WM_ACTIVATE`, so the cursor stays locked inside the game during play (no drift onto secondary monitors) and releases instantly on Alt+Tab.

| In-Game Setting (`user.ltx`) | Behavior (when patched) |
|---|---|
| **Full screen: OFF** (`rs_fullscreen off`) | **Borderless Fullscreen** (instant Alt+Tab, no mode switch, covers taskbar) |
| **Full screen: ON** (`rs_fullscreen on`) | **Exclusive Fullscreen** (vanilla D3D9 exclusive mode) |

---

## Technical Notes

- **100% Position-Independent Code (PIC)**: `xrGame.dll` (`ImageBase = 0x10000000`) frequently rebases at runtime on Windows. All injected code in `.uwui` and `.uwld` uses runtime `call/pop` PIC helpers (`pic_eax`/`ecx`/`edx`/`ebx`) with zero hardcoded absolute pointers.
- **Automatic `.reloc` Neutralization**: Whenever a patched callsite overwrites an instruction that originally contained a base-relocation fixup (such as `call dword ptr [IAT]` for `CGameFont::Out` or `fadd dword ptr [addr]` in `CUICursor`), `PEImage.patch_va()` automatically replaces the overlapping `.reloc` entry with `IMAGE_REL_BASED_ABSOLUTE` (`0`), preventing Windows from corrupting relative call displacements when the DLL rebases.
- **PE Integrity**: Strips trailing Authenticode certificate data (`DataDirectory[4]`) on `XR_3DA.exe` before appending `.uwld`, properly updates `SizeOfImage`, section alignment, and headers, and passes Win32 PE loader validation.
- **Non-Destructive Backups**: First run saves untouched originals as `xrGame.dll.orig.bak` and `XR_3DA.exe.orig.bak` (plus `user.ltx.bak` when modifying settings). Subsequent runs always re-patch from the pristine `.orig.bak` files so options can be changed safely without stacking patches.

---

## Usage

Requires **Python 3.10+** (no external pip packages needed for patching; `capstone` is only needed for `verify_patch.py`).

```powershell
# Apply full patch (Ultrawide UI + Centered Loading Screen + Borderless Fullscreen)
python soc_ultrawide_patcher.py

# Apply patch + configure 5120x1440 Borderless Fullscreen in _appdata_/user.ltx
python soc_ultrawide_patcher.py --set-res 5120x1440 --fullscreen off

# Switch to Exclusive Fullscreen (or toggle in-game Options -> Video -> Full screen)
python soc_ultrawide_patcher.py --fullscreen on

# Launch the GUI
python soc_ultrawide_patcher.py --gui

# Inspect current patch status & active user.ltx display mode
python soc_ultrawide_patcher.py --status

# Custom options (re-patches cleanly from *.orig.bak)
python soc_ultrawide_patcher.py --ui-aspect 16:9 --scope-aspect 16:9 --no-menu-bars
python soc_ultrawide_patcher.py --no-borderless      # Keep vanilla bordered window
python soc_ultrawide_patcher.py --no-load-screen     # Skip loading screen centering
python soc_ultrawide_patcher.py --no-exe             # Patch only xrGame.dll

# Specify a custom install directory
python soc_ultrawide_patcher.py --game-dir "D:\Games\STALKER Shadow of Chernobyl"

# Uninstall / restore untouched original binaries
python soc_ultrawide_patcher.py --restore
```

---

## Verification & Disassembly Audit

```powershell
pip install capstone
python verify_patch.py
```

`verify_patch.py` performs an in-memory audit from the clean `.orig.bak` binaries:
1. Disassembles 100% of `.uwui` and `.uwld` to verify zero invalid opcodes or truncated encodings.
2. Audits all **41 patched callsites** (31 in `xrGame.dll`, 10 in `XR_3DA.exe`) for exact instruction boundary re-synchronization and **zero stale `.reloc` entries**.
3. Verifies embedded `.uwld` flags and strings (`user32.dll`, `SetProcessDPIAware`).
4. Loads both patched binaries through Windows' native `LoadLibraryExW(..., LOAD_LIBRARY_AS_IMAGE_RESOURCE)` PE loader.

---

## Target Build & Compatibility

- **Supported binary**: S.T.A.L.K.E.R.: Shadow of Chernobyl **Steam 1.0006** (`xrCore` build 3312). Every callsite checks exact expected original bytes before patching and aborts safely without writing if a different engine binary is detected.
- **Renderers**: Verified on both R1 (Static) and R2 (Full Dynamic Lighting).
- **Mods**: Tested alongside **ZRP 1.07 R5**. Compatible with any `gamedata` mod that uses the standard 1.0006 `xrGame.dll` and `XR_3DA.exe`.
