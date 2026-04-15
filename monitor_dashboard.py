#!/usr/bin/env python3
"""
揽宝量化 - Streamlit 监控看板
展示：净值曲线、交易流水、当前持仓、最新反思、明日策略预告
"""

import streamlit as st
import sqlite3
import pandas as pd
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="揽宝量化监控看板", layout="wide")

DB_PATH = "/root/lanbao/data/lanbao.db"
RESULTS_DIR = Path("/root/lanbao/backtests/results")

# 经回测优化的最优参数
OPT_PARAMS = {
    "hard_stop_loss": 0.05,
    "max_single_position": 0.10,
    "lsi_buy_threshold": 50,
    "leader_change_pct_threshold": 0.07,
}

st.title("📊 揽宝量化 - 好运哥2008 监控看板")

# ==================== 数据加载函数 ====================

def ensure_reflections_table():
    """确保 reflections 表存在，避免 OperationalError"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                agent_version TEXT,
                reflection_type TEXT,
                summary TEXT,
                patterns TEXT,
                recommendations TEXT,
                raw_json TEXT,
                created_at TEXT
            )
        """)
        conn.commit()

def load_nav_history(account: str = "backtest_default") -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM paper_nav WHERE account = ? ORDER BY date",
            conn, params=(account,)
        )
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df

def load_trades(account: str = "backtest_default", limit: int = 50) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM paper_trades WHERE account = ? ORDER BY date DESC LIMIT ?",
            conn, params=(account, limit)
        )
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df

def load_positions(account: str = "backtest_default") -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM paper_positions WHERE account = ?",
            conn, params=(account,)
        )
    return df

def load_decision_stats(agent_version: str = "haoyunge_2008_v1.0") -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN tag = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN tag = 'loss' THEN 1 ELSE 0 END) as losses,
                AVG(pnl_pct) as avg_pnl,
                MAX(pnl_pct) as max_pnl,
                MIN(pnl_pct) as min_pnl
            FROM agent_decisions
            WHERE agent_version = ? AND pnl_pct IS NOT NULL
        """, (agent_version,))
        row = cursor.fetchone()
    total = row[0] or 0
    wins = row[1] or 0
    return {
        "total": total,
        "wins": wins,
        "losses": row[2] or 0,
        "win_rate": wins / total * 100 if total else 0,
        "avg_pnl": row[3] or 0,
        "max_pnl": row[4] or 0,
        "min_pnl": row[5] or 0,
    }

def load_latest_reflection(agent_version: str = "haoyunge_2008_v1.0") -> dict:
    ensure_reflections_table()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("""
            SELECT * FROM reflections
            WHERE agent_version = ?
            ORDER BY created_at DESC LIMIT 1
        """, (agent_version,))
        row = cursor.fetchone()
    return dict(row) if row else {}

def load_latest_backtest_result() -> dict:
    files = sorted(RESULTS_DIR.glob("backtest_*.json"))
    if not files:
        return {}
    return json.loads(files[-1].read_text(encoding="utf-8"))

def load_benchmark_index(initial_value: float = 500000.0, account: str = "paper_rolling") -> pd.DataFrame:
    """
    加载等权市场基准指数，与指定账户的初始日期对齐
    """
    with sqlite3.connect(DB_PATH) as conn:
        # 获取该账户首次净值日期
        cursor = conn.execute(
            "SELECT MIN(date) FROM paper_nav WHERE account = ?",
            (account,)
        )
        row = cursor.fetchone()
        start_date = row[0] if row and row[0] else None

        if not start_date:
            # 如果没有该账户数据，尝试 backtest_default
            cursor = conn.execute(
                "SELECT MIN(date) FROM paper_nav WHERE account = 'backtest_default'"
            )
            row = cursor.fetchone()
            start_date = row[0] if row and row[0] else None

        query = "SELECT date, AVG(close) as avg_close FROM daily_prices"
        if start_date:
            query += f" WHERE date >= '{start_date}'"
        query += " GROUP BY date ORDER BY date"

        df = pd.read_sql_query(query, conn)

    if df.empty:
        return pd.DataFrame()

    df['date'] = pd.to_datetime(df['date'])
    base = df['avg_close'].iloc[0]
    if base and base > 0:
        df['benchmark_value'] = df['avg_close'] / base * initial_value
    else:
        df['benchmark_value'] = initial_value
    return df[['date', 'benchmark_value']]

def load_account_nav_with_benchmark(account: str = "paper_rolling") -> pd.DataFrame:
    """合并账户净值和基准指数"""
    nav_df = load_nav_history(account=account)
    if nav_df.empty:
        nav_df = load_nav_history(account="backtest_default")

    bench_df = load_benchmark_index(initial_value=500000.0, account=account if not nav_df.empty else "backtest_default")

    if nav_df.empty and bench_df.empty:
        return pd.DataFrame()

    if nav_df.empty:
        return bench_df.rename(columns={"benchmark_value": "等权市场基准"})

    if bench_df.empty:
        return nav_df[["date", "total_value"]].rename(columns={"total_value": "模拟盘净值"})

    merged = pd.merge(nav_df[["date", "total_value"]], bench_df, on="date", how="outer")
    merged = merged.sort_values("date").fillna(method="ffill")
    merged = merged.rename(columns={
        "total_value": "模拟盘净值",
        "benchmark_value": "等权市场基准",
    })
    return merged

def load_radar_signals(account: str = "paper_rolling") -> List[Dict]:
    """生成好运雷达预警信号"""
    signals = []
    today = datetime.now().strftime("%Y-%m-%d")

    with sqlite3.connect(DB_PATH) as conn:
        # 最新日期
        cursor = conn.execute("SELECT MAX(date) FROM daily_prices")
        latest_market_date = cursor.fetchone()[0] or today

        # 1. LSI 预警
        lsi_score = load_latest_lsi()
        if lsi_score < OPT_PARAMS["lsi_buy_threshold"]:
            signals.append({
                "level": "danger",
                "icon": "🛡️",
                "title": "LSI 情绪低迷",
                "msg": f"LSI {lsi_score:.1f} 低于买入阈值 {OPT_PARAMS['lsi_buy_threshold']}，系统已强制空仓。",
            })
        elif lsi_score >= 70:
            signals.append({
                "level": "good",
                "icon": "🔥",
                "title": "情绪高涨",
                "msg": f"LSI {lsi_score:.1f}，市场氛围热烈，适合积极盯盘真龙。",
            })

        # 2. 持仓风险预警
        cursor = conn.execute(
            "SELECT code, name, cost_price, stop_loss FROM paper_positions WHERE account = ?",
            (account,)
        )
        positions = cursor.fetchall()
        if positions:
            for code, name, cost, stop in positions:
                # 获取最新价
                c2 = conn.execute(
                    "SELECT close, pct_change FROM daily_prices WHERE code = ? AND date = ?",
                    (code, latest_market_date)
                )
                row = c2.fetchone()
                if row:
                    price, pct = row
                    if price and stop and price <= stop:
                        signals.append({
                            "level": "danger",
                            "icon": "🚨",
                            "title": f"止损触发: {code}",
                            "msg": f"{name} 最新价 {price:.2f} 已触及止损线 {stop:.2f}，建议立即离场。",
                        })
                    elif pct is not None and pct <= -5:
                        signals.append({
                            "level": "warning",
                            "icon": "⚠️",
                            "title": f"持仓大跌: {code}",
                            "msg": f"{name} 当日跌幅 {pct:.2f}%，需密切关注。",
                        })

        # 3. 最新交易动作（最近 3 笔）
        cursor = conn.execute(
            "SELECT date, code, name, action, pnl FROM paper_trades WHERE account = ? ORDER BY date DESC LIMIT 3",
            (account,)
        )
        for date, code, name, action, pnl in cursor.fetchall():
            if action == "STOP_LOSS":
                signals.append({
                    "level": "danger",
                    "icon": "🚨",
                    "title": f"已执行止损: {code}",
                    "msg": f"{date} {name} 触发止损，盈亏 {pnl:,.2f} 元。",
                })
            elif action == "CLEAR" and pnl is not None and pnl < 0:
                signals.append({
                    "level": "warning",
                    "icon": "📉",
                    "title": f"清仓亏损: {code}",
                    "msg": f"{date} {name} 清仓离场，亏损 {pnl:,.2f} 元。",
                })
            elif action == "BUY":
                signals.append({
                    "level": "info",
                    "icon": "🐉",
                    "title": f"新建仓: {code}",
                    "msg": f"{date} 买入 {name}，价格请见交易明细。",
                })

        # 4. 市场整体预警（跌幅>5%数量）
        cursor = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE date = ? AND pct_change <= -5",
            (latest_market_date,)
        )
        drop_count = cursor.fetchone()[0] or 0
        if drop_count > 50:
            signals.append({
                "level": "danger",
                "icon": "💥",
                "title": "市场恐慌",
                "msg": f"{latest_market_date} 全市场跌幅>5%的股票多达 {drop_count} 只，建议空仓避险。",
            })
        elif drop_count > 20:
            signals.append({
                "level": "warning",
                "icon": "⚠️",
                "title": "市场偏弱",
                "msg": f"{latest_market_date} 全市场跌幅>5%的股票有 {drop_count} 只，谨慎操作。",
            })

        # 5. 龙头机会信号
        cursor = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE date = ? AND pct_change >= 9.0",
            (latest_market_date,)
        )
        limit_up_count = cursor.fetchone()[0] or 0
        if limit_up_count > 30:
            signals.append({
                "level": "good",
                "icon": "🚀",
                "title": "涨停潮",
                "msg": f"{latest_market_date} 涨停股 {limit_up_count} 只，情绪高涨，盯紧真龙。",
            })

        # 6. 空仓/满仓状态提示
        if not positions:
            signals.append({
                "level": "info",
                "icon": "🧘",
                "title": "当前空仓",
                "msg": "账户暂无持仓，风险为零。等待真龙出现再出手。",
            })

    # 去重并排序：danger > warning > info > good
    order = {"danger": 0, "warning": 1, "info": 2, "good": 3}
    seen = set()
    unique_signals = []
    for s in signals:
        key = (s["level"], s["title"])
        if key not in seen:
            seen.add(key)
            unique_signals.append(s)
    unique_signals.sort(key=lambda x: order.get(x["level"], 99))
    return unique_signals


def load_latest_lsi() -> float:
    """获取最新 LSI 得分"""
    df = load_lsi_history()
    if not df.empty:
        return float(df.iloc[-1]['lsi_score'])
    return 50.0


def load_lsi_history() -> pd.DataFrame:
    """加载 LSI 历史数据（支持多日 JSON 文件）"""
    lsi_dir = Path('/root/lanbao/data/lsi_history')
    if not lsi_dir.exists():
        return pd.DataFrame()

    records = []
    for f in sorted(lsi_dir.glob('lsi_history_*.json')):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            if isinstance(data, list):
                for item in data:
                    records.append({
                        'timestamp': pd.to_datetime(item.get('timestamp') or item.get('date')),
                        'lsi_score': float(item.get('lsi_score', 50.0)),
                    })
            elif isinstance(data, dict):
                ts = data.get('timestamp') or data.get('date')
                score = 50.0
                if 'result' in data and isinstance(data['result'], dict):
                    score = float(data['result'].get('lsi_score', 50.0))
                elif 'lsi_score' in data:
                    score = float(data['lsi_score'])
                records.append({
                    'timestamp': pd.to_datetime(ts) if ts else pd.NaT,
                    'lsi_score': score,
                })
        except Exception:
            continue

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records).dropna(subset=['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    return df

def generate_tomorrow_preview(lsi_score: float) -> str:
    """基于最优参数生成明日策略预告"""
    lines = []
    lines.append(f"**明日 LSI 情绪:** {lsi_score:.1f}")
    lines.append("")

    if lsi_score < OPT_PARAMS["lsi_buy_threshold"]:
        lines.append("🛡️ **策略判定: 空仓观望**")
        lines.append(f"> LSI {lsi_score:.1f} 低于买入阈值 {OPT_PARAMS['lsi_buy_threshold']}，明日不开新仓。")
        lines.append(f"> 若当前有持仓，开盘优先清仓。")
    elif lsi_score >= 70:
        lines.append("🔥 **策略判定: 积极做多**")
        lines.append(f"> 情绪高涨，明日重点盯盘真龙，单票仓位上限 {OPT_PARAMS['max_single_position']:.0%}。")
        lines.append(f"> 硬止损: -{OPT_PARAMS['hard_stop_loss']:.0%}，买入标的涨幅必须 ≥ {OPT_PARAMS['leader_change_pct_threshold']:.0%}。")
    elif lsi_score >= 60:
        lines.append("⚖️ **策略判定: 谨慎做多**")
        lines.append(f"> 情绪可控，只上最强龙头，仓位 {OPT_PARAMS['max_single_position']:.0%}。")
        lines.append(f"> 硬止损: -{OPT_PARAMS['hard_stop_loss']:.0%}，买不到龙头就空仓。")
    else:
        lines.append("🌫️ **策略判定: 轻仓试探**")
        lines.append(f"> LSI 偏弱，若出现明确龙头可小仓位参与（≤ {OPT_PARAMS['max_single_position']:.0%}）。")
        lines.append(f"> 硬止损: -{OPT_PARAMS['hard_stop_loss']:.0%}，不追涨、不做杂毛。")

    lines.append("")
    lines.append("**核心军规**")
    lines.append(f"1. 止损纪律: 触及 -{OPT_PARAMS['hard_stop_loss']:.0%} 无条件离场")
    lines.append(f"2. 仓位上限: 单票不超过 {OPT_PARAMS['max_single_position']:.0%}")
    lines.append(f"3. 选股门槛: 涨幅 ≥ {OPT_PARAMS['leader_change_pct_threshold']:.0%}")
    lines.append(f"4. 空仓底线: 没有真龙就空仓")

    return "\n".join(lines)

# ==================== 页面布局 ====================

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🚨 好运雷达", "📈 净值与绩效", "📋 交易明细", "🧠 反思与优化", "⚙️ 当前持仓", "🔮 明日策略预告"
])

# 全局自动刷新：每 10 分钟
st_autorefresh(interval=10 * 60 * 1000, limit=1000, key="radar_refresh")

# ---------- Tab 1: 好运雷达 ----------
with tab1:
    st.subheader("🚨 好运雷达 - 实时预警信号")

    # LSI 揽宝情绪指数 分时曲线
    lsi_df = load_lsi_history()
    if not lsi_df.empty:
        st.subheader("📊 揽宝情绪指数 LSI 走势")
        # 添加阈值线
        chart_df = lsi_df.set_index("timestamp")[["lsi_score"]].copy()
        chart_df["买入阈值"] = OPT_PARAMS["lsi_buy_threshold"]
        chart_df["积极做多"] = 70
        st.line_chart(chart_df)

        latest_lsi = float(lsi_df.iloc[-1]["lsi_score"])
        lsi_delta = latest_lsi - float(lsi_df.iloc[-2]["lsi_score"]) if len(lsi_df) >= 2 else 0.0
        c1, c2 = st.columns(2)
        c1.metric("当前 LSI", f"{latest_lsi:.2f}", delta=f"{lsi_delta:+.2f}")
        c2.metric("数据点数", len(lsi_df))
    else:
        st.info("暂无 LSI 历史数据。")

    st.divider()

    signals = load_radar_signals(account="paper_rolling")

    if not signals:
        st.success("当前无异常信号，市场平静，可耐心等待机会。")
    else:
        # 按严重程度分栏统计
        danger_count = sum(1 for s in signals if s["level"] == "danger")
        warning_count = sum(1 for s in signals if s["level"] == "warning")
        info_count = sum(1 for s in signals if s["level"] == "info")
        good_count = sum(1 for s in signals if s["level"] == "good")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🚨 危险", danger_count)
        c2.metric("⚠️ 警告", warning_count)
        c3.metric("ℹ️ 提示", info_count)
        c4.metric("✅ 机会", good_count)

        st.divider()

        # 卡片式展示信号
        level_styles = {
            "danger": {"bg": "#fee2e2", "border": "#ef4444", "title": "🔴 危险"},
            "warning": {"bg": "#fef3c7", "border": "#f59e0b", "title": "🟡 警告"},
            "info": {"bg": "#e0f2fe", "border": "#38bdf8", "title": "🔵 提示"},
            "good": {"bg": "#dcfce7", "border": "#22c55e", "title": "🟢 机会"},
        }

        for sig in signals:
            style = level_styles.get(sig["level"], level_styles["info"])
            with st.container():
                st.markdown(
                    f"""
                    <div style="
                        background-color: {style['bg']};
                        border-left: 6px solid {style['border']};
                        padding: 12px 16px;
                        margin-bottom: 10px;
                        border-radius: 6px;
                    ">
                        <div style="font-weight: 600; font-size: 16px; margin-bottom: 4px;">
                            {sig['icon']} {sig['title']}
                        </div>
                        <div style="color: #374151; font-size: 14px;">
                            {sig['msg']}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

# ---------- Tab 2: 净值与绩效 ----------
with tab2:
    col1, col2, col3, col4 = st.columns(4)

    nav_df = load_nav_history()
    result = load_latest_backtest_result()

    if result:
        col1.metric("总收益", f"{result.get('total_return', 0):.2f}%")
        col2.metric("夏普比率", f"{result.get('sharpe_ratio', 0):.3f}")
        col3.metric("最大回撤", f"{result.get('max_drawdown', 0):.2f}%")
        col4.metric("胜率", f"{result.get('win_rate', 0):.2f}%")
    else:
        col1.metric("总收益", "N/A")
        col2.metric("夏普比率", "N/A")

    if not nav_df.empty:
        st.subheader("净值曲线")
        st.line_chart(nav_df.set_index("date")["total_value"])

        st.subheader("现金 vs 市值")
        st.area_chart(nav_df.set_index("date")[["cash", "market_value"]])
    else:
        st.info("暂无净值数据，请先运行回测或模拟盘。")

    # 模拟盘 vs 大盘 对比
    st.subheader("模拟盘 vs 等权市场基准")
    nav_bench_df = load_account_nav_with_benchmark(account="paper_rolling")
    if not nav_bench_df.empty:
        st.line_chart(nav_bench_df.set_index("date"))
        # 计算相对收益
        if "模拟盘净值" in nav_bench_df.columns and "等权市场基准" in nav_bench_df.columns:
            final_nav = nav_bench_df["模拟盘净值"].dropna().iloc[-1]
            final_bench = nav_bench_df["等权市场基准"].dropna().iloc[-1]
            initial = 500000.0
            nav_return = (final_nav - initial) / initial * 100
            bench_return = (final_bench - initial) / initial * 100
            alpha = nav_return - bench_return

            c1, c2, c3 = st.columns(3)
            c1.metric("模拟盘收益", f"{nav_return:.2f}%")
            c2.metric("等权基准收益", f"{bench_return:.2f}%")
            c3.metric("超额收益 (Alpha)", f"{alpha:+.2f}%")
    else:
        st.info("暂无模拟盘或基准数据。")

# ---------- Tab 3: 交易明细 ----------
with tab3:
    trades_df = load_trades(limit=30)
    if not trades_df.empty:
        st.subheader("最近 30 笔交易")
        # 美化显示
        display_df = trades_df[["date", "code", "name", "action", "price", "volume", "pnl"]].copy()
        display_df["pnl"] = display_df["pnl"].apply(lambda x: f"{x:,.2f}" if pd.notna(x) else "")
        st.dataframe(display_df, use_container_width=True)
    else:
        st.info("暂无交易记录。")

# ---------- Tab 4: 反思与优化 ----------
with tab4:
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("决策统计")
        stats = load_decision_stats()
        if stats["total"] > 0:
            st.write(f"总决策数: **{stats['total']}**")
            st.write(f"盈利次数: **{stats['wins']}**")
            st.write(f"亏损次数: **{stats['losses']}**")
            st.write(f"平均盈亏: **{stats['avg_pnl']:.2f}%**")
            st.write(f"最大盈利: **{stats['max_pnl']:.2f}%**")
            st.write(f"最大亏损: **{stats['min_pnl']:.2f}%**")
        else:
            st.info("暂无决策统计数据。")

    with col_right:
        st.subheader("最新反思")
        reflection = load_latest_reflection()
        if reflection:
            try:
                raw = json.loads(reflection.get("raw_json", "{}"))
                win = raw.get("win_reflection", {})
                loss = raw.get("loss_reflection", {})
                if "win_pattern" in win:
                    st.markdown(f"**Win Pattern:** {win.get('win_pattern', '')}")
                if "loss_pattern" in loss:
                    st.markdown(f"**Loss Pattern:** {loss.get('loss_pattern', '')}")
                st.caption(f"生成时间: {reflection.get('created_at', 'N/A')}")
            except Exception:
                st.json(reflection)
        else:
            st.info("暂无反思报告，请运行 ReflectionEngine。")

# ---------- Tab 5: 当前持仓 ----------
with tab5:
    pos_df = load_positions()
    if not pos_df.empty:
        st.subheader("模拟盘持仓")
        st.dataframe(pos_df[["code", "name", "volume", "cost_price", "stop_loss", "take_profit"]], use_container_width=True)
    else:
        st.info("当前空仓。")

    st.subheader("最新回测结果文件")
    result_files = sorted(RESULTS_DIR.glob("backtest_*.json"))[-5:]
    if result_files:
        for f in result_files:
            st.write(f"- `{f.name}`")
    else:
        st.info("暂无回测结果。")

# ---------- Tab 6: 明日策略预告 ----------
with tab6:
    st.subheader("🔮 明日策略预告")
    latest_lsi = load_latest_lsi()
    st.markdown(generate_tomorrow_preview(latest_lsi))

    st.divider()
    st.subheader("⚙️ 当前生效的最优参数")
    param_cols = st.columns(4)
    param_cols[0].metric("硬止损", f"-{OPT_PARAMS['hard_stop_loss']:.0%}")
    param_cols[1].metric("单票仓位上限", f"{OPT_PARAMS['max_single_position']:.0%}")
    param_cols[2].metric("LSI 买入阈值", f"{OPT_PARAMS['lsi_buy_threshold']}")
    param_cols[3].metric("选股涨幅门槛", f"{OPT_PARAMS['leader_change_pct_threshold']:.0%}")

    st.caption(f"参数来源: 2025-04-01 ~ 2026-04-14 回测网格搜索 | 最优夏普: **2.100**")

st.divider()
st.caption(f"揽宝量化 · 好运哥2008 · 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
