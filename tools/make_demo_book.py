"""架空のゲーム攻略本（デモ用）を「OCR 済みスキャン PDF」風に生成する。

- 各ページは 1 枚のラスター画像（JPEG）＋ 透明なテキスト層（render_mode=3）
- 前付け（ローマ数字）、目次、PART 扉の小目次、本文中の「○ページ参照」、巻末索引、奥付を含む
- 本も ゲームも 架空。著作権上の問題なく pdf_book_linker のデモ・テストに使える

使い方:
    uv run python tools/make_demo_book.py [-o demo/out.pdf] [--lang ja|en]

--lang en で英語版（tools/demo_content_en.py の内容）を生成する。
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import sys

import pymupdf

W, H = 1750, 2790
ML, MR, MT, MB = 220, 220, 260, 330
TEXT_W = W - ML - MR
# A bundled CJK font makes both raster rendering and hidden text portable.
FONT_FILES = {"udr": None, "udb": None}
F = pymupdf.Font("japan")
FB = F
LANG = "ja"
BASE14 = {"udr": "helv", "udb": "hebo"}  # English demo: built-in Helvetica (no embedding needed)

def font_args(page, name):
    if LANG == "en" and not FONT_FILES[name]:
        return dict(fontname=BASE14[name])
    page.insert_font(fontname=name, fontbuffer=(FB if name == "udb" else F).buffer)
    return dict(fontname=name)

TEAL = (0.0, 0.58, 0.55)
GRAY = (0.35, 0.35, 0.35)
LIGHT = (0.88, 0.95, 0.94)
BOOK_TITLE = "ルミナ遺跡探索記 完全攻略ガイド"

# ---------------------------------------------------------------- content
PARTS = [
    ("1", "基本システム", [
        ("1-1", "操作方法とキャンプ",
         ["移動中もスタミナを意識する", "セーブポイントでは全回復できる", "ファストトラベルは一度訪れた場所のみ"],
         ("基本操作", "本作の主人公リオは、左スティックで移動し、ボタン長押しでダッシュする。ダッシュ中はスタミナを消費するので、ゲージが尽きる前に歩きに切り替えよう。高所から飛び降りる操作や、壁をよじ登る操作もスタミナを使う。セーブポイントでは HP と MP が全回復し、ファストトラベルの目的地にも登録される。"),
         "図1-1 画面の見かた",
         ("キャンプと宿屋", "フィールドではキャンプを張って休息できる。キャンプでは料理を作って一時的に能力を上げられるが、宿屋に泊まらないと治らない状態異常もある。毒や麻痺は回復薬で治せるが、睡眠は時間の経過で解けるのを待つしかない。詳しい対策は状態異常の項（{p:1-3}ページ参照）にまとめた。"),
         "ショートカットに回復薬を登録しておくと、戦闘中のメニュー操作が要らなくなる。"),
        ("1-2", "ステータスと成長",
         ["HP・MP・SP・ATK の4つが基本", "経験値は仲間全員に均等に入る", "スキルツリーは振り直せる"],
         ("4つのステータス", "キャラクターの強さは HP、MP、SP、ATK の4つで表される。SP は連携技に使うポイントで、通常攻撃を当てるたびに少しずつたまる。経験値は戦闘に参加しなかった仲間にも均等に入るので、控えのキャラクターを無理にレベル上げする必要はない。"),
         "図1-2 ステータス画面",
         ("スキルツリー", "レベルが上がると得られるポイントを、スキルツリーに振り分けて技を覚える。宿屋では一度だけ無料で振り直しができ、二回目以降はルミナ結晶を消費する。序盤はクリティカル率を上げる枝を優先すると、霧の森（{p:2-2}ページ参照）の戦闘が楽になる。"),
         "アクセサリの効果は、ステータス画面の右下に小さく表示される。"),
        ("1-3", "戦闘の流れ",
         ["ガードとジャスト回避を使い分ける", "弱点の属性を突くとコンボがつながる", "状態異常は早めに治す"],
         ("ガードとジャスト回避", "戦闘はリアルタイムで進む。敵の攻撃に合わせてガードすればダメージを減らせるが、直前に回避ボタンを押すジャスト回避に成功すると、無傷で反撃のチャンスが生まれる。属性は炎・氷・雷・光・闇の5種類で、弱点を突くとコンボが途切れにくくなる。"),
         "図1-3 ジャスト回避のタイミング",
         ("状態異常", "敵の攻撃で毒、麻痺、睡眠などの状態異常になることがある。毒は放置すると HP が減り続けるので、回復薬かエーテルで早めに治そう。麻痺は水晶洞窟の敵（{p:2-3}ページ参照）が多用してくる。オート戦闘では回復が後回しになりがちなので、ボス戦では手動の操作がおすすめだ。"),
         "ノーダメージでボス戦に勝つと、図鑑に特別な印が付く。"),
    ]),
    ("2", "ストーリー攻略", [
        ("2-1", "はじまりの村",
         ["鍛冶屋で武器を強化してから出発", "サブクエストで回復薬を集める", "宝箱は全部で6つ"],
         ("村でやること", "物語ははじまりの村から始まる。最初に鍛冶屋で武器を一段階強化しておくと、序盤の戦闘がかなり楽になる。村の中には宝箱が6つあり、そのうち1つはミミックなので注意が必要だ。宿屋の裏手にある井戸では、釣りのチュートリアルを受けられる。"),
         "図2-1 はじまりの村のマップ",
         ("仲間との出会い", "村の広場で、弓使いのセラが仲間に加わる。セラは風の精霊の加護を受けていて、遠くから弱点の属性を突ける。サブクエスト「迷子の子猫」をクリアすると回復薬を5個もらえるので、出発前に必ず受けておこう。"),
         "村を出る前にセーブポイントへ寄ると、霧の森へのファストトラベルが使えるようになる。"),
        ("2-2", "霧の森",
         ["霧の魔女は光属性が弱点", "月光草は夜にだけ採れる", "迷ったら青い花を目印に"],
         ("森の歩き方", "霧の森は視界が悪く、同じ景色が続くので迷いやすい。青い花が咲いている方向へ進めば奥へたどり着ける。夜になると月光草が採れるようになり、これを錬金に使うと上位の回復薬が作れる。ヤミガラスの群れは毒の攻撃をしてくるので、ガードで受けるのが安全だ。"),
         "図2-2 霧の森の正しい道順",
         ("ボス：霧の魔女", "森の最深部では霧の魔女と戦う。弱点は光属性で、セラの光の矢がよく効く。魔女が霧に隠れたらジャスト回避を狙わずにガードで耐え、姿を現した瞬間にコンボを叩き込もう。戦闘の基本は{p:1-3}ページを参照。"),
         "撃破すると手に入る銀の鍵は、水晶洞窟の鍵付きの扉で使う。"),
        ("2-3", "水晶洞窟",
         ["麻痺対策のアクセサリを装備", "水晶ゴーレムは雷属性が弱点", "ワープ床は踏む順番に注意"],
         ("洞窟の注意点", "水晶洞窟では、麻痺の攻撃をしてくる敵が多い。入る前に麻痺を防ぐアクセサリを装備しておくと安心だ。洞窟の中にはワープ床がいくつもあり、踏む順番を間違えると入口に戻されてしまう。正しい順番は図2-3を見てほしい。"),
         "図2-3 ワープ床の順番",
         ("ボス：水晶ゴーレム", "最深部の水晶ゴーレムは雷属性が弱点だ。ガルドの雷の斧で体の水晶を砕くと、しばらく動きが止まる。銀の鍵で開く扉の奥には星の欠片があり、これは天空の塔（{p:2-4}ページ参照）で必要になる。"),
         "ゴーレムの水晶を錬金で竜鱗と組み合わせると、強力な防具になる。"),
        ("2-4", "天空の塔",
         ["各階のセーブポイントを活用", "天空竜アルバは闇属性が弱点", "SP を最大までためてから挑む"],
         ("塔の登り方", "天空の塔は全10階で、各階にセーブポイントがある。上の階ほど敵が強くなるので、途中でレベル上げをしながら進もう。星の欠片を祭壇にはめると、最上階への道が開く。"),
         "図2-4 天空の塔の断面図",
         ("ボス：天空竜アルバ", "最上階で待つ天空竜アルバは闇属性が弱点だ。SP を最大までためてから挑み、開幕で仲間との連携技を使うと有利に進められる。アルバの竜鱗は、やりこみ要素の素材として重要になる（{p:3-2}ページ参照）。"),
         "塔をクリアすると闘技場が開放される。"),
    ]),
    ("3", "やりこみ要素", [
        ("3-1", "深淵の回廊",
         ["クリア後に開放される隠しダンジョン", "全50階、10階ごとにボス", "ルミナ結晶が大量に手に入る"],
         ("回廊への入り方", "ストーリーをクリアすると、はじまりの村の井戸から深淵の回廊に入れるようになる。全50階で、10階ごとにボスが待ち構えている。ここではルミナ結晶が大量に手に入るので、スキルツリーの振り直しに使おう。"),
         "図3-1 深淵の回廊の構造",
         ("回廊の進み方", "回廊ではファストトラベルが使えないので、回復薬とエーテルは多めに持ち込もう。ミミックが普通の宝箱に紛れているため、開ける前に図鑑で確かめるのが安全だ。最深部には黒曜の守護者が待っている（{p:3-3}ページ参照）。"),
         "回廊の30階には、闘技場の上位ランクに必要なアクセサリがある。"),
        ("3-2", "レア素材の集め方",
         ["竜鱗は天空竜アルバから", "月光草は霧の森で夜に", "星の欠片は釣りでも手に入る"],
         ("主なレア素材", "最強の装備を作るには、竜鱗、月光草、星の欠片などのレア素材が必要だ。竜鱗は天空竜アルバを倒すたびに一定の確率で手に入る。月光草は霧の森で夜にだけ採れる（{p:2-2}ページ参照）。"),
         "図3-2 素材の入手場所",
         ("錬金と鍛冶屋", "集めた素材は、錬金で中間素材に加工してから鍛冶屋に持ち込む。星の欠片は、はじまりの村の井戸で釣りをしていると、まれに釣り上げることができる。二周目では素材の入手率が上がる。"),
         "サブクエスト「竜の鱗を求めて」をクリアすると、竜鱗が出やすくなるアクセサリがもらえる。"),
        ("3-3", "裏ボス：黒曜の守護者",
         ["全属性に耐性、弱点はクリティカル", "状態異常は効かない", "連携技で SP を一気に使う"],
         ("守護者の特徴", "深淵の回廊の最深部で戦う黒曜の守護者は、本作で最強の敵だ。すべての属性に耐性を持ち、状態異常も効かない。唯一の弱点はクリティカルで、クリティカル率を上げるスキルツリーの枝（{p:1-2}ページ参照）を最大まで伸ばしておきたい。"),
         "図3-3 守護者の攻撃パターン",
         ("おすすめの戦い方", "リオ、セラ、ガルドの3人で挑み、守護者の大技はジャスト回避でかわす。SP がたまったら連携技を連発し、コンボを途切れさせないことが勝利の鍵だ。ノーダメージで倒すと、二周目に引き継げる特別な称号が手に入る。"),
         "守護者を倒すと、エンディングが追加される。"),
    ]),
]

INDEX_TERMS = [
    ("HP", "A"), ("MP", "A"), ("SP", "A"), ("ATK", "A"),
    ("アクセサリ", "あくせさり"), ("天空竜アルバ", "てんくうりゅうあるば"), ("エーテル", "えーてる"),
    ("オート戦闘", "おーとせんとう"), ("回復薬", "かいふくやく"), ("鍛冶屋", "かじや"), ("霧の魔女", "きりのまじょ"),
    ("霧の森", "きりのもり"), ("銀の鍵", "ぎんのかぎ"), ("クリティカル", "くりてぃかる"), ("経験値", "けいけんち"),
    ("月光草", "げっこうそう"), ("黒曜の守護者", "こくようのしゅごしゃ"), ("コンボ", "こんぼ"), ("ガード", "がーど"),
    ("ガルド", "がるど"), ("風の精霊", "かぜのせいれい"), ("サブクエスト", "さぶくえすと"), ("ジャスト回避", "じゃすとかいひ"),
    ("弱点", "じゃくてん"), ("状態異常", "じょうたいいじょう"), ("ショートカット", "しょーとかっと"), ("深淵の回廊", "しんえんのかいろう"),
    ("水晶ゴーレム", "すいしょうごーれむ"), ("水晶洞窟", "すいしょうどうくつ"), ("睡眠", "すいみん"), ("スキルツリー", "すきるつりー"),
    ("スタミナ", "すたみな"), ("図鑑", "ずかん"), ("セーブポイント", "せーぶぽいんと"), ("セラ", "せら"), ("属性", "ぞくせい"),
    ("宝箱", "たからばこ"), ("釣り", "つり"), ("天空の塔", "てんくうのとう"), ("闘技場", "とうぎじょう"), ("毒", "どく"),
    ("二周目", "にしゅうめ"), ("ノーダメージ", "のーだめーじ"), ("はじまりの村", "はじまりのむら"), ("ファストトラベル", "ふぁすととらべる"),
    ("星の欠片", "ほしのかけら"), ("ボス戦", "ぼすせん"), ("麻痺", "まひ"), ("ミミック", "みみっく"), ("宿屋", "やどや"),
    ("ヤミガラス", "やみがらす"), ("リオ", "りお"), ("竜鱗", "りゅうりん"), ("ルミナ結晶", "るみなけっしょう"),
    ("レベル上げ", "れべるあげ"), ("錬金", "れんきん"), ("連携技", "れんけいわざ"), ("ワープ床", "わーぷゆか"),
]
KANA_GROUPS = [("あ", "あいうえお"), ("か", "かきくけこがぎぐげご"), ("さ", "さしすせそざじずぜぞ"),
               ("た", "たちつてとだぢづでど"), ("な", "なにぬねの"), ("は", "はひふへほばびぶべぼぱぴぷぺぽ"),
               ("ま", "まみむめも"), ("や", "やゆよ"), ("ら", "らりるれろ"), ("わ", "わをん")]

STR = {
    "cover_kicker": "ACTION RPG", "cover_title": "ルミナ遺跡探索記", "cover_sub": "完全攻略ガイド",
    "cover_tag": "全ステージ・全ボス・やりこみ要素を完全網羅！", "cover_editor": "デモ攻略編集部 編",
    "cover_note": "※ 本書とゲームは、ツールのデモ用に作った架空のものです",
    "preface": "はじめに",
    "preface_body": [
        "本書は、アクションRPG『ルミナ遺跡探索記』の攻略ガイドです。基本のシステムから、ストーリーの攻略、クリア後のやりこみ要素までを、順を追って解説しています。",
        "WORLD 1 では操作とステータス、戦闘の流れを、WORLD 2 では4つのステージとボスの倒し方を、WORLD 3 では隠しダンジョンとレア素材、裏ボスの攻略を扱います。巻末のさくいんからは、用語ごとに該当ページを引くことができます。",
        "なお、本書とゲームはいずれも、PDF にリンクを自動で埋め込むツールの動作を確かめるために作った架空のものです。実在の作品・団体とは関係ありません。"],
    "contents": "CONTENTS", "contents_sub": "もくじ",
    "toc_preface": "はじめに", "toc_index": "さくいん", "toc_afterword": "あとがき",
    "part_list": "このワールドのステージ", "tip": "攻略のコツ",
    "index": "INDEX", "index_sub": "さくいん",
    "afterword": "あとがき",
    "afterword_body": [
        "最後までお読みいただき、ありがとうございました。本書が、ルミナ遺跡の冒険をより楽しむ手助けになれば幸いです。",
        "裏ボスの黒曜の守護者は手ごわい相手ですが、スキルツリーと連携技をうまく組み合わせれば、必ず道は開けます。"],
    "colophon": [("ルミナ遺跡探索記 完全攻略ガイド", 54), ("2026年11月5日　デモ版 第1刷", 40),
                 ("編者　デモ攻略編集部（架空）", 40), ("※ 本書は pdf_book_linker のデモ用に生成した架空の書籍です", 36)],
}

ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii"]

# ---------------------------------------------------------------- page plan
FRONT = ["cover", "blank", "preface", "toc"]  # index0 0..3 = i..iv
FRONT_COUNT = len(FRONT)
plan: list[tuple] = []
starts: dict[str, int] = {}   # chapter id / "part1" / "index" ... -> printed page


def build_plan() -> None:
    plan[:] = [(k,) for k in FRONT]
    starts.clear()
    pno = 1
    for num, ptitle, chapters in PARTS:
        starts[f"part{num}"] = pno
        plan.append(("part", num)); pno += 1
        for ch in chapters:
            starts[ch[0]] = pno
            plan.append(("chA", ch)); plan.append(("chB", ch)); pno += 2
    starts["index"] = pno
    plan.append(("index", 0)); plan.append(("index", 1)); pno += 2
    starts["afterword"] = pno
    plan.append(("afterword",)); pno += 1
    plan.append(("colophon",))


build_plan()


def set_language(lang: str) -> None:
    """Switch content, fonts and the page plan to 'ja' (default) or 'en'."""
    global LANG, PARTS, INDEX_TERMS, STR, BOOK_TITLE, F, FB
    if lang == "en":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import demo_content_en as en
        PARTS, INDEX_TERMS, STR, BOOK_TITLE = en.PARTS, en.INDEX_TERMS, en.STR, en.BOOK_TITLE
        F, FB = pymupdf.Font("helv"), pymupdf.Font("hebo")
    elif lang != "ja":
        raise ValueError(lang)
    LANG = lang
    build_plan()


def label(i: int) -> str | None:
    if i < FRONT_COUNT:
        return ROMAN[i] if i >= 2 else None
    kind = plan[i][0]
    if kind == "colophon":
        return None
    return str(i - FRONT_COUNT + 1)


def fill_refs(text: str) -> str:
    return re.sub(r"\{p:([0-9-]+)\}", lambda m: str(starts[m.group(1)]), text)


# ---------------------------------------------------------------- drawing helpers
def wrap(text: str, size: float, width: float) -> list[str]:
    if LANG == "en":
        lines, cur = [], ""
        for word in text.split():
            cand = f"{cur} {word}" if cur else word
            if cur and F.text_length(cand, fontsize=size) > width:
                lines.append(cur); cur = word
            else:
                cur = cand
        return lines + ([cur] if cur else [])
    lines, cur = [], ""
    for ch in text:
        if F.text_length(cur + ch, fontsize=size) > width and cur and ch not in "。、）」』・":
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def text(page, x, y, s, size=44, color=(0, 0, 0), bold=False):
    name = "udb" if bold else "udr"
    page.insert_text((x, y), s, **font_args(page, name), fontsize=size, color=color)


def tl(s, size, bold=False):
    return (FB if bold else F).text_length(s, fontsize=size)


def para(page, y, s, size=44, lh=78, x=ML, width=TEXT_W):
    for ln in wrap(("　" if LANG == "ja" else "") + s, size, width):
        text(page, x, y, ln, size); y += lh
    return y


NAVY = (0.13, 0.15, 0.27)
ORANGE = (0.96, 0.53, 0.13)
YELLOW = (1.0, 0.84, 0.25)
CREAM = (1.0, 0.98, 0.93)
SKY = (0.92, 0.95, 1.0)
WHITE = (1, 1, 1)


def star(page, cx, cy, r, color):
    pts = []
    for k in range(10):
        rr = r if k % 2 == 0 else r * 0.45
        a = math.pi / 2 + k * math.pi / 5
        pts.append((cx + rr * math.cos(a), cy - rr * math.sin(a)))
    page.draw_polyline(pts + [pts[0]], color=None, fill=color, closePath=True)


def bg(page):
    page.draw_rect(page.rect, color=None, fill=CREAM)


def chrome(page, i, right=""):
    """上部の紺の帯と、下端の外側の角のページ番号。"""
    bg(page)
    page.draw_rect(pymupdf.Rect(0, 0, W, 200), color=None, fill=NAVY)
    text(page, ML, 125, BOOK_TITLE, 34, WHITE)
    if right:
        text(page, W - MR - tl(right, 34, True), 125, right, 34, YELLOW, bold=True)
    lab = label(i)
    if lab:
        odd = (not lab.isdigit()) or int(lab) % 2 == 1
        x0 = W - 230 if odd else 90
        page.draw_rect(pymupdf.Rect(x0, H - 250, x0 + 140, H - 150), color=None, fill=ORANGE)
        text(page, x0 + 70 - tl(lab, 46, True) / 2, H - 183, lab, 46, WHITE, bold=True)


def heading(page, y, s):
    text(page, ML, y, s, 80, ORANGE, bold=True)
    page.draw_rect(pymupdf.Rect(ML, y + 30, ML + 260, y + 44), color=None, fill=NAVY)


def badge(page, x, y, s, size=40, fill=ORANGE):
    w = tl(s, size, True) + 40
    page.draw_rect(pymupdf.Rect(x, y - size - 4, x + w, y + 16), color=None, fill=fill, radius=0.3)
    text(page, x + 20, y, s, size, WHITE, bold=True)
    return x + w


def toc_row(page, y, num_label, title, num, size=44, x=ML, color=NAVY, rule=True):
    xt = badge(page, x, y, num_label, size - 8) + 30 if num_label else x
    text(page, xt, y, title, size, color)
    text(page, W - MR - tl(num, size, True), y, num, size, color, bold=True)
    if rule:
        page.draw_line((x, y + 26), (W - MR, y + 26), color=(0.8, 0.78, 0.72), width=2)


def toc_band(page, y, left, title, num):
    page.draw_rect(pymupdf.Rect(ML - 20, y - 62, W - MR + 20, y + 26), color=None, fill=NAVY)
    text(page, ML, y, left, 46, YELLOW, bold=True)
    text(page, ML + tl(left, 46, True) + 36, y, title, 46, WHITE, bold=True)
    text(page, W - MR - tl(num, 46, True), y, num, 46, WHITE, bold=True)


def figure(page, y, caption, h=640):
    r = pymupdf.Rect(ML, y, W - MR, y + h)
    page.draw_rect(r, color=NAVY, fill=SKY, width=5)
    for k in range(1, 12):
        x = r.x0 + k * r.width / 12
        page.draw_line((x, r.y0), (x, r.y1), color=(0.8, 0.85, 0.95), width=2)
    for k in range(1, 7):
        yy = r.y0 + k * h / 7
        page.draw_line((r.x0, yy), (r.x1, yy), color=(0.8, 0.85, 0.95), width=2)
    path = [(r.x0 + r.width * fx, r.y0 + h * fy) for fx, fy in
            [(0.08, 0.8), (0.25, 0.8), (0.25, 0.45), (0.5, 0.45), (0.5, 0.2), (0.75, 0.2), (0.75, 0.6), (0.92, 0.6)]]
    page.draw_polyline(path, color=ORANGE, width=10)
    page.draw_circle(path[0], 30, color=None, fill=NAVY)
    star(page, path[-1][0], path[-1][1], 50, YELLOW)
    bx = badge(page, ML, y + h + 80, "MAP", 34, NAVY)
    text(page, bx + 24, y + h + 80, caption, 38, NAVY, bold=True)
    return y + h + 170


def check_box(page, y, points, size=44, lh=74):
    h = 120 + lh * len(points)
    page.draw_rect(pymupdf.Rect(ML, y, W - MR, y + h), color=None, fill=(1.0, 0.94, 0.78))
    page.draw_rect(pymupdf.Rect(ML, y, ML + 22, y + h), color=None, fill=ORANGE)
    star(page, ML + 90, y + 60, 34, ORANGE)
    text(page, ML + 140, y + 76, "CHECK!", 46, ORANGE, bold=True)
    yy = y + 150
    for p in points:
        text(page, ML + 70, yy, "★ " + p, size, NAVY); yy += lh
    return y + h + 80


def tips(page, y, s, size=42, lh=70):
    body = wrap(s, size, TEXT_W - 220)
    h = 70 + lh * len(body) + 40
    page.draw_rect(pymupdf.Rect(ML, y, W - MR, y + h), color=NAVY, width=4, dashes="[18 10] 0", radius=0.08)
    page.draw_circle((ML + 90, y + h / 2), 45, color=None, fill=YELLOW)
    text(page, ML + 76, y + h / 2 + 18, "!", 52, NAVY, bold=True)
    text(page, ML + 170, y + 60, STR["tip"], 36, ORANGE, bold=True)
    yy = y + 60 + lh
    for b in body:
        text(page, ML + 170, yy, b, size); yy += lh
    return y + h + 60


def section(page, y, s):
    page.draw_polyline([(ML, y - 40), (ML + 34, y - 20), (ML, y), (ML, y - 40)], color=None, fill=ORANGE, closePath=True)
    text(page, ML + 56, y, s, 54, NAVY, bold=True)
    return y + 100


# ---------------------------------------------------------------- page renderers
def render(doc, i, item):
    page = doc.new_page(width=W, height=H)
    kind = item[0]
    if kind == "cover":
        page.draw_rect(page.rect, color=None, fill=NAVY)
        page.draw_polyline([(0, 1250), (W, 850), (W, 1450), (0, 1850)], color=None, fill=ORANGE, closePath=True)
        for cx, cy, r in [(1450, 400, 70), (1250, 620, 40), (1550, 700, 30), (300, 2250, 50), (1500, 2150, 90)]:
            star(page, cx, cy, r, YELLOW)
        text(page, 180, 700, STR["cover_kicker"], 60, YELLOW, bold=True)
        text(page, 180, 900, STR["cover_title"], 150, WHITE, bold=True)
        text(page, 220, 1390, STR["cover_sub"], 120, NAVY, bold=True)
        text(page, 180, 2050, STR["cover_tag"], 52, WHITE)
        text(page, 180, 2500, STR["cover_editor"], 50, WHITE)
        text(page, 180, 2600, STR["cover_note"], 36, (0.75, 0.78, 0.9))
    elif kind == "blank":
        bg(page)
    elif kind == "preface":
        chrome(page, i)
        heading(page, 420, STR["preface"])
        y = 640
        for s in STR["preface_body"]:
            y = para(page, y, s) + 40
    elif kind == "toc":
        chrome(page, i)
        text(page, ML, 430, STR["contents"], 100, ORANGE, bold=True)
        if STR["contents_sub"]:
            text(page, ML + tl(STR["contents"], 100, True) + 40, 430, STR["contents_sub"], 50, NAVY, bold=True)
        y = 620
        toc_row(page, y, None, STR["toc_preface"], "iii"); y += 110
        for num, ptitle, chapters in PARTS:
            toc_band(page, y, f"WORLD {num}", ptitle, str(starts[f"part{num}"])); y += 108
            for ch in chapters:
                toc_row(page, y, f"STAGE {ch[0][-1].zfill(2)}", ch[1], str(starts[ch[0]]), x=ML + 40)
                y += 92
            y += 24
        toc_row(page, y, None, STR["toc_index"], str(starts["index"])); y += 92
        toc_row(page, y, None, STR["toc_afterword"], str(starts["afterword"]))
    elif kind == "part":
        num = item[1]
        _, ptitle, chapters = next(p for p in PARTS if p[0] == num)
        page.draw_rect(page.rect, color=None, fill=NAVY)
        page.draw_polyline([(0, 1050), (W, 750), (W, 980), (0, 1280)], color=None, fill=ORANGE, closePath=True)
        text(page, ML, 520, "WORLD", 150, YELLOW, bold=True)
        text(page, ML + tl("WORLD", 150, True) + 40, 560, num, 320, WHITE, bold=True)
        text(page, ML, 780, ptitle, 110, WHITE, bold=True)
        star(page, 1450, 380, 90, YELLOW)
        y = 1560
        text(page, ML, y - 110, STR["part_list"], 44, YELLOW, bold=True)
        for ch in chapters:
            page.draw_rect(pymupdf.Rect(ML, y - 70, W - MR, y + 34), color=None, fill=CREAM, radius=0.25)
            toc_row(page, y, f"STAGE {ch[0][-1].zfill(2)}", ch[1], str(starts[ch[0]]), size=48, x=ML + 30, rule=False)
            y += 150
        lab = label(i)
        page.draw_rect(pymupdf.Rect(W - 230, H - 250, W - 90, H - 150), color=None, fill=ORANGE)
        text(page, W - 160 - tl(lab, 46, True) / 2, H - 183, lab, 46, WHITE, bold=True)
    elif kind in ("chA", "chB"):
        cid, ctitle, points, s1, figcap, s2, memo = item[1]
        chrome(page, i, f"WORLD {cid[0]} / STAGE {cid[-1].zfill(2)}")
        if kind == "chA":
            bx = badge(page, ML, 420, f"STAGE {cid[-1].zfill(2)}", 56)
            text(page, bx + 40, 422, ctitle, 84, NAVY, bold=True)
            page.draw_rect(pymupdf.Rect(ML, 470, W - MR, 482), color=None, fill=ORANGE)
            y = check_box(page, 560, points)
            y = section(page, y, s1[0])
            para(page, y, fill_refs(s1[1]))
        else:
            y = figure(page, 290, figcap)
            y = section(page, y, s2[0])
            y = para(page, y, fill_refs(s2[1]))
            tips(page, y + 50, memo)
    elif kind == "index":
        chrome(page, i)
        if item[1] == 0:
            text(page, ML, 430, STR["index"], 100, ORANGE, bold=True)
            if STR["index_sub"]:
                text(page, ML + tl(STR["index"], 100, True) + 40, 430, STR["index_sub"], 50, NAVY, bold=True)
        draw_index_page(page, item[1])
    elif kind == "afterword":
        chrome(page, i)
        heading(page, 420, STR["afterword"])
        y = 640
        for s in STR["afterword_body"]:
            y = para(page, y, s) + 40
    elif kind == "colophon":
        bg(page)
        y = 1700
        for s, size in STR["colophon"]:
            text(page, ML, y, s, size, NAVY); y += 100
        page.draw_rect(pymupdf.Rect(ML, 1610, W - MR, 1622), color=None, fill=ORANGE)
    return page


INDEX_LAYOUT: list[list[tuple]] = []  # filled by build_index(): per page, list of (col, kind, text, nums)


def draw_index_page(page, k):
    col_x = [ML, W / 2 + 40]
    for col, y, kind, s, nums in INDEX_LAYOUT[k]:
        x = col_x[col]
        if kind == "head":
            if len(s) == 1:
                page.draw_circle((x + 40, y - 16), 40, color=None, fill=NAVY)
                text(page, x + 40 - tl(s, 46, True) / 2, y, s, 46, WHITE, bold=True)
            else:
                badge(page, x, y, s, 38, NAVY)
        else:
            text(page, x, y, s, 42)
            text(page, x + tl(s, 42) + 50, y, ", ".join(map(str, nums)), 42)


def build_index(vector_doc) -> None:
    body = [i for i, it in enumerate(plan) if it[0] in ("chA", "chB")]
    entries = []
    for term, yomi in INDEX_TERMS:
        pages = sorted({int(label(i)) for i in body if vector_doc[i].search_for(term)})
        if pages:
            entries.append((term, yomi, pages[:5]))
        else:
            print("索引語が本文に見つからない:", term)
    if LANG == "en":
        groups = []
        for e in sorted(entries, key=lambda e: e[1]):
            letter = e[1][0].upper()
            if not groups or groups[-1][0] != letter:
                groups.append((letter, []))
            groups[-1][1].append(e)
    else:
        groups = [("アルファベット", [e for e in entries if e[1] == "A"])]
        for g, chars in KANA_GROUPS:
            items = [e for e in entries if e[1] != "A" and e[1][0] in chars]
            if items:
                groups.append((g, sorted(items, key=lambda e: e[1])))
    rows = []
    for g, items in groups:
        rows.append(("head", g, None))
        rows += [("entry", t, p) for t, _, p in items]
    pages: list[list[tuple]] = [[], []]
    slots = [(0, 0, 620), (0, 1, 620), (1, 0, 360), (1, 1, 360)]  # (page, col, y_start)
    s, y = 0, slots[0][2]
    for kind, t, p in rows:
        need = 100 if kind == "head" else 76
        if y + need > H - MB:
            s += 1
            if s >= len(slots):
                raise SystemExit("索引が2ページに収まりません")
            y = slots[s][2]
        pg, col, _ = slots[s]
        if kind == "head":
            y += 20
        pages[pg].append((col, y, kind, t, p))
        y += need
    INDEX_LAYOUT[:] = pages


# ---------------------------------------------------------------- scan-like output
def to_scanned(vector_doc, out_path: Path, quality: int = 80) -> None:
    out = pymupdf.open()
    for vp in vector_doc:
        pix = vp.get_pixmap(dpi=72)  # 1pt = 1px
        page = out.new_page(width=W, height=H)
        page.insert_image(page.rect, stream=pix.tobytes("jpeg", jpg_quality=quality))
        for block in vp.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip():
                        name = "udb" if "Bold" in span["font"] or span["font"].endswith("B") else "udr"
                        page.insert_text(span["origin"], span["text"], **font_args(page, name),
                                         fontsize=span["size"], render_mode=3)
    out.set_metadata({"title": "", "producer": "make_demo_book.py (scan-like demo)"})
    out.subset_fonts()
    out.save(out_path, garbage=3, deflate=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output")
    ap.add_argument("--lang", choices=["ja", "en"], default="ja", help="demo book language")
    ap.add_argument('--font', help='任意のフォントファイル（描画と透明文字層で共用）')
    args = ap.parse_args()
    set_language(args.lang)
    if args.output is None:
        args.output = "demo/lumina_guide_demo_en.pdf" if args.lang == "en" else "demo/ルミナ遺跡探索記_攻略ガイド_demo.pdf"
    if args.font:
        global F, FB
        FONT_FILES.update(udr=args.font, udb=args.font)
        F = FB = pymupdf.Font(fontfile=args.font)
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    INDEX_LAYOUT[:] = [[], []]
    draft = pymupdf.open()
    for i, item in enumerate(plan):
        render(draft, i, item)
    build_index(draft)
    final = pymupdf.open()
    for i, item in enumerate(plan):
        render(final, i, item)
    to_scanned(final, out)
    print(f"{out}  ({len(plan)} pages, {out.stat().st_size/1e6:.1f} MB)")
    print("印刷ページ: PART/章の開始 =", starts)


if __name__ == "__main__":
    main()
