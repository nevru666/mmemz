<!-- anchor: 4ba3a3a4 -->
# /reverse on mem0 v2.2.1 — the brief

`cd /home/mmonk/Downloads/shrimp && shrimp`, then type this at the `shrimp>` prompt
yourself:

```
/reverse /home/mmonk/Downloads/mem0-main(3)/mem0-main/mem0/memory/main.py
```

**Verdict: `/reverse` is the wrong instrument for this repo.** It routes a *file* by magic
bytes / `file -b` verdict / extension into one of 15 artefact kinds plus `website`. No
"python source tree" kind exists, so that command returns an `unknown` recon brief — a
finding, not an error. The repo root is refused as a directory, and `/reverse` is a **REPL
slash command, not an agent tool**. What fits is source-level recon (§4).

## 1. What the engine actually returned

`shrimp.reverse` calls from `/home/mmonk/Downloads/shrimp`; blocks are stdout byte-for-byte
(four-backtick fences, as two renders contain their own ``` fence). Block 1 — the Python
file, verdict `unknown`: `python -c "import shrimp.reverse as r;
print(r.render_brief('/home/mmonk/Downloads/mem0-main(3)/mem0-main/mem0/memory/main.py'))"`

````text
## Reverse-engineering brief: /home/mmonk/Downloads/mem0-main(3)/mem0-main/mem0/memory/main.py

Routed kind: **unknown** (from `file -b /home/mmonk/Downloads/mem0-main(3)/mem0-main/mem0/memory/main.py` + magic bytes, not from the extension).

### Fastest paths for this kind
No command row exists for kind `unknown` — do not invent one. Re-route the target with `file -b T` plus the magic bytes, or pass `--kind <kind>` once you know it. With no row the honest inventory is: `file -b T`, `strings -a -n 6 T | head -200`, `wc -c T`.

### Tools this host actually has
PRESENT: awk, basename, cargo, curl, cut, dd, file, find, grep, head, jq, ls, mkdir, nm, npx, objdump, python3, readelf, rm, sed, sort, sqlite3, strings, tail, tr, unzip, wc
MISSING: apktool, binwalk, codesign, ghidra, go, ilspycmd, jadx, lipo, otool, r2, rg, tshark, unblob, wasm-objdump
A missing tool is not a blocker: use the fallback named in the row above. Do not
`brew install` anything without asking me first.

### The Rules (they are the value; the commands above are just the fastest examples)
- Work on a copy. Put each extraction in a new dir under `$TMPDIR/re/<name>`. Reuse an
  extraction if one exists.
- Route by `file -b T` and the magic bytes first. Run the cheapest inventory next. Only
  then decompile or read code.
- Never `cat` a big or minified file. Slice it: `grep -oE '.{0,200}NEEDLE.{0,200}' F | head`.
  Dump a disassembly once to a file, then grep that file.
- **Batch independent probes into ONE shell call** with `;` and `echo '--- name'` markers
  (quoted — a bare `==x` is expanded by some shells). Cap every output with `head` or `-m`.
- **Answer from evidence.** In your answer, mark every claim as **seen** (cite
  file:offset, or a symbol/string) or **inferred**. A fact you did not find is
  **unknown**, not "absent". Static reading is not observed execution.
- Turns cost more than bytes. Aim for 2–4 tool calls: one batched recon call, one or two
  targeted slices, then answer. Stop when every item you were asked has evidence. Do
  not re-verify what you already saw.

### Acceptance before you answer
Every item I asked about carries a seen/inferred/unknown tag; every `seen` claim cites a
file:offset, symbol or string I can check; unknowns are named as unknown rather than
described as absent; and you have not claimed to have observed anything you only read
statically. If a required tool was missing and no fallback applied, say so explicitly
instead of answering around the gap.

Routed by **extension**: no magic, no 'file -b' verdict and no known extension matched /home/mmonk/Downloads/mem0-main(3)/mem0-main/mem0/memory/main.py.

No command row was emitted for this kind (see the note above); the acceptance paragraph still applies.
````

Block 2 — a file in the same repo that does route, verdict `js-bundle`: `python -c "import
shrimp.reverse as r;
print(r.render_brief('/home/mmonk/Downloads/mem0-main(3)/mem0-main/examples/yt-assistant-chrome/src/content.js'))"`

````text
## Reverse-engineering brief: /home/mmonk/Downloads/mem0-main(3)/mem0-main/examples/yt-assistant-chrome/src/content.js

Routed kind: **js-bundle** (from `file -b /home/mmonk/Downloads/mem0-main(3)/mem0-main/examples/yt-assistant-chrome/src/content.js` + magic bytes, not from the extension).

### Fastest paths for this kind
```sh
F=./bundle.js; echo '--- MAP'; grep -oE 'sourceMappingURL=[^ ]+' $F | head -3; echo '--- PRETTY'; npx -y prettier --parser babel $F > $F.pretty.js 2>/dev/null; wc -l $F.pretty.js; echo '--- SLICE'; grep -oE '.{0,200}NEEDLE.{0,200}' $F | head -20
```
If the map has ``sourcesContent``: ``jq -r '.sources[]' map.json``. For webpack, grep the module ids then slice around the hit. Never ``cat`` a minified file: it is one line and will blow the context.

### Tools this host actually has
PRESENT: awk, basename, cargo, curl, cut, dd, file, find, grep, head, jq, ls, mkdir, nm, npx, objdump, python3, readelf, rm, sed, sort, sqlite3, strings, tail, tr, unzip, wc
MISSING: apktool, binwalk, codesign, ghidra, go, ilspycmd, jadx, lipo, otool, r2, rg, tshark, unblob, wasm-objdump
A missing tool is not a blocker: use the fallback named in the row above. Do not
`brew install` anything without asking me first.

### The Rules (they are the value; the commands above are just the fastest examples)
- Work on a copy. Put each extraction in a new dir under `$TMPDIR/re/<name>`. Reuse an
  extraction if one exists.
- Route by `file -b T` and the magic bytes first. Run the cheapest inventory next. Only
  then decompile or read code.
- Never `cat` a big or minified file. Slice it: `grep -oE '.{0,200}NEEDLE.{0,200}' F | head`.
  Dump a disassembly once to a file, then grep that file.
- **Batch independent probes into ONE shell call** with `;` and `echo '--- name'` markers
  (quoted — a bare `==x` is expanded by some shells). Cap every output with `head` or `-m`.
- **Answer from evidence.** In your answer, mark every claim as **seen** (cite
  file:offset, or a symbol/string) or **inferred**. A fact you did not find is
  **unknown**, not "absent". Static reading is not observed execution.
- Turns cost more than bytes. Aim for 2–4 tool calls: one batched recon call, one or two
  targeted slices, then answer. Stop when every item you were asked has evidence. Do
  not re-verify what you already saw.

### Acceptance before you answer
Every item I asked about carries a seen/inferred/unknown tag; every `seen` claim cites a
file:offset, symbol or string I can check; unknowns are named as unknown rather than
described as absent; and you have not claimed to have observed anything you only read
statically. If a required tool was missing and no fallback applied, say so explicitly
instead of answering around the gap.

Routed by **file**: file -b: JavaScript source, ASCII text.
````

Block 3 — the host tool table: `python -c "import shrimp.reverse as r;
print(r.render_tools_text())"`

````text
PRESENT (27):
  awk              /usr/bin/awk
  basename         /usr/bin/basename
  cargo            /home/mmonk/.cargo/bin/cargo
  curl             /usr/bin/curl
  cut              /usr/bin/cut
  dd               /usr/bin/dd
  file             /usr/bin/file
  find             /usr/bin/find
  grep             /usr/bin/grep
  head             /usr/bin/head
  jq               /usr/bin/jq
  ls               /usr/bin/ls
  mkdir            /usr/bin/mkdir
  nm               /usr/bin/nm
  npx              /home/mmonk/.local/share/pi-node/node-v22.23.2-linux-x64/bin/npx
  objdump          /usr/bin/objdump
  python3          /home/mmonk/miniconda3/bin/python3
  readelf          /usr/bin/readelf
  rm               /usr/bin/rm
  sed              /usr/bin/sed
  sort             /usr/bin/sort
  sqlite3          /home/mmonk/miniconda3/bin/sqlite3
  strings          /usr/bin/strings
  tail             /usr/bin/tail
  tr               /usr/bin/tr
  unzip            /usr/bin/unzip
  wc               /usr/bin/wc
MISSING (14):
  apktool          fallback: unzip -p a.apk AndroidManifest.xml gives binary AXML: decode it with a short python3 AXML string-pool reader; resources stay unzip -o.
  binwalk          fallback: no signature scanner: python3 walk the file in 1 MiB chunks recording every offset where file -b on the slice stops saying 'data'.
  codesign         fallback: no signature/entitlement check on Linux; entitlements often survive as readable XML: strings -a T | grep -A5 -i entitlements.
  ghidra           fallback: objdump -d -M intel T > dis.txt plus the --defined-only export list; pseudocode is not available, so answer in assembly and strings.
  go               fallback: strings -a T | grep -m1 -A200 'Go buildinf', then strings -a T | grep -m50 -E '^main\.' for symbols.
  ilspycmd         fallback: strings -a T.dll reads the #Strings/#US heaps and names types, methods and P/Invoke targets; a real decompile needs the Windows row.
  jadx             fallback: unzip -o a.apk -d X, then read classes*.dex through strings -a or a python3 DEX string-table walk; no Java decompiler on this host.
  lipo             fallback: python3: fat header is magic cafebabe, '>I' arch count, then 20-byte entries; slicing a thin image needs a manual dd.
  otool            fallback: Apple-only, and readelf/objdump cannot read Mach-O: parse the load commands with python3 (struct.unpack from offset 32), read code with strings -a. file -b T gives the arch.
  r2               fallback: objdump -d -M intel T > dis.txt then slice dis.txt; nm -D -u plus strings usually answer first.
  rg               fallback: grep -rn --binary-files=text PATTERN DIR and grep -oE '.{0,200}NEEDLE.{0,200}' F | head. NOTE: grep -r has no --glob; exclude noise with --exclude-dir=node_modules.
  tshark           fallback: python3 walk of the 24-byte pcap header plus per-record struct.unpack('<IIII', ...), and strings -a C | grep -E '\.(com|org|net|io)' | sort -u as the cheap first cut.
  unblob           fallback: binwalk -e if installed, else dd-carve at the offsets from the python3 scan and route each slice back through this table.
  wasm-objdump     fallback: strings -a X.wasm | head -200 for imports/exports, plus a python3 LEB128 walk of the section table (magic \0asm, then id/size pairs).
````

## 2. Why: it routes on magic bytes, not on being source

`classify_target` (`shrimp/reverse.py:329-369`) ladders: URL scheme → `website`; **magic
bytes** (`classify_bytes`, `:224`, sniffed with `filename=None` so no suffix preempts it);
**`file -b`** verdict, substring-matched against `_KIND_BY_FILE_PHRASE` (`:118-141`);
**extension** last, via `_KIND_BY_EXTENSION` (`:101-114`); else `unknown`. In full — `.py`
and `.json` both absent: `elf`, `pe`, `macho`, `apk`, `asar`, `wasm`, `sqlite`, `pcap`,
`plist`, `js-bundle` (`.js .mjs .cjs`), `zip`, `html` (`.html .htm`).

**No kind exists for source code of any language.** A `.py` file has no magic; `file -b`
says `Python script, Unicode text, UTF-8 text executable`, matching no phrase; `.py` has no
extension row — fall-through to `unknown`, hence block 1. The engine is honest ("No command
row exists for kind `unknown` — do not invent one"), but an artefact router answers "how do
I take this binary apart", not "how is this source organised"; it can give you no module
map and nothing about `Memory.add`. `--kind` short-circuits the ladder (`:794-804`), so
`--kind zip` on a `.py` renders the zip row — don't; that is a plan for the wrong artefact.

## 3. Routing facts, re-verified at write time

**(a) `.py` → `unknown`.** `classify_target` on `mem0/memory/main.py` (169,881 bytes) gives
`kind='unknown'`, `via='extension'`, reason `no magic, no 'file -b' verdict and no known
extension matched /home/mmonk/Downloads/mem0-main(3)/mem0-main/mem0/memory/main.py` — the
literal fall-through string at `:366-369`; same for `mem0/client/main.py` (93,023 bytes).

**(b) Directory refused before the engine runs** (`_reverse_target_error`,
`shrimp/cli.py:5745`, branch `:5761`; mirrored `shrimp/reverse.py:353`):

```text
/home/mmonk/Downloads/mem0-main(3)/mem0-main is a directory, not a file; point /reverse at a file inside it
```

**(c) REPL-only.** Dispatch `shrimp/cli.py:6193` `elif cmd == "/reverse":` →
`_reverse_command` (`:5767`), same dispatcher as `/goal`; nothing under
`shrimp/tools/builtin/`. Grammar (`_parse_reverse_arg`, `:5699-5744`): `/reverse <target>
[--kind <kind>] [--list-tools] [--json]`, one target only, `--list-tools` alone prints
block 3. `/quit` to leave.

## 4. Next step

**Offer: source-level architecture recon of mem0's add/search pipeline** —
`mem0/memory/main.py` (3912 lines) and `mem0/client/main.py` (2313), in 421 `.py` files.
Located already: `class Memory(MemoryBase)` 487, `.add` 760, `_add_to_vector_store` 881,
`.search` 1393, `_create_memory` 1975, async twins 2556/3671; client `class MemoryClient`
227, `.add`/`.search` 321/436. `Memory.add` (OSS) and `MemoryClient.add` (HTTP) are separate
implementations of one verb, and `main.py` holds sync *and* async copies — every edge needs
an OSS/hosted and a sync/async label. Accept when: provider families counted without reading
them; `add` → `_add_to_vector_store` → LLM extraction → `_create_memory` → vector/history/
entity store and `search` → embed → vector search → rerank → filter graphed; client path
matched call-for-call; file:line citations tagged seen vs inferred.

### What does route (confirmed with `classify_target`; all exist on disk)

`js-bundle` — `examples/yt-assistant-chrome/src/`: `content.js` (22247, `file -b: JavaScript
source`), `background.js` (7720), `options.js` (12249), `popup.js` (7842); plus
`cli/node/telemetry-sender.cjs` (3812), `server/dashboard/next.config.mjs` (943),
`examples/mem0-demo/{postcss,eslint}.config.mjs` (135/393). `html` by magic (text opens
`<html>`, `:290-291`), not suffix: `…/public/popup.html` (6023), `…/public/options.html`
(6385), `examples/vercel-ai-sdk-chat-app/index.html` (378). Relative to
`/home/mmonk/Downloads/mem0-main(3)/mem0-main/`. Type:

```text
/reverse /home/mmonk/Downloads/mem0-main(3)/mem0-main/examples/yt-assistant-chrome/src/content.js
/reverse /home/mmonk/Downloads/mem0-main(3)/mem0-main/examples/yt-assistant-chrome/public/popup.html
```

**What does not route:** every `.py` file (both entry modules) and **every JSON file** —
`docs/openapi.json` (339,707), `cli/cli-spec.json` (25,832), `manifest.json`, `package.json`.
Correcting the brief I was handed: `docs/openapi.json` does **not** reach `js-bundle` —
`file -b` prints `JSON text data` (file-5.45, detecting it fine) while the phrase row reads
`json data`, a plain substring (`:118-141`) of `json text data`, so it cannot fire; `.json`
has no extension row either, so `classify_target` says `unknown`/`extension`. `--kind
js-bundle` forces it (`:794-804`).

### Re-checking any claim

Blocks were captured by shell redirect, spliced byte-for-byte, then re-run and diffed at
sign-off. The probe behind every routing claim here (`python -c`, from
`/home/mmonk/Downloads/shrimp`) — run it to re-confirm a `kind`, `via` or `reason`:

```sh
python -c "
import shrimp.reverse as r
for t in ['/home/mmonk/Downloads/mem0-main(3)/mem0-main/mem0/memory/main.py',
          '/home/mmonk/Downloads/mem0-main(3)/mem0-main/docs/openapi.json',
          '/home/mmonk/Downloads/mem0-main(3)/mem0-main/examples/yt-assistant-chrome/src/content.js']:
    k = r.classify_target(t); print(k.kind, '|', k.via, '|', k.reason)
"
```

Every byte count and line number above came from a call made while writing this file.
