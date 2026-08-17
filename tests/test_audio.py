from digital_human.audio import split_script


def test_split_script_preserves_short_sentences() -> None:
    assert split_script("大家好。今天介绍产品！", 20) == ["大家好。", "今天介绍产品！"]


def test_split_script_uses_minor_boundaries() -> None:
    result = split_script("这是第一部分，接下来介绍第二部分，最后进行总结。", 12)
    assert result == ["这是第一部分，", "接下来介绍第二部分，", "最后进行总结。"]


def test_split_script_empty() -> None:
    assert split_script("   ") == []

