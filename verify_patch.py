#!/usr/bin/env python3
"""
Verification & Disassembly Audit for S.T.A.L.K.E.R. SoC 1.0006 Ultrawide UI Patch
Runs in-memory / scratch-only by default (does not modify game files) OR inspects live game files with --live.
"""

import argparse
import ctypes
import os
import struct
import sys
import tempfile
from pathlib import Path

import capstone

import soc_ultrawide_patcher as patcher

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
LOAD_LIBRARY_AS_IMAGE_RESOURCE = 0x00000020


def check_win32_pe_load(path: Path) -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
    k32.LoadLibraryExW.restype = ctypes.c_void_p
    k32.FreeLibrary.argtypes = [ctypes.c_void_p]
    k32.FreeLibrary.restype = ctypes.c_bool

    h = k32.LoadLibraryExW(str(path), None, LOAD_LIBRARY_AS_IMAGE_RESOURCE)
    if not h:
        err = ctypes.get_last_error()
        print(f"  [FAIL] LoadLibraryExW failed on {path.name}: Win32 error {err}")
        return False
    k32.FreeLibrary(h)
    print(f"  [PASS] Win32 PE loader accepted {path.name} cleanly (handle=0x{h:x})")
    return True


def disasm_section(pe: patcher.PEImage, sec_name: str, data_size: int, labels: dict[str, int]):
    sec = pe.get_section(sec_name)
    assert sec is not None, f"Missing section {sec_name}"
    _, va, vsz, ra, rsz, chars = sec
    base_va = pe.ib + va
    rev_labels = {v: k for k, v in labels.items()}
    code = bytes(pe.data[ra + data_size : ra + rsz])

    # Stop at trailing zero padding after last function
    end_idx = len(code)
    while end_idx > 0 and code[end_idx - 1] == 0:
        end_idx -= 1
    code = code[:end_idx]

    print(f"\n=== Disassembly of {sec_name} (base=0x{base_va:08x}, code={len(code)} bytes) ===")
    decoded_end = base_va + data_size
    bad = 0
    for ins in md.disasm(code, base_va + data_size):
        if ins.address in rev_labels:
            print(f"\n{rev_labels[ins.address]}:  ; 0x{ins.address:08x}")
        note = ""
        if ins.mnemonic in ("call", "jmp", "je", "jne", "jbe", "ja", "jle", "jge") and ins.op_str.startswith("0x"):
            tgt = int(ins.op_str, 16)
            if tgt in rev_labels:
                note = f" ; -> {rev_labels[tgt]}"
            elif tgt == patcher.FN_STATIC_RENDER:
                note = " ; -> CUIStaticItem::Render"
            elif tgt == patcher.FN_DIALOGS_RENDER:
                note = " ; -> CDialogHolder::DoRenderDialogs"
        print(f"  {ins.address:08x}: {ins.bytes.hex(' '):<26s} {ins.mnemonic:10s} {ins.op_str}{note}")
        decoded_end = ins.address + ins.size

    expected_end = base_va + data_size + len(code)
    if decoded_end != expected_end:
        print(f"  [FAIL] Disassembly stopped early: decoded to 0x{decoded_end:08x}, expected 0x{expected_end:08x}")
        bad += 1
    else:
        print(f"  [PASS] 100% of {sec_name} decoded cleanly ({len(code)} bytes, zero invalid opcodes)")
    return bad == 0


def verify_callsites(pe: patcher.PEImage, callsites: list[tuple[int, int, str]], rev_labels: dict[int, str]):
    print("\n=== Patched Callsite Boundary & Reloc Check ===")
    ok = True
    for va, length, name in callsites:
        # Check NO stale .reloc entries overlap this patched site!
        stale_relocs = pe.strip_relocs_in_range(va, length)
        # Disassemble from va through va + length + 8 to confirm clean re-sync on next instruction!
        chunk = pe.read_va(va, length + 12)
        insns = list(md.disasm(chunk, va))
        hit_end = any(ins.address == va + length for ins in insns)
        first = insns[0] if insns else None
        tgt_note = ""
        if first and first.mnemonic == "call" and first.op_str.startswith("0x"):
            tgt = int(first.op_str, 16)
            tgt_note = rev_labels.get(tgt, f"0x{tgt:08x}")
        elif first:
            tgt_note = first.op_str
        site_ok = hit_end and (stale_relocs == 0)
        status = "PASS" if site_ok else "FAIL"
        if not site_ok:
            ok = False
        reloc_warn = f" [STALE RELOCS={stale_relocs}!]" if stale_relocs else ""
        print(f"  [{status}] 0x{va:08x} ({name:28s}) -> {first.mnemonic if first else '?'} {tgt_note}{reloc_warn} | next@0x{va+length:08x}: "
              + (" ".join(f"{i.mnemonic} {i.op_str}" for i in insns if i.address == va + length) or "MISALIGNED!"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", type=Path, default=patcher.find_game_dir())
    ap.add_argument("--dump-asm", action="store_true", help="Print full disassembly of .uwui and .uwld")
    args = ap.parse_args()

    bin_dir = args.game_dir / "bin"
    xrgame_src = (bin_dir / "xrGame.dll.orig.bak") if (bin_dir / "xrGame.dll.orig.bak").is_file() else (bin_dir / "xrGame.dll")
    xr3da_src = (bin_dir / "XR_3DA.exe.orig.bak") if (bin_dir / "XR_3DA.exe.orig.bak").is_file() else (bin_dir / "XR_3DA.exe")

    print(f"Testing patch generation from:\n  {xrgame_src}\n  {xr3da_src}")

    patched_game, uwui_va, game_labels = patcher.patch_xrgame(
        xrgame_src.read_bytes(), 16.0 / 9.0, 4.0 / 3.0, True, True
    )
    patched_exe, uwld_va, exe_labels = patcher.patch_xr3da(xr3da_src.read_bytes())

    pe_game = patcher.PEImage(patched_game)
    pe_exe = patcher.PEImage(patched_exe)

    # Check disassembly of generated sections
    ok_game = disasm_section(pe_game, ".uwui", patcher.DATA_BLOCK_SIZE, game_labels)
    ok_exe = disasm_section(pe_exe, ".uwld", patcher.LD_DATA_SIZE, exe_labels)

    # Verify callsites align cleanly to next instruction
    rev_game = {v: k for k, v in game_labels.items()}
    game_sites = [
        (0x103B82B1, 6, "ui_core::OnDeviceReset"),
        (0x103B8B01, 6, "ui_core::pp_start"),
        (0x103B84E7, 6, "PushScissor left"),
        (0x103B8502, 6, "PushScissor right"),
        (0x103B870A, 9, "PopScissor left"),
        (0x103B87CD, 9, "PopScissor right"),
        (0x103F3488, 9, "CUIStaticItem::Render"),
        (0x103EE1A5, 6, "Rot v0"),
        (0x103EE1D4, 6, "Rot v1"),
        (0x103EE203, 6, "Rot v2"),
        (0x103EE224, 6, "Rot v3"),
        (0x103F89EE, 8, "DrawBox x1"),
        (0x103F8A19, 5, "DrawBox x2"),
        (0x10402529, 6, "CUIProgressShape x1"),
        (0x1040255E, 6, "CUIProgressShape x2"),
        (0x1041A43D, 10, "CUICellContainer"),
        (0x103F3C43, 6, "FontOut #1"),
        (0x103F52C7, 6, "FontOut #2"),
        (0x10413E3B, 6, "FontOut #3"),
        (0x1041585F, 6, "FontOut #4"),
        (0x10425641, 6, "FontOut #5"),
        (0x1042E32F, 6, "FontOut #6/7 EBP"),
        (0x1042E4FB, 6, "FontOut #8"),
        (0x1043F140, 6, "FontOut #9"),
        (0x1044D5BE, 12, "CUICursor width"),
        (0x1044D5CD, 10, "CUICursor fixup NOP"),
        (0x1044C408, 10, "CHUDCrosshair scale"),
        (0x1044D206, 9, "CHUDCursor dot"),
        (0x1021DF65, 5, "CWeapon::RenderUI scope"),
        (0x103B656B, 5, "CMainMenu::OnRender"),
        (0x103B65DF, 5, "CMainMenu::OnRenderPPUI"),
    ]
    ok_sites_game = verify_callsites(pe_game, game_sites, rev_game)

    rev_exe = {v: k for k, v in exe_labels.items()}
    exe_sites = [
        (0x00412BFB, 5, "load_draw scale init"),
        (0x00412D0D, 5, "load_draw bg x1"),
        (0x00412D2B, 5, "load_draw bg x2"),
        (0x004130C1, 5, "load_draw bar x1"),
        (0x004130DF, 5, "load_draw bar x2"),
        (0x004136AB, 5, "load_draw logo x1"),
        (0x004136C9, 5, "load_draw logo x2"),
        (0x00484FA0, 6, "Init WS_POPUP + DPIAware"),
        (0x0048B591, 5, "SetWindowLongA WS_POPUP"),
        (0x0048B60D, 5, "AdjustWindowRect WS_POPUP"),
        (0x00424E39, 34, "CInput::OnAppActivate"),
        (0x00424F48, 34, "CInput::OnAppDeactivate"),
        (0x00425033, 30, "CInput::OnFrame active gate"),
        (0x00484192, 8, "OnWM_Activate HideCursor"),
        (0x004841A7, 8, "OnWM_Activate Show+Unlock"),
    ]
    ok_sites_exe = verify_callsites(pe_exe, exe_sites, rev_exe)

    # Verify embedded borderless strings & flags in .uwld
    s_u32 = pe_exe.read_va(uwld_va + patcher.LD_OFF_STR_USER32, 11)
    s_dpi = pe_exe.read_va(uwld_va + patcher.LD_OFF_STR_SETDPI, 19)
    s_clip = pe_exe.read_va(uwld_va + patcher.LD_OFF_STR_CLIPCURSOR, 11)
    ld_flags = struct.unpack("<I", pe_exe.read_va(uwld_va + patcher.LD_OFF_FLAGS, 4))[0]
    ok_ld_data = (
        s_u32 == b"user32.dll\x00"
        and s_dpi == b"SetProcessDPIAware\x00"
        and s_clip == b"ClipCursor\x00"
        and ld_flags == 3
    )
    print(f"\n  [{'PASS' if ok_ld_data else 'FAIL'}] .uwld data block: flags=0x{ld_flags:x}, strings=({s_u32!r}, {s_dpi!r}, {s_clip!r})")

    # Test Win32 PE loader acceptance in a temp directory
    print("\n=== Win32 PE Loader Check ===")
    with tempfile.TemporaryDirectory() as td:
        tg = Path(td) / "xrGame.dll"
        te = Path(td) / "XR_3DA.exe"
        tg.write_bytes(patched_game)
        te.write_bytes(patched_exe)
        ok_load_g = check_win32_pe_load(tg)
        ok_load_e = check_win32_pe_load(te)

    all_ok = all([ok_game, ok_exe, ok_sites_game, ok_sites_exe, ok_ld_data, ok_load_g, ok_load_e])
    print("\n" + ("=== ALL VERIFICATION CHECKS PASSED ===" if all_ok else "=== VERIFICATION FAILED ==="))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
