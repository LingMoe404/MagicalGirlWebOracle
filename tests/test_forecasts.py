"""Forecasts require a full observation week and settled local samples."""
from datetime import datetime,timezone
import time

from test_key_pool import runtime


def samples(cfg,ledger,executor,*,count=5,cost=1,now=None,uncertain=False):
    moment=time.time() if now is None else now
    ledger.db.execute('UPDATE pools SET created=?',(moment-8*86400,))
    for index in range(count):
        call=ledger.reserve(cfg.keys[0],executor.pools,'tavily','search',cost)
        ledger.dispatch(call)
        if uncertain: ledger.uncertain(call,'timeout')
        else: ledger.settle(call,cost)
        ledger.db.execute('UPDATE calls SET ts=? WHERE id=?',(moment-(1+index%2)*86400,call))


def test_fresh_or_sparse_samples_do_not_invent_prediction(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=1000)
    assert ledger.forecast(cfg.pools[0]) is None
    samples(cfg,ledger,executor,count=4)
    assert ledger.forecast(cfg.pools[0]) is None
    ledger.close()


def test_valid_week_uses_settled_costs_and_real_current_budget(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10)
    samples(cfg,ledger,executor)
    result=ledger.forecast(cfg.pools[0])
    assert result['sample_calls']==5 and result['sample_days']==7
    assert result['estimated_days']==7
    assert result['source']=='local_settled_usage'
    ledger.close()


def test_unsettled_costs_do_not_become_estimated_daily_consumption(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10)
    samples(cfg,ledger,executor,uncertain=True)
    assert ledger.forecast(cfg.pools[0]) is None
    ledger.close()


def test_month_reset_before_depletion_is_reported_without_false_alarm(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10)
    moment=datetime(2026,11,30,12,tzinfo=timezone.utc).timestamp()
    samples(cfg,ledger,executor,now=moment)
    result=ledger.forecast(cfg.pools[0],now=moment)
    assert result['estimated_days']==7 and result['before_reset'] is False
    assert not any(alert['kind']=='forecast_depletion' for alert in executor.alerts.evaluate(now=moment))
    ledger.close()


def test_burn_rate_warns_before_percentage_threshold_without_duplicate_key_alerts(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=900)
    samples(cfg,ledger,executor,cost=140)
    alerts=executor.alerts.evaluate()
    assert len(alerts)==1 and alerts[0]['kind']=='forecast_depletion'
    assert alerts[0]['details']['estimated_days']==2
    ledger.close()
