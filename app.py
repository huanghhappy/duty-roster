import streamlit as st
import os
import matplotlib
matplotlib.use('Agg')  # 背景執行
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.font_manager as fm
import pandas as pd
import random
import datetime
import io

# --- 1. 基礎設定 ---
st.set_page_config(page_title="成大整外住院醫師智能排班系統", layout="wide")

if 'generated' not in st.session_state:
    st.session_state.generated = False
    st.session_state.schedule = None
    st.session_state.stats = None
    st.session_state.quotas = None
    st.session_state.mode = None
    st.session_state.fig_schedule = None
    st.session_state.fig_stats = None
    st.session_state.report_text = None
    st.session_state.residents_data = None 

import os
import matplotlib.font_manager as fm
import streamlit as st

@st.cache_resource
def get_chinese_font():
    # 獲取目前 app.py 所在的資料夾路徑
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    font_paths = [
        # 1. 優先讀取：專案資料夾內的 .ttf 檔 (請確認檔名完全一致)
        os.path.join(current_dir, 'NotoSansTC-Regular.ttf'),
        
        # 2. Windows 本機測試用 (微軟正黑體)
        r'C:\Windows\Fonts\msjh.ttc',
        r'C:\Windows\Fonts\msjh.ttf',
        
        # 3. Linux 系統預設 (備用)
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc'
    ]
    
    for path in font_paths:
        if os.path.exists(path): 
            return path
            
    return None

# 設定字體屬性
font_path = get_chinese_font()
font_prop = fm.FontProperties(fname=font_path) if font_path else fm.FontProperties()

# --- 2. 核心排班邏輯 (v26.0 全場景) ---

def calculate_scenario_and_quotas(residents_data, num_days):
    """
    判斷場景 (A/B/C) 並計算每人配額與雙人班目標。
    """
    MAX_SHIFTS = 8
    total_slots_needed_for_double = num_days * 2
    
    r6s = [r for r in residents_data if r['rank'] == 'R6']
    r5s = [r for r in residents_data if r['rank'] == 'R5']
    r4s = [r for r in residents_data if r['rank'] == 'R4']
    r3s = [r for r in residents_data if r['rank'] == 'R3']
    
    total_supply = len(residents_data) * MAX_SHIFTS
    quotas = {r['name']: MAX_SHIFTS for r in residents_data}
    target_double_count = num_days # 預設全雙人
    mode = ""

    # 場景 A: 人力充足 (Total Supply >= 60/62)
    if total_supply >= total_slots_needed_for_double:
        mode = "Scenario A (Surplus)"
        # 減班邏輯：R6 -> R5 -> R4 -> R3
        excess = total_supply - total_slots_needed_for_double
        reduce_order = r6s + r5s + r4s + r3s
        
        while excess > 0:
            reduced = False
            for r in reduce_order:
                name = r['name']
                if quotas[name] > 7 and excess > 0: # 至少值7班，不能減太多
                    quotas[name] -= 1
                    excess -= 1
                    reduced = True
            if not reduced: break
            
        # 場景 A 強制全雙人
        target_double_count = num_days

    # 場景 B/C: 人力不足
    else:
        mode = "Scenario B/C (Shortage)"
        # 配額維持每人 8 班，計算數學極限
        
        # 1. 資深職責需求 (每天至少 1 人站 Line 2 或 Single)
        senior_role_demand = num_days
        senior_supply = (len(r5s) + len(r6s)) * MAX_SHIFTS
        
        # 2. 計算資深缺口 (需 R4 支援的天數)
        senior_deficit = max(0, senior_role_demand - senior_supply)
        
        # 3. 計算 R4 剩餘給一線的人力
        r4_total = len(r4s) * MAX_SHIFTS
        r4_for_line1 = max(0, r4_total - senior_deficit)
        
        # 4. 計算一線總供給
        r3_total = len(r3s) * MAX_SHIFTS
        total_line1_capacity = r3_total + r4_for_line1
        
        # 5. 雙人班天數上限
        target_double_count = min(num_days, total_line1_capacity)

    return quotas, target_double_count, mode

def run_scheduler(year, month, residents_data, flap_dates, fixed_shifts, vs_schedule, custom_holidays):
    
    num_days = pd.Period(f'{year}-{month}').days_in_month
    dates = range(1, num_days + 1)
    weekend_dates = custom_holidays

    seniors = [r['name'] for r in residents_data if r['rank'] in ['R5', 'R6']]
    r4s = [r['name'] for r in residents_data if r['rank'] == 'R4']
    r3s = [r['name'] for r in residents_data if r['rank'] == 'R3']
    all_names = [r['name'] for r in residents_data]
    res_dict = {r['name']: r for r in residents_data}

    # 1. 計算場景參數
    quotas, target_double_count, mode_desc = calculate_scenario_and_quotas(residents_data, num_days)
    
    # 識別 R3/R4 的鎖定 (這些必須是雙人班)
    locked_junior_dates = set()
    for name, locked_days in fixed_shifts.items():
        if res_dict[name]['rank'] in ['R3', 'R4']:
            for d in locked_days: locked_junior_dates.add(d)

    # --- Monte Carlo 模擬 ---
    for attempt in range(10000):
        schedule = {d: {'line1': None, 'line2': None, 'type': 'single'} for d in dates} # 預設單人，慢慢升級
        res_state = {name: {'count': 0, 'dates': [], 'weekend_count': 0, 'single_count': 0, 'flap_count': 0} for name in all_names}
        possible = True
        
        # 2. 分配雙人班名額 (Quota Allocation)
        # 優先級：Junior鎖定 > Flap > 假日 > 平日
        
        current_credits = target_double_count
        double_days = set()
        
        # (1) Junior 鎖定日 (絕對優先)
        for d in locked_junior_dates:
            if current_credits > 0:
                double_days.add(d)
                current_credits -= 1
            else:
                # 理論上不該發生 Credits 不夠 Junior 鎖定的狀況 (除非鎖太多)
                # 若發生，仍強制設為雙人 (因為沒人帶 Junior 會出事)，會稍微爆預算
                double_days.add(d)

        # (2) 剩餘名額分配
        pool_flap = [d for d in dates if d in flap_dates and d not in double_days]
        pool_holiday = [d for d in dates if d in weekend_dates and d not in flap_dates and d not in double_days]
        pool_weekday = [d for d in dates if d not in flap_dates and d not in weekend_dates and d not in double_days]
        
        random.shuffle(pool_flap)
        random.shuffle(pool_holiday)
        random.shuffle(pool_weekday)
        
        priority_list = pool_flap + pool_holiday + pool_weekday
        
        for d in priority_list:
            if current_credits > 0:
                double_days.add(d)
                current_credits -= 1
        
        # 寫入 Schedule Type
        for d in dates:
            if d in double_days: schedule[d]['type'] = 'double'
            else: schedule[d]['type'] = 'single'

        # 3. 填入邏輯 (Pipeline)
        
        def is_available(name, day):
            if day in res_dict[name]['unavailable']: return False
            if (day - 1) in res_state[name]['dates']: return False # No consecutive
            if (day + 1) in res_state[name]['dates']: return False
            if res_state[name]['count'] >= quotas[name]: return False
            if day in res_state[name]['dates']: return False 
            return True
        
        def sort_key(name):
            return (res_state[name]['count'], random.random())

        # Phase 1: 填入指定班 (Fixed)
        fixed_items = list(fixed_shifts.items())
        random.shuffle(fixed_items)
        for p_name, p_dates in fixed_items:
            rank = res_dict[p_name]['rank']
            for d in p_dates:
                if d not in res_state[p_name]['dates']:
                    res_state[p_name]['count'] += 1
                    res_state[p_name]['dates'].append(d)
                    if d in weekend_dates: res_state[p_name]['weekend_count'] += 1
                    # Flap計數稍後算
                
                is_single = (schedule[d]['type'] == 'single')
                
                if rank == 'R3':
                    schedule[d]['line1'] = p_name
                    if is_single: schedule[d]['type'] = 'double' # 強制升級
                elif rank in ['R5', 'R6']:
                    schedule[d]['line2'] = p_name
                elif rank == 'R4':
                    # R4 鎖定：單人日填 L2，雙人日填 L1 (優先)
                    if is_single: schedule[d]['line2'] = p_name
                    else:
                        if schedule[d]['line1']: schedule[d]['line2'] = p_name
                        else: schedule[d]['line1'] = p_name

        # Phase 2: 填補 Senior Role (單人 + 雙人Line2)
        # 優先順序：Flap > 假日 > 平日 (因為單人Flap最痛苦，雙人Flap L2最重要)
        senior_slots = []
        for d in dates:
            if schedule[d]['line2'] is None:
                # 權重計算
                weight = 0
                if d in flap_dates: weight += 100
                if d in weekend_dates: weight += 50
                if schedule[d]['type'] == 'single': weight += 20 # 單人優先於雙人L2 (為了保證有人)
                senior_slots.append((d, weight))
        
        senior_slots.sort(key=lambda x: x[1], reverse=True)
        
        for d, w in senior_slots:
            # 先找 R5/R6 (主力)
            cands = [s for s in seniors if is_available(s, d)]
            
            # 若沒人，找 R4 (支援)
            if not cands:
                cands = [r for r in r4s if is_available(r, d)]
                # 若是雙人班，需避開 Line 1 是自己的情況 (已擋連值，這裡擋同日)
                if schedule[d]['line1']:
                    cands = [c for c in cands if c != schedule[d]['line1']]
            
            if cands:
                cands.sort(key=sort_key)
                p = cands[0]
                schedule[d]['line2'] = p
                res_state[p]['count'] += 1; res_state[p]['dates'].append(d)
                if d in weekend_dates: res_state[p]['weekend_count'] += 1
            else:
                # 在極限模式下，這一步失敗率高，需要 R4 更多支援
                possible = False; break
        
        if not possible: continue

        # Phase 3: 填補 Junior Role (雙人Line1)
        junior_slots = [d for d in dates if schedule[d]['type'] == 'double' and schedule[d]['line1'] is None]
        # 排序：Flap > 假日 > 平日
        junior_slots.sort(key=lambda x: (0 if x in flap_dates else 1, 0 if x in weekend_dates else 1))
        
        for d in junior_slots:
            # 優先找 R3
            pool = r3s + r4s
            cands = [j for j in pool if is_available(j, d)]
            
            # 避開 L2
            l2 = schedule[d]['line2']
            cands = [c for c in cands if c != l2]
            
            if cands:
                cands.sort(key=sort_key)
                p = cands[0]
                schedule[d]['line1'] = p
                res_state[p]['count'] += 1; res_state[p]['dates'].append(d)
                if d in weekend_dates: res_state[p]['weekend_count'] += 1
            else:
                possible = False; break
        
        if not possible: continue

        # 最終統計與檢查 (Stats)
        for name in all_names:
            res_state[name]['flap_count'] = 0
            res_state[name]['single_count'] = 0
            
        for d in dates:
            info = schedule[d]
            l2 = info['line2']
            # Flap 統計：僅計算 二線 或 單人 (即 line2 位置)
            if l2 and d in flap_dates:
                res_state[l2]['flap_count'] += 1
            # Single 統計
            if info['type'] == 'single' and l2:
                res_state[l2]['single_count'] += 1

        # 驗收：場景 B/C 允許誤差，場景 A 盡量滿
        # 這裡只要能跑完流程，且每人班數不要太離譜 (>= quotas - 2) 就回傳
        # 剩下的交給人為微調
        return schedule, res_state, mode_desc, quotas

    return None, None, None, None

# --- 3. 生成報告與圖表 ---

def generate_logic_report(year, month, schedule, stats, mode, quotas, residents_data, flap_dates, weekend_dates):
    lines = []
    lines.append(f"【智能排班邏輯說明報告】 {year}年{month}月")
    lines.append("="*40)
    
    single_count = sum(1 for d in schedule if schedule[d]['type'] == 'single')
    
    lines.append(f"1. 判斷場景：{mode}")
    lines.append(f"   - 雙人班名額分配順序：指定值班 > Flap > 假日 > 平日。")
    if single_count > 0:
        lines.append(f"   - 因人力結構限制，本月安排 {single_count} 天單人值班。")
        lines.append(f"   - 單人班已依照痛苦程度 (Flap單人 > 假日單人 > 平日單人) 盡量避免高痛點。")
    else:
        lines.append(f"   - 人力充足，全月雙人值班。")
    
    l4_count = 0 
    r4_names = [r['name'] for r in residents_data if r['rank'] == 'R4']
    for d, info in schedule.items():
        if info['type'] == 'single' and info['line2'] in r4_names:
            l4_count += 1

    if l4_count > 0: lines.append(f"   - ⚠️ R4 支援：R4 支援單人值班共 {l4_count} 天 (填補資深缺口)。")

    lines.append(f"\n2. 醫師目標班數：")
    for r in residents_data:
        lines.append(f"   - {r['name']}: 目標 {quotas[r['name']]} 班 | 實際 {stats[r['name']]['count']} 班")
    
    lines.append(f"\n4. 公平性數據 (Flap班僅統計二線/單人)：")
    lines.append(f"   {'醫師':<6} {'總班':<4} {'假日':<4} {'單人':<4} {'Flap':<4}")
    lines.append("-" * 40)
    for r in residents_data:
        n = r['name']
        s = stats[n]
        lines.append(f"   {n:<6} {s['count']:<4} {s['weekend_count']:<4} {s['single_count']:<4} {s['flap_count']:<4}")

    return "\n".join(lines)

def plot_schedule(year, month, schedule, flap_dates, weekend_dates, vs_schedule, font_prop, mode, residents_data):
    # Colors
    c_double_flap = '#E8F5E9'     
    c_double_holiday = '#FFEBEE'  
    c_double_normal = '#FFFFFF'   
    c_single_normal = '#FFF9C4'   
    c_single_holiday = '#F48FB1'  
    c_single_flap = '#81C784'     
    c_single_r4 = '#FFB74D'       
    c_text = '#424242'
    c_line = '#E0E0E0'

    r4_names = [r['name'] for r in residents_data if r['rank'] == 'R4']

    fig, ax = plt.subplots(figsize=(12, 12)) 
    ax.set_xlim(0, 7)
    ax.set_ylim(-1.5, 6) 
    ax.axis('off')

    cal = pd.Period(f'{year}-{month}')
    start_weekday = datetime.date(year, month, 1).weekday()
    days_in_month = cal.days_in_month
    weeks = (start_weekday + days_in_month) // 7 + 1
    if (start_weekday + days_in_month) % 7 == 0: weeks -= 1
    row_height = (6 - 0.5) / weeks
    
    weekdays_text = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
    for i, d in enumerate(weekdays_text):
        ax.text(i + 0.5, 6 - 0.25, d, ha='center', va='center', fontsize=12, fontweight='bold', color=c_text)

    current_day = 1
    for w in range(weeks):
        vs_name = vs_schedule[w] if w < len(vs_schedule) else ""
        ax.text(-0.3, 6 - 0.5 - w * row_height - row_height/2, f"{vs_name}", 
                ha='center', va='center', fontsize=14, fontweight='bold', color=c_text, fontproperties=font_prop)

        for d_idx in range(7):
            if w == 0 and d_idx < start_weekday: continue
            if current_day > days_in_month: break
                
            x, y_bot = d_idx, 6 - 0.5 - w * row_height - row_height
            info = schedule[current_day]
            is_single = (info['type'] == 'single')
            is_flap = current_day in flap_dates
            is_holiday = current_day in weekend_dates
            name_on_duty = info['line2'] if info['line2'] else info['line1']
            is_r4_solo = is_single and (name_on_duty in r4_names)

            bg_color = c_double_normal 
            if is_single:
                if is_r4_solo: bg_color = c_single_r4      
                elif is_flap: bg_color = c_single_flap    
                elif is_holiday: bg_color = c_single_holiday 
                else: bg_color = c_single_normal  
            else:
                if is_flap: bg_color = c_double_flap
                elif is_holiday: bg_color = c_double_holiday
            
            ax.add_patch(patches.Rectangle((x, y_bot), 1, row_height, linewidth=1, edgecolor=c_line, facecolor=bg_color))
            ax.text(x + 0.05, y_bot + row_height - 0.05, str(current_day), ha='left', va='top', fontsize=10, fontweight='bold', color=c_text)
            
            l1, l2 = info['line1'], info['line2']
            if is_single:
                ax.text(x + 0.5, y_bot + row_height/2, str(l2 if l2 else l1), ha='center', va='center', fontsize=16, color=c_text, fontproperties=font_prop)
            else:
                ax.text(x + 0.5, y_bot + row_height*0.65, str(l1) if l1 else "-", ha='center', va='center', fontsize=14, color=c_text, fontproperties=font_prop)
                ax.text(x + 0.5, y_bot + row_height*0.25, str(l2) if l2 else "-", ha='center', va='center', fontsize=14, color=c_text, fontproperties=font_prop)
            current_day += 1
            
    title_text = f'{year}年 {month}月 住院醫師班表'
    if "Scenario A" in mode: title_text += " (標準模式)"
    else: title_text += " (缺工生存模式)"
    ax.text(3.5, 6.2, title_text, ha='center', va='center', fontsize=18, fontweight='bold', color=c_text, fontproperties=font_prop)

    legend_y = -0.6
    ax.text(0.5, legend_y, "底色說明：", fontsize=12, fontweight='bold', color=c_text, fontproperties=font_prop)
    ax.add_patch(patches.Rectangle((1.5, legend_y-0.15), 0.3, 0.3, facecolor=c_double_flap, edgecolor='gray')); ax.text(1.9, legend_y, "Flap雙人", va='center', fontsize=10, fontproperties=font_prop)
    ax.add_patch(patches.Rectangle((3.0, legend_y-0.15), 0.3, 0.3, facecolor=c_double_holiday, edgecolor='gray')); ax.text(3.4, legend_y, "假日雙人", va='center', fontsize=10, fontproperties=font_prop)
    ax.add_patch(patches.Rectangle((4.5, legend_y-0.15), 0.3, 0.3, facecolor=c_single_normal, edgecolor='gray')); ax.text(4.9, legend_y, "平日單人", va='center', fontsize=10, fontproperties=font_prop)
    legend_y2 = -1.0
    ax.add_patch(patches.Rectangle((1.5, legend_y2-0.15), 0.3, 0.3, facecolor=c_single_flap, edgecolor='gray')); ax.text(1.9, legend_y2, "Flap單人", va='center', fontsize=10, fontproperties=font_prop)
    ax.add_patch(patches.Rectangle((3.0, legend_y2-0.15), 0.3, 0.3, facecolor=c_single_holiday, edgecolor='gray')); ax.text(3.4, legend_y2, "假日單人", va='center', fontsize=10, fontproperties=font_prop)
    ax.add_patch(patches.Rectangle((4.5, legend_y2-0.15), 0.3, 0.3, facecolor=c_single_r4, edgecolor='gray')); ax.text(4.9, legend_y2, "R4單人", va='center', fontsize=10, fontproperties=font_prop)
    return fig

def plot_stats_table(stats, quotas, residents_data, font_prop):
    columns = ["醫師", "職級", "總班數", "目標", "假日班", "單人班", "Flap班(二線)"]
    cell_data = []
    for r in residents_data:
        n = r['name']
        s = stats[n]
        cell_data.append([n, r['rank'], s['count'], quotas[n], s['weekend_count'], s['single_count'], s['flap_count']])
    fig, ax = plt.subplots(figsize=(8, len(residents_data) * 0.5 + 2))
    ax.axis('off'); ax.axis('tight')
    table = ax.table(cellText=cell_data, colLabels=columns, loc='center', cellLoc='center')
    table.auto_set_font_size(False); table.set_fontsize(12); table.scale(1.2, 1.5)
    for key, cell in table.get_celld().items():
        cell.set_text_props(fontproperties=font_prop)
        if key[0] == 0: cell.set_text_props(weight='bold', color='white'); cell.set_facecolor('#424242')
    plt.title("公平性詳細數據統計", fontproperties=font_prop, fontsize=16, pad=20)
    return fig

# --- 4. Streamlit UI ---
st.title("🏥 成大整外住院醫師智能排班系統")
st.markdown("---")
col_a, col_b = st.columns([1, 2])
with col_a:
    year = st.number_input("年份", 2024, 2030, 2026)
    month = st.number_input("月份", 1, 12, 6)
    num_residents = st.number_input("住院醫師總數", 4, 15, 8)
with col_b:
    st.info("""
    **智能排班重點邏輯：**
    1. **一鍵智能**：減輕CR排班身心壓力，目標實現零負評的自動化班表。
    2. **全境適配**：涵蓋場景A (8人標準)、B (7人小缺)、C (6人極限)，自動識別切換模式。
    3. **智慧公平**：系統自動演算，將假日班/單值班/減班數/flap手術日盡量達成最佳化平均。
    4. **先雙再單**：優先排雙人值班，無解才解鎖單人值班。
    5. **優序配置**：缺工生存模式的雙人班名額，依痛苦階梯依序分配給flap日>假日>平日。
    6. **跨級補位**：極限場景需R4跨級填補部分資深人力缺口，確保排班成功率。
    7. **完整輸出**：一鍵同時產出「班表圖檔」、「班數統計圖表」、「智能排班邏輯說明」。
    8. **視覺警示**：班表圖檔採視覺化底色分級，區分該班別的風險等級與人力配置狀況。
    """)

days_in_month = pd.Period(f'{year}-{month}').days_in_month
all_days = list(range(1, days_in_month + 1))

st.header("1. 當月值班住院醫師名單")
residents_input = []
cols = st.columns(3)
fixed_shifts_map = {}
# 預設為八人名單
default_ranks = ['R3', 'R3', 'R4', 'R4', 'R5', 'R5', 'R6', 'R6']
for i in range(num_residents):
    with cols[i % 3]:
        with st.container(border=True):
            def_rank = default_ranks[i] if i < len(default_ranks) else 'R3'
            name = st.text_input(f"姓名", value=f"醫師{i+1}", key=f"n_{i}")
            rank = st.selectbox(f"職級", ['R3', 'R4', 'R5', 'R6'], index=['R3','R4','R5','R6'].index(def_rank), key=f"r_{i}")
            off = st.multiselect("休假/預約不值班", all_days, key=f"off_{i}")
            fix = st.multiselect("指定值班", all_days, key=f"fix_{i}")
            residents_input.append({'name': name, 'rank': rank, 'unavailable': off})
            if fix: fixed_shifts_map[name] = fix

st.header("2. 已知 flap combine 刀日")
flap_input = st.multiselect("請選擇目前已知日期", all_days)

st.header("3. 當月假日 (含國定假日/彈性假日)")
default_weekends = []
for d in all_days:
    dt = datetime.date(year, month, d)
    if dt.weekday() >= 5: default_weekends.append(d)
holiday_input = st.multiselect("請確認假日 (系統預設週六日，可自行增減)", all_days, default=default_weekends)

st.header("4. VS 輪值名單")
vs_input = []
c_vs = st.columns(6)
for i in range(6):
    with c_vs[i]:
        v = st.text_input(f"第 {i+1} 週 VS", key=f"vs_{i}")
        if v: vs_input.append(v)

st.markdown("---")

if st.button("🚀 生成班表", type="primary"):
    with st.spinner("正在進行 Monte Carlo 模擬運算 (全場景通用)..."):
        schedule, stats, mode, quotas = run_scheduler(year, month, residents_input, flap_input, fixed_shifts_map, vs_input, holiday_input)
        if schedule:
            st.session_state.generated = True; st.session_state.schedule = schedule; st.session_state.stats = stats; st.session_state.quotas = quotas; st.session_state.mode = mode; st.session_state.residents_data = residents_input 
            st.session_state.fig_schedule = plot_schedule(year, month, schedule, flap_input, holiday_input, vs_input, font_prop, mode, residents_input)
            st.session_state.fig_stats = plot_stats_table(stats, quotas, residents_input, font_prop)
            st.session_state.report_text = generate_logic_report(year, month, schedule, stats, mode, quotas, residents_input, flap_input, holiday_input)
            st.rerun()
        else:
            st.error(f"❌ 排班失敗。請確認是否鎖定日期衝突過多。")

if st.session_state.generated:
    st.success(f"✅ 排班成功！ (模式：{st.session_state.mode})")
    st.pyplot(st.session_state.fig_schedule)
    st.pyplot(st.session_state.fig_stats)
    c1, c2, c3 = st.columns(3)
    buf_sch = io.BytesIO(); st.session_state.fig_schedule.savefig(buf_sch, format="png", dpi=200, bbox_inches='tight')
    c1.download_button("⬇️ 下載班表圖檔 (.png)", buf_sch.getvalue(), f"schedule_{year}_{month}.png", "image/png")
    buf_stat = io.BytesIO(); st.session_state.fig_stats.savefig(buf_stat, format="png", dpi=200, bbox_inches='tight')
    c2.download_button("⬇️ 下載班數統計圖表 (.png)", buf_stat.getvalue(), f"stats_{year}_{month}.png", "image/png")

    c3.download_button("⬇️ 下載智能排班邏輯說明 (.txt)", st.session_state.report_text, f"report_{year}_{month}.txt", "text/plain")
