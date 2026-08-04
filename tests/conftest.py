"""全域pytest fixture：所有測試共用的安全網，避免測試意外污染正式環境的檔案。"""

import pytest

from src.presentation import pipeline_status


@pytest.fixture(autouse=True)
def _isolate_pipeline_status(tmp_path, monkeypatch):
    """把pipeline_status.STATUS_PATH隔離到每個測試各自的tmp_path，避免測試呼叫
    run_daily_pipeline()時不小心寫到真正的data/pipeline_status.json，把正式環境
    的排程狀態(桌面版讀這個檔案顯示「股價更新至/候選清單算至」)覆蓋成測試用的假資料。

    2026-08-04發現：test_daily_pipeline.py裡只有少數測試自己手動monkeypatch這個
    路徑，其餘呼叫run_daily_pipeline()的測試都會真的寫到data/pipeline_status.json，
    多次執行`pytest tests/`後被寫入明顯是測試資料的內容(date="2026-07-22"、
    candidate_count=0)，桌面版因此顯示錯誤的狀態。改成全域autouse預設隔離，個別
    測試需要驗證真正的STATUS_PATH行為(例如test_pipeline_status.py測試路徑本身的
    行為)時，在測試函式主體內用monkeypatch覆蓋即可(晚於這個fixture執行，會蓋過
    去)，跟test_daily_pipeline.py既有的`_no_real_taiex_network_calls`同一種模式。
    """
    monkeypatch.setattr(pipeline_status, "STATUS_PATH", tmp_path / "test_pipeline_status.json")
