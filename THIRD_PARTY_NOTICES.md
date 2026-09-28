# Third-party notices

The Glosswork runtime image redistributes third-party work, and both licences involved
require their notices to travel with any copy. FSL-1.1-ALv2's Redistribution clause says
that if you redistribute copies, modifications or derivatives of the Software you must
include a copy of or a link to its terms and not remove any copyright notices provided
in or with the Software. OFL-1.1 requires the copyright notice and the licence to be
distributed with the font software. Publishing an image is redistribution, and the image
contains both the application and the fonts, so `LICENSE` and this file are copied into
it at `/app/`.

## What this file covers, and what it does not

**It covers four works: the three fonts bundled into the web build, and the embedding
model baked into the image.**

**It is not a complete inventory of everything the image redistributes.** The image also
carries 70 Python packages under `/opt/venv`, several of them Apache-2.0 with a notice
clause of its own, and a Vite bundle under `/app/web/dist` built from `react`,
`react-dom`, `@tanstack/react-query`, `@tanstack/react-table`, `react-router-dom`,
`react-markdown` and `remark-breaks` with their transitive dependencies. The argument
above applies to every one of them. Covering them means generating the list from
`uv.lock` and `web/package-lock.json` rather than maintaining it by hand, which is
tracked separately and is not done here. A notices file that looks complete and is not
would be worse than one that says what it covers, so this says it.

## Where each text came from

Each licence below was downloaded from the source named here and is reproduced verbatim.
The digests are of the files as fetched on 2026-09-20 and re-fetched on 2026-09-23.

| Work | Source of the licence text | sha256 of the fetched file |
| --- | --- | --- |
| Bricolage Grotesque | `https://raw.githubusercontent.com/ateliertriay/bricolage/main/OFL.txt` | `4b5a7d8f37f5602621c8a8d7358a6a2e71317e6c231c661e15aef0275d3e07ba` |
| Figtree | `https://raw.githubusercontent.com/erikdkennedy/figtree/master/OFL.txt` | `140d37233e7f3ce7313798befa9600893bcceaf41a55fa0fa5ad52f7f657a268` |
| DM Mono | `https://raw.githubusercontent.com/google/fonts/main/ofl/dmmono/OFL.txt` | `2bada5ea45c3c63b7f1ea1f88ce9672c9e4f0c42b2c3b7378949084fe55a3066` |
| BAAI/bge-small-en-v1.5 | `https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE` | `587a673933425dbc36ec61268d3b954051b2d3ef3c9b322ede357976055ffdd5` |

DM Mono's own project repository carries no licence file; the canonical text is the
Google Fonts catalogue entry above, which is also where the bundled package's copy comes
from.

## The three bundled fonts

Bricolage Grotesque, Figtree and DM Mono are installed from the `@fontsource` packages
pinned in `web/package.json` and compiled into `web/dist`, which the image copies to
`/app/web/dist`. They are never fetched at runtime. Each package declares `OFL-1.1`.

The `@fontsource` builds are subset and converted to woff2, which makes them Modified
Versions under the licence. None of the three upstream files reserves a font name: the
phrase "Reserved Font Name" appears only inside clause 3's body and after no copyright
statement. So the names may be kept, and reproducing the copyright notice and the licence
is the whole obligation.

### Copyright notices

```
Copyright 2022 The Bricolage Grotesque Project Authors (https://github.com/ateliertriay/bricolage)

Copyright 2022 The Figtree Project Authors (https://github.com/erikdkennedy/figtree)

Copyright 2020 The DM Mono Project Authors (https://www.github.com/googlefonts/dm-mono)
```

### SIL Open Font License, Version 1.1

All three are licensed under the same text, so it appears once. Transcribed from the
Bricolage Grotesque file above. Figtree's and DM Mono's copies differ from it in one
character each, writing `http://scripts.sil.org/OFL` where this writes `https://` in the
informational line pointing at the FAQ. No clause differs between the three.

```
This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
https://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```

## BAAI/bge-small-en-v1.5, the embedding model

The image bakes in three files from this model at revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, fetched from
`https://huggingface.co/BAAI/bge-small-en-v1.5` and verified by the `Dockerfile` against
these digests:

| File | sha256 |
| --- | --- |
| `onnx/model.onnx` | `828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35` |
| `tokenizer.json` | `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66` |
| `config.json` | `094f8e891b932f2000c92cfc663bac4c62069f5d8af5b5278c4306aef3084750` |

**Every fact in this entry is attributed, because the upstream position is less tidy than
it looks.** The model repository carries **no licence file** at that revision: the raw
URL for `LICENSE` returns 404. Its model card declares `license: mit` in its front
matter, and its License section reads, in full: "FlagEmbedding is licensed under the MIT
License. The released models can be used for commercial purposes free of charge." That
sentence is about FlagEmbedding, which is the code repository, not the weights, and the
model itself is published by BAAI.

So: the card designates MIT, and the MIT text reproduced below is the one the card points
at, taken from FlagEmbedding's repository. **The copyright line in it is FlagEmbedding's,
not a copyright line the model states for itself**, and it is reproduced because it is
part of the licence text the card designates. Nothing here asserts a copyright holder for
the model weights, because no upstream file states one.

### The copyright line carried by the designated licence text

```
Copyright (c) 2022 staoxiao
```

### MIT License

Transcribed from FlagEmbedding's `LICENSE`, the file the model card links.

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
