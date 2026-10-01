# Bundled dependencies

Pure-Python copies of the packages listed in `../requirements.txt`, unpacked
from their official wheels. They're bundled so that the Mac app starts with any
Python 3.9+ (including the one in Apple's Command Line Tools), without pip,
without a virtual environment, and without network access.

- **MarkupSafe:** the optional C speedup module (`_speedups`) was removed, so
  MarkupSafe uses its built-in pure-Python fallback.
- **No compiled packages:** nothing here needs a compiler. Encryption uses
  `admin/crypto.py`, which needs only the standard library.

To refresh, download the pinned wheels and unzip them here:

```sh
pip download --no-deps --only-binary=:all: --python-version 3.9 --platform macosx_11_0_arm64 -r requirements.txt -d /tmp/wheels
for w in /tmp/wheels/*.whl; do unzip -o -q "$w" -d vendor; done
rm -f vendor/markupsafe/_speedups.*
```
