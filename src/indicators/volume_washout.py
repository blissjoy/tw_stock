"""低檔量縮落底訊號（Layer 0）：成交量萎縮到近期高峰的一定比例，視為籌碼洗清訊號。

來源：陳家豐《看懂籌碼 股市賺大錢》第7篇第4章「凶狠新鉅科 主力完美操控」
（筆記見`ai/ebook-summary-chen/P07-C4-凶狠新鉅科主力完美操控.md`）。書中原文：
「2012年我發現有人默默布局新鉅科時，成交量已萎縮到當年高峰期的10分之1。這其實是
市場的常態，高檔時成交量都是大又熱情，等到低檔時成交量小又冷清，代表散戶已經徹底
死心，卻也是股價真正落底、主力再度進場布局的好時機。」

書中的「當年高峰期」是指前一波價格高點附近的成交量，不是任意時間窗口的最大值；這裡
用固定天數的rolling window近似「當年高峰期」(預設240個交易日，約1年)，是工程簡化，
不是精確重現書中「找出前一波高點」的做法(書中沒有給出這個判斷的精確演算法，屬於
`ai/ebook-summary-chen/_待確認總表.md`裡的量化門檻缺失)。

⚠️ 2026-07-29教訓：第一版直接用lookback窗口內的「單日最大量」當高峰基準，對本機
真實DB跑400檔股票測試，觸發率高達56%(225/400)，明顯不合理(這類「訊號」書中脈絡
應該是相對少見的落底訊號，不該過半股票天天觸發)。追查發現是「單日爆量」的雜訊：
例如1102在240天內只有單一天成交量衝到2億股(平常均量僅約1千萬股，很可能是指數
調整/大宗交易/單一事件造成的離群值)，這個離群值把「高峰基準」拉得極高，導致之後
幾乎任何正常量能的日子都能滿足「近期均量<=高峰10%」，訊號因此變得沒有鑑別力。
修正方式：**高峰基準改用「近recent_window天移動平均量」的rolling max，不是單日
最大量**——要求高峰期本身也是「一段時間持續放量」，而不是單一天的異常尖峰，這樣
才能對應書中「上一次見高點時，成交量相當活絡」這種「一段期間都熱絡」的語意，而非
被單日離群值主導。修正後對本機真實DB(400檔)重測，觸發率從56%降到36.5%(146/400)。

⚠️ 36.5%對「落底訊號」來說仍不算低，但書中原文自己說「這其實是市場的常態，高檔時
成交量都是大又熱情，等到低檔時成交量小又冷清」——作者明確表示這是普遍現象、不是
罕見訊號，所以較高的觸發率不一定代表訊號設計有問題。實務上建議**不要把這個訊號當
獨立的高信心買進理由，而是搭配其他訊號（例如`src/indicators/trend_position.py`的
`is_at_low`，或本書其他章節的高手/贏家券商辨識）一起看**，這也是書中原文的做法——
書中案例是「量縮」加上「集中度轉紅、特定券商持續吃貨」共同出現才視為進場訊號，不是
單靠量縮就進場。
"""

from __future__ import annotations

import pandas as pd

VOLUME_WASHOUT_LOOKBACK = 240  # 「當年高峰期」的近似窗口，書中未給精確天數，取約1年交易日數
VOLUME_WASHOUT_SHRINK_RATIO = 0.10  # 書中明確數字：萎縮到高峰期的10分之1
VOLUME_WASHOUT_RECENT_WINDOW = 5  # 用近5日均量代表「現在的量」，避免單日雜訊


def volume_washout_signal(
    volume: pd.Series,
    lookback: int = VOLUME_WASHOUT_LOOKBACK,
    shrink_ratio: float = VOLUME_WASHOUT_SHRINK_RATIO,
    recent_window: int = VOLUME_WASHOUT_RECENT_WINDOW,
) -> pd.Series:
    """逐日判斷「近期均量」是否已萎縮到「近lookback天峰值量」的shrink_ratio以下。

    峰值基準用「recent_window天移動平均量」的rolling max(而非單日最大量)，避免單一天
    的異常爆量(如大宗交易、指數調整)把基準拉得過高、讓訊號變得沒有鑑別力(見上方教訓)。
    `lookback`天的峰值窗口包含今天在內(rolling含右端點)，這不構成look-ahead——峰值窗口
    只往回看過去已發生的資料，不會用到未來資訊。
    """
    smoothed_volume = volume.rolling(window=recent_window, min_periods=recent_window).mean()
    peak_volume = smoothed_volume.rolling(window=lookback, min_periods=lookback).max()
    threshold = peak_volume * shrink_ratio
    return (smoothed_volume <= threshold) & peak_volume.notna() & smoothed_volume.notna()
