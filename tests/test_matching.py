from __future__ import annotations

from pathlib import Path

from autotrade.config import load_settings
from autotrade.matching import KeywordMatcher

M = KeywordMatcher(
    keywords=["記憶體", "DRAM", "HBM", "HBM4", "DDR5", "LPDDR5X", "LTA", "NOR Flash", "跌價"],
    risk_keywords=["跌價"],
    companies=["南亞科", "華邦電"],
    peers={"美光": ["Micron"], "SK海力士": ["SK hynix", "海力士"], "三星": ["Samsung"]},
)


def test_chinese_substring() -> None:
    r = M.match("南亞科第三季記憶體出貨成長")
    assert r.keywords == ("記憶體",)
    assert r.companies == ("南亞科",)


def test_english_is_case_insensitive() -> None:
    assert M.match("dram prices rise").keywords == ("DRAM",)


def test_english_respects_word_boundaries() -> None:
    assert M.match("DELTA 電子").keywords == ()  # 不能誤中 LTA
    assert M.match("新手機採用 LPDDR5X").keywords == ("LPDDR5X",)  # 不能誤中 DDR5
    assert M.match("HBM4 量產").keywords == ("HBM4",)  # 不能誤中 HBM
    assert M.match("簽訂LTA長約").keywords == ("LTA",)  # 中文緊鄰仍然算


def test_full_width_characters_are_normalized() -> None:
    assert M.match("ＤＲＡＭ報價").keywords == ("DRAM",)


def test_multi_word_keyword() -> None:
    assert M.match("旺宏 NOR flash 需求").keywords == ("NOR Flash",)


def test_peer_aliases_report_canonical_name() -> None:
    r = M.match("SK hynix 與 海力士 HBM 擴產")
    assert r.peers == ("SK海力士",)


def test_peer_alone_is_not_relevant() -> None:
    assert not M.match("Samsung 新款摺疊手機開賣").relevant
    assert M.match("Samsung 記憶體報價").relevant


def test_company_alone_is_relevant() -> None:
    assert M.match("華邦電股東會").relevant


def test_risk_detection() -> None:
    r = M.match("DRAM 現貨價跌價")
    assert r.risk == ("跌價",)
    assert M.is_risk_tags(r.tags) == ("跌價",)


def test_tags_order_and_dedupe() -> None:
    r = M.match("美光 Micron 與南亞科 DRAM")
    assert r.tags == ("南亞科", "美光", "DRAM")


def test_from_real_config(real_settings_path: Path, tmp_path: Path) -> None:
    app = load_settings(real_settings_path, env_path=None, project_root=tmp_path).app
    m = KeywordMatcher.from_config(app)
    r = m.match("群聯：NAND 缺貨延續，長鑫擴產值得留意")
    assert "群聯" in r.companies
    assert {"NAND", "缺貨", "擴產", "長鑫"} <= set(r.keywords)
    assert "長鑫" in r.risk
