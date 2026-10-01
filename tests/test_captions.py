import xml.etree.ElementTree as ET

from youtube_mcp.captions import normalize_srv3_windows


def test_rolling_cues_end_at_next_replacement_without_losing_word_timings(tmp_path):
    path = tmp_path / "rolling.srv3"
    path.write_text('''<timedtext format="3"><head>
      <ws id="1" mh="2" ju="0" sd="3"/>
      <wp id="1" ap="6" ah="20" av="100" rc="2" cc="40"/>
    </head><body>
      <w t="0" id="1" wp="1" ws="1"/>
      <p t="160" d="3440" w="1"><s>first</s><s t="240"> line</s></p>
      <p t="1990" d="1610" w="1" a="1">\n</p>
      <p t="2000" d="4080" w="1"><s>second</s><s t="240"> line</s></p>
      <p t="3590" d="2490" w="1" a="1">\n</p>
      <p t="3600" d="5440" w="1">third line</p>
    </body></timedtext>''', encoding="utf-8")

    normalize_srv3_windows(path)

    cues = ET.parse(path).findall("body/p")
    assert [cue.get("d") for cue in cues] == ["1840", "1600", "5440"]
    assert ["".join(cue.itertext()) for cue in cues] == [
        "first line", "second line", "third line"
    ]
    assert cues[0].findall("s")[1].get("t") == "240"


def test_window_reset_ends_its_cue_without_clipping_other_windows(tmp_path):
    path = tmp_path / "windows.srv3"
    path.write_text('''<timedtext format="3"><body>
      <p t="0" d="5000" w="1">first window</p>
      <p t="1000" d="5000" w="2">second window</p>
      <w t="3000" id="1"/>
      <p t="4000" d="2000" w="1">replacement</p>
    </body></timedtext>''', encoding="utf-8")

    normalize_srv3_windows(path)

    assert [cue.get("d") for cue in ET.parse(path).findall("body/p")] == [
        "3000", "5000", "2000"
    ]


def test_authored_styled_overlaps_are_unchanged(tmp_path):
    path = tmp_path / "styled.srv3"
    original = '''<timedtext format="3"><head>
      <pen id="1" fc="#F4C02D" i="1"/>
      <wp id="1" ap="7" ah="50" av="90"/>
    </head><body>
      <p t="0" d="4000" wp="1" p="1"><s t="200">colored text</s></p>
      <p t="1000" d="4000" wp="1" p="1">deliberate overlap</p>
    </body></timedtext>'''
    path.write_text(original, encoding="utf-8")

    normalize_srv3_windows(path)

    assert path.read_text("utf-8") == original
