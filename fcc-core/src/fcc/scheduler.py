"""Domain-neutral feedback evaluation scheduling.

Fixed and adaptive intervals can drive Engine.tick. Cron is supported for
five-field minute-level schedules with explicit timezone; trend is a detector
usable by external observation-trigger orchestration (NOT automatically polled).
Scheduler adaptation changes frequency, NOT PID/MPC/ARX controller parameters.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from . import util
from .models import CycleResult, SchedulerConfig


class Scheduler:
    def __init__(self, store) -> None:
        self.store = store

    def adaptive_interval(self, base_seconds: float = 30.0, *,
                          severity: float = 0.0, uncertainty: float = 0.0,
                          min_seconds: float = 5.0, max_seconds: float = 600.0,
                          severity_gain: float = 2.0,
                          uncertainty_gain: float = 1.0) -> float:
        """Bounded heuristic for evaluation frequency (not adaptive controller law)."""
        vals = (base_seconds,severity,uncertainty,min_seconds,max_seconds,severity_gain,uncertainty_gain)
        if not all(math.isfinite(x) for x in vals) or min_seconds <= 0 or max_seconds < min_seconds or base_seconds<=0 or min(severity,uncertainty,severity_gain,uncertainty_gain)<0:
            raise ValueError('Invalid adaptive scheduler limits / inputs')
        return float(np.clip(base_seconds/(1+severity_gain*severity+uncertainty_gain*uncertainty),
                             min_seconds,max_seconds))

    def _interval_from_last(self, config: SchedulerConfig, last: dict | None) -> float:
        if config.mode!='adaptive':
            return config.interval_in_seconds()
        severity=0.0
        uncertainty=0.0
        if last:
            for err in last.get('errors',[]):
                if err.get('error') is not None:
                    tol=float(err.get('tolerance',0.) or 0.)
                    # Unscaled physical error is not meaningful; tolerance is
                    # required for severity. When tol=0, baseline frequency.
                    if tol > 0:
                        severity=max(severity, min(100.,abs(float(err['error']))/tol))
            uncertainty=float(last.get('uncertainty') or 0.)
        return self.adaptive_interval(config.interval_in_seconds(), severity=severity,
              uncertainty=uncertainty,min_seconds=config.min_interval_seconds,
              max_seconds=config.max_interval_seconds,
              severity_gain=config.severity_gain,
              uncertainty_gain=config.uncertainty_gain)

    def next_evaluation(self, loop_id: str, config: SchedulerConfig,
                        now: str | None = None) -> str | None:
        now=now or util.utcnow_iso()
        if config.mode=='cron':
            return CronTrigger(config.cron_expression,timezone=config.cron_timezone).next_at(now)
        if config.mode not in ('fixed','adaptive'):
            return None
        last=self.store.last_cycle(loop_id)
        base=last['started_at'] if last else now
        next_at=util.parse_iso(base)+dt.timedelta(seconds=self._interval_from_last(config,last))
        now_dt=util.parse_iso(now)
        return util.to_iso(max(next_at,now_dt))

    def is_due(self,loop_id:str,config:SchedulerConfig,now:str|None=None)->bool:
        if config.mode not in ('fixed','adaptive','cron'):
            return False
        now=now or util.utcnow_iso()
        last=self.store.last_cycle(loop_id)
        if last is None:
            return True
        if config.mode=='cron':
            next_run=CronTrigger(config.cron_expression,timezone=config.cron_timezone).next_at(last['started_at'])
            return util.parse_iso(now)>=util.parse_iso(next_run)
        elapsed=util.seconds_between(last['started_at'],now)
        return elapsed>=self._interval_from_last(config,last)

    def should_run_on_event(self,config:SchedulerConfig)->bool:
        return config.mode=='event'

    def should_run_on_threshold(self,config:SchedulerConfig,
                                last_result:CycleResult|dict[str,Any]|None)->bool:
        if config.mode!='threshold' or last_result is None:
            return False
        if isinstance(last_result,dict):
            return bool(last_result.get('should_intervene',False))
        return bool(getattr(last_result,'should_intervene',False))


class CronTrigger:
    """5-field standard cron subset: *, step, list, range. DOM/DOW use OR.

    Supports IANA timezone names, datetime output is ISO with correct offset.
    No seconds, @hourly aliases, L/W/#, or seconds-level Cron syntax.
    """
    STATUS='implemented'

    def __init__(self, expression: str, *, timezone: str='UTC') -> None:
        self.expression=expression
        self.tz=ZoneInfo(timezone)
        parts=expression.strip().split()
        if len(parts)!=5:
            raise ValueError('Cron requires exactly 5 fields: min hour dom month dow')
        self.fields=[self._field(s,lo,hi,weekday=(i==4))
                     for i,(s,lo,hi) in enumerate(zip(parts,(0,0,1,1,0),(59,23,31,12,7)))]
        self.dom_any=parts[2]=='*';self.dow_any=parts[4]=='*'

    @staticmethod
    def _field(token,lo,hi,weekday=False):
        selected=set()
        for part in token.split(','):
            raw,sep,step=part.partition('/')
            if sep and (not step.isdecimal() or int(step)<1):
                raise ValueError('Invalid cron step')
            stride=int(step) if sep else 1
            if raw=='*': start,end=lo,hi
            elif '-' in raw:
                left,right=raw.split('-',1)
                if not left.isdecimal() or not right.isdecimal():
                    raise ValueError('Invalid cron range')
                start,end=int(left),int(right)
            elif raw.isdecimal():
                start=int(raw);end=hi if sep else start
            else:
                raise ValueError('Unsupported cron field')
            if start<lo or end>hi or start>end:
                raise ValueError('Cron range out of bounds')
            for val in range(start,end+1,stride):
                selected.add(0 if weekday and val==7 else val)
        if not selected: raise ValueError('Empty cron field')
        return selected

    def _matches(self,now):
        minute,hour,dom,month,dow=self.fields
        weekday=(now.weekday()+1)%7
        date_match=(now.day in dom or weekday in dow) if not (self.dom_any or self.dow_any) else (
            (now.day in dom if not self.dom_any else True) and
            (weekday in dow if not self.dow_any else True))
        return now.minute in minute and now.hour in hour and now.month in month and date_match

    def next_at(self,now:str)->str:
        candidate=util.parse_iso(now).astimezone(self.tz)
        candidate=candidate.replace(second=0,microsecond=0)+dt.timedelta(minutes=1)
        # Iterate UTC minutes to handle DST gaps and repeated local minutes correctly.
        z=candidate.astimezone(dt.timezone.utc)
        for _ in range(2*366*24*60):
            cur=z.astimezone(self.tz)
            if self._matches(cur):return cur.isoformat()
            z+=dt.timedelta(minutes=1)
        raise ValueError('Cron next occurrence not found within two years')


class TrendTrigger:
    """Small deterministic slope detector. Call on new trusted samples only."""
    STATUS='implemented'

    def __init__(self, window:int=10,*,min_abs_slope:float=0.1,direction:str='either'):
        if window<3 or not math.isfinite(min_abs_slope) or min_abs_slope<=0 or direction not in ('either','up','down'):
            raise ValueError('Invalid trend detector settings')
        self.window=window;self.min_abs_slope=min_abs_slope;self.direction=direction

    def should_fire(self,history:list[float])->bool:
        if len(history)<self.window:return False
        y=np.asarray(history[-self.window:],dtype=float)
        if not np.all(np.isfinite(y)):return False
        x=np.arange(self.window,dtype=float)
        slope=float(np.sum((x-x.mean())*(y-y.mean()))/np.sum((x-x.mean())**2))
        return ((slope>=self.min_abs_slope and self.direction in ('up','either')) or
                (slope<=-self.min_abs_slope and self.direction in ('down','either')))
