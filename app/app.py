"""EWS Live: the front end of the retail bank early warning system demo.

Runs as a Databricks App. Reads the Delta tables written by notebooks 03-09
through a SQL warehouse, using the app's own service principal.
"""
import os
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config

CATALOG = os.getenv("EWS_CATALOG", "workspace")
GOLD, OPS = f"{CATALOG}.ews_gold", f"{CATALOG}.ews_ops"
WAREHOUSE = os.getenv("DATABRICKS_WAREHOUSE_ID")

TIER_ORDER = ["Performing", "Monitor", "Watch", "Early intervention", "Collections", "Recoveries"]
TIER_COLOUR = {"Performing": "#2e7d32", "Monitor": "#9e9d24", "Watch": "#f9a825", "Early intervention": "#ef6c00",
               "Collections": "#c62828", "Recoveries": "#6a1b9a"}
USES = [("limit_and_activation", "Approval, activation and limits"), ("pricing", "Pricing"),
        ("exposure", "Exposure management"), ("offers_engagement", "Offers and engagement"),
        ("support", "Support and early intervention"), ("collections", "Collections treatment")]

st.set_page_config(page_title="EWS Live", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.2rem;}
.tier {display:inline-block;padding:2px 10px;border-radius:12px;color:white;font-size:0.8rem;font-weight:600}
.card {border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.small {font-size:0.82rem;opacity:.8}
</style>""", unsafe_allow_html=True)

cfg = Config()


def q(statement: str) -> pd.DataFrame:
    with sql.connect(server_hostname=cfg.host, http_path=f"/sql/1.0/warehouses/{WAREHOUSE}",
                     credentials_provider=lambda: cfg.authenticate) as conn:
        with conn.cursor() as cur:
            cur.execute(statement)
            return cur.fetchall_arrow().to_pandas()


@st.cache_data(ttl=4)
def live(statement):
    try:
        return q(statement)
    except Exception as e:
        return pd.DataFrame({"_error": [str(e)[:300]]})


@st.cache_data(ttl=600)
def static(statement):
    try:
        return q(statement)
    except Exception as e:
        return pd.DataFrame({"_error": [str(e)[:300]]})


def ok(df):
    return len(df) > 0 and "_error" not in df.columns


def badge(tier):
    return f'<span class="tier" style="background:{TIER_COLOUR.get(tier, "#555")}">{tier}</span>'


# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("EWS Live")
    st.caption("Retail bank early warning system · synthetic data")
    auto = st.toggle("Auto-refresh", value=True)
    every = st.slider("Refresh every (seconds)", 3, 30, 5)
    st.divider()
    st.caption(f"Catalog: `{CATALOG}`")
    if not WAREHOUSE:
        st.error("DATABRICKS_WAREHOUSE_ID is not set. Add a SQL warehouse resource to the app.")

kpi = live(f"SELECT * FROM {GOLD}.live_kpis ORDER BY sim_day DESC LIMIT 1")
if not ok(kpi):
    st.info("No live data yet. Run notebook 09_live_simulator, then refresh.")
    if "_error" in kpi:
        st.caption(kpi["_error"].iloc[0])
else:
    k = kpi.iloc[0]
    st.markdown(f"### Simulated date: {pd.Timestamp(k.sim_date):%A %d %B %Y}  ·  day {int(k.sim_day)}")
    c = st.columns(6)
    c[0].metric("On watchlist", f"{int(k.watchlist_total):,}")
    c[1].metric("Monitor", f"{int(k.tier_1_monitor):,}")
    c[2].metric("Watch", f"{int(k.tier_2_watch):,}")
    c[3].metric("Early intervention", f"{int(k.tier_3_early_intervention):,}")
    c[4].metric("Collections", f"{int(k.tier_4_collections):,}")
    c[5].metric("Alerts today", f"{int(k.alerts_today):,}", f"{int(k.events_today)} events")

tab_live, tab_personas, tab_watch, tab_hood = st.tabs(["Live feed", "Customer stories", "Watchlist", "Under the hood"])

# ---------------------------------------------------------------------------
with tab_live:
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Alerts")
        only_stories = st.toggle("Customer stories only", value=False)
        where = "WHERE persona IS NOT NULL" if only_stories else ""
        alerts = live(f"""SELECT sim_day, customer_id, persona, alert_type, tier_before, tier_after, pd_before, pd_after,
                                 new_triggers, pay_lean, recommended_action, summary
                          FROM {GOLD}.live_alerts {where} ORDER BY sim_day DESC, pd_after DESC LIMIT 25""")
        if ok(alerts):
            for _, a in alerts.iterrows():
                who = f"<b>{a.persona}</b> ({a.customer_id})" if pd.notna(a.persona) else a.customer_id
                arrow = f"{badge(a.tier_before)} → {badge(a.tier_after)}" if a.tier_before != a.tier_after else badge(a.tier_after)
                st.markdown(f'<div class="card">Day {int(a.sim_day)} · {who} · {a.alert_type}<br>{arrow} '
                            f'&nbsp; PD {a.pd_before:.1%} → <b>{a.pd_after:.1%}</b> · {a.pay_lean if pd.notna(a.pay_lean) else ""}'
                            f'<div class="small">New signals: {a.new_triggers or "-"}</div>'
                            f'<div class="small"><b>Action:</b> {a.recommended_action}</div>'
                            f'<div class="small">{a.summary}</div></div>', unsafe_allow_html=True)
    with right:
        st.subheader("Watchlist over the month")
        k_hist = live(f"SELECT * FROM {GOLD}.live_kpis ORDER BY sim_day")
        if ok(k_hist):
            m = k_hist.melt(id_vars="sim_day", value_vars=["tier_1_monitor", "tier_2_watch", "tier_3_early_intervention",
                                                           "tier_4_collections"], var_name="tier", value_name="n")
            m["tier"] = m.tier.map({"tier_1_monitor": "Monitor", "tier_2_watch": "Watch",
                                    "tier_3_early_intervention": "Early intervention", "tier_4_collections": "Collections"})
            fig = px.line(m, x="sim_day", y="n", color="tier", color_discrete_map=TIER_COLOUR, height=300,
                          labels={"n": "customers", "sim_day": "simulated day"})
            fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), legend_title=None)
            st.plotly_chart(fig, width="stretch")
        st.subheader("Event stream")
        ev = live(f"""SELECT event_ts, customer_id, source, event_type, detail FROM {GOLD}.live_events
                      ORDER BY sim_day DESC, event_ts DESC LIMIT 30""")
        if ok(ev):
            st.dataframe(ev, hide_index=True, width="stretch", height=420)

# ---------------------------------------------------------------------------
with tab_personas:
    cards = live(f"SELECT * FROM {GOLD}.live_persona_cards ORDER BY customer_id")
    hist = live(f"SELECT sim_day, persona, pd_6m, tier FROM {GOLD}.live_personas ORDER BY sim_day")
    if ok(cards):
        cols = st.columns(3)
        for i, (_, p) in enumerate(cards.iterrows()):
            with cols[i % 3]:
                st.markdown(f'<div class="card"><b>{p.persona}</b> · {p.segment} · {p.region}<br>'
                            f'<span class="small">{p.story}</span><br><br>{badge(p.tier_name)} &nbsp; PD <b>{p.pd_6m:.1%}</b>'
                            f' (band {p.risk_band}) · {p.pay_lean or ""}<div class="small">Signals: {p.fired_triggers or "none"}</div></div>',
                            unsafe_allow_html=True)
        st.divider()
        name = st.selectbox("Follow a customer", cards.persona.tolist())
        p = cards[cards.persona == name].iloc[0]
        a, b = st.columns([3, 2])
        with a:
            if ok(hist):
                h = hist[hist.persona == name]
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=h.sim_day, y=h.pd_6m, mode="lines+markers", name="6-month PD"))
                fig.update_layout(height=260, margin=dict(l=0, r=0, t=30, b=0), title=f"{name}: 6-month PD by day",
                                  yaxis_tickformat=".0%")
                st.plotly_chart(fig, width="stretch")
            tl = live(f"""SELECT e.sim_day, e.source, e.event_type, e.detail FROM {GOLD}.live_events e
                          WHERE e.customer_id = '{p.customer_id}' ORDER BY e.sim_day""")
            if ok(tl):
                st.markdown("**What happened**")
                st.dataframe(tl, hide_index=True, width="stretch")
        with b:
            st.markdown(f"**Decisions today** {badge(p.tier_name)}", unsafe_allow_html=True)
            st.caption(p.tier_reason)
            for key, label in USES:
                st.markdown(f"<div class='small'><b>{label}</b><br>{p[key]}</div>", unsafe_allow_html=True)
            al = live(f"""SELECT sim_day, summary FROM {GOLD}.live_alerts WHERE customer_id = '{p.customer_id}'
                          ORDER BY sim_day DESC LIMIT 1""")
            if ok(al):
                st.markdown("**Analyst summary**")
                st.info(al.summary.iloc[0])

# ---------------------------------------------------------------------------
with tab_watch:
    wl = live(f"SELECT * FROM {GOLD}.live_watchlist")
    if ok(wl):
        f1, f2, f3 = st.columns(3)
        tiers = f1.multiselect("Tier", [t for t in TIER_ORDER if t in set(wl.tier_name)],
                               default=["Watch", "Early intervention"])
        lean = f2.multiselect("Can't / won't pay", sorted(wl.pay_lean.dropna().unique()))
        region = f3.multiselect("Region", sorted(wl.region.dropna().unique()))
        v = wl[wl.tier_name.isin(tiers)] if tiers else wl
        v = v[v.pay_lean.isin(lean)] if lean else v
        v = v[v.region.isin(region)] if region else v
        st.caption(f"{len(v):,} customers")
        st.dataframe(v[["customer_id", "persona", "tier_name", "tier_reason", "pd_6m", "risk_band", "p_arrears_3m",
                        "pay_lean", "fired_triggers", "breadth", "region", "support", "collections"]]
                     .sort_values("pd_6m", ascending=False),
                     hide_index=True, width="stretch", height=520,
                     column_config={"pd_6m": st.column_config.ProgressColumn("PD 6m", format="%.1f%%", min_value=0, max_value=1),
                                    "p_arrears_3m": st.column_config.NumberColumn("P(arrears 3m)", format="%.2f")})
        heat = wl.groupby(["region", "tier_name"]).customer_id.count().reset_index()
        fig = px.density_heatmap(heat, x="tier_name", y="region", z="customer_id", histfunc="sum",
                                 category_orders={"tier_name": TIER_ORDER}, height=420, color_continuous_scale="OrRd")
        fig.update_layout(margin=dict(l=0, r=0, t=30, b=0), title="Watchlist by region and tier", coloraxis_showscale=False)
        st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
with tab_hood:
    st.subheader("Pipeline runs")
    runs = static(f"""SELECT notebook, step, status, rows_written, duration_s, finished_at FROM {OPS}.pipeline_runs
                      ORDER BY finished_at DESC LIMIT 30""")
    if ok(runs):
        st.dataframe(runs, hide_index=True, width="stretch")
    a, b = st.columns(2)
    with a:
        st.subheader("Data quality")
        dqr = static(f"""SELECT rule_id, dimension, description, severity, records_failed, fail_rate, status
                         FROM {OPS}.dq_results WHERE run_id = (SELECT max_by(run_id, run_ts) FROM {OPS}.dq_results)""")
        if ok(dqr):
            st.dataframe(dqr, hide_index=True, width="stretch")
        dqc = static(f"SELECT commentary, source FROM {OPS}.dq_commentary ORDER BY created_at DESC LIMIT 1")
        if ok(dqc):
            st.info(dqc.commentary.iloc[0])
    with b:
        st.subheader("Monitoring")
        mm = static(f"""SELECT metric, value, amber, red, status FROM {OPS}.monitoring_metrics
                        WHERE created_at = (SELECT max(created_at) FROM {OPS}.monitoring_metrics)""")
        if ok(mm):
            st.dataframe(mm, hide_index=True, width="stretch")
        mc = static(f"SELECT commentary FROM {OPS}.monitoring_commentary ORDER BY created_at DESC LIMIT 1")
        if ok(mc):
            st.info(mc.commentary.iloc[0])
    st.subheader("Model comparison (out of time)")
    mcmp = static(f"""SELECT model, is_champion, dev_gini, gini, ks, brier, top_decile_capture, calibration_ratio,
                             psi_vs_dev, explainability FROM {GOLD}.model_comparison ORDER BY gini DESC""")
    if ok(mcmp):
        fig = px.bar(mcmp, x="model", y=["dev_gini", "gini"], barmode="group", height=320,
                     labels={"value": "Gini", "variable": ""})
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
        st.dataframe(mcmp, hide_index=True, width="stretch")
    mcc = static(f"SELECT commentary FROM {GOLD}.model_comparison_commentary ORDER BY created_at DESC LIMIT 1")
    if ok(mcc):
        st.info(mcc.commentary.iloc[0])
    st.subheader("Trigger thresholds")
    th = static(f"""SELECT trigger_id, name, dimension, kind, direction, policy_amber, calibrated_amber, amber,
                           policy_red, calibrated_red, red, threshold_source FROM {OPS}.kri_thresholds ORDER BY trigger_id""")
    if ok(th):
        st.dataframe(th, hide_index=True, width="stretch")

if auto:
    time.sleep(every)
    st.rerun()
