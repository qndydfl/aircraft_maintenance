# IPC merged PDF pilot

The pilot is enabled per processed IPC package, not globally. Existing single PDF
uploads and packages without a merge manifest keep their existing behavior.

```sh
python manage.py merge_manual_package PACKAGE_ID
```

This creates a versioned PDF and atomically publishes `merged-package.json` in
the package extraction directory. Chapter primary-key order follows creation
order from index.html (or the existing sorted-file fallback). Source PDFs remain
available. The manifest records each chapter's start page and page count.

The viewer translates existing chapter-local links and search records into
merged page numbers. The workspace translates viewer notifications back to local
pages, so refresh and bookmarks still use existing chapter links. Byte-range
responses are authenticated and PDF.js uses range loading without automatic
whole-file prefetch.

Large merged outlines are not rendered all at once. Chapter outlines are read
from the source PDF on demand through the authenticated chapter endpoint and
cached in the viewer. This matters for the local B777 IPC pilot (package 3):
530 chapters, 117,763 pages, and 123,164 bookmarks. The generated PDF is
1,057,809,948 bytes. Initial generation does not re-index or change source PDFs.

After activation, re-upload/re-index uses the existing re-index worker to rebuild
the merged IPC automatically. It creates a new extraction directory and commits
chapter records only after merging and indexing finish. Previous extraction
directories are deliberately retained for recovery; plan disk capacity and
review them before manually removing obsolete versions. SQLite holds a write
transaction during this work: reads can continue with WAL, but other writes may
wait or time out. Schedule pilot re-indexing outside busy hours.

Only run one merge/re-index for a package at a time. Deploy code and restart the
web process and re-index worker before activating on the deployment host. Local
generated media is not transferred by Git. Verify RAM and disk capacity first:
a large source package, merged output and retained previous versions coexist.

To return to the original chapter viewer without deleting PDFs:

```sh
python manage.py merge_manual_package PACKAGE_ID --disable
```

Acceptance: chapter-boundary next/previous pages, selecting another chapter
without iframe reload, nested bookmarks, first/last search match wraparound,
workspace refresh, and initial load on the deployment network.
