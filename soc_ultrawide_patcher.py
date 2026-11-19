#!/usr/bin/env python3
"""
S.T.A.L.K.E.R.: Shadow of Chernobyl (1.0006) — Ultrawide UI Patcher
===================================================================
Centers and un-stretches the entire X-Ray 1.0006 2D UI into a crisp 16:9
canvas (e.g. 2560x1440 centered at [1280..3840] on 5120x1440 32:9, or
2560x1440 centered on 3440x1440 21:9), while keeping the 3D world rendering
in full native ultrawide / super-ultrawide Hor+!

What it fixes (in xrGame.dll + XR_3DA.exe, 100% compatible with ZRP & mods):
  - All HUD elements, minimap, compass, health/armor, ammo, icons
  - Main menu & pause menu (with optional solid black pillarboxes)
  - Inventory, Trade, Loot (carbody), Talk/Dialogs, PDA & PDA Map
  - All UI fonts, text wrapping, alignment, and text edit boxes
  - All scroll view scissor clipping regions
  - Rotated UI elements (compass needle, menu gears, map arrows)
  - Mouse cursor proportions & 1:1 horizontal/vertical cursor sensitivity
  - Dynamic crosshair & center dot sizing (kept dead-center)
  - Sniper scopes & binoculars (geometrically circular 4:3 or 16:9 + black wings)
  - Loading screen background, progress bar, and level logo (XR_3DA.exe)
  - True Borderless Fullscreen mode (XR_3DA.exe: rs_fullscreen off = borderless
    WS_POPUP + SetProcessDPIAware; rs_fullscreen on = exclusive fullscreen)
  - Automatically falls back to vanilla behavior on 16:9 or 4:3 resolutions!

Usage:
  python soc_ultrawide_patcher.py                    # Auto-detect & patch (or GUI with --gui)
  python soc_ultrawide_patcher.py --restore          # Restore original backups
  python soc_ultrawide_patcher.py --status           # Inspect current patch status
  python soc_ultrawide_patcher.py --set-res 5120x1440 --fullscreen off   # Borderless!
  python soc_ultrawide_patcher.py --scope-aspect 16:9
"""

import argparse
import hashlib
import os
import re
import shutil
import struct
import sys
from pathlib import Path

# =============================================================================
# Constants & X-Ray 1.0006 (Steam build 3312) Static Addresses
# =============================================================================

MAGIC_XRGAME = b"UWUIv1.0"
MAGIC_XR3DA = b"UWLDv1.0"

# Data block layout at start of .uwui section in xrGame.dll
OFF_UI_OFFSET_X     = 0x00  # float: horizontal UI pixel offset (e.g. 1280.0 on 5120x1440)
OFF_UI_WIDTH        = 0x04  # float: virtual-mapped UI pixel width (e.g. 2560.0)
OFF_PP_OFFSET_X     = 0x08  # float: R2 post-process UI pixel offset
OFF_PP_WIDTH        = 0x0C  # float: R2 post-process UI pixel width
OFF_UI_CORE_PTR     = 0x10  # u32:   saved ui_core*
OFF_UI_ACTIVE       = 0x14  # u32:   1 if ultrawide active, else 0
OFF_TARGET_ASPECT   = 0x18  # float: target UI aspect ratio (default 16/9)
OFF_SCOPE_ASPECT    = 0x1C  # float: weapon scope aspect ratio (default 4/3)
OFF_MENU_BARS       = 0x20  # u32:   1 = draw black pillarboxes behind Main/Pause Menu
OFF_SCOPE_BARS      = 0x24  # u32:   1 = draw black pillarboxes around Scopes/Binoculars
OFF_INV_1024        = 0x28  # float: 1.0 / 1024.0
OFF_HALF            = 0x2C  # float: 0.5
OFF_ASPECT_EPS      = 0x30  # float: 0.01
OFF_1024            = 0x34  # float: 1024.0
OFF_768             = 0x38  # float: 768.0
OFF_UI_OFFSET_I     = 0x3C  # s32:   int(g_ui_offset_x)
OFF_UI_RIGHT_I      = 0x40  # s32:   int(g_ui_offset_x + g_ui_width)
OFF_PP_OFFSET_I     = 0x44  # s32:   int(g_pp_offset_x)
OFF_PP_RIGHT_I      = 0x48  # s32:   int(g_pp_offset_x + g_pp_width)
OFF_DEV_W_I         = 0x4C  # s32:   Device.dwWidth
OFF_DEV_H_I         = 0x50  # s32:   Device.dwHeight
OFF_PP_W_I          = 0x54  # s32:   PP render target width
OFF_PP_H_I          = 0x58  # s32:   PP render target height
OFF_MAGIC           = 0x60  # 8 bytes: "UWUIv1.0"
DATA_BLOCK_SIZE     = 0x80

# xrGame.dll known static VAs (ImageBase = 0x10000000)
XRGAME_IB           = 0x10000000
IAT_DEVICE          = 0x104589F8  # CRenderDevice* Device
IAT_HW              = 0x104589DC  # CHW* HW (HW.pDevice at +0x08)
IAT_FONT_OUT        = 0x10458988  # CGameFont::Out(__cdecl)
FN_STATIC_RENDER    = 0x103F3430  # CUIStaticItem::Render()
FN_DIALOGS_RENDER   = 0x103B9420  # CDialogHolder::DoRenderDialogs()

# Data block layout at start of .uwld section in XR_3DA.exe
LD_OFF_OFFSET_X     = 0x00  # float: loading screen horizontal offset
LD_OFF_1024         = 0x04  # float: 1024.0
LD_OFF_HALF         = 0x08  # float: 0.5
LD_OFF_EPS          = 0x0C  # float: 0.01
LD_OFF_MAGIC        = 0x10  # 8 bytes: "UWLDv1.0"
LD_OFF_FLAGS        = 0x18  # u32: bit0=load_screen, bit1=borderless
LD_OFF_STR_USER32   = 0x20  # char[16]: "user32.dll\0"
LD_OFF_STR_SETDPI   = 0x30  # char[24]: "SetProcessDPIAware\0"
LD_DATA_SIZE        = 0x50

LD_FLAG_LOAD_SCREEN = 0x01
LD_FLAG_BORDERLESS  = 0x02

# XR_3DA.exe known static VAs (ImageBase = 0x00400000)
XR3DA_IB            = 0x00400000
XR3DA_DEV_W         = 0x0050BD88  # Device.dwWidth
XR3DA_DEV_H         = 0x0050BD8C  # Device.dwHeight
XR3DA_HW_PDEVICE    = 0x0050BFF0  # IDirect3DDevice9* HW.pDevice
XR3DA_IAT_GETMOD    = 0x004CC058  # KERNEL32.dll!GetModuleHandleA
XR3DA_IAT_GETPROC   = 0x004CC08C  # KERNEL32.dll!GetProcAddress


# =============================================================================
# Minimal, Exact 32-bit x86 Assembler (Zero Dependencies)
# =============================================================================

EAX, ECX, EDX, EBX, ESP, EBP, ESI, EDI = range(8)
XMM0, XMM1, XMM2, XMM3, XMM4, XMM5, XMM6, XMM7 = range(8)


class Asm:
    """Clean 32-bit x86 assembler with symbolic label & rel32 resolution."""

    def __init__(self, base_va: int):
        self.base_va = base_va
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str, int]] = []  # (offset_in_buf, target_label_or_va, addend)

    @property
    def pc(self) -> int:
        return self.base_va + len(self.buf)

    def label(self, name: str):
        if name in self.labels:
            raise ValueError(f"Duplicate label: {name}")
        self.labels[name] = self.pc

    def align(self, n: int = 16, pad: int = 0xCC):
        rem = len(self.buf) % n
        if rem:
            self.buf.extend(bytes([pad]) * (n - rem))

    def raw(self, data: bytes):
        self.buf.extend(data)

    def u32(self, v: int):
        self.buf.extend(struct.pack("<I", v & 0xFFFFFFFF))

    def i32(self, v: int):
        self.buf.extend(struct.pack("<i", v))

    def f32(self, v: float):
        self.buf.extend(struct.pack("<f", v))

    # --- Basic GPR & Stack ---
    def push_r(self, r: int): self.buf.append(0x50 + r)
    def pop_r(self, r: int): self.buf.append(0x58 + r)
    def pushad(self): self.buf.append(0x60)
    def popad(self): self.buf.append(0x61)
    def pushfd(self): self.buf.append(0x9C)
    def popfd(self): self.buf.append(0x9D)
    def nop(self): self.buf.append(0x90)
    def ret(self, imm16: int = 0):
        if imm16 == 0:
            self.buf.append(0xC3)
        else:
            self.buf.append(0xC2)
            self.buf.extend(struct.pack("<H", imm16))

    def push_imm8(self, v: int): self.buf.extend([0x6A, v & 0xFF])
    def push_imm32(self, v: int):
        self.buf.append(0x68)
        self.u32(v)

    def mov_rr(self, dst: int, src: int):
        self.buf.extend([0x8B, 0xC0 | (dst << 3) | src])

    def xor_rr(self, dst: int, src: int):
        self.buf.extend([0x31, 0xC0 | (src << 3) | dst])

    def test_rr(self, r1: int, r2: int):
        self.buf.extend([0x85, 0xC0 | (r2 << 3) | r1])

    def test_al_al(self):
        self.buf.extend([0x84, 0xC0])

    def cmp_rr(self, r1: int, r2: int):
        self.buf.extend([0x39, 0xC0 | (r2 << 3) | r1])

    def add_r_imm8(self, r: int, v: int):
        self.buf.extend([0x83, 0xC0 | r, v & 0xFF])

    def sub_r_imm8(self, r: int, v: int):
        self.buf.extend([0x83, 0xE8 | r, v & 0xFF])

    def add_r_imm32(self, r: int, v: int):
        self.buf.extend([0x81, 0xC0 | r])
        self.i32(v)

    def cmp_r_imm8(self, r: int, v: int):
        self.buf.extend([0x83, 0xF8 | r, v & 0xFF])

    def mov_r_imm32(self, r: int, v: int):
        self.buf.append(0xB8 + r)
        self.u32(v)

    # --- Memory addressing (base != ESP) ---
    def _modrm_disp(self, reg: int, base: int, disp: int):
        assert base != ESP, "Use *_esp helpers for ESP addressing"
        if disp == 0 and base != EBP:
            self.buf.append((reg << 3) | base)
        elif -128 <= disp <= 127:
            self.buf.extend([0x40 | (reg << 3) | base, disp & 0xFF])
        else:
            self.buf.append(0x80 | (reg << 3) | base)
            self.i32(disp)

    def mov_r_mem(self, dst: int, base: int, disp: int = 0):
        self.buf.append(0x8B)
        self._modrm_disp(dst, base, disp)

    def mov_mem_r(self, base: int, disp: int, src: int):
        self.buf.append(0x89)
        self._modrm_disp(src, base, disp)

    def mov_mem_imm32(self, base: int, disp: int, imm: int):
        self.buf.append(0xC7)
        self._modrm_disp(0, base, disp)
        self.u32(imm)

    def lea_r_mem(self, dst: int, base: int, disp: int):
        self.buf.append(0x8D)
        self._modrm_disp(dst, base, disp)

    def cmp_mem8_imm8(self, base: int, disp: int, imm8: int):
        self.buf.append(0x80)
        self._modrm_disp(7, base, disp)
        self.buf.append(imm8 & 0xFF)

    def cmp_mem32_imm8(self, base: int, disp: int, imm8: int):
        self.buf.append(0x83)
        self._modrm_disp(7, base, disp)
        self.buf.append(imm8 & 0xFF)

    def call_mem(self, base: int, disp: int = 0):
        self.buf.append(0xFF)
        self._modrm_disp(2, base, disp)

    def call_r(self, r: int):
        self.buf.extend([0xFF, 0xD0 | r])

    # --- ESP addressing ---
    def _modrm_esp(self, reg: int, disp: int):
        if disp == 0:
            self.buf.extend([(reg << 3) | 4, 0x24])
        elif -128 <= disp <= 127:
            self.buf.extend([0x44 | (reg << 3), 0x24, disp & 0xFF])
        else:
            self.buf.extend([0x84 | (reg << 3), 0x24])
            self.i32(disp)

    def mov_r_esp(self, dst: int, disp: int = 0):
        self.buf.append(0x8B)
        self._modrm_esp(dst, disp)

    def mov_esp_r(self, disp: int, src: int):
        self.buf.append(0x89)
        self._modrm_esp(src, disp)

    def mov_esp_imm32(self, disp: int, imm: int):
        self.buf.append(0xC7)
        self._modrm_esp(0, disp)
        self.u32(imm)

    def lea_r_esp(self, dst: int, disp: int = 0):
        self.buf.append(0x8D)
        self._modrm_esp(dst, disp)

    def xchg_eax_esp0(self):
        self.buf.extend([0x87, 0x04, 0x24])

    # --- SSE scalar float ---
    def movss_x_mem(self, xmm: int, base: int, disp: int = 0):
        self.buf.extend([0xF3, 0x0F, 0x10])
        self._modrm_disp(xmm, base, disp)

    def movss_mem_x(self, base: int, disp: int, xmm: int):
        self.buf.extend([0xF3, 0x0F, 0x11])
        self._modrm_disp(xmm, base, disp)

    def movss_x_esp(self, xmm: int, disp: int = 0):
        self.buf.extend([0xF3, 0x0F, 0x10])
        self._modrm_esp(xmm, disp)

    def movss_esp_x(self, disp: int, xmm: int):
        self.buf.extend([0xF3, 0x0F, 0x11])
        self._modrm_esp(xmm, disp)

    def addss_x_mem(self, xmm: int, base: int, disp: int = 0):
        self.buf.extend([0xF3, 0x0F, 0x58])
        self._modrm_disp(xmm, base, disp)

    def addss_xx(self, dst: int, src: int):
        self.buf.extend([0xF3, 0x0F, 0x58, 0xC0 | (dst << 3) | src])

    def subss_xx(self, dst: int, src: int):
        self.buf.extend([0xF3, 0x0F, 0x5C, 0xC0 | (dst << 3) | src])

    def mulss_x_mem(self, xmm: int, base: int, disp: int = 0):
        self.buf.extend([0xF3, 0x0F, 0x59])
        self._modrm_disp(xmm, base, disp)

    def mulss_x_esp(self, xmm: int, disp: int = 0):
        self.buf.extend([0xF3, 0x0F, 0x59])
        self._modrm_esp(xmm, disp)

    def mulss_xx(self, dst: int, src: int):
        self.buf.extend([0xF3, 0x0F, 0x59, 0xC0 | (dst << 3) | src])

    def divss_xx(self, dst: int, src: int):
        self.buf.extend([0xF3, 0x0F, 0x5E, 0xC0 | (dst << 3) | src])

    def movaps_xx(self, dst: int, src: int):
        self.buf.extend([0x0F, 0x28, 0xC0 | (dst << 3) | src])

    def xorps_xx(self, dst: int, src: int):
        self.buf.extend([0x0F, 0x57, 0xC0 | (dst << 3) | src])

    def comiss_xx(self, x1: int, x2: int):
        self.buf.extend([0x0F, 0x2F, 0xC0 | (x1 << 3) | x2])

    def cvtsi2ss_xr(self, xmm: int, r: int):
        self.buf.extend([0xF3, 0x0F, 0x2A, 0xC0 | (xmm << 3) | r])

    def cvttss2si_rx(self, r: int, xmm: int):
        self.buf.extend([0xF3, 0x0F, 0x2C, 0xC0 | (r << 3) | xmm])

    # --- x87 FPU ---
    def fld_mem(self, base: int, disp: int = 0):
        self.buf.append(0xD9)
        self._modrm_disp(0, base, disp)

    def fstp_mem(self, base: int, disp: int = 0):
        self.buf.append(0xD9)
        self._modrm_disp(3, base, disp)

    def fild_mem(self, base: int, disp: int = 0):
        self.buf.append(0xDB)
        self._modrm_disp(0, base, disp)

    def fld_esp(self, disp: int = 0):
        self.buf.append(0xD9)
        self._modrm_esp(0, disp)

    # --- Control flow ---
    def _rel32(self, opcode: bytes, target):
        self.buf.extend(opcode)
        pos = len(self.buf)
        self.i32(0)
        self.fixups.append((pos, target, 0))

    def call(self, target): self._rel32(b"\xE8", target)
    def jmp(self, target): self._rel32(b"\xE9", target)
    def je(self, target): self._rel32(b"\x0F\x84", target)
    def jne(self, target): self._rel32(b"\x0F\x85", target)
    def jbe(self, target): self._rel32(b"\x0F\x86", target)
    def ja(self, target): self._rel32(b"\x0F\x87", target)
    def jle(self, target): self._rel32(b"\x0F\x8E", target)
    def jge(self, target): self._rel32(b"\x0F\x8D", target)

    # --- PIC base helper (sets reg = runtime base_va, preserves ALL other regs & flags!) ---
    def emit_pic_helpers(self, prefix: str = "pic"):
        for r, name in ((EAX, "eax"), (ECX, "ecx"), (EDX, "edx"), (EBX, "ebx")):
            self.align(4)
            self.label(f"{prefix}_{name}")
            self.mov_r_esp(r, 0)
            self.ret()

    def emit_pic_base(self, reg: int, prefix: str = "pic"):
        """Emits: call <prefix>_<reg>; lea reg, [reg - (after_va - base_va)]. Clobbers ONLY reg; preserves EFLAGS."""
        rname = {EAX: "eax", ECX: "ecx", EDX: "edx", EBX: "ebx"}[reg]
        self.call(f"{prefix}_{rname}")
        after_offset = len(self.buf)  # exact offset from base_va
        self.lea_r_mem(reg, reg, -after_offset)

    def finalize(self) -> bytes:
        for pos, target, addend in self.fixups:
            if isinstance(target, str):
                if target not in self.labels:
                    raise KeyError(f"Undefined label: {target}")
                target_va = self.labels[target]
            else:
                target_va = int(target)
            next_ip = self.base_va + pos + 4
            rel = (target_va + addend) - next_ip
            struct.pack_into("<i", self.buf, pos, rel)
        return bytes(self.buf)


# =============================================================================
# PE Image Manipulation Helpers
# =============================================================================

def align_up(val: int, align: int) -> int:
    return (val + align - 1) // align * align


class PEImage:
    def __init__(self, data: bytes):
        self.data = bytearray(data)
        self.pe_off = struct.unpack_from("<I", self.data, 0x3C)[0]
        if self.data[self.pe_off:self.pe_off + 4] != b"PE\x00\x00":
            raise ValueError("Not a valid PE file")
        self.opt_off = self.pe_off + 24
        magic = struct.unpack_from("<H", self.data, self.opt_off)[0]
        if magic != 0x10B:
            raise ValueError("Expected 32-bit PE32 image")
        self.ib = struct.unpack_from("<I", self.data, self.opt_off + 28)[0]
        self.sec_align, self.file_align = struct.unpack_from("<II", self.data, self.opt_off + 32)
        self.opt_sz = struct.unpack_from("<H", self.data, self.pe_off + 20)[0]
        self.sec_table_off = self.opt_off + self.opt_sz
        self._reload_sections()

    def _reload_sections(self):
        self.num_sec = struct.unpack_from("<H", self.data, self.pe_off + 6)[0]
        self.secs = []
        for i in range(self.num_sec):
            o = self.sec_table_off + i * 40
            name = bytes(self.data[o:o + 8]).rstrip(b"\x00").decode("latin1")
            vsz, va, rsz, ra = struct.unpack_from("<IIII", self.data, o + 8)
            chars = struct.unpack_from("<I", self.data, o + 36)[0]
            self.secs.append((name, va, vsz, ra, rsz, chars))

    def has_section(self, name: str) -> bool:
        return any(s[0] == name for s in self.secs)

    def get_section(self, name: str):
        for s in self.secs:
            if s[0] == name:
                return s
        return None

    def va2off(self, va: int) -> int:
        rva = va - self.ib
        for name, sva, vsz, ra, rsz, _ in self.secs:
            if sva <= rva < sva + max(vsz, rsz):
                return ra + (rva - sva)
        raise ValueError(f"VA 0x{va:08x} not mapped in PE")

    def read_va(self, va: int, size: int) -> bytes:
        off = self.va2off(va)
        return bytes(self.data[off:off + size])

    def strip_relocs_in_range(self, start_va: int, length: int) -> int:
        """Neutralize any base relocation entries overlapping [start_va, start_va + length)."""
        dd5_off = self.opt_off + 96 + 5 * 8
        reloc_rva, reloc_sz = struct.unpack_from("<II", self.data, dd5_off)
        if not reloc_rva or not reloc_sz:
            return 0
        start_rva = start_va - self.ib
        end_rva = start_rva + length
        base_off = self.va2off(self.ib + reloc_rva)
        pos = 0
        stripped = 0
        while pos + 8 <= reloc_sz:
            page_rva, block_sz = struct.unpack_from("<II", self.data, base_off + pos)
            if block_sz < 8:
                break
            count = (block_sz - 8) // 2
            for i in range(count):
                eo = base_off + pos + 8 + i * 2
                entry = struct.unpack_from("<H", self.data, eo)[0]
                rtype = entry >> 12
                if rtype == 0:  # IMAGE_REL_BASED_ABSOLUTE (padding/NOP)
                    continue
                target_rva = page_rva + (entry & 0x0FFF)
                # HIGHLOW (type 3) modifies 4 bytes [target_rva .. target_rva + 4)
                if target_rva < end_rva and (target_rva + 4) > start_rva:
                    struct.pack_into("<H", self.data, eo, 0)
                    stripped += 1
            pos += block_sz
        return stripped

    def patch_va(self, va: int, expected: bytes, replacement: bytes, desc: str):
        if len(expected) != len(replacement):
            raise ValueError(f"{desc}: length mismatch ({len(expected)} vs {len(replacement)})")
        actual = self.read_va(va, len(expected))
        if actual != expected:
            raise ValueError(
                f"{desc} @ 0x{va:08x}: byte mismatch!\n"
                f"  Expected: {expected.hex(' ')}\n"
                f"  Actual:   {actual.hex(' ')}"
            )
        off = self.va2off(va)
        self.data[off:off + len(replacement)] = replacement
        self.strip_relocs_in_range(va, len(replacement))

    def append_section(self, name: str, payload: bytes, chars: int = 0xE0000060) -> int:
        """Append a new PE section (stripping any trailing Authenticode cert if present)."""
        if self.has_section(name):
            raise ValueError(f"Section {name} already exists")
        soh = struct.unpack_from("<I", self.data, self.opt_off + 60)[0]
        new_hdr_off = self.sec_table_off + self.num_sec * 40
        if new_hdr_off + 40 > soh:
            raise RuntimeError("No free space in PE header table for new section")

        # Strip trailing Authenticode certificate table (DataDirectory[4]) if present
        dd4_off = self.opt_off + 96 + 4 * 8
        cert_off, cert_sz = struct.unpack_from("<II", self.data, dd4_off)
        last_sec = self.secs[-1]
        last_end_raw = last_sec[3] + last_sec[4]
        if cert_off and cert_off == last_end_raw and cert_off + cert_sz == len(self.data):
            del self.data[cert_off:]
            struct.pack_into("<II", self.data, dd4_off, 0, 0)

        # Pad raw file to FileAlignment
        raw_addr = align_up(len(self.data), self.file_align)
        if raw_addr > len(self.data):
            self.data.extend(b"\x00" * (raw_addr - len(self.data)))

        raw_size = align_up(max(len(payload), 0x1000), self.file_align)
        virt_size = align_up(max(len(payload), 0x1000), self.sec_align)
        virt_addr = align_up(last_sec[1] + max(last_sec[2], last_sec[4]), self.sec_align)

        # Write payload + zero pad
        self.data.extend(payload)
        if len(payload) < raw_size:
            self.data.extend(b"\x00" * (raw_size - len(payload)))

        # Write IMAGE_SECTION_HEADER
        name_bytes = name.encode("latin1")[:8].ljust(8, b"\x00")
        hdr = struct.pack(
            "<8sIIIIIIHHI",
            name_bytes,
            virt_size,
            virt_addr,
            raw_size,
            raw_addr,
            0, 0, 0, 0,
            chars,
        )
        self.data[new_hdr_off:new_hdr_off + 40] = hdr

        # Update NumberOfSections, SizeOfImage, and clear CheckSum
        struct.pack_into("<H", self.data, self.pe_off + 6, self.num_sec + 1)
        new_soi = align_up(virt_addr + virt_size, self.sec_align)
        struct.pack_into("<I", self.data, self.opt_off + 56, new_soi)
        struct.pack_into("<I", self.data, self.opt_off + 64, 0)  # CheckSum = 0

        self._reload_sections()
        return self.ib + virt_addr


# =============================================================================
# xrGame.dll Patch Builder (.uwui section)
# =============================================================================

def build_uwui_section(
    uwui_va: int,
    target_aspect: float,
    scope_aspect: float,
    enable_menu_bars: bool,
    enable_scope_bars: bool,
) -> tuple[bytes, dict[str, int]]:
    a = Asm(uwui_va)

    # ---- 0x00..0x7F: Shared Configuration & Runtime State Data Block ----
    data = bytearray(DATA_BLOCK_SIZE)
    struct.pack_into("<f", data, OFF_UI_OFFSET_X, 0.0)
    struct.pack_into("<f", data, OFF_UI_WIDTH, 1024.0)
    struct.pack_into("<f", data, OFF_PP_OFFSET_X, 0.0)
    struct.pack_into("<f", data, OFF_PP_WIDTH, 1024.0)
    struct.pack_into("<I", data, OFF_UI_CORE_PTR, 0)
    struct.pack_into("<I", data, OFF_UI_ACTIVE, 0)
    struct.pack_into("<f", data, OFF_TARGET_ASPECT, target_aspect)
    struct.pack_into("<f", data, OFF_SCOPE_ASPECT, scope_aspect)
    struct.pack_into("<I", data, OFF_MENU_BARS, 1 if enable_menu_bars else 0)
    struct.pack_into("<I", data, OFF_SCOPE_BARS, 1 if enable_scope_bars else 0)
    struct.pack_into("<f", data, OFF_INV_1024, 1.0 / 1024.0)
    struct.pack_into("<f", data, OFF_HALF, 0.5)
    struct.pack_into("<f", data, OFF_ASPECT_EPS, 0.01)
    struct.pack_into("<f", data, OFF_1024, 1024.0)
    struct.pack_into("<f", data, OFF_768, 768.0)
    data[OFF_MAGIC:OFF_MAGIC + 8] = MAGIC_XRGAME
    a.raw(bytes(data))

    # ---- PIC helpers (4 bytes each; touch ONLY target register & preserve EFLAGS) ----
    a.align(16)
    a.emit_pic_helpers("pic")

    # get_cur_offset_ptr_eax:
    # Returns &g_ui_offset_x (or &g_pp_offset_x if ui_core->m_bPostprocess != 0) in EAX.
    # Preserves ALL other registers (EBX..EDI, XMM0..7, x87) and EFLAGS!
    a.align(16)
    a.label("get_cur_offset_ptr_eax")
    a.pushfd()
    a.emit_pic_base(EAX, "pic")
    a.push_r(EDX)
    a.mov_r_mem(EDX, EAX, OFF_UI_CORE_PTR)
    a.test_rr(EDX, EDX)
    a.je("gcop_done")
    a.cmp_mem8_imm8(EDX, 0x14C, 0)
    a.je("gcop_done")
    a.add_r_imm8(EAX, OFF_PP_OFFSET_X)
    a.label("gcop_done")
    a.pop_r(EDX)
    a.popfd()
    a.ret()

    # Reusable helpers: add_cur_offset_xmmN (preserves ALL GPRs, all other XMMs, EFLAGS!)
    for xmm_idx in (XMM0, XMM1, XMM2, XMM4):
        a.align(16)
        a.label(f"add_cur_offset_xmm{xmm_idx}")
        a.push_r(EAX)
        a.call("get_cur_offset_ptr_eax")
        a.addss_x_mem(xmm_idx, EAX, 0)
        a.pop_r(EAX)
        a.ret()

    # ---- clear_pillarboxes(__stdcall: left_px, right_px, full_w, full_h) ----
    # Clears [0, 0, left_px, full_h] and [right_px, 0, full_w, full_h] to opaque black
    # via IDirect3DDevice9::Clear(2, rects, D3DCLEAR_TARGET, 0xFF000000, 0.0f, 0).
    # Preserves EBX, ESI, EDI, EBP (stdcall) + saves/restores EAX, ECX, EDX.
    a.align(16)
    a.label("clear_pillarboxes")
    a.push_r(EBP)
    a.mov_rr(EBP, ESP)
    a.push_r(EAX)
    a.push_r(ECX)
    a.push_r(EDX)
    a.push_r(EBX)
    # Args: [ebp+8]=left_px, [ebp+12]=right_px, [ebp+16]=full_w, [ebp+20]=full_h
    a.mov_r_mem(EAX, EBP, 8)     # left_px
    a.cmp_r_imm8(EAX, 0)
    a.jle("cpb_skip")
    a.mov_r_mem(ECX, EBP, 12)    # right_px
    a.mov_r_mem(EDX, EBP, 16)    # full_w
    a.cmp_rr(ECX, EDX)
    a.jge("cpb_skip")
    a.mov_r_mem(EBX, EBP, 20)    # full_h
    a.cmp_r_imm8(EBX, 0)
    a.jle("cpb_skip")

    # Allocate 32 bytes for 2 D3DRECTs on stack
    a.sub_r_imm8(ESP, 32)
    # rect[0] = { 0, 0, left_px, full_h }
    a.mov_esp_imm32(0, 0)
    a.mov_esp_imm32(4, 0)
    a.mov_esp_r(8, EAX)
    a.mov_esp_r(12, EBX)
    # rect[1] = { right_px, 0, full_w, full_h }
    a.mov_esp_r(16, ECX)
    a.mov_esp_imm32(20, 0)
    a.mov_esp_r(24, EDX)
    a.mov_esp_r(28, EBX)

    # Load HW.pDevice via PIC IAT_HW
    a.emit_pic_base(EAX, "pic")
    a.mov_r_mem(EDX, EAX, IAT_HW - uwui_va)  # edx = &HW
    a.test_rr(EDX, EDX)
    a.je("cpb_free")
    a.mov_r_mem(ECX, EDX, 8)                  # ecx = HW.pDevice (IDirect3DDevice9*)
    a.test_rr(ECX, ECX)
    a.je("cpb_free")
    a.mov_r_mem(EDX, ECX, 0)                  # edx = pDevice vtable
    a.lea_r_esp(EAX, 0)                       # eax = &rects[0]

    # Clear(2, rects, D3DCLEAR_TARGET=1, 0xFF000000, 0.0f, 0)
    a.push_imm8(0)            # Stencil = 0
    a.push_imm8(0)            # Z = 0.0f
    a.push_imm32(0xFF000000)  # Color = opaque black
    a.push_imm8(1)            # Flags = D3DCLEAR_TARGET
    a.push_r(EAX)             # pRects
    a.push_imm8(2)            # Count = 2
    a.push_r(ECX)             # this = pDevice
    a.call_mem(EDX, 0xAC)     # IDirect3DDevice9::Clear

    a.label("cpb_free")
    a.add_r_imm8(ESP, 32)
    a.label("cpb_skip")
    a.pop_r(EBX)
    a.pop_r(EDX)
    a.pop_r(ECX)
    a.pop_r(EAX)
    a.pop_r(EBP)
    a.ret(16)

    # =========================================================================
    # Hook 1: ui_core::OnDeviceReset @ 0x103b82b1
    # Displaced: fstp dword ptr [ecx + 0x164] (6 bytes)
    # Entry: ecx = ui_core*, st(0) = m_scale_.y, eax = 0, xmm0 = 0.0f
    # =========================================================================
    a.align(16)
    a.label("Hook_OnDeviceReset")
    a.fstp_mem(ECX, 0x164)
    a.pushfd()
    a.push_r(EAX)
    a.push_r(EDX)
    a.push_r(EBX)
    a.sub_r_imm8(ESP, 16)
    a.movss_esp_x(0, XMM1)
    a.movss_esp_x(4, XMM2)
    a.movss_esp_x(8, XMM3)

    a.emit_pic_base(EBX, "pic")
    a.mov_mem_r(EBX, OFF_UI_CORE_PTR, ECX)

    # Device* in EAX
    a.mov_r_mem(EAX, EBX, IAT_DEVICE - uwui_va)
    a.mov_r_mem(EDX, EAX, 0x100)  # dwWidth
    a.mov_r_mem(EAX, EAX, 0x104)  # dwHeight
    a.mov_mem_r(EBX, OFF_DEV_W_I, EDX)
    a.mov_mem_r(EBX, OFF_DEV_H_I, EAX)

    # xmm0 = float(W), xmm1 = float(H)
    a.cvtsi2ss_xr(XMM0, EDX)
    a.cvtsi2ss_xr(XMM1, EAX)
    # xmm2 = W / H
    a.movaps_xx(XMM2, XMM0)
    a.divss_xx(XMM2, XMM1)
    # xmm3 = target_aspect + 0.01
    a.movss_x_mem(XMM3, EBX, OFF_TARGET_ASPECT)
    a.addss_x_mem(XMM3, EBX, OFF_ASPECT_EPS)
    a.comiss_xx(XMM2, XMM3)
    a.jbe("odr_not_uw")

    # Ultrawide active!
    # ui_w (xmm2) = H * target_aspect
    a.movaps_xx(XMM2, XMM1)
    a.mulss_x_mem(XMM2, EBX, OFF_TARGET_ASPECT)
    # offset_x (xmm3) = (W - ui_w) * 0.5
    a.movaps_xx(XMM3, XMM0)
    a.subss_xx(XMM3, XMM2)
    a.mulss_x_mem(XMM3, EBX, OFF_HALF)
    # Store g_ui_width, g_ui_offset_x, g_ui_active = 1
    a.movss_mem_x(EBX, OFF_UI_WIDTH, XMM2)
    a.movss_mem_x(EBX, OFF_UI_OFFSET_X, XMM3)
    a.mov_mem_imm32(EBX, OFF_UI_ACTIVE, 1)
    # Int pillarbox bounds
    a.cvttss2si_rx(EAX, XMM3)
    a.mov_mem_r(EBX, OFF_UI_OFFSET_I, EAX)
    a.movaps_xx(XMM0, XMM3)
    a.addss_xx(XMM0, XMM2)
    a.cvttss2si_rx(EAX, XMM0)
    a.mov_mem_r(EBX, OFF_UI_RIGHT_I, EAX)
    # Override ui_core->m_scale_.x ([ecx+0x160]) = ui_w * (1/1024)
    a.mulss_x_mem(XMM2, EBX, OFF_INV_1024)
    a.movss_mem_x(ECX, 0x160, XMM2)
    a.jmp("odr_done")

    a.label("odr_not_uw")
    a.movss_mem_x(EBX, OFF_UI_WIDTH, XMM0)
    a.xorps_xx(XMM2, XMM2)
    a.movss_mem_x(EBX, OFF_UI_OFFSET_X, XMM2)
    a.mov_mem_imm32(EBX, OFF_UI_ACTIVE, 0)
    a.mov_mem_imm32(EBX, OFF_UI_OFFSET_I, 0)
    a.mov_mem_r(EBX, OFF_UI_RIGHT_I, EDX)

    a.label("odr_done")
    a.xorps_xx(XMM0, XMM0)  # OnDeviceReset expects xmm0 == 0.0f!
    a.movss_x_esp(XMM1, 0)
    a.movss_x_esp(XMM2, 4)
    a.movss_x_esp(XMM3, 8)
    a.add_r_imm8(ESP, 16)
    a.pop_r(EBX)
    a.pop_r(EDX)
    a.pop_r(EAX)
    a.popfd()
    a.ret()

    # =========================================================================
    # Hook 2: ui_core::pp_start @ 0x103b8b01
    # Displaced: fstp dword ptr [ebx + 0x15c] (6 bytes)
    # Entry: ebx = ui_core*, [ebx+0x158] = rt_w/1024, st(0) = rt_h/768
    # =========================================================================
    a.align(16)
    a.label("Hook_PPStart")
    a.fstp_mem(EBX, 0x15C)
    a.pushfd()
    a.push_r(EAX)
    a.push_r(ECX)
    a.push_r(EDX)
    a.sub_r_imm8(ESP, 16)
    a.movss_esp_x(0, XMM0)
    a.movss_esp_x(4, XMM1)
    a.movss_esp_x(8, XMM2)
    a.movss_esp_x(12, XMM3)

    a.emit_pic_base(EDX, "pic")
    a.mov_mem_r(EDX, OFF_UI_CORE_PTR, EBX)

    # rt_w = m_pp_scale_.x * 1024, rt_h = m_pp_scale_.y * 768
    a.movss_x_mem(XMM0, EBX, 0x158)
    a.mulss_x_mem(XMM0, EDX, OFF_1024)
    a.movss_x_mem(XMM1, EBX, 0x15C)
    a.mulss_x_mem(XMM1, EDX, OFF_768)

    # Save integer rt_w / rt_h (rounded)
    a.movaps_xx(XMM2, XMM0)
    a.addss_x_mem(XMM2, EDX, OFF_HALF)
    a.cvttss2si_rx(EAX, XMM2)
    a.mov_mem_r(EDX, OFF_PP_W_I, EAX)
    a.movaps_xx(XMM2, XMM1)
    a.addss_x_mem(XMM2, EDX, OFF_HALF)
    a.cvttss2si_rx(ECX, XMM2)
    a.mov_mem_r(EDX, OFF_PP_H_I, ECX)

    # aspect = rt_w / rt_h
    a.movaps_xx(XMM2, XMM0)
    a.divss_xx(XMM2, XMM1)
    a.movss_x_mem(XMM3, EDX, OFF_TARGET_ASPECT)
    a.addss_x_mem(XMM3, EDX, OFF_ASPECT_EPS)
    a.comiss_xx(XMM2, XMM3)
    a.jbe("pps_not_uw")

    # Ultrawide PPUI active
    a.movaps_xx(XMM2, XMM1)
    a.mulss_x_mem(XMM2, EDX, OFF_TARGET_ASPECT)  # pp_ui_w
    a.movaps_xx(XMM3, XMM0)
    a.subss_xx(XMM3, XMM2)
    a.mulss_x_mem(XMM3, EDX, OFF_HALF)           # pp_offset_x
    a.movss_mem_x(EDX, OFF_PP_WIDTH, XMM2)
    a.movss_mem_x(EDX, OFF_PP_OFFSET_X, XMM3)
    a.cvttss2si_rx(EAX, XMM3)
    a.mov_mem_r(EDX, OFF_PP_OFFSET_I, EAX)
    a.movaps_xx(XMM0, XMM3)
    a.addss_xx(XMM0, XMM2)
    a.cvttss2si_rx(EAX, XMM0)
    a.mov_mem_r(EDX, OFF_PP_RIGHT_I, EAX)
    # Override m_pp_scale_.x
    a.mulss_x_mem(XMM2, EDX, OFF_INV_1024)
    a.movss_mem_x(EBX, 0x158, XMM2)
    a.jmp("pps_done")

    a.label("pps_not_uw")
    a.movss_mem_x(EDX, OFF_PP_WIDTH, XMM0)
    a.xorps_xx(XMM2, XMM2)
    a.movss_mem_x(EDX, OFF_PP_OFFSET_X, XMM2)
    a.mov_mem_imm32(EDX, OFF_PP_OFFSET_I, 0)
    a.mov_mem_r(EDX, OFF_PP_RIGHT_I, EAX)  # EAX still holds pp_w_i

    a.label("pps_done")
    a.movss_x_esp(XMM0, 0)
    a.movss_x_esp(XMM1, 4)
    a.movss_x_esp(XMM2, 8)
    a.movss_x_esp(XMM3, 12)
    a.add_r_imm8(ESP, 16)
    a.pop_r(EDX)
    a.pop_r(ECX)
    a.pop_r(EAX)
    a.popfd()
    a.ret()

    # =========================================================================
    # Hook 3: ui_core::PushScissor left (0x103b84e7) & right (0x103b8502)
    # =========================================================================
    a.align(16)
    a.label("Hook_PushScissor_Left")
    a.mulss_x_esp(XMM2, 0x1C)  # caller [esp+0x18] + 4
    a.jmp("add_cur_offset_xmm2")

    a.align(16)
    a.label("Hook_PushScissor_Right")
    a.mulss_x_esp(XMM0, 0x24)  # caller [esp+0x20] + 4
    a.jmp("add_cur_offset_xmm0")

    # =========================================================================
    # Hook 4: ui_core::PopScissor left (0x103b870a) & right (0x103b87cd)
    # =========================================================================
    a.align(16)
    a.label("Hook_PopScissor_Left")
    a.movss_x_mem(XMM0, ESI, -0x10)
    a.mulss_x_mem(XMM0, EBX, 0)
    a.jmp("add_cur_offset_xmm0")

    a.align(16)
    a.label("Hook_PopScissor_Right")
    a.movss_x_mem(XMM0, ESI, 0x08)
    a.mulss_x_mem(XMM0, EBX, 0)
    a.jmp("add_cur_offset_xmm0")

    # =========================================================================
    # Hook 5: CUIStaticItem::Render pos.x (0x103f3488)
    # =========================================================================
    a.align(16)
    a.label("Hook_StaticItem_RenderX")
    a.movss_x_mem(XMM0, ESI, 0x40)
    a.mulss_x_mem(XMM0, EAX, 0)
    a.jmp("add_cur_offset_xmm0")

    # =========================================================================
    # Hook 6: CUIStaticItem::Render(angle) 4 corners (0x103ee1a5..0x103ee224)
    # =========================================================================
    for lbl, xmm_reg, disp in [
        ("Hook_Rot_X0", XMM2, 0x48),  # caller [esp+0x44] + 4
        ("Hook_Rot_X1", XMM2, 0x58),  # caller [esp+0x54] + 4
        ("Hook_Rot_X2", XMM2, 0x68),  # caller [esp+0x64] + 4
        ("Hook_Rot_X3", XMM4, 0x78),  # caller [esp+0x74] + 4
    ]:
        a.align(16)
        a.label(lbl)
        a.call(f"add_cur_offset_xmm{xmm_reg}")
        a.movss_esp_x(disp, xmm_reg)
        a.ret()

    # =========================================================================
    # Hook 7: DrawBox (0x103f89ee & 0x103f8a19)
    # =========================================================================
    a.align(16)
    a.label("Hook_DrawBox_X1")
    a.movss_x_mem(XMM1, ESI, 0)
    a.mulss_x_mem(XMM1, ECX, 0)
    a.jmp("add_cur_offset_xmm1")

    a.align(16)
    a.label("Hook_DrawBox_X2")
    a.mulss_x_mem(XMM1, ESI, 8)
    a.jmp("add_cur_offset_xmm1")

    # =========================================================================
    # Hook 8: CUIProgressShape::Draw (0x10402529 & 0x1040255e)
    # =========================================================================
    a.align(16)
    a.label("Hook_ProgShape_X1")
    a.mulss_x_esp(XMM4, 0x58)  # caller [esp+0x54] + 4
    a.jmp("add_cur_offset_xmm4")

    a.align(16)
    a.label("Hook_ProgShape_X2")
    a.mulss_x_esp(XMM0, 0x60)  # caller [esp+0x5c] + 4
    a.jmp("add_cur_offset_xmm0")

    # =========================================================================
    # Hook 9: CUICellContainer::Draw (0x1041a43d)
    # =========================================================================
    a.align(16)
    a.label("Hook_CellContainer_X")
    a.mulss_x_mem(XMM0, ECX, 0)
    a.call("add_cur_offset_xmm0")
    a.movss_esp_x(0x7C, XMM0)  # caller [esp+0x78] + 4
    a.ret()

    # =========================================================================
    # Hook 10: ALL 9 UI CGameFont::Out(__cdecl: this, float x, float y, fmt, ...)
    # Stack on entry: [esp+0]=retaddr, [esp+4]=this, [esp+8]=x, [esp+12]=y
    # =========================================================================
    a.align(16)
    a.label("UI_FontOut_Thunk")
    a.push_r(EAX)
    # Now [esp+12] is float x
    a.call("get_cur_offset_ptr_eax")
    a.movss_x_esp(XMM0, 12)
    a.addss_x_mem(XMM0, EAX, 0)
    a.movss_esp_x(12, XMM0)
    # Tail-jump to real CGameFont::Out via PIC IAT lookup, restoring EAX!
    a.emit_pic_base(EAX, "pic")
    a.mov_r_mem(EAX, EAX, IAT_FONT_OUT - uwui_va)
    a.xchg_eax_esp0()
    a.ret()

    # Helper for 0x1042e32f (mov ebp, [IAT_FONT_OUT]) -> puts &UI_FontOut_Thunk in EBP!
    a.align(16)
    a.label("Hook_LoadFontOut_EBP")
    a.push_r(EAX)
    a.emit_pic_base(EAX, "pic")
    a.lea_r_mem(EBP, EAX, a.labels["UI_FontOut_Thunk"] - uwui_va)
    a.pop_r(EAX)
    a.ret()

    # =========================================================================
    # Hook 11: CUICursor::UpdateCursorPosition (0x1044d5be)
    # Pushes g_ui_width onto x87 stack so horizontal cursor speed matches vertical 1:1
    # Preserves EAX, ECX, EFLAGS (caller uses ECX for Device.dwHeight right after!)
    # =========================================================================
    a.align(16)
    a.label("Hook_Cursor_LoadWidth")
    a.pushfd()
    a.push_r(EDX)
    a.emit_pic_base(EDX, "pic")
    a.cmp_mem32_imm8(EDX, OFF_UI_ACTIVE, 0)
    a.je("clw_vanilla")
    a.fld_mem(EDX, OFF_UI_WIDTH)
    a.jmp("clw_done")
    a.label("clw_vanilla")
    a.fild_mem(ECX, 0x100)
    a.label("clw_done")
    a.pop_r(EDX)
    a.popfd()
    a.ret()

    # =========================================================================
    # Hook 12: CHUDCrosshair::OnRender (0x1044c408)
    # Displaced: fld [esp+0x24]; mulss xmm0, [esp+0x24] (10 bytes)
    # Uses g_ui_width instead of rt_w when ultrawide active (keeps crosshair 16:9 size!)
    # =========================================================================
    a.align(16)
    a.label("Hook_Crosshair_Scale")
    a.pushfd()
    a.push_r(EAX)
    a.emit_pic_base(EAX, "pic")
    a.cmp_mem32_imm8(EAX, OFF_UI_ACTIVE, 0)
    a.je("chs_vanilla")
    a.fld_mem(EAX, OFF_UI_WIDTH)
    a.mulss_x_mem(XMM0, EAX, OFF_UI_WIDTH)
    a.jmp("chs_done")
    a.label("chs_vanilla")
    # Stack: [esp+0]=saved eax, [esp+4]=saved eflags, [esp+8]=retaddr -> caller [esp+0x24] is [esp+0x30]
    a.fld_esp(0x30)
    a.mulss_x_esp(XMM0, 0x30)
    a.label("chs_done")
    a.pop_r(EAX)
    a.popfd()
    a.ret()

    # =========================================================================
    # Hook 13: CHUDCursor::OnRender (0x1044d206)
    # Displaced: movaps xmm0, xmm3; mulss xmm0, [esp+0x54] (9 bytes)
    # Uses g_ui_width for center dot size while xmm3 keeps Device.dwWidth for centering!
    # =========================================================================
    a.align(16)
    a.label("Hook_HUDCursor_Size")
    a.pushfd()
    a.push_r(ECX)
    a.emit_pic_base(ECX, "pic")
    a.cmp_mem32_imm8(ECX, OFF_UI_ACTIVE, 0)
    a.je("hcs_vanilla")
    a.movss_x_mem(XMM0, ECX, OFF_UI_WIDTH)
    a.jmp("hcs_mul")
    a.label("hcs_vanilla")
    a.movaps_xx(XMM0, XMM3)
    a.label("hcs_mul")
    # Stack: [esp+0]=saved ecx, [esp+4]=saved eflags, [esp+8]=retaddr -> caller [esp+0x54] is [esp+0x60]
    a.mulss_x_esp(XMM0, 0x60)
    a.pop_r(ECX)
    a.popfd()
    a.ret()

    # =========================================================================
    # Hook 14: CWeapon::RenderUI (0x1021df65 — replaces call 0x103f3430)
    # Entry: esi = CUIStaticItem* (m_UIScope), vPos=(0,0), vRect=(0,0,1024,768)
    # Centers scope at g_scope_aspect (e.g. 4:3 circular!) and draws black pillarboxes!
    # =========================================================================
    a.align(16)
    a.label("Hook_WeaponScopeRender")
    a.push_r(EBX)
    a.emit_pic_base(EBX, "pic")
    a.cmp_mem32_imm8(EBX, OFF_UI_ACTIVE, 0)
    a.je("wsr_render")

    # scope_virt_w (xmm0) = 1024.0 * (scope_aspect / target_aspect)
    a.movss_x_mem(XMM0, EBX, OFF_SCOPE_ASPECT)
    a.movss_x_mem(XMM1, EBX, OFF_TARGET_ASPECT)
    a.divss_xx(XMM0, XMM1)
    a.mulss_x_mem(XMM0, EBX, OFF_1024)
    # Clamp scope_virt_w <= 1024.0
    a.movss_x_mem(XMM1, EBX, OFF_1024)
    a.comiss_xx(XMM0, XMM1)
    a.jbe("wsr_clamped")
    a.movaps_xx(XMM0, XMM1)
    a.label("wsr_clamped")
    # scope_virt_x (xmm2) = (1024.0 - scope_virt_w) * 0.5
    a.movaps_xx(XMM2, XMM1)
    a.subss_xx(XMM2, XMM0)
    a.mulss_x_mem(XMM2, EBX, OFF_HALF)
    # Apply to m_UIScope: vPos.x ([esi+0x40]) = scope_virt_x, vRect.x2 ([esi+0x10]) = scope_virt_w
    a.movss_mem_x(ESI, 0x40, XMM2)
    a.movss_mem_x(ESI, 0x10, XMM0)

    # Clear black pillarboxes if enabled
    a.cmp_mem32_imm8(EBX, OFF_SCOPE_BARS, 0)
    a.je("wsr_render")
    # scale_x (xmm3) = g_ui_width * (1/1024)
    a.movss_x_mem(XMM3, EBX, OFF_UI_WIDTH)
    a.mulss_x_mem(XMM3, EBX, OFF_INV_1024)
    # left_px = int(g_ui_offset_x + scope_virt_x * scale_x) + 2
    a.movaps_xx(XMM1, XMM2)
    a.mulss_xx(XMM1, XMM3)
    a.addss_x_mem(XMM1, EBX, OFF_UI_OFFSET_X)
    a.cvttss2si_rx(EAX, XMM1)
    a.add_r_imm8(EAX, 2)
    # right_px = int(g_ui_offset_x + (scope_virt_x + scope_virt_w) * scale_x) - 2
    a.addss_xx(XMM2, XMM0)
    a.mulss_xx(XMM2, XMM3)
    a.addss_x_mem(XMM2, EBX, OFF_UI_OFFSET_X)
    a.cvttss2si_rx(EDX, XMM2)
    a.sub_r_imm8(EDX, 2)
    # Push stdcall args (full_h, full_w, right_px, left_px)
    a.mov_r_mem(ECX, EBX, OFF_DEV_H_I)
    a.push_r(ECX)
    a.mov_r_mem(ECX, EBX, OFF_DEV_W_I)
    a.push_r(ECX)
    a.push_r(EDX)
    a.push_r(EAX)
    a.call("clear_pillarboxes")

    a.label("wsr_render")
    a.pop_r(EBX)
    a.jmp(FN_STATIC_RENDER)

    # =========================================================================
    # Hook 15a: CMainMenu::OnRender (0x103b656b — replaces call 0x103b9420)
    # Entry: esi = CMainMenu*, edi = CDialogHolder*
    # Only clears black pillarboxes when CMainMenu::IsActive() == true!
    # =========================================================================
    a.align(16)
    a.label("Hook_MainMenu_Render")
    a.push_r(EBX)
    a.emit_pic_base(EBX, "pic")
    a.cmp_mem32_imm8(EBX, OFF_UI_ACTIVE, 0)
    a.je("mmr_skip")
    a.cmp_mem32_imm8(EBX, OFF_MENU_BARS, 0)
    a.je("mmr_skip")
    # Check CMainMenu::IsActive() -> [esi]->vtable[+8](esi)
    a.mov_rr(ECX, ESI)
    a.mov_r_mem(EAX, ESI, 0)
    a.call_mem(EAX, 8)
    a.test_al_al()
    a.je("mmr_skip")
    # Clear left & right pillarboxes on main backbuffer
    a.mov_r_mem(EAX, EBX, OFF_DEV_H_I)
    a.push_r(EAX)
    a.mov_r_mem(EAX, EBX, OFF_DEV_W_I)
    a.push_r(EAX)
    a.mov_r_mem(EAX, EBX, OFF_UI_RIGHT_I)
    a.push_r(EAX)
    a.mov_r_mem(EAX, EBX, OFF_UI_OFFSET_I)
    a.push_r(EAX)
    a.call("clear_pillarboxes")
    a.label("mmr_skip")
    a.pop_r(EBX)
    a.jmp(FN_DIALOGS_RENDER)

    # =========================================================================
    # Hook 15b: CMainMenu::OnRenderPPUI_main (0x103b65df — replaces call 0x103b9420)
    # Entry: IsActive() is ALREADY guaranteed true; active RT size is g_pp_w_i x g_pp_h_i
    # =========================================================================
    a.align(16)
    a.label("Hook_MainMenu_RenderPP")
    a.push_r(EBX)
    a.emit_pic_base(EBX, "pic")
    a.cmp_mem32_imm8(EBX, OFF_UI_ACTIVE, 0)
    a.je("mmrp_skip")
    a.cmp_mem32_imm8(EBX, OFF_MENU_BARS, 0)
    a.je("mmrp_skip")
    a.mov_r_mem(EAX, EBX, OFF_PP_H_I)
    a.push_r(EAX)
    a.mov_r_mem(EAX, EBX, OFF_PP_W_I)
    a.push_r(EAX)
    a.mov_r_mem(EAX, EBX, OFF_PP_RIGHT_I)
    a.push_r(EAX)
    a.mov_r_mem(EAX, EBX, OFF_PP_OFFSET_I)
    a.push_r(EAX)
    a.call("clear_pillarboxes")
    a.label("mmrp_skip")
    a.pop_r(EBX)
    a.jmp(FN_DIALOGS_RENDER)

    code = a.finalize()
    return code, dict(a.labels)


# =============================================================================
# XR_3DA.exe Patch Builder (.uwld section: Loading Screen + Borderless)
# =============================================================================

def build_uwld_section(
    uwld_va: int,
    center_load: bool = True,
    borderless: bool = True,
) -> tuple[bytes, dict[str, int]]:
    a = Asm(uwld_va)

    flags = (LD_FLAG_LOAD_SCREEN if center_load else 0) | (LD_FLAG_BORDERLESS if borderless else 0)
    data = bytearray(LD_DATA_SIZE)
    struct.pack_into("<f", data, LD_OFF_OFFSET_X, 0.0)
    struct.pack_into("<f", data, LD_OFF_1024, 1024.0)
    struct.pack_into("<f", data, LD_OFF_HALF, 0.5)
    struct.pack_into("<f", data, LD_OFF_EPS, 0.01)
    data[LD_OFF_MAGIC:LD_OFF_MAGIC + 8] = MAGIC_XR3DA
    struct.pack_into("<I", data, LD_OFF_FLAGS, flags)
    s_user32 = b"user32.dll\x00"
    s_setdpi = b"SetProcessDPIAware\x00"
    data[LD_OFF_STR_USER32:LD_OFF_STR_USER32 + len(s_user32)] = s_user32
    data[LD_OFF_STR_SETDPI:LD_OFF_STR_SETDPI + len(s_setdpi)] = s_setdpi
    a.raw(bytes(data))

    a.align(16)
    a.emit_pic_helpers("ld_pic")

    # Hook_LoadInit (replaces call 0x423c20 at 0x00412bfb; stdcall ret 4!)
    # Entry: eax = &[ebp-0x60], xmm0 = dwWidth/1024, [esp+4] = dwHeight/768
    a.align(16)
    a.label("Hook_LoadInit")
    a.movss_mem_x(EAX, 0, XMM0)       # [ebp-0x60] = scale.x
    a.movss_x_esp(XMM1, 4)            # xmm1 = scale.y (dwHeight/768)
    a.movss_mem_x(EAX, 4, XMM1)       # [ebp-0x5c] = scale.y

    a.push_r(EBX)
    a.push_r(ECX)
    a.push_r(EDX)
    a.mov_rr(EBX, EAX)                # ebx = &[ebp-0x60]
    a.emit_pic_base(EDX, "ld_pic")

    # Compare scale.x (xmm0) vs scale.y + 0.01 (xmm2) -> > 4:3?
    a.movaps_xx(XMM2, XMM1)
    a.addss_x_mem(XMM2, EDX, LD_OFF_EPS)
    a.comiss_xx(XMM0, XMM2)
    a.jbe("li_not_wide")

    # Set scale.x = scale.y (4:3 unstretched!), offset_x = (scale.x - scale.y) * 1024 * 0.5
    a.movss_mem_x(EBX, 0, XMM1)
    a.subss_xx(XMM0, XMM1)
    a.mulss_x_mem(XMM0, EDX, LD_OFF_1024)
    a.mulss_x_mem(XMM0, EDX, LD_OFF_HALF)
    a.movss_mem_x(EDX, LD_OFF_OFFSET_X, XMM0)

    # Clear entire backbuffer to solid black so wings have zero artifacts
    a.mov_r_mem(ECX, EDX, XR3DA_HW_PDEVICE - uwld_va)  # IDirect3DDevice9*
    a.test_rr(ECX, ECX)
    a.je("li_done")
    a.mov_r_mem(EAX, ECX, 0)          # vtable
    a.push_imm8(0)                    # Stencil = 0
    a.push_imm32(0x3F800000)          # Z = 1.0f
    a.push_imm32(0xFF000000)          # Color = black
    a.push_imm8(1)                    # Flags = D3DCLEAR_TARGET
    a.push_imm8(0)                    # pRects = NULL (entire viewport)
    a.push_imm8(0)                    # Count = 0
    a.push_r(ECX)                     # this
    a.call_mem(EAX, 0xAC)             # IDirect3DDevice9::Clear
    a.jmp("li_done")

    a.label("li_not_wide")
    a.xorps_xx(XMM2, XMM2)
    a.movss_mem_x(EDX, LD_OFF_OFFSET_X, XMM2)

    a.label("li_done")
    a.pop_r(EDX)
    a.pop_r(ECX)
    a.pop_r(EBX)
    a.ret(4)

    # Hook_LoadScaleX (replaces 5-byte `mulss xmm0, [ebp-0x60]` at 6 sites)
    a.align(16)
    a.label("Hook_LoadScaleX")
    a.mulss_x_mem(XMM0, EBP, -0x60)
    a.push_r(EAX)
    a.emit_pic_base(EAX, "ld_pic")
    a.addss_x_mem(XMM0, EAX, LD_OFF_OFFSET_X)
    a.pop_r(EAX)
    a.ret()

    # Hook_Borderless_Init (replaces 6-byte `mov dword ptr [esi], 0x00c00000` at 0x00484FA0)
    # Sets initial window style to WS_POPUP (0x80000000) and calls user32!SetProcessDPIAware()
    # so borderless fullscreen maps 1:1 to physical pixels even when Windows DPI scaling > 100%.
    a.align(16)
    a.label("Hook_Borderless_Init")
    a.mov_mem_imm32(ESI, 0, 0x80000000)
    a.pushfd()
    a.pushad()
    a.emit_pic_base(EBX, "ld_pic")
    a.lea_r_mem(EAX, EBX, LD_OFF_STR_USER32)
    a.push_r(EAX)
    a.call_mem(EBX, XR3DA_IAT_GETMOD - uwld_va)   # GetModuleHandleA("user32.dll")
    a.test_rr(EAX, EAX)
    a.je("bi_done")
    a.lea_r_mem(EDX, EBX, LD_OFF_STR_SETDPI)
    a.push_r(EDX)
    a.push_r(EAX)
    a.call_mem(EBX, XR3DA_IAT_GETPROC - uwld_va)  # GetProcAddress(hUser32, "SetProcessDPIAware")
    a.test_rr(EAX, EAX)
    a.je("bi_done")
    a.call_r(EAX)                                  # SetProcessDPIAware()
    a.label("bi_done")
    a.popad()
    a.popfd()
    a.ret()

    code = a.finalize()
    return code, dict(a.labels)


# =============================================================================
# Callsite Patching
# =============================================================================

def make_call_patch(call_va: int, target_va: int, total_len: int) -> bytes:
    assert total_len >= 5
    rel = target_va - (call_va + 5)
    return b"\xE8" + struct.pack("<i", rel) + b"\x90" * (total_len - 5)


def patch_xrgame(
    src_bytes: bytes,
    target_aspect: float,
    scope_aspect: float,
    enable_menu_bars: bool,
    enable_scope_bars: bool,
) -> tuple[bytes, int, dict[str, int]]:
    pe = PEImage(src_bytes)
    if pe.has_section(".uwui"):
        raise RuntimeError("xrGame.dll is already patched (has .uwui section). Restore from backup first.")

    # Predict next section VA so we can assemble directly for it
    last_sec = pe.secs[-1]
    uwui_va = pe.ib + align_up(last_sec[1] + max(last_sec[2], last_sec[4]), pe.sec_align)
    code, labels = build_uwui_section(
        uwui_va, target_aspect, scope_aspect, enable_menu_bars, enable_scope_bars
    )
    actual_va = pe.append_section(".uwui", code, chars=0xE0000060)
    assert actual_va == uwui_va, f"Section VA mismatch: {hex(actual_va)} != {hex(uwui_va)}"

    # Exact byte-verified callsites in Steam 1.0006 xrGame.dll
    patches = [
        # 1. ui_core::OnDeviceReset (fstp [ecx+0x164])
        (0x103B82B1, bytes.fromhex("d9 99 64 01 00 00"), "Hook_OnDeviceReset", "ui_core::OnDeviceReset"),
        # 2. ui_core::pp_start (fstp [ebx+0x15c])
        (0x103B8B01, bytes.fromhex("d9 9b 5c 01 00 00"), "Hook_PPStart", "ui_core::pp_start"),
        # 3. ui_core::PushScissor left & right
        (0x103B84E7, bytes.fromhex("f3 0f 59 54 24 18"), "Hook_PushScissor_Left", "PushScissor left"),
        (0x103B8502, bytes.fromhex("f3 0f 59 44 24 20"), "Hook_PushScissor_Right", "PushScissor right"),
        # 4. ui_core::PopScissor left & right
        (0x103B870A, bytes.fromhex("f3 0f 10 46 f0 f3 0f 59 03"), "Hook_PopScissor_Left", "PopScissor left"),
        (0x103B87CD, bytes.fromhex("f3 0f 10 46 08 f3 0f 59 03"), "Hook_PopScissor_Right", "PopScissor right"),
        # 5. CUIStaticItem::Render pos.x
        (0x103F3488, bytes.fromhex("f3 0f 10 46 40 f3 0f 59 00"), "Hook_StaticItem_RenderX", "CUIStaticItem::Render"),
        # 6. CUIStaticItem::Render(angle) 4 rotated corners
        (0x103EE1A5, bytes.fromhex("f3 0f 11 54 24 44"), "Hook_Rot_X0", "CUIStaticItem::Render(angle) v0"),
        (0x103EE1D4, bytes.fromhex("f3 0f 11 54 24 54"), "Hook_Rot_X1", "CUIStaticItem::Render(angle) v1"),
        (0x103EE203, bytes.fromhex("f3 0f 11 54 24 64"), "Hook_Rot_X2", "CUIStaticItem::Render(angle) v2"),
        (0x103EE224, bytes.fromhex("f3 0f 11 64 24 74"), "Hook_Rot_X3", "CUIStaticItem::Render(angle) v3"),
        # 7. DrawBox x1 & x2
        (0x103F89EE, bytes.fromhex("f3 0f 10 0e f3 0f 59 09"), "Hook_DrawBox_X1", "DrawBox x1"),
        (0x103F8A19, bytes.fromhex("f3 0f 59 4e 08"), "Hook_DrawBox_X2", "DrawBox x2"),
        # 8. CUIProgressShape::Draw x1 & x2
        (0x10402529, bytes.fromhex("f3 0f 59 64 24 54"), "Hook_ProgShape_X1", "CUIProgressShape x1"),
        (0x1040255E, bytes.fromhex("f3 0f 59 44 24 5c"), "Hook_ProgShape_X2", "CUIProgressShape x2"),
        # 9. CUICellContainer::Draw origin.x
        (0x1041A43D, bytes.fromhex("f3 0f 59 01 f3 0f 11 44 24 78"), "Hook_CellContainer_X", "CUICellContainer"),
        # 10. All 8 UI CGameFont::Out call/load sites (covering all 9 UI text invocations)
        (0x103F3C43, bytes.fromhex("ff 15 88 89 45 10"), "UI_FontOut_Thunk", "FontOut #1"),
        (0x103F52C7, bytes.fromhex("ff 15 88 89 45 10"), "UI_FontOut_Thunk", "FontOut #2"),
        (0x10413E3B, bytes.fromhex("ff 15 88 89 45 10"), "UI_FontOut_Thunk", "FontOut #3"),
        (0x1041585F, bytes.fromhex("ff 15 88 89 45 10"), "UI_FontOut_Thunk", "FontOut #4"),
        (0x10425641, bytes.fromhex("ff 15 88 89 45 10"), "UI_FontOut_Thunk", "FontOut #5"),
        (0x1042E32F, bytes.fromhex("8b 2d 88 89 45 10"), "Hook_LoadFontOut_EBP", "FontOut #6/7 (CUIEditBox ebp)"),
        (0x1042E4FB, bytes.fromhex("ff 15 88 89 45 10"), "UI_FontOut_Thunk", "FontOut #8"),
        (0x1043F140, bytes.fromhex("ff 15 88 89 45 10"), "UI_FontOut_Thunk", "FontOut #9"),
        # 11. CUICursor::UpdateCursorPosition horizontal speed (keep sub esp, 0xc at 0x1044d5ca intact!)
        (0x1044D5BE, bytes.fromhex("8b 91 00 01 00 00 db 81 00 01 00 00"), "Hook_Cursor_LoadWidth", "CUICursor width load"),
        # 12. CHUDCrosshair::OnRender line length/radius scaling
        (0x1044C408, bytes.fromhex("d9 44 24 24 f3 0f 59 44 24 24"), "Hook_Crosshair_Scale", "CHUDCrosshair scale"),
        # 13. CHUDCursor::OnRender center dot sizing
        (0x1044D206, bytes.fromhex("0f 28 c3 f3 0f 59 44 24 54"), "Hook_HUDCursor_Size", "CHUDCursor dot size"),
        # 14. CWeapon::RenderUI (sniper scopes & binoculars)
        (0x1021DF65, bytes.fromhex("e8 c6 54 1d 00"), "Hook_WeaponScopeRender", "CWeapon::RenderUI scope"),
        # 15a/b. CMainMenu::OnRender & OnRenderPPUI_main (black pillarboxes behind main/pause menu)
        (0x103B656B, bytes.fromhex("e8 b0 2e 00 00"), "Hook_MainMenu_Render", "CMainMenu::OnRender"),
        (0x103B65DF, bytes.fromhex("e8 3c 2e 00 00"), "Hook_MainMenu_RenderPP", "CMainMenu::OnRenderPPUI_main"),
    ]

    for va, expected, lbl, desc in patches:
        repl = make_call_patch(va, labels[lbl], len(expected))
        pe.patch_va(va, expected, repl, desc)

    # Also NOP out the unsigned-int fixup right after 0x1044d5ca (10 bytes: test edx,edx; jge +6; fadd 2^32)
    pe.patch_va(
        0x1044D5CD,
        bytes.fromhex("85 d2 7d 06 d8 05 cc 23 4d 10"),
        b"\x90" * 10,
        "CUICursor unsigned fixup NOP",
    )

    return bytes(pe.data), uwui_va, labels


def patch_xr3da(
    src_bytes: bytes,
    center_load: bool = True,
    borderless: bool = True,
) -> tuple[bytes, int, dict[str, int]]:
    pe = PEImage(src_bytes)
    if pe.has_section(".uwld"):
        raise RuntimeError("XR_3DA.exe is already patched (has .uwld section). Restore from backup first.")

    last_sec = pe.secs[-1]
    uwld_va = pe.ib + align_up(last_sec[1] + max(last_sec[2], last_sec[4]), pe.sec_align)
    code, labels = build_uwld_section(uwld_va, center_load=center_load, borderless=borderless)
    actual_va = pe.append_section(".uwld", code, chars=0xE0000060)
    assert actual_va == uwld_va

    if center_load:
        load_patches = [
            (0x00412BFB, bytes.fromhex("e8 20 10 01 00"), "Hook_LoadInit", "load_draw_internal scale init"),
            (0x00412D0D, bytes.fromhex("f3 0f 59 45 a0"), "Hook_LoadScaleX", "load_draw bg x1"),
            (0x00412D2B, bytes.fromhex("f3 0f 59 45 a0"), "Hook_LoadScaleX", "load_draw bg x2"),
            (0x004130C1, bytes.fromhex("f3 0f 59 45 a0"), "Hook_LoadScaleX", "load_draw bar x1"),
            (0x004130DF, bytes.fromhex("f3 0f 59 45 a0"), "Hook_LoadScaleX", "load_draw bar x2"),
            (0x004136AB, bytes.fromhex("f3 0f 59 45 a0"), "Hook_LoadScaleX", "load_draw logo x1"),
            (0x004136C9, bytes.fromhex("f3 0f 59 45 a0"), "Hook_LoadScaleX", "load_draw logo x2"),
        ]
        for va, expected, lbl, desc in load_patches:
            repl = make_call_patch(va, labels[lbl], len(expected))
            pe.patch_va(va, expected, repl, desc)

    if borderless:
        # 1. CRenderDevice::Initialize: WS_CAPTION -> WS_POPUP + SetProcessDPIAware()
        pe.patch_va(
            0x00484FA0,
            bytes.fromhex("c7 06 00 00 c0 00"),
            make_call_patch(0x00484FA0, labels["Hook_Borderless_Init"], 6),
            "CRenderDevice::Initialize borderless + DPI aware",
        )
        # 2. CHW::updateWindowProps (windowed branch): SetWindowLongA style -> WS_POPUP|WS_VISIBLE
        pe.patch_va(
            0x0048B591,
            bytes.fromhex("68 00 00 ca 10"),
            bytes.fromhex("68 00 00 00 90"),
            "CHW::updateWindowProps SetWindowLongA WS_POPUP|WS_VISIBLE",
        )
        # 3. CHW::updateWindowProps (windowed branch): AdjustWindowRect style -> WS_POPUP|WS_VISIBLE
        pe.patch_va(
            0x0048B60D,
            bytes.fromhex("68 00 00 ca 10"),
            bytes.fromhex("68 00 00 00 90"),
            "CHW::updateWindowProps AdjustWindowRect WS_POPUP|WS_VISIBLE",
        )

    return bytes(pe.data), uwld_va, labels


# =============================================================================
# High-Level Management (Auto-detect, Backup, Restore, Status, user.ltx)
# =============================================================================

def parse_aspect(val: str) -> float:
    val = val.strip()
    if ":" in val:
        w, h = val.split(":", 1)
        return float(w) / float(h)
    return float(val)


def find_game_dir() -> Path | None:
    candidates = [
        Path(r"H:\SteamLibrary\steamapps\common\STALKER Shadow of Chernobyl"),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\STALKER Shadow of Chernobyl"),
        Path(r"C:\Program Files\Steam\steamapps\common\STALKER Shadow of Chernobyl"),
        Path(r"D:\SteamLibrary\steamapps\common\STALKER Shadow of Chernobyl"),
        Path(r"E:\SteamLibrary\steamapps\common\STALKER Shadow of Chernobyl"),
        Path(r"F:\SteamLibrary\steamapps\common\STALKER Shadow of Chernobyl"),
        Path(r"G:\SteamLibrary\steamapps\common\STALKER Shadow of Chernobyl"),
        Path(r"C:\GOG Games\S.T.A.L.K.E.R. Shadow of Chernobyl"),
    ]
    for c in candidates:
        if (c / "bin" / "xrGame.dll").is_file():
            return c
    return None


def inspect_status(game_dir: Path) -> dict:
    bin_dir = game_dir / "bin"
    res = {"game_dir": str(game_dir)}
    for fn, sec_name, magic_off, magic in [
        ("xrGame.dll", ".uwui", OFF_MAGIC, MAGIC_XRGAME),
        ("XR_3DA.exe", ".uwld", LD_OFF_MAGIC, MAGIC_XR3DA),
    ]:
        p = bin_dir / fn
        bak = bin_dir / (fn + ".orig.bak")
        info = {"exists": p.is_file(), "backup": bak.is_file(), "patched": False}
        if p.is_file():
            pe = PEImage(p.read_bytes())
            sec = pe.get_section(sec_name)
            if sec is not None:
                va = pe.ib + sec[1]
                tag = pe.read_va(va + magic_off, 8)
                info["patched"] = (tag == magic)
                info["magic"] = tag.decode("latin1", errors="replace")
                if fn == "xrGame.dll" and info["patched"]:
                    info["ui_aspect"] = struct.unpack("<f", pe.read_va(va + OFF_TARGET_ASPECT, 4))[0]
                    info["scope_aspect"] = struct.unpack("<f", pe.read_va(va + OFF_SCOPE_ASPECT, 4))[0]
                    info["menu_bars"] = bool(struct.unpack("<I", pe.read_va(va + OFF_MENU_BARS, 4))[0])
                    info["scope_bars"] = bool(struct.unpack("<I", pe.read_va(va + OFF_SCOPE_BARS, 4))[0])
                elif fn == "XR_3DA.exe" and info["patched"]:
                    info["load_screen"] = (pe.read_va(0x00412BFB, 5) != bytes.fromhex("e8 20 10 01 00"))
                    info["borderless"] = (pe.read_va(0x0048B591, 5) == bytes.fromhex("68 00 00 00 90"))
        res[fn] = info

    ltx = game_dir / "_appdata_" / "user.ltx"
    if ltx.is_file():
        txt = ltx.read_text(encoding="latin1", errors="replace")
        m_res = re.search(r"(?m)^vid_mode\s+(\S+)", txt)
        m_fs = re.search(r"(?m)^rs_fullscreen\s+(\S+)", txt)
        res["user_ltx"] = {
            "vid_mode": m_res.group(1) if m_res else None,
            "rs_fullscreen": m_fs.group(1) if m_fs else None,
        }
    return res


def update_user_ltx(game_dir: Path, res_str: str | None = None, fullscreen: bool | None = None):
    if res_str is not None and not re.fullmatch(r"\d+x\d+", res_str):
        raise ValueError(f"Invalid resolution format '{res_str}' (expected e.g. 5120x1440)")
    ltx = game_dir / "_appdata_" / "user.ltx"
    if not ltx.is_file():
        print(f"[!] Could not find {ltx} to update settings.")
        return
    bak = ltx.with_suffix(".ltx.bak")
    if not bak.exists():
        shutil.copy2(ltx, bak)
    text = ltx.read_text(encoding="latin1")
    parts = []
    if res_str is not None:
        if re.search(r"(?m)^vid_mode\s+", text):
            text = re.sub(r"(?m)^vid_mode\s+.*$", f"vid_mode {res_str}", text)
        else:
            text += f"\nvid_mode {res_str}\n"
        parts.append(f"vid_mode {res_str}")
    if fullscreen is not None:
        fs_val = "on" if fullscreen else "off"
        if re.search(r"(?m)^rs_fullscreen\s+", text):
            text = re.sub(r"(?m)^rs_fullscreen\s+.*$", f"rs_fullscreen {fs_val}", text)
        else:
            text += f"\nrs_fullscreen {fs_val}\n"
        parts.append(f"rs_fullscreen {fs_val}")
    if parts:
        ltx.write_text(text, encoding="latin1")
        print(f"[+] Updated {ltx}: {', '.join(parts)}")


def apply_patch(
    game_dir: Path,
    ui_aspect: float = 16.0 / 9.0,
    scope_aspect: float = 4.0 / 3.0,
    menu_bars: bool = True,
    scope_bars: bool = True,
    center_load: bool = True,
    borderless: bool = True,
):
    bin_dir = game_dir / "bin"
    xrgame_path = bin_dir / "xrGame.dll"
    xrgame_bak = bin_dir / "xrGame.dll.orig.bak"
    xr3da_path = bin_dir / "XR_3DA.exe"
    xr3da_bak = bin_dir / "XR_3DA.exe.orig.bak"

    if not xrgame_path.is_file():
        raise FileNotFoundError(f"xrGame.dll not found in {bin_dir}")

    # Create pristine backup if missing (always source from pristine backup on re-patch!)
    if not xrgame_bak.is_file():
        if PEImage(xrgame_path.read_bytes()).has_section(".uwui"):
            raise RuntimeError("xrGame.dll is already patched and xrGame.dll.orig.bak is missing!")
        shutil.copy2(xrgame_path, xrgame_bak)
        print(f"[+] Created backup: {xrgame_bak}")
    else:
        print(f"[*] Using existing backup as clean source: {xrgame_bak}")

    orig_game = xrgame_bak.read_bytes()
    patched_game, uwui_va, game_labels = patch_xrgame(
        orig_game, ui_aspect, scope_aspect, menu_bars, scope_bars
    )
    xrgame_path.write_bytes(patched_game)
    print(
        f"[+] Patched {xrgame_path} (.uwui @ 0x{uwui_va:08x}, {len(game_labels)} symbols, "
        f"UI={ui_aspect:.4f}, Scope={scope_aspect:.4f}, MenuBars={menu_bars}, ScopeBars={scope_bars})"
    )

    patch_exe = center_load or borderless
    if patch_exe and xr3da_path.is_file():
        if not xr3da_bak.is_file():
            if PEImage(xr3da_path.read_bytes()).has_section(".uwld"):
                raise RuntimeError("XR_3DA.exe is already patched and XR_3DA.exe.orig.bak is missing!")
            shutil.copy2(xr3da_path, xr3da_bak)
            print(f"[+] Created backup: {xr3da_bak}")
        else:
            print(f"[*] Using existing backup as clean source: {xr3da_bak}")

        orig_exe = xr3da_bak.read_bytes()
        patched_exe, uwld_va, exe_labels = patch_xr3da(
            orig_exe, center_load=center_load, borderless=borderless
        )
        xr3da_path.write_bytes(patched_exe)
        feats = []
        if center_load:
            feats.append("centered 4:3 loading screen")
        if borderless:
            feats.append("borderless fullscreen + DPI-aware")
        print(f"[+] Patched {xr3da_path} (.uwld @ 0x{uwld_va:08x}, {len(exe_labels)} symbols, {', '.join(feats)})")
    elif not patch_exe and xr3da_bak.is_file():
        shutil.copy2(xr3da_bak, xr3da_path)
        print(f"[*] Restored clean {xr3da_path} (--no-exe)")


def restore_backups(game_dir: Path):
    bin_dir = game_dir / "bin"
    restored = 0
    for fn in ("xrGame.dll", "XR_3DA.exe"):
        dst = bin_dir / fn
        bak = bin_dir / (fn + ".orig.bak")
        if bak.is_file():
            shutil.copy2(bak, dst)
            print(f"[+] Restored {dst} from {bak}")
            restored += 1
        else:
            print(f"[-] No backup found for {fn}")
    if restored == 0:
        print("[!] Nothing was restored.")


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("S.T.A.L.K.E.R. SoC 1.0006 — Ultrawide UI & Borderless Patcher")
    root.geometry("580x385")
    root.resizable(False, False)

    detected = find_game_dir()
    dir_var = tk.StringVar(value=str(detected) if detected else "")
    ui_var = tk.StringVar(value="16:9")
    scope_var = tk.StringVar(value="4:3")
    menu_bars_var = tk.BooleanVar(value=True)
    scope_bars_var = tk.BooleanVar(value=True)
    load_var = tk.BooleanVar(value=True)
    borderless_var = tk.BooleanVar(value=True)
    res_var = tk.StringVar(value="")

    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Game Directory:").grid(row=0, column=0, sticky="w")
    ttk.Entry(frm, textvariable=dir_var, width=54).grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 8))

    def browse():
        d = filedialog.askdirectory(title="Select STALKER Shadow of Chernobyl folder")
        if d:
            dir_var.set(d)

    ttk.Button(frm, text="Browse...", command=browse).grid(row=1, column=2, padx=(6, 0), pady=(2, 8))

    ttk.Label(frm, text="UI Canvas Aspect (recommended 16:9):").grid(row=2, column=0, sticky="w", pady=3)
    ttk.Combobox(frm, textvariable=ui_var, values=["16:9", "16:10", "4:3"], width=12).grid(row=2, column=1, sticky="w")

    ttk.Label(frm, text="Scope Aspect (4:3 vanilla/ZRP, 16:9 modded):").grid(row=3, column=0, sticky="w", pady=3)
    ttk.Combobox(frm, textvariable=scope_var, values=["4:3", "16:9"], width=12).grid(row=3, column=1, sticky="w")

    ttk.Checkbutton(frm, text="Black pillarboxes behind Main/Pause Menu", variable=menu_bars_var).grid(row=4, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Checkbutton(frm, text="Black pillarboxes around Sniper Scopes & Binoculars", variable=scope_bars_var).grid(row=5, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Checkbutton(frm, text="Center Loading Screen at 4:3 (patches XR_3DA.exe)", variable=load_var).grid(row=6, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Checkbutton(frm, text="Borderless Fullscreen when Full Screen is OFF (patches XR_3DA.exe)", variable=borderless_var).grid(row=7, column=0, columnspan=3, sticky="w", pady=2)

    ttk.Label(frm, text="Optional: Update user.ltx resolution (e.g. 5120x1440):").grid(row=8, column=0, sticky="w", pady=(8, 2))
    ttk.Entry(frm, textvariable=res_var, width=16).grid(row=8, column=1, sticky="w", pady=(8, 2))

    btn_row = ttk.Frame(frm)
    btn_row.grid(row=9, column=0, columnspan=3, pady=(16, 0))

    def on_patch():
        try:
            gd = Path(dir_var.get())
            apply_patch(
                gd,
                ui_aspect=parse_aspect(ui_var.get()),
                scope_aspect=parse_aspect(scope_var.get()),
                menu_bars=menu_bars_var.get(),
                scope_bars=scope_bars_var.get(),
                center_load=load_var.get(),
                borderless=borderless_var.get(),
            )
            if res_var.get().strip():
                update_user_ltx(gd, res_var.get().strip(), fullscreen=(not borderless_var.get()))
            messagebox.showinfo(
                "Success",
                "Ultrawide patch applied cleanly!\n"
                "Backups saved as *.orig.bak.\n\n"
                "Tip: With Borderless enabled, In-Game Full Screen OFF = Borderless Fullscreen, "
                "ON = Exclusive Fullscreen.",
            )
        except Exception as e:
            messagebox.showerror("Patch Failed", str(e))

    def on_restore():
        try:
            restore_backups(Path(dir_var.get()))
            messagebox.showinfo("Restored", "Original binaries restored from *.orig.bak.")
        except Exception as e:
            messagebox.showerror("Restore Failed", str(e))

    ttk.Button(btn_row, text="Apply Patch", command=on_patch).pack(side="left", padx=6)
    ttk.Button(btn_row, text="Restore Original Backups", command=on_restore).pack(side="left", padx=6)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(
        description="S.T.A.L.K.E.R.: Shadow of Chernobyl (1.0006) Ultrawide UI & Borderless Fullscreen Patcher"
    )
    parser.add_argument("--game-dir", type=Path, default=None, help="Path to STALKER Shadow of Chernobyl folder")
    parser.add_argument("--ui-aspect", default="16:9", help="Centered UI canvas aspect ratio (default: 16:9)")
    parser.add_argument("--scope-aspect", default="4:3", help="Weapon scope aspect ratio (default: 4:3 for vanilla/ZRP; use 16:9 for widescreen scope mods)")
    parser.add_argument("--no-menu-bars", action="store_true", help="Disable black pillarboxes on Main/Pause menu")
    parser.add_argument("--no-scope-bars", action="store_true", help="Disable black pillarboxes around Sniper Scopes/Binoculars")
    parser.add_argument("--no-load-screen", action="store_true", help="Skip XR_3DA.exe loading screen centering")
    parser.add_argument("--no-borderless", action="store_true", help="Skip XR_3DA.exe Borderless Fullscreen patch")
    parser.add_argument("--no-exe", action="store_true", help="Skip all XR_3DA.exe patches (both loading screen and borderless)")
    parser.add_argument("--set-res", default=None, help="Also update vid_mode in _appdata_/user.ltx (e.g. 5120x1440)")
    parser.add_argument("--fullscreen", choices=["on", "off"], default=None, help="Also set rs_fullscreen in user.ltx (off = Borderless when patched, on = Exclusive)")
    parser.add_argument("--restore", action="store_true", help="Restore original binaries from *.orig.bak")
    parser.add_argument("--status", action="store_true", help="Show current patch status")
    parser.add_argument("--gui", action="store_true", help="Launch graphical interface")

    args = parser.parse_args()
    if args.gui:
        run_gui()
        return

    game_dir = args.game_dir or find_game_dir()
    if game_dir is None or not (game_dir / "bin" / "xrGame.dll").is_file():
        print("[!] Could not locate STALKER Shadow of Chernobyl. Specify --game-dir PATH.")
        sys.exit(1)

    if args.status:
        st = inspect_status(game_dir)
        print(f"Game directory: {st['game_dir']}")
        for fn in ("xrGame.dll", "XR_3DA.exe"):
            i = st[fn]
            state = "PATCHED" if i["patched"] else "VANILLA"
            print(f"  {fn:12s}: {state} (backup={'YES' if i['backup'] else 'NO'})")
            if fn == "xrGame.dll" and i["patched"]:
                print(
                    f"    UI Aspect={i['ui_aspect']:.4f} | Scope Aspect={i['scope_aspect']:.4f} | "
                    f"MenuBars={i['menu_bars']} | ScopeBars={i['scope_bars']}"
                )
            elif fn == "XR_3DA.exe" and i["patched"]:
                print(
                    f"    LoadScreen={i['load_screen']} | Borderless={i['borderless']}"
                )
        if "user_ltx" in st:
            u = st["user_ltx"]
            fs = u["rs_fullscreen"]
            bl = st["XR_3DA.exe"].get("borderless", False)
            mode = (
                "Exclusive Fullscreen"
                if fs == "on"
                else ("Borderless Fullscreen" if bl else "Windowed")
                if fs == "off"
                else str(fs)
            )
            print(f"  user.ltx    : vid_mode={u['vid_mode']} | rs_fullscreen={fs} ({mode})")
        return

    if args.restore:
        restore_backups(game_dir)
        return

    center_load = (not args.no_exe) and (not args.no_load_screen)
    borderless = (not args.no_exe) and (not args.no_borderless)

    apply_patch(
        game_dir=game_dir,
        ui_aspect=parse_aspect(args.ui_aspect),
        scope_aspect=parse_aspect(args.scope_aspect),
        menu_bars=not args.no_menu_bars,
        scope_bars=not args.no_scope_bars,
        center_load=center_load,
        borderless=borderless,
    )

    if args.set_res is not None or args.fullscreen is not None:
        fs = None if args.fullscreen is None else (args.fullscreen == "on")
        update_user_ltx(game_dir, args.set_res, fullscreen=fs)


if __name__ == "__main__":
    main()
