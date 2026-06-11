"""
compile_locales — compile .po translation catalogs to .mo binaries.

Pure-Python msgfmt implementation so this works on Windows hosts where
GNU gettext is not installed. Replaces the old itambox/compile_locale.py
standalone script.

Usage:
    manage.py compile_locales            # compile all locales
    manage.py compile_locales de         # compile a specific locale
"""

import array
import ast
import codecs
import os
import struct
from email.parser import HeaderParser
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _compile_po(po_path: str, mo_path: str) -> None:
    """Compile a single .po file to a .mo file (pure Python, no GNU gettext required)."""
    messages: dict = {}

    def _add(ctxt, msgid, msgstr, fuzzy):
        if not fuzzy and msgstr:
            key = b"%b\x04%b" % (ctxt, msgid) if ctxt is not None else msgid
            messages[key] = msgstr

    ID, STR, CTXT = 1, 2, 3
    section = msgctxt = None
    msgid = msgstr = b''
    fuzzy = 0
    is_plural = False
    encoding = 'latin-1'

    with open(po_path, 'rb') as fh:
        lines = fh.readlines()

    if lines and lines[0].startswith(codecs.BOM_UTF8):
        raise CommandError(f"{po_path} starts with a UTF-8 BOM — remove it and retry.")

    for lno, raw in enumerate(lines, 1):
        l = raw.decode(encoding)
        if l[0] == '#' and section == STR:
            _add(msgctxt, msgid, msgstr, fuzzy)
            section = msgctxt = None
            fuzzy = 0
        if l[:2] == '#,' and 'fuzzy' in l:
            fuzzy = 1
        if l[0] == '#':
            continue
        if l.startswith('msgctxt'):
            if section == STR:
                _add(msgctxt, msgid, msgstr, fuzzy)
            section = CTXT
            l = l[7:]
            msgctxt = b''
        elif l.startswith('msgid') and not l.startswith('msgid_plural'):
            if section == STR:
                if not msgid:
                    msgstr = b''.join(
                        line for line in msgstr.splitlines(True)
                        if not line.startswith(b'POT-Creation-Date:')
                    )
                    charset = HeaderParser().parsestr(msgstr.decode(encoding)).get_content_charset()
                    if charset:
                        encoding = charset
                _add(msgctxt, msgid, msgstr, fuzzy)
                msgctxt = None
            section = ID
            l = l[5:]
            msgid = msgstr = b''
            is_plural = False
        elif l.startswith('msgid_plural'):
            if section != ID:
                raise CommandError(f"msgid_plural not preceded by msgid on {po_path}:{lno}")
            l = l[12:]
            msgid += b'\0'
            is_plural = True
        elif l.startswith('msgstr'):
            section = STR
            if l.startswith('msgstr['):
                if not is_plural:
                    raise CommandError(f"plural without msgid_plural on {po_path}:{lno}")
                l = l.split(']', 1)[1]
                if msgstr:
                    msgstr += b'\0'
            else:
                if is_plural:
                    raise CommandError(f"indexed msgstr required for plural on {po_path}:{lno}")
                l = l[6:]
        l = l.strip()
        if not l:
            continue
        l = ast.literal_eval(l)
        if section == CTXT:
            msgctxt += l.encode(encoding)
        elif section == ID:
            msgid += l.encode(encoding)
        elif section == STR:
            msgstr += l.encode(encoding)
        else:
            raise CommandError(f"Syntax error on {po_path}:{lno}: {l!r}")

    if section == STR:
        _add(msgctxt, msgid, msgstr, fuzzy)

    keys = sorted(messages.keys())
    offsets = []
    ids = strs = b''
    for key in keys:
        offsets.append((len(ids), len(key), len(strs), len(messages[key])))
        ids += key + b'\0'
        strs += messages[key] + b'\0'
    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets, voffsets = [], []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    output = struct.pack("Iiiiiii", 0x950412de, 0, len(keys), 7 * 4, 7 * 4 + len(keys) * 8, 0, 0)
    output += array.array("i", koffsets + voffsets).tobytes()
    output += ids
    output += strs

    with open(mo_path, 'wb') as fh:
        fh.write(output)


class Command(BaseCommand):
    help = "Compile .po translation catalogs to .mo binaries (pure Python, no GNU gettext required)."

    def add_arguments(self, parser):
        parser.add_argument(
            'locales', nargs='*', metavar='LOCALE',
            help='Locale codes to compile (default: all discovered locales)',
        )

    def handle(self, *args, **options):
        locale_root = Path(settings.BASE_DIR) / 'locale'
        if not locale_root.exists():
            raise CommandError(f"Locale directory not found: {locale_root}")

        requested = set(options['locales'])
        compiled = 0
        for po_file in sorted(locale_root.glob('*/LC_MESSAGES/django.po')):
            locale_code = po_file.parts[-3]
            if requested and locale_code not in requested:
                continue
            mo_file = po_file.with_suffix('.mo')
            try:
                _compile_po(str(po_file), str(mo_file))
                self.stdout.write(self.style.SUCCESS(f"  compiled {po_file.relative_to(locale_root.parent)}"))
                compiled += 1
            except Exception as exc:
                raise CommandError(f"Failed to compile {po_file}: {exc}") from exc

        if compiled == 0:
            self.stdout.write(self.style.WARNING("No .po files found to compile."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done — {compiled} catalog(s) compiled."))
