"""Pure HWH parameter reader used by the Stage B-1 launcher."""
from pathlib import Path
import xml.etree.ElementTree as ET


def read_threshold(hwh_path: str | Path) -> int:
    """Return the sole integer THRESHOLD parameter from a Vivado HWH file."""
    root = ET.parse(hwh_path).getroot()
    parameters = root.findall(".//PARAMETER[@NAME='THRESHOLD']")
    if len(parameters) != 1:
        raise ValueError(
            f"expected exactly one THRESHOLD parameter in {hwh_path}; found {len(parameters)}"
        )
    raw_value = parameters[0].get("VALUE")
    if raw_value is None:
        raise ValueError(f"THRESHOLD parameter has no VALUE attribute in {hwh_path}")
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"THRESHOLD value is not an integer: {raw_value!r}") from exc
