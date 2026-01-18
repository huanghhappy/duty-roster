import streamlit as st
import pandas as pd
import random
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.font_manager as fm
import datetime
import os

# --- 設定網頁配置 ---
st.set_page_config(page_title="住院醫師排班系統 v2.0", layout="wide")

# --- 1. 中文字型設定 ---
def get_chinese_font():
    font_names = ['wqy-microhei.ttc', 'wqy-zenhei.ttc', 'NotoSansCJK-Regular.ttc', 
                  'Microsoft JhengHei.ttf', 'msjh.ttc', 'SimHei.ttf']
    search_paths = ['/usr/share/fonts', 'C:\\Windows\\Fonts', '/System/Library/Fonts']
    for path in search_paths:
        if not os.path.exists(path): continue
        for root, dirs, files in os.walk(path):
            for file in files:
                if file in font_names or any(x in file for x in ['CJK', 'Hei', 'Kai', 'Ming']):
                    return os.path.join(root, file)
    return None

font_path = get_chinese_font()
font_prop = fm.FontProperties(fname=font_path) if font_path else fm.FontProperties()

# --- 2. 排班核心邏輯 (升級版) ---

def run_scheduler(year, month, residents_data, flap_dates, fixed_shifts, vs_schedule):
    
    # 初始化
    num_days = pd.Period(f'{year}-{month}').days_in_month
    dates = range(1, num_days + 1)
    
    # 週末與假日
    weekend_dates = []
    for d in dates:
        dt = datetime.date(year, month, d)
        if dt.weekday() >= 5: weekend_dates.append(d)
    if month == 1 and 1 not in weekend_dates: weekend_dates.append(1) # 元旦
    
    # 資料結構
    seniors = [r['name'] for r in residents_data if r['rank'] in ['R5', 'R6']]
    r4s = [r['name'] for r in residents_data if r['rank'] == 'R4']
    r3s = [r['name'] for r in residents_data if r['rank'] == 'R3']
    juniors_all = r4s + r3s
    all_names = [r['name'] for r in residents_data]
    res_dict = {r['name']: r for r in residents_data}
    
    # 目標單人值班總數 (R5/R6)
    TARGET_SINGLE_DAYS = 6

    # --- 演算法開始 ---
    # 嘗試次數分配：前 3000 次嘗試標準模式，後 2000 次嘗試 Last Resort (允許 R4 單人)
    
    for attempt in range(5000):
        # 決定模式
        allow_r4_solo = False
        if attempt > 3000:
            allow_r4_solo = True # 進入不得已模式
            
        schedule = {d: {'line1': None, 'line2': None, 'type': 'double'} for d in dates}
        res_state = {name: {'count': 0, 'dates': []} for name in all_names}
        possible = True

        # 輔助：檢查可用性
        def is_available(name, day):
            if day in res_dict[name]['unavailable']: return False
            if (day - 1) in res_state[name]['dates']: return False
            if (day + 1) in res_state[name]['dates']: return False
            if res_state[name]['count'] >= 8: return False
            if day in res_state[name]['dates']: return False 
            return True

        # ==========================================
        # 步驟 1: 處理指定值班 (Fixed Shifts) - 包含邏輯判斷
        # ==========================================
        # 為了優化，我們打亂處理順序，但優先處理 R4 以便判斷搭配
        fixed_items = list(fixed_shifts.items())
        random.shuffle(fixed_items) # 隨機順序處理鎖定，增加多樣性

        for p_name, p_dates in fixed_items:
            rank = res_dict[p_name]['rank']
            for d in p_dates:
                # 記錄
                res_state[p_name]['count'] += 1
                res_state[p_name]['dates'].append(d)
                
                # --- 邏輯分支 ---
                if rank in ['R5', 'R6']:
                    # Senior 鎖定：優先填 Line 2 (若是單人班邏輯上也是 Line 2)
                    schedule[d]['line2'] = p_name
                    
                elif rank == 'R3':
                    # R3 鎖定：優先填 Line 1
                    schedule[d]['line1'] = p_name
                    
                elif rank == 'R4':
                    # R4 鎖定：複雜邏輯
                    # 邏輯 1: 嘗試找 R5/6 當 Line 2 (R4 快樂當 Line 1)
                    # 這裡只是「預判」，實際填入稍後做，但我們先佔位
                    
                    # 檢查當天是否已有 Senior 鎖定在 Line 2?
                    if schedule[d]['line2'] and schedule[d]['line2'] in seniors:
                        schedule[d]['line1'] = p_name # 完美搭配
                    else:
                        # 暫時放在 Line 1，稍後補 Senior
                        # 但如果稍後補不到 Senior 怎麼辦？
                        # 我們先放在 Line 1，若後續填補失敗，再嘗試移動
                        schedule[d]['line1'] = p_name 
                        # 標記這個 R4 需要 Senior 支援，若沒支援就要轉 Line 2
        
        # ==========================================
        # 步驟 2: 決定單人值班日 (Single Days)
        # ==========================================
        # 排除 Flap、假日、已被 R3/R4 佔據 Line 1 且無法移走的日子
        # 找出哪些日子適合單人 (平日優先)
        candidates_single = []
        for d in dates:
            if d in flap_dates: continue
            if d in weekend_dates: continue
            
            # 檢查鎖定衝突：如果這天已經鎖定了 R3 (Line 1)，那這天絕對不能單人(因為單人是 Senior/R4)
            if schedule[d]['line1'] and schedule[d]['line1'] in r3s: continue
            
            # 如果這天 R4 鎖定在 Line 1，能否轉單人？
            # 只有在 allow_r4_solo = True 時，且該 R4 同意 (但這裡是鎖定日)
            # 簡化：若 R4 鎖定，先假設這天盡量雙人，除非沒招
            
            candidates_single.append(d)

        # 隨機選出需要的單人天數 (6天)
        # 這裡要注意：如果使用者手動鎖定了 Senior 單人班 (雖然介面沒給選項，但邏輯上可能發生)
        # 目前邏輯：先隨機選空日
        candidates_single = [d for d in candidates_single if not (schedule[d]['line1'] or schedule[d]['line2'])]
        
        needed_singles = TARGET_SINGLE_DAYS
        if len(candidates_single) < needed_singles:
            # 放寬到假日
            candidates_single += [d for d in dates if d not in flap_dates and d in weekend_dates]
        
        real_singles = []
        if len(candidates_single) >= needed_singles:
            real_singles = sorted(random.sample(candidates_single, needed_singles))
            for d in real_singles: schedule[d]['type'] = 'single'

        # ==========================================
        # 步驟 3: 填補單人值班 (優先 R5/R6)
        # ==========================================
        curr_seniors = seniors[:]
        random.shuffle(curr_seniors)
        
        for d in real_singles:
            if schedule[d]['line2']: continue # 已有人(鎖定)
            
            # 找 Senior
            found = False
            curr_seniors.sort(key=lambda x: res_state[x]['count'])
            for s in curr_seniors:
                if is_available(s, d):
                    schedule[d]['line2'] = s
                    res_state[s]['count'] += 1
                    res_state[s]['dates'].append(d)
                    found = True
                    break
            
            if not found and allow_r4_solo:
                # --- Last Resort: R4 單人 ---
                valid_r4 = [r for r in r4s if is_available(r, d)]
                if valid_r4:
                    valid_r4.sort(key=lambda x: res_state[x]['count'])
                    r4 = valid_r4[0]
                    schedule[d]['line2'] = r4 # 單人放在 Line 2 位置顯示
                    res_state[r4]['count'] += 1
                    res_state[r4]['dates'].append(d)
                    found = True
            
            if not found: possible = False; break

        if not possible: continue

        # ==========================================
        # 步驟 4: 處理 R4 鎖定日期的邏輯檢核 (補人)
        # ==========================================
        # 針對那些 R4 已經鎖定在 Line 1 的日子，我們必須幫他找 Line 2
        for d in dates:
            if schedule[d]['type'] == 'double' and schedule[d]['line1'] in r4s and schedule[d]['line2'] is None:
                # 情況：R4 指定值班，目前在 Line 1，缺 Line 2
                
                # 優先：找 Senior
                valid_s = [s for s in seniors if is_available(s, d)]
                if valid_s:
                    valid_s.sort(key=lambda x: res_state[x]['count'])
                    s = valid_s[0]
                    schedule[d]['line2'] = s
                    res_state[s]['count'] += 1
                    res_state[s]['dates'].append(d)
                else:
                    # 沒 Senior！R4 必須降級去當 Line 2 (為了帶 R3)
                    r4_name = schedule[d]['line1']
                    schedule[d]['line1'] = None # 先清空
                    schedule[d]['line2'] = r4_name # 移到二線
                    
                    # 現在找 R3 當一線
                    valid_r3 = [r for r in r3s if is_available(r, d)]
                    if valid_r3:
                        valid_r3.sort(key=lambda x: res_state[x]['count'])
                        r3 = valid_r3[0]
                        schedule[d]['line1'] = r3
                        res_state[r3]['count'] += 1
                        res_state[r3]['dates'].append(d)
                    else:
                        # 連 R3 都沒有！
                        if allow_r4_solo and d not in flap_dates:
                            # 變成 R4 單人
                            schedule[d]['type'] = 'single'
                            # R4 已經在 Line 2 了，ok
                        else:
                            possible = False # 無解
            
            if not possible: break
        if not possible: continue

        # ==========================================
        # 步驟 5: 填補剩餘雙人班 Line 2 (Senior > R4)
        # ==========================================
        days_needs_l2 = [d for d in dates if schedule[d]['type'] == 'double' and schedule[d]['line2'] is None]
        # 排序：Flap 優先 -> 假日 -> 平日
        days_needs_l2.sort(key=lambda x: (0 if x in flap_dates else 1, 0 if x in weekend_dates else 1))

        for d in days_needs_l2:
            # 優先找 Senior
            valid_s = [s for s in seniors if is_available(s, d)]
            if valid_s:
                valid_s.sort(key=lambda x: res_state[x]['count'])
                s = valid_s[0]
                schedule[d]['line2'] = s
                res_state[s]['count'] += 1
                res_state[s]['dates'].append(d)
            else:
                # 沒 Senior，找 R4 (Line 2)
                valid_r4 = [r for r in r4s if is_available(r, d)]
                # 排除已經在 Line 1 的 R4 (雖然後面邏輯擋掉，但這裡再次確認)
                if schedule[d]['line1'] in r4s: valid_r4 = [] # 不能自己跟自己值

                if valid_r4:
                    valid_r4.sort(key=lambda x: res_state[x]['count'])
                    r4 = valid_r4[0]
                    schedule[d]['line2'] = r4
                    res_state[r4]['count'] += 1
                    res_state[r4]['dates'].append(d)
                    
                    # 強制規則：L2 是 R4，L1 必須是 R3
                    # 如果 L1 空著 -> 找 R3
                    if schedule[d]['line1'] is None:
                        valid_r3 = [r for r in r3s if is_available(r, d)]
                        if valid_r3:
                            valid_r3.sort(key=lambda x: res_state[x]['count'])
                            r3 = valid_r3[0]
                            schedule[d]['line1'] = r3
                            res_state[r3]['count'] += 1
                            res_state[r3]['dates'].append(d)
                        else:
                            possible = False # 有 R4 二線但沒 R3 一線
                    # 如果 L1 已有鎖定的人
                    elif schedule[d]['line1'] not in r3s:
                        possible = False # 鎖定衝突 (例如鎖定了 R4+R4)
                else:
                    # 沒 Senior 也沒 R4
                    if allow_r4_solo and d not in flap_dates and schedule[d]['line1'] is None:
                        # 轉單人 (需要 Line 1 是空的，或把 Line 1 的 R4 變單人)
                        # 這裡比較複雜，暫時視為失敗，除非...
                        possible = False

            if not possible: break
        
        if not possible: continue

        # ==========================================
        # 步驟 6: 填補剩餘 Line 1
        # ==========================================
        days_needs_l1 = [d for d in dates if schedule[d]['type'] == 'double' and schedule[d]['line1'] is None]
        for d in days_needs_l1:
            # 優先找 Juniors
            valid_j = [j for j in juniors_all if is_available(j, d)]
            # 排除已在 Line 2 的人
            l2 = schedule[d]['line2']
            valid_j = [j for j in valid_j if j != l2]
            
            if not valid_j:
                # 極少數情況：Seniors 補位 Line 1? (通常不建議，但為了排出來...)
                valid_j = [s for s in seniors if is_available(s, d) and s != l2]
            
            if valid_j:
                valid_j.sort(key=lambda x: res_state[x]['count'])
                j = valid_j[0]
                schedule[d]['line1'] = j
                res_state[j]['count'] += 1
                res_state[j]['dates'].append(d)
            else:
                possible = False; break
        
        if not possible: continue
        
        # 最終檢查：每人至少 8 班 (或剛好 8 班)
        # 由於鎖定可能導致有人超過 8 班，我們這裡只檢查是否有人 < 8
        min_shifts = min(res_state[r]['count'] for r in all_names)
        if min_shifts >= 8:
            return schedule, res_state, allow_r4_solo
            
    return None, None, False

# --- 3. 繪圖邏輯 (無印風) ---
def plot_schedule(year, month, schedule, flap_dates, vs_schedule, font_prop, is_r4_solo_mode):
    
    # Muji Colors
    c_flap = '#E8F5E9'   # 淡綠
    c_single = '#FFF9C4' # 淡黃
    c_r4_solo = '#FFE0B2' # 淡橘 (用於區分 R4 單人，如果不希望區分可改回 c_single)
    c_holiday = '#FFEBEE' # 淡紅
    c_normal = '#FFFFFF'
    c_text = '#424242'
    c_line = '#E0E0E0'

    fig, ax = plt.subplots(figsize=(12, 10))
    ax.set_xlim(0, 7)
    ax.set_ylim(0, 6)
    ax.axis('off')

    # 計算週數
    cal = pd.Period(f'{year}-{month}')
    start_weekday = datetime.date(year, month, 1).weekday()
    days_in_month = cal.days_in_month
    total_slots = start_weekday + days_in_month
    weeks = (total_slots // 7) + (1 if total_slots % 7 > 0 else 0)
    
    row_height = (6 - 0.5) / weeks
    
    # Headers
    weekdays_text = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
    for i, d in enumerate(weekdays_text):
        ax.text(i + 0.5, 6 - 0.25, d, ha='center', va='center', fontsize=12, fontweight='bold', color=c_text)

    current_day = 1
    for w in range(weeks):
        # VS
        vs_name = vs_schedule[w] if w < len(vs_schedule) else ""
        ax.text(-0.3, 6 - 0.5 - w * row_height - row_height/2, f"{vs_name}", 
                ha='center', va='center', fontsize=14, fontweight='bold', color=c_text, fontproperties=font_prop)

        for d_idx in range(7):
            if w == 0 and d_idx < start_weekday: continue
            if current_day > days_in_month: break
                
            x = d_idx
            y_top = 6 - 0.5 - w * row_height
            y_bot = y_top - row_height
            
            # Logic for color
            info = schedule[current_day]
            is_flap = current_day in flap_dates
            dt = datetime.date(year, month, current_day)
            is_holiday = dt.weekday() >= 5 or (month==1 and current_day==1)
            is_single = (info['type'] == 'single')
            
            bg_color = c_normal
            if is_single: bg_color = c_single
            elif is_flap: bg_color = c_flap
            elif is_holiday: bg_color = c_holiday
            
            rect = patches.Rectangle((x, y_bot), 1, row_height, linewidth=1, edgecolor=c_line, facecolor=bg_color)
            ax.add_patch(rect)
            
            # Date
            ax.text(x + 0.05, y_top - 0.05, str(current_day), ha='left', va='top', fontsize=10, fontweight='bold', color=c_text)
            
            # Names
            l1 = info['line1']
            l2 = info['line2']
            
            if is_single:
                name_show = l2 if l2 else l1
                ax.text(x + 0.5, y_bot + row_height/2, str(name_show), 
                        ha='center', va='center', fontsize=16, color=c_text, fontproperties=font_prop)
            else:
                ax.text(x + 0.5, y_top - row_height*0.35, str(l1) if l1 else "-", 
                        ha='center', va='center', fontsize=14, color=c_text, fontproperties=font_prop)
                ax.text(x + 0.5, y_top - row_height*0.75, str(l2) if l2 else "-", 
                        ha='center', va='center', fontsize=14, color=c_text, fontproperties=font_prop)
            
            current_day += 1
            
    title_text = f'{year}年 {month}月 住院醫師班表'
    if is_r4_solo_mode: title_text += " (含R4單人支援)"
    plt.title(title_text, fontsize=16, pad=20, color=c_text, fontproperties=font_prop)
    return fig

# --- 4. Streamlit UI ---

st.title("🏥 智慧排班系統 v2.0 (邏輯升級版)")
st.markdown("""
<style>
    .stButton>button { width: 100%; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

col_a, col_b = st.columns([1, 2])
with col_a:
    year = st.number_input("年份", 2024, 2030, 2026)
    month = st.number_input("月份", 1, 12, 1)
    num_residents = st.number_input("住院醫師總數", 4, 15, 7)

with col_b:
    st.info("""
    **邏輯說明：**
    1. **指定值班**：若 R4 指定值班，系統優先幫找 Senior 搭檔；若無 Senior，R4 轉二線並配 R3；若皆無，才考慮單人。
    2. **R4 二線規則**：R4 只有在 Senior 不足時才升級做二線，且一線強制搭配 R3。
    3. **生存模式**：若正規排法無解，系統會自動啟動「R4 單人值班」作為最後手段。
    """)

st.subheader("1. 醫師名單與個別設定")
# 動態欄位
residents_input = []
cols = st.columns(3)
days_in_month = pd.Period(f'{year}-{month}').days_in_month
all_days = list(range(1, days_in_month + 1))
fixed_shifts_map = {}

for i in range(num_residents):
    with cols[i % 3]:
        with st.container(border=True):
            name = st.text_input(f"姓名", value=f"醫師{i+1}", key=f"n_{i}")
            rank = st.selectbox(f"職級", ['R3', 'R4', 'R5', 'R6'], key=f"r_{i}")
            
            # 休假
            off = st.multiselect("休假 (OFF)", all_days, key=f"off_{i}")
            # 指定值班
            fix = st.multiselect("指定值班 (LOCK)", all_days, key=f"fix_{i}")
            
            residents_input.append({'name': name, 'rank': rank, 'unavailable': off})
            if fix: fixed_shifts_map[name] = fix

st.subheader("2. 全局參數")
flap_input = st.multiselect("Flap 手術日 (全雙人/優先資深)", all_days)

st.subheader("3. VS 輪值")
vs_input = []
c_vs = st.columns(6)
for i in range(6):
    with c_vs[i]:
        v = st.text_input(f"第 {i+1} 週 VS", key=f"vs_{i}")
        if v: vs_input.append(v)

st.markdown("---")

if st.button("🚀 開始排程運算", type="primary"):
    with st.spinner("正在嘗試數千種組合，請稍候..."):
        schedule, stats, r4_solo_mode = run_scheduler(
            year, month, residents_input, flap_input, fixed_shifts_map, vs_input
        )
        
        if schedule:
            if r4_solo_mode:
                st.warning("⚠️ 注意：由於限制條件嚴格，系統已啟用「生存模式」，安排了部分 R4 單人值班。")
            else:
                st.success("✅ 排班成功！完全符合標準邏輯 (無 R4 單人)。")
            
            # 統計數據
            st.write("### 📊 排班統計")
            df_stat = pd.DataFrame.from_dict(stats, orient='index')
            st.dataframe(df_stat, use_container_width=True)
            
            # 圖表
            fig = plot_schedule(year, month, schedule, flap_input, vs_input, font_prop, r4_solo_mode)
            st.pyplot(fig)
            
            # 下載
            fn = f"schedule_{year}_{month}.png"
            plt.savefig(fn, dpi=200, bbox_inches='tight')
            with open(fn, "rb") as img:
                st.download_button("⬇️ 下載圖檔", img, file_name=fn, mime="image/png")
        else:
            st.error("❌ 排班失敗。條件過於嚴苛 (如：鎖定日期造成連續值班、人力嚴重不足)。請減少鎖定天數後重試。")