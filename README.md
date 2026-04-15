# lanbao-dashboard

揽宝量化 Streamlit 监控看板。

## 功能

- 📊 揽宝情绪指数 LSI 实时走势
- 🚨 好运雷达 — 实时预警信号（市场情绪、持仓风险、交易动作）
- 📈 净值与绩效 — 模拟盘净值曲线、现金/市值分布、模拟盘 vs 等权基准对比
- 📋 交易明细 — 最近 30 笔交易流水
- 🧠 反思与优化 — 决策统计、最新反思报告
- ⚙️ 当前持仓 — 模拟盘持仓列表、最新回测结果文件
- 🔮 明日策略预告 — 基于最优参数的次日操作指引

## 启动

```bash
pip install -r requirements.txt
streamlit run monitor_dashboard.py --server.port 8501
```

## 自动刷新

页面每 10 分钟自动刷新一次数据。
