import streamlit as st
import os
import matplotlib
matplotlib.use('Agg')  # 設定 matplotlib 在背景執行，避免 GUI 錯誤
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.font_manager as fm
import pandas as pd
import random
import datetime
import io
import base64

# --- 1. 基礎設定 (必須放在程式碼最上方) ---

# 設定網頁標題、寬度佈局、以及瀏覽器分頁的小圖示
st.set_page_config(
    page_title="成大整外住院醫師智能排班系統", 
    page_icon="logo.png",  # 這會讀取您上傳的 logo.png
    layout="wide"
)

# --- 2. 手機主畫面 Icon 設定函式 (網址版) ---
def setup_app_icon(icon_url):
    icon_tags = f'''
    <style>
        /* Icon Injection */
    </style>
    <link rel="apple-touch-icon" href="{icon_url}">
    <link rel="icon" type="image/png" sizes="192x192" href="{icon_url}">
    <link rel="shortcut icon" href="{icon_url}">
    '''
    st.markdown(icon_tags, unsafe_allow_html=True)

# ⬇️⬇️⬇️ 請把下面這行引號內的網址，換成您剛剛複製到的那串 ⬇️⬇️⬇️
my_icon_url = "https://raw.githubusercontent.com/huanghhappy/duty-roster/main/logo.png" 

# 執行設定
setup_app_icon(my_icon_url)

# 初始化 Session State
if 'generated' not in st.session_state:
    st.session_state.generated = False

if 'result_df' not in st.session_state:
    st.session_state.result_df = None

# --- 3. 字型設定 ---
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

# --- 4. 核心排班邏輯 (v26.1 含標準模式修正) ---

def calculate_standard_8_person_shifts(residents_data, num_days):
    """
    [新增] 八人標準模式專用計算邏輯
    原則：
    1. 嚴格區分一線(R3/4)與二線(R5/6)
    2. 一線總班數 = 天數 (每天一班)
    3. 二線總班數 = 天數 (每天一班)
    4. 剩餘班數(餘數)由資淺者(R3, R5)優先承擔
    """
    # 1. 分組並排序 (確保資淺在前，以便優先分配餘數)
    r3s = sorted([r for r in residents_data if r['rank'] == 'R3'], key=lambda x: x['name'])
    r4s = sorted([r for r in residents_data if r['rank'] == 'R4'], key=lambda x: x['name'])
    r5s = sorted([r for r in residents_data if r['rank'] == 'R5'], key=lambda x: x['name'])
    r6s = sorted([r for r in residents_data if r['rank'] == 'R6'], key=lambda x: x['name'])

    quotas = {r['name']: 0 for r in residents_data}

    # 2. 定義一線與二線的人力池
    # 順序很重要：[R3, R4] -> 餘數會先給 R3
    line1_pool = r3s + r4s 
    # 順序很重要：[R5, R6] -> 餘數會先給 R5
    line2_pool = r5s + r6s

    def distribute_shifts(pool, total_slots):
        if not pool: return
        n = len(pool)
        base_shifts = total_slots // n  # 基本班數
        remainder = total_slots % n     # 餘數

        for i, r in enumerate(pool):
            # 前 remainder 個人多拿一班
            extra = 1 if i < remainder else 0
            quotas[r['name']] = base_shifts + extra

    # 3. 執行分配
    distribute_shifts(line1_pool, num_days) # 一線分配
    distribute_shifts(line2_pool, num_days) # 二線分配

    return quotas

def calculate_scenario_and_quotas(residents_data, num_days):
    """
    判斷場景並計算配額。
    新增：判斷是否為標準 8 人模式 (R3/4/5/6 各 2 人)。
    """
    MAX_SHIFTS = 8
    total_slots_needed_for_double = num_days * 2
    
    r6s = [r for r in residents_data if r['rank'] == 'R6']
    r5s = [r for r in residents_data if r['rank'] == 'R5']
    r4s = [r for r in residents_data if r['rank'] == 'R4']
    r3s = [r for r in residents_data if r['rank'] == 'R3']
    
    # 判斷是否為標準 8 人模式 (Strict Mode)
    is_standard_8 = (len(r6s)==2 and len(r5s)==2 and len(r4s)==2 and len(r3s)==2)
    
    strict_mode = False
    quotas = {}
    target_double_count = num_days
    mode = ""

    if is_standard_8:
        # --- 標準 8 人模式邏輯 ---
        mode = "Standard 8-Person (Strict Line Separation)"
        strict_mode = True # 開啟嚴格分流
        target_double_count = num_days # 全雙人
        # 使用專用函數計算 (R3/4 一線, R5/6 二線)
        quotas = calculate_standard_8_person_shifts(residents_data, num_days)

    else:
        # --- 原有邏輯 (非標準模式) ---
        total_supply = len(residents_data) * MAX_SHIFTS
        quotas = {r['name']: MAX_SHIFTS for r in residents_data}
        
        if total_supply >= total_slots_needed_for_double:
            mode = "Scenario A (Surplus)"
            # 減班邏輯：R6 -> R5 -> R4 -> R3
            excess = total_supply - total_slots_needed_for_double
            reduce_order = r6s + r5s + r4s + r3s
            while excess > 0:
                reduced = False
                for r in reduce_order:
                    name = r['name']
                    if quotas[name] > 7 and excess > 0:
                        quotas[name] -= 1
                        excess -= 1
                        reduced = True
                if not reduced: break
            target_double_count = num_days
        else:
            mode = "Scenario B/C (Shortage)"
            senior_role_demand = num_days
            senior_supply = (len(r5s) + len(r6s)) * MAX_SHIFTS
            senior_deficit = max(0, senior_role_demand - senior_supply)
            r4_total = len(r4s) * MAX_SHIFTS
            r4_for_line1 = max(0, r4_total - senior_deficit)
            r3_total = len(r3s) * MAX_SHIFTS
            total_line1_capacity = r3_total + r4_for_line1
            target_double_count = min(num_days, total_line1_capacity)

    return quotas, target_double_count, mode, strict_mode

# --- 修正後的核心排班邏輯 (v26.2 極限模式優化版) ---

def run_scheduler(year, month, residents_data, flap_dates, fixed_shifts, vs_schedule, custom_holidays):
    
    num_days = pd.Period(f'{year}-{month}').days_in_month
    dates = range(1, num_days + 1)
    weekend_dates = custom_holidays

    seniors = [r['name'] for r in residents_data if r['rank'] in ['R5', 'R6']]
    r4s = [r['name'] for r in residents_data if r['rank'] == 'R4']
    r3s = [r['name'] for r in residents_data if r['rank'] == 'R3']
    all_names = [r['name'] for r in residents_data]
    res_dict = {r['name']: r for r in residents_data}
    
    is_extreme_mode = (len(residents_data) <= 6)

    # 1. 計算場景參數
    quotas, target_double_count, mode_desc, strict_mode = calculate_scenario_and_quotas(residents_data, num_days)
    
    # 極限模式：允許 Quota 彈性 +1 以確保生成成功，後續再用微調修回來
    if is_extreme_mode:
        for name in quotas: quotas[name] += 1 

    # 計算 Credits
    if is_extreme_mode:
        r3_shifts = len(r3s) * 8
        r4_shifts = len(r4s) * 8
        senior_demand = num_days
        senior_supply = len(seniors) * 8
        r4_support_line2 = max(0, senior_demand - senior_supply) 
        r4_for_line1 = max(0, r4_shifts - r4_support_line2)
        real_double_credits = r3_shifts + r4_for_line1
        target_double_count = min(num_days, real_double_credits)

    locked_junior_dates = set()
    for name, locked_days in fixed_shifts.items():
        if res_dict[name]['rank'] in ['R3', 'R4']:
            for d in locked_days: locked_junior_dates.add(d)

    # --- Monte Carlo 模擬 ---
    for attempt in range(5000):
        schedule = {d: {'line1': None, 'line2': None, 'type': 'single', 'warning': ''} for d in dates}
        res_state = {name: {'count': 0, 'dates': [], 'weekend_count': 0, 'single_count': 0, 'flap_count': 0} for name in all_names}
        possible = True
        
        # 2. 分配雙人班名額
        current_credits = target_double_count
        double_days = set()
        
        for d in locked_junior_dates:
            double_days.add(d)
            if current_credits > 0: current_credits -= 1

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
        
        for d in dates:
            if d in double_days: schedule[d]['type'] = 'double'
            else: schedule[d]['type'] = 'single'

        # --- Helper: Check Availability ---
        def is_available(name, day, strict_consecutive=True, strict_quota=True):
            if day in res_dict[name]['unavailable']: return False
            if day in res_state[name]['dates']: return False 
            if strict_quota and res_state[name]['count'] >= quotas[name]: return False
            if strict_consecutive:
                if (day - 1) in res_state[name]['dates']: return False 
                if (day + 1) in res_state[name]['dates']: return False
            return True

        # Phase 1: Fixed Shifts
        fixed_items = list(fixed_shifts.items())
        random.shuffle(fixed_items)
        for p_name, p_dates in fixed_items:
            rank = res_dict[p_name]['rank']
            for d in p_dates:
                if d not in res_state[p_name]['dates']:
                    res_state[p_name]['count'] += 1
                    res_state[p_name]['dates'].append(d)
                    if d in weekend_dates: res_state[p_name]['weekend_count'] += 1
                
                is_single = (schedule[d]['type'] == 'single')
                if rank == 'R3':
                    schedule[d]['line1'] = p_name
                    if is_single: schedule[d]['type'] = 'double'
                elif rank in ['R5', 'R6']:
                    schedule[d]['line2'] = p_name
                elif rank == 'R4':
                    if is_single: schedule[d]['line2'] = p_name
                    else:
                        if schedule[d]['line1']: schedule[d]['line2'] = p_name
                        else: schedule[d]['line1'] = p_name

        # Phase 2: Fill Line 2 (Senior Role)
        senior_slots = []
        for d in dates:
            if schedule[d]['line2'] is None:
                is_flap = d in flap_dates
                is_weekend = d in weekend_dates
                priority = 0
                if is_extreme_mode:
                    if is_weekend: priority = 200 
                    elif is_flap: priority = 100
                    else: priority = 10
                else:
                    if is_flap: priority = 100
                    elif is_weekend: priority = 50
                    else: priority = 10
                senior_slots.append((d, priority))
        
        senior_slots.sort(key=lambda x: x[1], reverse=True)
        
        for d, prio in senior_slots:
            # Definition of candidates
            pool = []
            if strict_mode and not is_extreme_mode: pool = seniors
            else: pool = seniors + r4s
            
            l1 = schedule[d]['line1']
            current_pool = [p for p in pool if p != l1]

            # Survival Protocol
            cands = [p for p in current_pool if is_available(p, d, True, True)]
            if not cands:
                cands = [p for p in current_pool if is_available(p, d, False, True)]
                if cands: schedule[d]['warning'] += '連值 '
            if not cands:
                cands = [p for p in current_pool if is_available(p, d, True, False)]
                if cands: schedule[d]['warning'] += '超班 '
            if not cands:
                cands = [p for p in current_pool if is_available(p, d, False, False)]
                if cands: schedule[d]['warning'] += '連值+超班 '

            if cands:
                def get_p2_key(n):
                    rank = res_dict[n]['rank']
                    st_score = 10
                    if is_extreme_mode:
                        if (d in weekend_dates): st_score = 0 if rank=='R4' else 1
                        elif (d in flap_dates): st_score = 0 if rank in ['R5','R6'] else 1
                        else: st_score = 0 if rank in ['R5','R6'] else 1
                    else: st_score = 0 if rank in ['R5','R6'] else 1
                    return (st_score, res_state[n]['count'], random.random())
                
                cands.sort(key=get_p2_key)
                best = cands[0]
                schedule[d]['line2'] = best
                res_state[best]['count'] += 1
                res_state[best]['dates'].append(d)
                if d in weekend_dates: res_state[best]['weekend_count'] += 1
            else:
                possible = False; break

        if not possible: continue

        # Phase 3: Fill Line 1 (Junior Role)
        junior_slots = [d for d in dates if schedule[d]['type'] == 'double' and schedule[d]['line1'] is None]
        junior_slots.sort(key=lambda x: (0 if x in flap_dates else 1, 0 if x in weekend_dates else 1))
        
        for d in junior_slots:
            pool = r3s + r4s
            l2 = schedule[d]['line2']
            current_pool = [p for p in pool if p != l2]
            
            cands = [p for p in current_pool if is_available(p, d, True, True)]
            if not cands:
                cands = [p for p in current_pool if is_available(p, d, False, True)]
                if cands: schedule[d]['warning'] += 'L1連值 '
            if not cands:
                cands = [p for p in current_pool if is_available(p, d, True, False)]
                 
            if cands:
                cands.sort(key=lambda n: (res_state[n]['count'], random.random()))
                best = cands[0]
                schedule[d]['line1'] = best
                res_state[best]['count'] += 1
                res_state[best]['dates'].append(d)
                if d in weekend_dates: res_state[best]['weekend_count'] += 1
            else:
                schedule[d]['type'] = 'single'
                if 'L1連值' in schedule[d]['warning']: schedule[d]['warning'] = schedule[d]['warning'].replace('L1連值', '')

        # ==========================================
        # Phase 4: 智慧微調 (Smart Rebalancing) - 新增區塊
        # ==========================================
        
        # [Step A: Senior (Over) -> R4 (Under)]
        # 嘗試把 R5/R6 多出的班，轉移給 R4 (若 R4 還沒滿班)
        target_shift_per_person = 8 # 理想目標
        
        # 找出超班的 Senior 和 缺班的 R4
        over_seniors = [n for n in seniors if res_state[n]['count'] > target_shift_per_person]
        under_r4s = [n for n in r4s if res_state[n]['count'] < target_shift_per_person]
        
        if over_seniors and under_r4s:
            # 建立可交換的日期池 (候選日)
            # 優先順序：Flap日(若R4要扛) > 平日單人 > 平日雙人
            # 這裡我們專注抓出「平日 Flap」或「平日 Single」
            swap_candidates = []
            for d in dates:
                l2 = schedule[d]['line2']
                if l2 in over_seniors and d not in weekend_dates:
                    priority = 0
                    if d in flap_dates: priority = 10 # 地獄月優先轉移 Flap
                    elif schedule[d]['type'] == 'single': priority = 5 # 平靜月優先轉移 Single
                    else: priority = 1
                    swap_candidates.append((d, l2, priority))
            
            swap_candidates.sort(key=lambda x: x[2], reverse=True)
            
            for d, senior_name, prio in swap_candidates:
                # 重新檢查是否有缺班的 R4
                under_r4s = [n for n in r4s if res_state[n]['count'] < target_shift_per_person]
                if not under_r4s: break # R4 都飽了，停止
                
                # 檢查 R4 當天是否有空 (Strict check)
                valid_r4 = [r for r in under_r4s if is_available(r, d, True, False) and r != schedule[d]['line1']]
                
                if valid_r4 and res_state[senior_name]['count'] > target_shift_per_person:
                    # 執行交換
                    r4_name = valid_r4[0]
                    
                    # 1. 扣除 Senior
                    schedule[d]['line2'] = r4_name
                    res_state[senior_name]['count'] -= 1
                    res_state[senior_name]['dates'].remove(d)
                    
                    # 2. 加入 R4
                    res_state[r4_name]['count'] += 1
                    res_state[r4_name]['dates'].append(d)
                    
                    # (如果是假日交換，理論上這裡有過濾掉假日，但若邏輯放寬需注意 weekend_count 更新)

        # [Step B: R3 (Under) -> Fill Single Slots]
        # 嘗試把缺班的 R3 塞進 Single 班，使其升級為 Double
        under_r3s = [n for n in r3s if res_state[n]['count'] < target_shift_per_person]
        
        if under_r3s:
            # 找出單人班日子
            single_days = [d for d in dates if schedule[d]['type'] == 'single']
            # 優先填 Flap 單人 (優先救苦)
            single_days.sort(key=lambda x: 10 if x in flap_dates else 1, reverse=True)
            
            for d in single_days:
                under_r3s = [n for n in r3s if res_state[n]['count'] < target_shift_per_person]
                if not under_r3s: break
                
                l2 = schedule[d]['line2']
                # 檢查 R3 是否有空
                valid_r3 = [r for r in under_r3s if is_available(r, d, True, False) and r != l2]
                
                if valid_r3:
                    r3_name = valid_r3[0]
                    # 執行填補
                    schedule[d]['line1'] = r3_name
                    schedule[d]['type'] = 'double' # 升級！
                    
                    res_state[r3_name]['count'] += 1
                    res_state[r3_name]['dates'].append(d)
                    if d in weekend_dates: res_state[r3_name]['weekend_count'] += 1

        # ==========================================
        # End of Phase 4
        # ==========================================

        # 最終統計與檢查
        for name in all_names:
            res_state[name]['flap_count'] = 0
            res_state[name]['single_count'] = 0
            
        for d in dates:
            info = schedule[d]
            l2 = info['line2']
            
            # Flap 統計：只要該日是 Flap 且你是 Line 2 (不論 Senior 或 R4)，都算 Flap 班
            if l2 and d in flap_dates:
                res_state[l2]['flap_count'] += 1
            
            # Single 統計
            if info['type'] == 'single' and l2:
                res_state[l2]['single_count'] += 1

        return schedule, res_state, mode_desc, quotas

    return None, None, None, None

# --- 5. 生成報告與圖表 ---

def generate_logic_report(year, month, schedule, stats, mode, quotas, residents_data, flap_dates, weekend_dates):
    lines = []
    lines.append(f"【智能排班邏輯說明報告】 {year}年{month}月")
    lines.append("="*40)
    
    single_count = sum(1 for d in schedule if schedule[d]['type'] == 'single')
    
    lines.append(f"1. 判斷場景：{mode}")
    if "Standard 8-Person" in mode:
        lines.append(f"   - 啟動【8人標準模式】：嚴格執行職級分流。")
        lines.append(f"   - 一線班(Line 1)：僅由 R3、R4 擔任。")
        lines.append(f"   - 二線班(Line 2)：僅由 R5、R6 擔任。")
        lines.append(f"   - 班數分配：資淺者(R3, R5)優先承擔剩餘班數(例如30天=R6七班+R5八班)。")
    elif single_count > 0:
        lines.append(f"   - 因人力結構限制，本月安排 {single_count} 天單人值班。")
        lines.append(f"   - 單人班已依照痛苦程度 (Flap單人 > 假日單人 > 平日單人) 盡量避免高痛點。")
    else:
        lines.append(f"   - 人力充足，全月雙人值班。")
    
    lines.append(f"\n2. 醫師目標班數：")
    for r in residents_data:
        lines.append(f"   - {r['name']}: 目標 {quotas[r['name']]} 班 | 實際 {stats[r['name']]['count']} 班")
    
    lines.append(f"\n3. 公平性數據 (Flap班僅統計二線/單人)：")
    lines.append(f"   {'醫師':<6} {'總班':<4} {'假日':<4} {'單人':<4} {'Flap':<4}")
    lines.append("-" * 40)
    for r in residents_data:
        n = r['name']
        s = stats[n]
        lines.append(f"   {n:<6} {s['count']:<4} {s['weekend_count']:<4} {s['single_count']:<4} {s['flap_count']:<4}")

    return "\n".join(lines)

def plot_schedule(year, month, schedule, flap_dates, weekend_dates, vs_schedule, font_prop, mode, residents_data):
    # Colors Definition
    c_double_flap = '#E8F5E9'     # 淺綠 (雙人Flap)
    c_double_holiday = '#FFEBEE'  # 淺粉 (雙人假日)
    c_double_normal = '#FFFFFF'   # 白 (雙人平日)
    
    c_single_normal = '#FFF9C4'   # 淺黃 (單人平日)
    c_single_holiday = '#F48FB1'  # 深粉 (單人假日)
    
    # [關鍵修改色票用途]
    c_deep_green = '#81C784'      # 深綠 (Flap單人 OR R4扛Flap二線)
    c_deep_yellow = '#FFB74D'     # 深黃 (R4單人 OR R4扛一般二線)
    
    c_text = '#424242'
    c_line = '#E0E0E0'

    # 找出所有 R4 的名字
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
            
            # 狀態判斷
            is_single = (info['type'] == 'single')
            is_flap = current_day in flap_dates
            is_holiday = current_day in weekend_dates
            
            l1, l2 = info['line1'], info['line2']
            
            # 找出「二線/主力」是誰 (若是單人班，主力就是那唯一的人；若是雙人班，主力是 l2)
            # 在前面的邏輯中，單人班的人通常也會填在 l2，但保險起見用 fallback
            name_on_duty_l2 = l2 if l2 else l1
            
            # 判斷 R4 是否擔任二線/主力
            is_r4_duty = (name_on_duty_l2 in r4_names)

            # --- [核心底色邏輯] ---
            bg_color = c_double_normal # 預設白
            
            if is_r4_duty:
                # === R4 特殊邏輯 ===
                if is_flap:
                    bg_color = c_deep_green  # R4 扛 Flap 二線 -> 深綠
                else:
                    bg_color = c_deep_yellow # R4 扛 其他二線 (假日/平日/單人) -> 深黃
            else:
                # === 非 R4 (Senior) 一般邏輯 ===
                if is_single:
                    if is_flap: bg_color = c_deep_green    # Senior 單人 Flap -> 深綠
                    elif is_holiday: bg_color = c_single_holiday # Senior 單人 假日 -> 深粉
                    else: bg_color = c_single_normal       # Senior 單人 平日 -> 淺黃
                else:
                    # 雙人班 (Senior 為二線)
                    if is_flap: bg_color = c_double_flap   # Senior 雙人 Flap -> 淺綠
                    elif is_holiday: bg_color = c_double_holiday # Senior 雙人 假日 -> 淺粉
                    else: bg_color = c_double_normal       # Senior 雙人 平日 -> 白
            
            # 繪製方塊
            ax.add_patch(patches.Rectangle((x, y_bot), 1, row_height, linewidth=1, edgecolor=c_line, facecolor=bg_color))
            
            # 日期與 Warning 標記
            day_text = str(current_day)
            if 'warning' in info and info['warning']:
                day_text += " (!)"
            ax.text(x + 0.05, y_bot + row_height - 0.05, day_text, ha='left', va='top', fontsize=10, fontweight='bold', color=c_text)
            
            # 填入醫師名字
            if is_single:
                ax.text(x + 0.5, y_bot + row_height/2, str(name_on_duty_l2), ha='center', va='center', fontsize=16, color=c_text, fontproperties=font_prop)
            else:
                ax.text(x + 0.5, y_bot + row_height*0.65, str(l1) if l1 else "-", ha='center', va='center', fontsize=14, color=c_text, fontproperties=font_prop)
                ax.text(x + 0.5, y_bot + row_height*0.25, str(l2) if l2 else "-", ha='center', va='center', fontsize=14, color=c_text, fontproperties=font_prop)
            current_day += 1
            
    title_text = f'{year}年 {month}月 住院醫師班表'
    if "Standard" in mode: title_text += " (標準模式)"
    elif "Scenario A" in mode: title_text += " (人力充足)"
    else: title_text += " (缺工模式)"
    
    ax.text(3.5, 6.2, title_text, ha='center', va='center', fontsize=18, fontweight='bold', color=c_text, fontproperties=font_prop)

    # --- 圖例說明 (Legend) 更新 ---
    legend_y = -0.6
    ax.text(0.5, legend_y, "底色說明：", fontsize=12, fontweight='bold', color=c_text, fontproperties=font_prop)
    
    # 第一排
    ax.add_patch(patches.Rectangle((1.5, legend_y-0.15), 0.3, 0.3, facecolor=c_double_flap, edgecolor='gray')); 
    ax.text(1.9, legend_y, "Flap雙人", va='center', fontsize=10, fontproperties=font_prop)
    
    ax.add_patch(patches.Rectangle((3.0, legend_y-0.15), 0.3, 0.3, facecolor=c_double_holiday, edgecolor='gray')); 
    ax.text(3.4, legend_y, "假日雙人", va='center', fontsize=10, fontproperties=font_prop)
    
    ax.add_patch(patches.Rectangle((4.5, legend_y-0.15), 0.3, 0.3, facecolor=c_single_normal, edgecolor='gray')); 
    ax.text(4.9, legend_y, "平日單人", va='center', fontsize=10, fontproperties=font_prop)
    
    # 第二排
    legend_y2 = -1.0
    
    # [修改] 深綠色圖例說明
    ax.add_patch(patches.Rectangle((1.5, legend_y2-0.15), 0.3, 0.3, facecolor=c_deep_green, edgecolor='gray')); 
    ax.text(1.9, legend_y2, "Flap單人/R4", va='center', fontsize=10, fontproperties=font_prop)
    
    ax.add_patch(patches.Rectangle((3.0, legend_y2-0.15), 0.3, 0.3, facecolor=c_single_holiday, edgecolor='gray')); 
    ax.text(3.4, legend_y2, "假日單人", va='center', fontsize=10, fontproperties=font_prop)
    
    # [修改] 深黃色圖例說明
    ax.add_patch(patches.Rectangle((4.5, legend_y2-0.15), 0.3, 0.3, facecolor=c_deep_yellow, edgecolor='gray')); 
    ax.text(4.9, legend_y2, "R4單人/二線", va='center', fontsize=10, fontproperties=font_prop)
    
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

# --- 6. Streamlit UI ---
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







