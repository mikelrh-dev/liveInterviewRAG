"""The first thing a reader sees, and the name they will repeat.

THREE NAMES, THIRTY SECONDS
---------------------------
A reader landing on this repository used to meet three products in under a
minute: the README heading said ``InterviewTTS``, the remote said
``liveInterviewRAG``, and the running application said ``MIKEL OS v2.0``
("Mission Control"). Nothing reconciled them, and the two that disagree are the
two a reader is most likely to repeat back -- the URL they clone and the title
in their browser tab.

This file pins ONE of them, and is explicit about which one it is not pinning.

What is unified on ``InterviewTTS``: the name the PRODUCT presents. The README
heading, and the application's own title, header and disclaimer footnote. Three
places that a reader can see at once, which is what made the drift visible.

What is NOT renamed: ``liveInterviewRAG``. It is the name of the remote, it
appears inside ``git clone`` URLs, and changing it in the prose would make the
documented command wrong. A clone directory is named after the repository and
not after the project -- the READMEs already say so where a reader is about to
run the command. A factual reference is not an inconsistency.

THE IMAGE, AND WHY "TRACKED" IS THE LOAD-BEARING WORD
-----------------------------------------------------
All three README images were external, on ``lh3.googleusercontent.com``. That
CDN answers 403 and 400 for them, so they render as broken images, and no test
could have told us because nothing in the repository referenced them at all.

The repository shipped its own banner, ``docs/hero-banner.png``: tracked, 29 KB,
and referenced by nothing. So the first correction points the README at it.

The word "tracked" is the assertion, not "exists". Pointing a README at a file
that is only on the author's disk reproduces, in the image path, exactly the
defect ``tests/real_wiki.py`` exists to prevent: a document that describes the
author's checkout rather than a clone's. So the path is resolved through
``git ls-files`` rather than through the filesystem.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO_ROOT / "frontend" / "index.html"
EXTERNAL_IMAGE_HOST = "lh3.googleusercontent.com"

#: The product name, as the three visible surfaces present it.
PRODUCT_NAME = "InterviewTTS"

#: The remote's name. Present in `git clone` URLs on purpose.
REPOSITORY_NAME = "liveInterviewRAG"


def _tracked(path: str) -> bool:
    """True when ``path`` is in the index, not merely on this disk."""
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _local_images(markdown: str) -> list[str]:
    return re.findall(r'<img[^>]+src="([^"]+)"', markdown)


class TestTheProductHasOneName:
    def test_the_document_title_is_the_product_name(self):
        html = _read(INDEX_HTML)
        match = re.search(r"<title>(.*?)</title>", html, re.DOTALL)

        assert match is not None, "frontend/index.html has no <title>."
        assert match.group(1).strip() == PRODUCT_NAME, (
            f"the browser tab says {match.group(1).strip()!r} while the README "
            f"heading says {PRODUCT_NAME!r}. The tab is what a reader sees after "
            "they clone and run it, so it is the surface where a second name "
            "does the most damage."
        )

    def test_the_visible_header_is_the_product_name(self):
        html = _read(INDEX_HTML)
        headings = re.findall(r'<h1[^>]*class="header-title"[^>]*>(.*?)</h1>', html, re.S)

        assert headings, (
            "the header <h1> no longer carries class=\"header-title\", so this "
            "guard cannot see the application's own name."
        )
        for heading in headings:
            assert heading.strip() == PRODUCT_NAME, (
                f"the application header says {heading.strip()!r}, not "
                f"{PRODUCT_NAME!r}."
            )

    def test_no_surface_still_carries_the_old_application_name(self):
        """The whole point: the old name must be GONE, not merely supplemented."""
        html = _read(INDEX_HTML)

        for stale in ("MIKEL OS", "Mikel OS", "Mission Control"):
            assert stale not in html, (
                f"frontend/index.html still contains {stale!r}. Three names in "
                "sixty seconds is the defect; adding the right one next to the "
                "wrong one leaves it in place."
            )

    def test_the_remote_name_is_left_alone(self):
        """A factual reference is not an inconsistency.

        Guarded in the OTHER direction on purpose. The temptation, once three
        names are unified, is to also rename the remote in the prose -- and that
        would break the documented ``git clone`` command, because the URL is a
        fact about where the code lives rather than a label.
        """
        for readme in ("README.md", "README_ES.md"):
            text = _read(REPO_ROOT / readme)
            assert f"{REPOSITORY_NAME}.git" in text, (
                f"{readme} no longer contains the real remote "
                f"{REPOSITORY_NAME}.git. If the remote was renamed, the clone "
                "command above this line is now wrong and the README has to say "
                "so explicitly -- do not quietly drop it."
            )


class TestTheReadmeShowsOnlyImagesThatExistInAClone:
    @pytest.mark.parametrize("readme", ["README.md", "README_ES.md"])
    def test_no_image_is_loaded_from_an_external_host(self, readme):
        text = _read(REPO_ROOT / readme)

        assert EXTERNAL_IMAGE_HOST not in text, (
            f"{readme} still loads an image from {EXTERNAL_IMAGE_HOST}. That CDN "
            "answers 403/400 for these URLs, so the README renders a broken image "
            "and nothing in the suite noticed -- an image nothing references "
            "cannot be caught by a test that only reads text. The repository "
            "ships its own banner in docs/."
        )

    @pytest.mark.parametrize("readme", ["README.md", "README_ES.md"])
    def test_every_image_path_is_a_file_the_clone_will_have(self, readme):
        images = _local_images(_read(REPO_ROOT / readme))
        assert images, (
            f"{readme} has no <img> at all. The banner is a tracked asset in "
            "docs/hero-banner.png; if it was removed on purpose, that is a "
            "decision to state rather than one to arrive at by accident."
        )

        for src in images:
            assert not src.startswith(("http://", "https://", "//")), (
                f"{readme} loads {src!r} from the network. See the external-host "
                "assertion in this class for why."
            )
            assert _tracked(src), (
                f"{readme} points an image at {src!r}, which is not in the git "
                "index. It exists on this machine and will 404 in every clone. "
                "This is the image-path version of the defect "
                "tests/real_wiki.py exists to prevent: a document describing the "
                "author's checkout instead of a clone's."
            )
