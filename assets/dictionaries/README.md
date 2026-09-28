# Bundled spell-check dictionaries

The Hunspell dictionaries `engine.spellcheck` (EDT-11) checks against,
offline. PDFWorkerz reads them with [spylls](https://github.com/zverok/spylls),
a pure-Python Hunspell implementation, so no system Hunspell install is needed.

Currently: **en_US** (`en_US.aff`, `en_US.dic`), version 2020.12.07, derived
from [SCOWL](http://wordlist.sourceforge.net) at size 60. These are the
unmodified files from LibreOffice's dictionaries repository
(`https://github.com/LibreOffice/dictionaries/tree/master/en`). Their
copyright and permission notices (SCOWL, Ispell's BSD license, and the word
lists SCOWL draws on) are in `README_en_US.txt`, which must ship alongside
them.

To add a language, drop its `<lang>.aff`/`<lang>.dic` pair, plus its license,
into this directory; `engine.spellcheck.available_languages()` finds it.
