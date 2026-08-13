"""Routing rules — what SnapDox offers, and what it refuses to offer."""

from __future__ import annotations

import pytest

from snapdox.errors import UnknownFormat, UnsupportedPair
from snapdox.formats import FORMATS, normalize
from snapdox.registry import resolve, targets_for


def test_direct_route_is_preferred():
    route = resolve("docx", "pdf")
    assert route.is_direct
    assert route.engines == ("libreoffice",)


@pytest.mark.parametrize(
    "src,dst,hops",
    [
        ("md", "pdf", ("md", "docx", "pdf")),
        ("pptx", "png", ("pptx", "pdf", "png")),
        ("svg", "png", ("svg", "pdf", "png")),
        ("pdf", "odt", ("pdf", "docx", "odt")),
        ("epub", "pdf", ("epub", "docx", "pdf")),
    ],
)
def test_two_hop_chains(src, dst, hops):
    route = resolve(src, dst)
    assert not route.is_direct
    assert (route.edges[0].src, route.edges[0].dst, route.edges[1].dst) == hops


def test_unsupported_pair_names_alternatives():
    with pytest.raises(UnsupportedPair) as exc:
        resolve("png", "xlsx")
    assert "png" in exc.value.message
    assert "pdf" in exc.value.hint  # suggests what png *can* become


def test_unknown_format_rejected():
    with pytest.raises(UnknownFormat):
        resolve("docx", "xyz")


def test_resolving_an_unsupported_pair_does_not_recurse():
    """resolve() builds its hint from the target list; that must not re-enter."""
    for src in FORMATS:
        targets_for(src)  # would blow the stack if the two called each other


def test_aliases_fold_onto_canonical_names():
    assert normalize(".JPEG") == "jpg"
    assert normalize("htm") == "html"
    assert resolve("jpeg", "png").is_direct


def test_images_are_not_routed_into_text_extraction():
    """png -> pdf -> docx would always fail, so it must not be offered."""
    png = targets_for("png")
    assert "txt" not in png
    assert "md" not in png
    # ...but a real image-into-Word conversion is offered, and directly.
    assert png["docx"].is_direct


def test_same_format_is_rejected():
    with pytest.raises(UnsupportedPair):
        resolve("pdf", "pdf")


def test_every_offered_target_is_a_known_format():
    for src in FORMATS:
        for target in targets_for(src):
            assert target in FORMATS
