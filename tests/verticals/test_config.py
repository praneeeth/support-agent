"""Loading a vertical. A bad config must fail at startup, not at the first customer message."""

from pathlib import Path

import pytest

from app.verticals.config import Business, VerticalNotFound, load_vertical

NORTHWIND = load_vertical("northwind")


def test_northwind_loads_from_the_repo() -> None:
    assert NORTHWIND.id == "northwind"
    assert NORTHWIND.business.name == "Northwind Goods"
    assert NORTHWIND.business.currency_symbol == "₹"
    assert "get_order_status" in NORTHWIND.tools


def test_docs_dir_resolves_inside_the_vertical_folder() -> None:
    docs = Path(NORTHWIND.docs_dir)
    assert docs.is_dir()
    assert docs.parent.name == "northwind"
    assert list(docs.glob("*.md")), "the launch vertical ships its own documents"


def test_business_describes_itself_for_the_prompt() -> None:
    described = NORTHWIND.business.described
    assert described == "Northwind Goods, an online home-goods store based in Pune, India"


def test_a_business_without_a_location_still_reads_well() -> None:
    business = Business(name="Acme", kind="a hardware shop")
    assert business.described == "Acme, a hardware shop"


def test_missing_vertical_names_the_ones_that_exist(tmp_path: Path) -> None:
    (tmp_path / "seaside").mkdir()
    (tmp_path / "seaside" / "vertical.yaml").write_text(
        "business:\n  name: Seaside\n  kind: a homestay\n", encoding="utf-8"
    )
    with pytest.raises(VerticalNotFound) as caught:
        load_vertical("nope", root=tmp_path)
    assert "seaside" in str(caught.value)


def test_a_second_vertical_needs_no_code(tmp_path: Path) -> None:
    """The whole point: a new customer is a folder, not a branch."""
    folder = tmp_path / "seaside"
    (folder / "docs").mkdir(parents=True)
    (folder / "docs" / "house-rules.md").write_text("# House rules\n", encoding="utf-8")
    (folder / "vertical.yaml").write_text(
        "business:\n"
        "  name: Seaside Homestay\n"
        "  kind: a three-room guest house\n"
        "  location: Gokarna, India\n"
        "  reference_name: booking reference\n"
        "tools: [check_availability]\n",
        encoding="utf-8",
    )
    config = load_vertical("seaside", root=tmp_path)
    assert config.id == "seaside"
    assert config.business.reference_name == "booking reference"
    assert config.business.currency_symbol == "₹"  # sensible default
    assert Path(config.docs_dir).is_dir()


@pytest.mark.parametrize("body", ["[]", "- a\n- b", "just a string"])
def test_a_config_that_is_not_a_mapping_is_rejected(tmp_path: Path, body: str) -> None:
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "vertical.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_vertical("broken", root=tmp_path)


def test_a_config_missing_the_business_is_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()
    (folder / "vertical.yaml").write_text("tools: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_vertical("empty", root=tmp_path)


def test_an_id_that_could_escape_the_folder_is_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "sneaky"
    folder.mkdir()
    (folder / "vertical.yaml").write_text(
        'id: "../../etc"\nbusiness:\n  name: X\n  kind: y\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="slug"):
        load_vertical("sneaky", root=tmp_path)
