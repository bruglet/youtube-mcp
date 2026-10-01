from pathlib import Path
import xml.etree.ElementTree as ET


def normalize_srv3_windows(path: Path) -> None:
    """End windowed captions when replaced, preserving independent styled cues."""
    tree = ET.parse(path)
    body = tree.getroot().find("body")
    if body is None:
        return
    previous: dict[str, ET.Element] = {}
    changed = False

    def end_previous(window: str, timestamp: int) -> None:
        nonlocal changed
        cue = previous.pop(window, None)
        if cue is None:
            return
        start = int(cue.get("t", "0"))
        duration = int(cue.get("d", "5000"))
        if start <= timestamp < start + duration:
            cue.set("d", str(timestamp - start))
            changed = True

    for element in list(body):
        if element.tag == "w" and element.get("id") is not None:
            end_previous(element.attrib["id"], int(element.get("t", "0")))
        elif element.tag == "p" and element.get("w") is not None:
            if element.get("a") == "1":
                # YouTube's rolling captions append a newline between replacement
                # cues. It is a window command, not an independently visible cue.
                if not "".join(element.itertext()).strip():
                    body.remove(element)
                    changed = True
                continue
            window = element.attrib["w"]
            end_previous(window, int(element.get("t", "0")))
            previous[window] = element

    if changed:
        tree.write(path, encoding="utf-8", xml_declaration=True)
