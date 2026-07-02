from collector.nameutil import kana_part_variants, normalize_name


def test_basic():
    assert kana_part_variants("ヤマダ") == {"yamada"}
    assert kana_part_variants("すずき") == {"suzuki"}


def test_long_vowel_variants():
    assert kana_part_variants("タロウ") == {"tarou", "taro", "taroh"}
    assert kana_part_variants("ユウコ") == {"yuuko", "yuko"}
    assert kana_part_variants("オオノ") == {"oono", "ono", "ohno"}
    assert "yoko" in kana_part_variants("ヨウコ")


def test_ei_variants():
    assert kana_part_variants("ケイコ") == {"keiko", "keko"}


def test_digraph_sokuon_n():
    assert kana_part_variants("シュンスケ") == {"shunsuke"}
    assert kana_part_variants("ハットリ") == {"hattori"}
    assert "junichirou" in kana_part_variants("ジュンイチロウ")
    # ン + b/m/p → m 変体
    assert kana_part_variants("ホンマ") == {"honma", "homma"}


def test_chouon_mark():
    assert "ryusei" in {v.replace("ryuu", "ryu") for v in kana_part_variants("リュウセイ")} or "ryusei" in kana_part_variants("リュウセイ")


def test_small_kana_digraph_variants():
    assert kana_part_variants("ジェシ") == {"jeshi"}
    assert "hewon" in kana_part_variants("ヘウォン")


def test_normalize_name():
    assert normalize_name("Jun'ichiro  Tanaka-Sato") == "junichiro tanakasato"
    assert normalize_name("Daiju Ueda") == "daiju ueda"


def test_normalize_name_folds_diacritics():
    assert normalize_name("Shûichi Ōno") == "shuichi ono"


def test_empty_and_unknown_chars():
    assert kana_part_variants("") == set()
    assert kana_part_variants("山田") == set()  # 漢字は変換不能→空
