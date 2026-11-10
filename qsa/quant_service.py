"""Versioned strategies, immutable inputs, cancellable local research runs."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
import secrets
import threading
from pathlib import Path

from .data import atomic_json, now_iso
from .quant import ENGINE_VERSION, config_settings, dataset_info, preset_catalog, replay, scan
from .quant_stocks import (history_coverage, replay_days, replay_stocks, select_stocks, stock_row)


def config_summary(c):
    rules = [f"沪深主板与创业板，当前ST/退市名称过滤；至少{c['min_bars']}根历史",
             f"成交额≥{c['min_amount']/1e8:g}亿元；换手率{c['min_turnover']:g}—{c['max_turnover']:g}%",
             f"当日量/前20根均量{c['min_volume_ratio']:g}—{c['max_volume_ratio']:g}倍；5日涨幅≥{c['min_return5']:g}%",
             f"距MA20不超过{c['max_extension20']:g}%；收盘位置≥{c['close_position_min']*100:g}%"]
    if c["ma_alignment"]: rules.append("收盘高于MA5，且MA5>MA10>MA20")
    if c["ma20_rising"]: rules.append("MA20高于5根之前的MA20")
    if c["breakout_days"]: rules.append(f"收盘突破前{c['breakout_days']}根最高价，不含信号当日")
    if c["pullback_ma"]: rules.append(f"收盘距MA{c['pullback_ma']}在±{c['pullback_distance_pct']:g}%内")
    if c["min_boards"]: rules.append(f"已核验连续涨停≥{c['min_boards']}板（仅当前快照）")
    if c["sector_min"]: rules.append(f"同分类板块强度≥{c['sector_min']:g}（仅当前快照）")
    return rules


class QuantStore:
    def __init__(self, root):
        self.root = Path(root)/"quant"
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()

    def strategies(self):
        path = self.root/"strategies.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def save_strategy(self, payload):
        if not isinstance(payload,dict) or not isinstance(payload.get("name"),str): raise ValueError("策略名称需要是文字")
        cfg = config_settings(payload.get("config"))
        name = payload.get("name", "").strip()
        if not 1<=len(name)<=40: raise ValueError("策略名称需要1—40个字符")
        with self.lock:
            rows = self.strategies()
            strategy_id = payload.get("id")
            if strategy_id:
                strategy = next((x for x in rows if x["id"]==strategy_id),None)
                if not strategy: raise ValueError("策略不存在")
            else:
                if len(rows)>=100: raise ValueError("最多保存100个策略，请复用已有策略版本")
                strategy = {"id":secrets.token_hex(8),"name":name,"created_at":now_iso(),"versions":[]}
                rows.insert(0,strategy)
            strategy["name"]=name
            strategy["versions"].append({"version":len(strategy["versions"])+1,"saved_at":now_iso(),
                "name":name,"config":cfg,"rules":config_summary(cfg)})
            atomic_json(self.root/"strategies.json",rows)
            return copy.deepcopy(strategy)

    def history(self):
        path=self.root/"runs.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def run_folder(self, run_id):
        if not isinstance(run_id,str) or not re.fullmatch(r"[a-f0-9]{20}",run_id):
            raise ValueError("运行记录编号无效")
        return self.root/"runs"/run_id

    def read_run(self,run_id):
        try: return json.loads((self.run_folder(run_id)/"result.json").read_text(encoding="utf-8"))
        except FileNotFoundError: raise ValueError("运行记录不存在") from None

    def freeze_input(self,dataset):
        raw=json.dumps(dataset,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
        digest=hashlib.sha256(raw).hexdigest()
        path=self.root/"inputs"/(digest+".json")
        if not path.exists(): atomic_json(path,dataset)
        else: self.read_input(digest)
        return digest

    def read_input(self,digest):
        if not isinstance(digest,str) or not re.fullmatch(r"[a-f0-9]{64}",digest): raise ValueError("输入摘要无效")
        try: value=json.loads((self.root/"inputs"/(digest+".json")).read_text(encoding="utf-8"))
        except FileNotFoundError: raise ValueError("原始输入已不可用") from None
        check=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
        if check!=digest: raise ValueError("原始输入摘要不一致，停止复现")
        return value

    def save_result(self,result):
        with self.lock:
            folder=self.run_folder(result["id"])
            if (folder/"result.json").exists(): raise ValueError("运行编号已经保存，不能覆盖")
            atomic_json(folder/"result.json",result)
            rows=self.history()
            rows.insert(0,{k:result.get(k) for k in ("id","type","status","created_at","strategy_name","strategy_version","mode","trade_date","metrics","funnel","summary","input_digest","request")})
            atomic_json(self.root/"runs.json",rows)
            return result

    def export(self,run_id,format):
        result=self.read_run(run_id)
        if format=="json": return json.dumps(result,ensure_ascii=False,indent=2).encode("utf-8"),"application/json; charset=utf-8"
        if format!="csv": raise ValueError("只支持JSON和CSV导出")
        buf=io.StringIO(newline="");writer=csv.writer(buf)
        def safe(x):
            value=str(x if x is not None else "")
            return "'"+value if value.startswith(("=","+","-","@","\t","\r")) else value
        if result["type"]=="scan":
            writer.writerow(["运行编号","数据模式","信号日期","代码","名称","排序分","收盘","5日涨幅%","20日均量倍数","规则"])
            for row in result.get("signals",[]):
                writer.writerow(list(map(safe,[run_id,result["mode"],result["trade_date"],row["code"],row["name"],row["score"],row["features"]["close"],row["features"]["return5"],row["features"]["volume_ratio"],"；".join(row["reasons"])])))
        elif result["type"]=="stock_backtest":
            writer.writerow(["运行编号","数据模式","起始日期","结束日期","代码","名称","状态","每股独立初始资金","区间收益%","最大回撤%","已闭合交易","胜率%","费用与滑点","期末资产","持有观察收益%","未完成原因"])
            for row in result.get("results",[]):
                m=row.get("metrics") or {}
                writer.writerow(list(map(safe,[run_id,result["mode"],result.get("start"),result.get("end"),row["code"],row["name"],row["status"],result["capital_per_stock"],m.get("total_return_pct"),m.get("max_drawdown_pct"),m.get("closed_trades"),m.get("win_rate_pct"),m.get("total_fees"),m.get("final_equity"),m.get("reference_return_pct"),row.get("failure")])))
        else:
            writer.writerow(["运行编号","数据模式","代码","名称","信号日","模拟进入日","模拟退出日","进入价","退出价","股数","持有交易日","费用后损益","费用后收益%","退出原因"])
            for t in result.get("trades",[]):
                writer.writerow(list(map(safe,[run_id,result["mode"],t["code"],t["name"],t["signal_date"],t["entry_date"],t["exit_date"],t["entry_price"],t["exit_price"],t["shares"],t["holding_sessions"],t["pnl"],t["return_pct"],t["reason"]])))
        return ("\ufeff"+buf.getvalue()).encode("utf-8"),"text/csv; charset=utf-8"


class QuantService:
    def __init__(self,snapshots,lock=None):
        self.snapshots=snapshots
        self.store=QuantStore(snapshots.root)
        self.lock=lock or threading.RLock()
        self.cancel=threading.Event()
        self.job={"state":"idle","progress":0,"message":"选择模板，先运行一次当日筛选","id":None}

    def status(self,mode="active"):
        if mode not in ("active","demo"): raise ValueError("无效的数据模式")
        with self.lock:
            return {"job":dict(self.job),"presets":preset_catalog(),"strategies":self.store.strategies(),
                "history":self.store.history()[:50],"data":dataset_info(self.snapshots.read(mode)),"engine_version":ENGINE_VERSION}

    def progress(self,percent,message):
        with self.lock:self.job.update(progress=percent,message=message)

    def stock_choices(self,mode="active",query="",values=None):
        if mode not in ("active","demo"): raise ValueError("无效的数据模式")
        if not isinstance(query,str) or len(query)>80: raise ValueError("搜索内容过长")
        with self.lock:
            dataset=self.snapshots.read(mode)
            if values is not None:
                rows=select_stocks(dataset,values)
            else:
                text=query.strip().upper()
                from .quant import universe_ok
                rows=[s for s in dataset["stocks"] if universe_ok(s,dataset["meta"].get("mode")=="demo")
                    and (text in s["code"].upper() or text in s["name"].upper())]
                rows.sort(key=lambda s:(not s["code"].startswith(text),-len(s.get("bars",[])),s["code"]))
            return {"stocks":[stock_row(s,dataset["meta"].get("mode")=="demo") for s in rows[:20]],
                "total":len(rows),"trade_date":dataset["meta"]["trade_date"],"mode":mode}

    def check_stocks(self,payload):
        """Read-only preparation check; searching/selecting never starts a download."""
        if not isinstance(payload,dict): raise ValueError("检查参数需要是对象")
        mode=payload.get("mode","active")
        if mode not in ("active","demo"): raise ValueError("无效的数据模式")
        cfg=config_settings(payload.get("config"))
        with self.lock:
            dataset=self.snapshots.read(mode)
            selected=select_stocks(dataset,payload.get("codes"))
            info=dataset_info(dataset)
            start,end,days=replay_days(dataset,payload.get("start") or info["default_start"],payload.get("end") or info["default_end"])
            rows=[]
            for stock in selected:
                coverage=history_coverage(stock,cfg,days)
                if not coverage["issues"]: status,message="ready","本机数据已覆盖区间与预热要求"
                elif len(stock.get("bars",[]))<=1: status,message="needs_history","本机仅有当日报价，运行时将加载历史日线与复权数据"
                else: status,message="needs_history","本机历史尚未覆盖本次要求，运行时将尝试补齐"
                need=coverage["required_bars"]
                bars=stock.get("bars",[])
                rows.append({**stock_row(stock,dataset["meta"].get("mode")=="demo"),"coverage":coverage,
                    "status":status,"message":message,
                    "earliest_start":bars[need-1]["date"] if len(bars)>=need and all(b.get("adj_factor") for b in bars) else None})
            return {"stocks":rows,"ready":sum(r["status"]=="ready" for r in rows),"total":len(rows),
                "start":start,"end":end,"trade_date":info["trade_date"],"mode":info["mode"],
                "history_blocked":bool(cfg["min_boards"] or cfg["sector_min"] or cfg["ranking"]=="relay")}

    def cache_history(self,original,code,bars,source,evidence):
        """Share validated history with later runs, while guarding snapshot identity."""
        from .stock_history import merge_history,quote_identity
        with self.lock:
            latest=self.snapshots.read("active")
            if quote_identity(latest,code)!=quote_identity(original,code):
                return {"cache_status":"snapshot_changed","cache_message":"行情快照已改变；仅用于本次冻结输入，不覆盖新快照"}
            current=next(s for s in latest["stocks"] if s["code"]==code)
            if len(current["bars"])>=len(bars) and current["bars"][0]["date"]<=bars[0]["date"] and all(b.get("adj_factor") for b in current["bars"]):
                return {"cache_status":"already_ready","cache_message":"本机已有同日报价核验通过的历史"}
            enriched=merge_history(latest,code,bars,source,evidence)
            filename=self.snapshots.save(enriched)
            return {"cache_status":"saved","cache_message":"校验历史已缓存，后续筛选、详情和回测可复用","snapshot":filename}

    def start(self,payload,replay_id=None):
        if not isinstance(payload,dict): raise ValueError("运行参数需要是对象")
        with self.lock:
            if self.job["state"]=="running": raise ValueError("已有策略在运行，可等待完成或取消")
            original=None
            if replay_id:
                original=self.store.read_run(replay_id)
                if original.get("engine_version")!=ENGINE_VERSION: raise ValueError("原运行引擎版本不同，无法保证完全复现")
                payload=copy.deepcopy(original["request"])
                dataset=self.store.read_input(original["input_digest"])
            else:
                mode=payload.get("mode","active")
                if mode not in ("active","demo"): raise ValueError("无效的数据模式")
                dataset=copy.deepcopy(self.snapshots.read(mode))
            cfg=config_settings(payload.get("config"))
            kind=payload.get("type","scan")
            if kind not in ("scan","backtest","stock_backtest"): raise ValueError("不支持的运行类型")
            if kind in ("backtest","stock_backtest") and (cfg["min_boards"] or cfg["sector_min"] or cfg["ranking"]=="relay"):
                raise ValueError("策略使用了缺少逐日历史的连板或板块条件；请先运行当日筛选，或关闭相关条件后回测")
            strategy_name=payload.get("strategy_name") or preset_catalog()[cfg["template"]]["name"]+" · 参数快照"
            if not isinstance(strategy_name,str) or len(strategy_name)>80: raise ValueError("策略名称过长")
            strategy_id,version=payload.get("strategy_id"),payload.get("strategy_version")
            if strategy_id and not replay_id:
                item=next((s for s in self.store.strategies() if s["id"]==strategy_id),None)
                chosen=next((v for v in item["versions"] if v["version"]==version),None) if item else None
                if not chosen or chosen["config"]!=cfg:
                    raise ValueError("参数与保存版本不一致，请保存新版本或作为未保存参数运行")
                strategy_name=chosen["name"]
            request={"type":kind,"config":cfg,"mode":"demo" if dataset["meta"].get("mode")=="demo" else "active",
                "strategy_name":strategy_name,"strategy_id":strategy_id,"strategy_version":version}
            if kind in ("backtest","stock_backtest"):
                info=dataset_info(dataset)
                from .data import iso_date
                request.update(start=iso_date(payload.get("start") or info["default_start"]),end=iso_date(payload.get("end") or info["default_end"]))
                if request["start"]>request["end"] or request["end"]>dataset["meta"]["trade_date"]:
                    raise ValueError("日期范围无效或超过快照日期")
            if kind=="stock_backtest":
                request["codes"]=[s["code"] for s in select_stocks(dataset,payload.get("codes"))]
                auto=payload.get("auto_history",True)
                if type(auto) is not bool: raise ValueError("自动补齐历史需要是开关值")
                request["auto_history"]=auto
            run_id=secrets.token_hex(10)
            cancel=threading.Event();self.cancel=cancel
            self.job={"state":"running","id":run_id,"progress":0,"message":"冻结本次行情输入与参数版本","started_at":now_iso()}

        def worker():
            nonlocal dataset
            digest=None
            base={"id":run_id,"engine_version":ENGINE_VERSION,"created_at":now_iso(),"mode":request["mode"],
                "strategy_name":strategy_name,"strategy_id":strategy_id,"strategy_version":version,
                "trade_date":dataset["meta"]["trade_date"],"request":request,"rules":config_summary(cfg),
                "data":dataset_info(dataset),"replayed_from":replay_id}
            try:
                if kind=="stock_backtest":
                    receipts=copy.deepcopy(original.get("history_preparation",[])) if original else []
                    if request["auto_history"] and not replay_id and request["mode"]!="demo":
                        from .stock_history import fetch_history,merge_history
                        _,_,days=replay_days(dataset,request["start"],request["end"])
                        selected=select_stocks(dataset,request["codes"])
                        for index,stock in enumerate(selected):
                            if cancel.is_set(): raise InterruptedError("运行已取消")
                            if not history_coverage(stock,cfg,days)["issues"]: continue
                            self.progress(int(index/len(selected)*25),f"准备历史 {index+1}/{len(selected)} · {stock['name']}")
                            try:
                                def check_coverage(enriched):
                                    candidate=next(s for s in enriched["stocks"] if s["code"]==stock["code"])
                                    issues=history_coverage(candidate,cfg,days)["issues"]
                                    if issues: raise ValueError("该源历史未覆盖本次回测："+"；".join(issues))
                                bars,source,evidence=fetch_history(dataset,stock["code"],self.snapshots.root,
                                    lambda message:self.progress(int(index/len(selected)*25),f"{stock['name']} · {message}"),
                                    validate_enriched=check_coverage)
                                if cancel.is_set(): raise InterruptedError("运行已取消")
                                cache={}
                                try: cache=self.cache_history(dataset,stock["code"],bars,source,evidence)
                                except Exception as error: cache={"cache_status":"error","cache_message":str(error)[:300]}
                                dataset=merge_history(dataset,stock["code"],bars,source,evidence)
                                receipts.append({"code":stock["code"],"status":"fetched","source":source,"evidence":evidence,"bars":len(bars),**cache})
                            except InterruptedError: raise
                            except Exception as error:
                                receipts.append({"code":stock["code"],"status":"failed","message":str(error)[:500]})
                    if cancel.is_set(): raise InterruptedError("运行已取消")
                    base.update(history_preparation=receipts,data=dataset_info(dataset))
                digest=self.store.freeze_input(dataset)
                base["input_digest"]=digest
                if kind=="scan": result=scan(dataset,cfg,self.progress,cancel.is_set)
                elif kind=="stock_backtest":
                    result=replay_stocks(dataset,cfg,request["codes"],request["start"],request["end"],
                        lambda p,m:self.progress(25+int(p*.75),m),cancel.is_set)
                else: result=replay(dataset,cfg,request["start"],request["end"],self.progress,cancel.is_set)
                if cancel.is_set(): raise InterruptedError("运行已取消")
                result={**base,**result}
                self.store.save_result(result)
                message="运行完成" if result["status"]=="completed" else "逐股运行结束，请查看未完成股票的原因" if kind=="stock_backtest" else "运行中止：数据核验需要处理"
                with self.lock:self.job.update(state="done",progress=100,message=message,result_id=run_id)
            except InterruptedError:
                result={**base,"type":kind,"status":"cancelled","message":"用户已取消本次运行","input_digest":digest}
                self.store.save_result(result)
                with self.lock:self.job.update(state="cancelled",message="已取消，原有结果保留",result_id=run_id)
            except Exception as error:
                result={**base,"type":kind,"status":"error","failure":str(error)[:500],"input_digest":digest}
                try:self.store.save_result(result)
                except Exception:pass
                with self.lock:self.job.update(state="error",message=f"运行未完成：{str(error)[:300]}",result_id=run_id)
        self.worker=threading.Thread(target=worker,name="quant-research",daemon=True)
        self.worker.start()
        return {"job":dict(self.job)}

    def cancel_run(self):
        with self.lock:
            if self.job["state"]!="running": raise ValueError("当前没有运行中的策略")
            self.cancel.set();self.job["message"]="正在取消，等待当前计算步骤结束"
            return {"message":"已请求取消"}
