import re
import unicodedata

# ヘボン式（拗音含む）。キーはカタカナ
_DIGRAPHS = {
    "キャ": "kya", "キュ": "kyu", "キョ": "kyo",
    "シャ": "sha", "シュ": "shu", "ショ": "sho",
    "チャ": "cha", "チュ": "chu", "チョ": "cho",
    "ニャ": "nya", "ニュ": "nyu", "ニョ": "nyo",
    "ヒャ": "hya", "ヒュ": "hyu", "ヒョ": "hyo",
    "ミャ": "mya", "ミュ": "myu", "ミョ": "myo",
    "リャ": "rya", "リュ": "ryu", "リョ": "ryo",
    "ギャ": "gya", "ギュ": "gyu", "ギョ": "gyo",
    "ジャ": "ja", "ジュ": "ju", "ジョ": "jo",
    "ビャ": "bya", "ビュ": "byu", "ビョ": "byo",
    "ピャ": "pya", "ピュ": "pyu", "ピョ": "pyo",
    "ヂャ": "ja", "ヂュ": "ju", "ヂョ": "jo",
    # 外来語表記に使われる小書きかな二重母音
    "ジェ": "je", "シェ": "she", "チェ": "che",
    "ティ": "ti", "ディ": "di", "デュ": "du",
    "ファ": "fa", "フィ": "fi", "フェ": "fe", "フォ": "fo",
    "ウィ": "wi", "ウェ": "we", "ウォ": "wo",
    "ヴァ": "va", "ヴィ": "vi", "ヴェ": "ve", "ヴォ": "vo",
    "トゥ": "tu",
}
_MONO = {
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so",
    "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo",
    "ヤ": "ya", "ユ": "yu", "ヨ": "yo",
    "ラ": "ra", "リ": "ri", "ル": "ru", "レ": "re", "ロ": "ro",
    "ワ": "wa", "ヲ": "o",
    "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo",
    "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "ヴ": "vu",
}
_MAX_VARIANTS = 32


def _to_katakana(s: str) -> str:
    return "".join(
        chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in s)


def _base_romaji(kana: str) -> str | None:
    kana = _to_katakana(kana.strip())
    out = []
    i = 0
    sokuon = False
    while i < len(kana):
        two = kana[i:i + 2]
        ch = kana[i]
        if ch == "ッ":
            sokuon = True
            i += 1
            continue
        if ch == "ー":
            if out and out[-1] and out[-1][-1] in "aiueo":
                out.append(out[-1][-1])  # 直前母音を伸ばす
            i += 1
            continue
        if ch == "ン":
            out.append("n")
            i += 1
            continue
        if two in _DIGRAPHS:
            syl = _DIGRAPHS[two]
            i += 2
        elif ch in _MONO:
            syl = _MONO[ch]
            i += 1
        else:
            return None  # カナ以外（漢字等）は変換不能
        if sokuon:
            syl = syl[0] + syl
            sokuon = False
        out.append(syl)
    return "".join(out) if out else None


def kana_part_variants(part: str) -> set[str]:
    base = _base_romaji(part)
    if base is None:
        return set()
    variants = {base}
    # ン + b/m/p → m
    m_variant = re.sub(r"n(?=[bmp])", "m", base)
    variants.add(m_variant)
    # 長音の表記ゆれ展開
    expansions = [("ou", ("ou", "o", "oh")), ("oo", ("oo", "o", "oh")),
                  ("uu", ("uu", "u")), ("ei", ("ei", "e"))]
    for _ in range(3):  # 複数箇所に対応するため数回適用
        new = set()
        for v in variants:
            new.add(v)
            for pat, alts in expansions:
                if pat in v:
                    for alt in alts:
                        new.add(v.replace(pat, alt, 1))
        variants = new
        if len(variants) > _MAX_VARIANTS:
            break
    return set(list(variants)[:_MAX_VARIANTS])


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)  # 分音記号（^, ¯ 等）を分解して除去可能にする
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r" +", " ", s).strip()
